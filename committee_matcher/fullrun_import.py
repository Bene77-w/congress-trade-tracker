"""Uebernimmt einen abgenommenen Fullrun als Mapping.

Der Fullrun laesst den ganzen Korpus von einer CLI labeln
(``committee_matcher/goldset_label.py label --fullrun``). Was Bene in der
Reviewmappe abgenommen hat, wird hier zu den beiden Dateien, die der Rest des
Programms als Mapping liest:

- ``reviews/<review-id>/reviewed_assignments.csv``
- ``reviews/<review-id>/reviewed_unit_coverage.csv``

Dieses Modul darf die Matcher-Vertraege benutzen, das Labelwerkzeug nicht. Die
Unabhaengigkeitsregel dort gilt dem Gold-Set: es misst den Matcher und darf
deshalb nicht aus ihm entstehen. Ein Fullrun ist kein Massstab, sondern
Produktivmapping - er soll die Formate des Matchers treffen und nicht eigene
erfinden. Das Labelwerkzeug ruft dieses Modul als eigenen Prozess auf, damit
die Trennung im Labelpfad bestehen bleibt.

Was hier **nicht** geprueft wird: Belegregel, Anzahlgrenze und
Broad-Scope-Bedingungen. Die prueft ``goldset_label.py verify`` unmittelbar
vorher, und ``finish`` bricht bei jedem Befund ab. Eine zweite Auslegung
derselben Regeln waere eine zweite Wahrheit.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import load_workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from committee_matcher import artifacts, paths, review, taxonomy_snapshot  # noqa: E402
from committee_matcher import goldset_label as gs  # noqa: E402
from committee_matcher.errors import ErrorCollector  # noqa: E402

#: Herkunftsvermerk je Engine. Die CLIs rechnen ueber das Abo ab und melden
#: weder Provider noch Generation-ID; der Vermerk nennt deshalb den Aufrufweg
#: und nicht einen erfundenen Anbieter.
PROVIDER = {"claude": "claude-cli", "codex": "codex-cli"}

#: Fassung des Uebernahmemanifests dieses Wegs. Bewusst nicht die Fassung des
#: regulaeren Reviewimports: dort steht eine Aenderungsbilanz gegen einen
#: Modellauf, den es hier nicht gibt.
MANIFEST_VERSION = "fullrun-import-v1"
MANIFEST_FILENAME = "import_manifest.json"

#: Felder, deren Abweichung eine Zeile zur Handarbeit macht. ``unit_id`` und
#: ``industry_id`` bilden den Schluessel und stehen deshalb nicht darunter.
COMPARED_FIELDS = ("confidence", "evidence_excerpt", "rationale_short")


class ImportBlocked(Exception):
    """Ein Grund, aus dem kein Mapping entsteht."""

    def __init__(self, was: str, abhilfe: str) -> None:
        super().__init__(was)
        self.was = was
        self.abhilfe = abhilfe


def _blocked(was: str, abhilfe: str) -> ImportBlocked:
    return ImportBlocked(was, abhilfe)


def read_sheet(book, name: str, columns: Sequence[str]) -> list[dict[str, str]]:
    """Liest ein Blatt der Reviewmappe als Werte und prueft die Kopfzeile.

    Die Kopfzeilenpruefung ist keine Doppelung der Pruefung in ``verify``: sie
    faengt den Fall, dass zwischen Abnahme und Uebernahme eine andere Datei
    untergeschoben wurde, und verhindert, dass spaltenweise falsch gelesene
    Werte als Mapping in die Pipeline gehen.
    """
    if name not in book.sheetnames:
        raise _blocked(
            f"Das Blatt {name!r} fehlt in der Reviewmappe.",
            "Mappe mit 'goldset_label.py to-xlsx --fullrun' neu erzeugen und "
            "die Aenderungen uebertragen.")
    sheet = book[name]
    header = [str(cell.value or "").strip() for cell in sheet[1]] if sheet.max_row else []
    if header[:len(columns)] != list(columns):
        raise _blocked(
            f"Blatt {name!r}: Kopfzeile {header[:len(columns)]} statt "
            f"{list(columns)}.",
            "Blatt nicht umbauen; 'to-xlsx --fullrun' erzeugt die Mappe neu.")
    rows: list[dict[str, str]] = []
    for line in range(2, sheet.max_row + 1):
        werte = {column: str(sheet.cell(row=line, column=index).value or "").strip()
                 for index, column in enumerate(columns, start=1)}
        if any(werte.values()):
            rows.append(werte)
    return rows


def _by_unit(rows: Sequence[Mapping[str, str]]) -> dict[str, list[dict[str, str]]]:
    gruppen: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        gruppen.setdefault(row["unit_id"], []).append(dict(row))
    return gruppen


def _wahrheit(wert: str) -> bool:
    """Broad-Scope-Zelle als Wahrheitswert, exakt wie ``verify`` sie liest.

    Bewusst nur ``ja``: ``to-xlsx`` schreibt ``ja``/``nein``, und ``verify``
    prueft auf ``ja``. Ein zusaetzlich akzeptiertes ``true`` waere die
    Umgehung - die Pruefung auf die Pflichtbegruendung liefe daran vorbei, die
    Uebernahme setzte die Ausnahme trotzdem.
    """
    return wert.strip().casefold() == "ja"


def _unveraendert(row: Mapping[str, str], vorlage: Mapping[str, str]) -> bool:
    return all(row.get(field, "") == vorlage.get(field, "")
               for field in COMPARED_FIELDS)


def build_units(labels: Sequence[Mapping[str, str]],
                notes: Mapping[str, Mapping[str, str]],
                gelaufen: Mapping[tuple[str, str], Mapping[str, str]],
                herkunft: Mapping[str, Mapping[str, str]],
                ) -> dict[str, review.EffectiveUnit]:
    """Baut den Reviewstand je Unit aus den Blaettern der Mappe.

    Die Entscheidung wird nicht aus einer Spalte gelesen, sondern aus dem
    Bestand abgeleitet: eine Unit mit Zuordnungen ist ``mapped``, eine ohne
    ``no_mapping``. Eine Steuerspalte, die dem widersprechen kann, war im
    Reviewpfad die Hauptfehlerquelle.
    """
    units: dict[str, review.EffectiveUnit] = {}
    for unit_id, rows in _by_unit(labels).items():
        note = notes.get(unit_id, {})
        zuordnungen = []
        for row in rows:
            industry_id = row["industry_id"]
            if not industry_id:
                continue
            vorlage = gelaufen.get((unit_id, industry_id))
            aus_lauf = vorlage is not None and _unveraendert(row, vorlage)
            zuordnungen.append(review.EffectiveAssignment(
                unit_id=unit_id, industry_id=industry_id,
                confidence=row["confidence"],
                evidence_excerpt=row["evidence_excerpt"],
                rationale_short=row["rationale_short"],
                source="model" if aus_lauf else "manual",
                origin=herkunft.get(unit_id, {}) if aus_lauf else {}))
        gruende = [row["no_mapping_reason"] for row in rows
                   if row["no_mapping_reason"]]
        units[unit_id] = review.EffectiveUnit(
            unit_id=unit_id,
            decision="mapped" if zuordnungen else "no_mapping",
            no_mapping_reason="" if zuordnungen else (gruende[0] if gruende else ""),
            broad_scope_exception=_wahrheit(note.get("broad_scope", "")),
            broad_scope_reason=note.get("broad_scope_reason", ""),
            assignments=tuple(zuordnungen))
    return units


def base_engine_of(source: str) -> str:
    """Der Lauf, dessen Zeilen unter ``source`` uebernommen werden.

    ``info_merged.csv`` traegt beide Laeufe, ``labels_merged.csv`` dagegen nur
    die Basis. Ohne diese Bindung nennte die Herkunft irgendeine der beiden
    Engines - in der Praxis die alphabetisch letzte - und behauptete damit ein
    Modell, das die Zeile nie erzeugt hat.
    """
    return gs.MERGE_BASE_ENGINE if source == "merged" else source


def origins_from(infos: Sequence[Mapping[str, str]],
                 tasks: Mapping[str, Mapping[str, Any]],
                 engine: str,
                 ) -> dict[str, dict[str, str]]:
    """Die Herkunft je Unit, so weit ein CLI-Lauf sie belegen kann.

    Gelesen werden ausschliesslich die Zeilen von ``engine``: nur dieser Lauf
    hat die uebernommenen Labels erzeugt.

    ``call_id``, ``generation_id`` und ``from_repair`` bleiben leer: die CLIs
    kennen sie nicht, und ein gefuellter Wert waere eine Falschaussage ueber
    die Herkunft eines Urteils.
    """
    herkunft: dict[str, dict[str, str]] = {}
    for info in infos:
        if info.get("engine", "") != engine:
            continue
        unit_id = info["unit_id"]
        binding = (tasks.get(unit_id) or {}).get("binding") or {}
        herkunft[unit_id] = {
            "provider": PROVIDER.get(engine, engine),
            "model": info.get("model", ""),
            "prompt_id": str(binding.get("prompt_id", "")),
            "prompt_version": info.get("prompt_version", ""),
            "prompt_sha256": info.get("prompt_sha256", ""),
        }
    return herkunft


def load_tasks(unit_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Die Aufgabenpakete der genannten Units, soweit vorhanden."""
    tasks: dict[str, dict[str, Any]] = {}
    for unit_id in unit_ids:
        paket = gs.read_json(gs.TASK_ROOT / f"{unit_id}.json")
        if isinstance(paket, dict):
            tasks[unit_id] = paket
    return tasks


def _snapshot():
    collector = ErrorCollector()
    snapshot = taxonomy_snapshot.approved_snapshot(collector)
    if snapshot is None:
        detail = "; ".join(f"{finding.code} {finding.message}"
                           for finding in collector.findings) or "kein Befund"
        raise _blocked(
            f"Die freigegebene Taxonomie ist nicht ladbar: {detail}.",
            "'python -m committee_matcher snapshot ...' pruefen; ohne Snapshot "
            "fehlen Industry- und Sektornamen.")
    return snapshot


def _unbekannte_units(units: Mapping[str, Any],
                      corpus: Mapping[str, Any]) -> list[str]:
    return sorted(unit_id for unit_id in units if unit_id not in corpus)


def _unbekannte_industries(units: Mapping[str, Any], snapshot) -> list[str]:
    bekannt = snapshot.industry_ids()
    return sorted({entry.industry_id
                   for state in units.values()
                   for entry in state.assignments
                   if entry.industry_id not in bekannt})


def import_fullrun(review_id: str, source: str) -> dict[str, Any]:
    """Schreibt das Mapping der abgenommenen Fullrun-Mappe.

    ``run_id`` traegt denselben Wert wie ``review_id``: es gab keinen Lauf im
    Sinne des Matchers, dem ein eigener ``run_id`` zustuende, und ein
    erfundener zweiter Name suggerierte eine Runrevision, die niemand findet.
    """
    mappe = gs.WORKBOOK_PATH
    if not mappe.exists():
        raise _blocked(
            f"Die Reviewmappe {paths.relative_to_base(mappe)} fehlt.",
            "'goldset_label.py label --fullrun' erzeugt sie.")
    if review.reviewed_path_exists(review_id):
        raise _blocked(
            f"Die Revision {review_id!r} traegt bereits einen Importstand.",
            "Anderen Namen waehlen; ein vorhandener Stand wird nicht "
            "ueberschrieben.")

    try:
        book = load_workbook(mappe, data_only=False)
    except Exception as fehler:  # openpyxl wirft je nach Schaden anderes
        raise _blocked(
            f"Die Reviewmappe {paths.relative_to_base(mappe)} ist nicht "
            f"lesbar: {type(fehler).__name__}: {fehler}.",
            "Mappe in Excel oeffnen und als .xlsx speichern, oder mit "
            "'goldset_label.py to-xlsx --fullrun' neu erzeugen.") from fehler
    labels = read_sheet(book, gs.SHEET_LABELS, gs.LABEL_COLUMNS)
    notes = {row["unit_id"]: row
             for row in read_sheet(book, gs.SHEET_NOTES, gs.NOTE_COLUMNS)}
    if not labels:
        raise _blocked(
            "Das Blatt 'labels' der Reviewmappe ist leer.",
            "Ohne Zeilen entstuende ein Mapping ohne Inhalt; Mappe pruefen.")

    gelaufen = {(row["unit_id"], row["industry_id"]): row
                for row in gs.read_csv(gs.labels_path(source), gs.LABEL_COLUMNS,
                                       was=f"mit den Labels von {source}")
                if row["industry_id"]}
    infos = gs.read_csv(gs.info_path(source), gs.INFO_COLUMNS,
                        was=f"mit der Provenienz von {source}")

    unit_ids = sorted({row["unit_id"] for row in labels})
    herkunft = origins_from(infos, load_tasks(unit_ids), base_engine_of(source))
    units = build_units(labels, notes, gelaufen, herkunft)

    corpus = gs.load_corpus_units()
    fremd = _unbekannte_units(units, corpus)
    if fremd:
        raise _blocked(
            f"{len(fremd)} Units der Mappe kennt der Korpus nicht: "
            + ", ".join(fremd[:3]) + (" ..." if len(fremd) > 3 else "") + ".",
            "Korpus und Mappe stammen aus verschiedenen Staenden; Fullrun mit "
            "'prepare --fullrun' auf den aktuellen Korpus bringen.")
    snapshot = _snapshot()

    # Der Export holt Industry- und Sektornamen aus dem Snapshot. Eine ID, die
    # er nicht kennt, faellt dabei nicht auf: die Zeile entstuende mit leeren
    # Namen und ginge als gueltiges Mapping in die Pipeline.
    unbekannt = _unbekannte_industries(units, snapshot)
    if unbekannt:
        raise _blocked(
            f"{len(unbekannt)} Industry-IDs der Mappe stehen nicht in der "
            "freigegebenen Taxonomie: "
            + ", ".join(unbekannt[:5]) + (" ..." if len(unbekannt) > 5 else "")
            + ".",
            "IDs gegen den freigegebenen Snapshot pruefen; Schreibweise "
            "beachten (Excel korrigiert Bindestrichnamen gern eigenmaechtig).")

    # Eine Zeile gilt als Modellurteil, wenn sie unveraendert aus dem Lauf
    # kommt. Fehlt dazu die Provenienz, ist die Sammlung unvollstaendig - die
    # Zeile behauptete sonst ein Modell, das niemand mehr benennen kann.
    ohne_beleg = sorted(unit_id for unit_id, state in units.items()
                        if any(entry.source == "model" and not entry.origin
                               for entry in state.assignments))
    if ohne_beleg:
        raise _blocked(
            f"{len(ohne_beleg)} Units tragen Modellzeilen ohne Provenienz: "
            + ", ".join(ohne_beleg[:3])
            + (" ..." if len(ohne_beleg) > 3 else "") + ".",
            f"'goldset_label.py collect --fullrun --engine "
            f"{base_engine_of(source)}' wiederholen; die Provenienzdatei ist "
            "unvollstaendig.")

    zuordnungen = sum(len(state.assignments) for state in units.values())
    manuell = sum(1 for state in units.values() for entry in state.assignments
                  if entry.source == "manual")
    ohne = sorted(unit_id for unit_id, state in units.items()
                  if state.decision == "no_mapping")
    payload = {
        "version": MANIFEST_VERSION,
        "review_id": review_id,
        "run_id": review_id,
        "origin": "goldset_label.py --fullrun",
        "source": source,
        "snapshot_id": snapshot.snapshot_id,
        "taxonomy_content_hash": snapshot.taxonomy_content_hash,
        "unit_count": len(units),
        "assignment_count": zuordnungen,
        "manual_assignment_count": manuell,
        "no_mapping_units": ohne,
        "review_status": review.REVIEW_STATUS_REVIEWED,
        "files": [review.REVIEWED_ASSIGNMENTS_FILENAME,
                  review.REVIEWED_COVERAGE_FILENAME],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Das Manifest steht zuletzt: es ist der Marker, an dem ein spaeterer Lauf
    # die Revision als uebernommen erkennt. Geschrieben wird ueber die
    # modulinterne Funktion des Reviews - sie raeumt bereits geschriebene
    # Dateien wieder weg, wenn eine spaetere scheitert. Ein eigener
    # Schreibweg waere ein zweites Verhalten fuer dasselbe Verzeichnis.
    written = review._write_review_files(review_id, (
        (review.REVIEWED_ASSIGNMENTS_FILENAME,
         artifacts.render_csv(
             review.REVIEWED_COLUMNS,
             review.reviewed_rows(review_id, review_id, units, corpus, snapshot))),
        (review.REVIEWED_COVERAGE_FILENAME,
         artifacts.render_csv(
             review.REVIEWED_COVERAGE_COLUMNS,
             review.reviewed_coverage_rows(review_id, review_id, units, corpus))),
        (MANIFEST_FILENAME, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
    ))
    payload["written"] = list(written)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m committee_matcher.fullrun_import",
        description="Uebernimmt die abgenommene Fullrun-Reviewmappe als "
                    "Mapping in eine Reviewrevision.")
    parser.add_argument("--review-id", required=True, metavar="NAME",
                        help="Revision, in die das Mapping geschrieben wird")
    parser.add_argument("--source", default="merged", metavar="QUELLE",
                        help="Zwischentabelle des Vergleichs: 'merged' oder "
                             "ein Enginename; Default 'merged'")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Der Fullrun liegt in eigener Ablage; ohne den Umschalter laèse dieses
    # Modul die Gold-Set-Mappe.
    gs.use_fullrun_paths()
    try:
        payload = import_fullrun(args.review_id, args.source)
    except ImportBlocked as blocked:
        print(f"Kein Mapping: {blocked.was}", file=sys.stderr)
        print(f"Abhilfe: {blocked.abhilfe}", file=sys.stderr)
        return 1
    except gs.GoldsetBlocked as blocked:
        print(blocked.problem.render(), file=sys.stderr)
        return 1
    print(f"Mapping der Revision {args.review_id}: {payload['unit_count']} "
          f"Units, {payload['assignment_count']} Zuordnungen davon "
          f"{payload['manual_assignment_count']} von Hand, "
          f"{len(payload['no_mapping_units'])} ohne Zuordnung")
    for name in payload["written"]:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
