"""Review-Workbook des Committee Matchers V2 (Plan 2.8, Phase P7d).

Der Export spiegelt genau eine abgeschlossene Runrevision in eine XLSX. Er
liest ausschliesslich die geschriebenen Artefakte, nie den Laufzustand: was
Bene reviewt, ist der Stand, der auch in den CSVs steht.

Das Workbook ist eine Arbeitstabelle, kein Formular. Wer reviewt, aendert den
Wert, den er anders sieht - er traegt keine Aktion, keine Entscheidung und
keine Begruendung dazu ein. Was geschehen ist, rechnet der Import selbst aus,
indem er jede Zeile gegen die Laufartefakte haelt:

* gleiche Werte -> die Zuordnung stammt weiter vom Modell und behaelt ihre
  Callprovenienz;
* geaenderte oder neue Werte -> ``assignment_source=manual`` ohne
  Callprovenienz, da hinter einer Handeingabe kein Modellaufruf steht;
* fehlende Zeile -> die Zuordnung ist entfernt;
* keine Zuordnung mehr fuer eine Unit -> ``no_mapping``.

Zwei Eigenschaften bleiben Vertrag:

* Jede Zelle wird als Textzelle geschrieben. Ohne den erzwungenen Zelltyp
  wuerde openpyxl einen Wert mit fuehrendem ``=`` als Formel typisieren; der
  Rueckimport bekaeme dann eine Formel statt des Werts zu sehen.
* Die Bindung an Lauf, Snapshot und Inputstand wird nachgerechnet. Ein
  Workbook aus einem anderen Lauf wird nie importiert.

Geprueft wird nur, was das Ergebnis unbrauchbar machen wuerde: unbekannte
Unit, unbekannte Industry, doppelte Industry, unlesbare Confidence. Die
Anzahlgrenzen des Modellvertrags gelten fuer den Menschen als Hinweis, nicht
als Sperre - er entscheidet, das Modell nicht.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import artifacts, paths, validation, workflow
from .errors import ErrorCollector, MatcherError, Stage
from .models import MAX_CONFIDENCE, MIN_CONFIDENCE
from .status import ExitCode

#: Reviewrevisionen liegen neben den Runrevisionen, nicht in ihnen: ein Review
#: darf die Artefakte des Laufs nicht beruehren.
REVIEWS_DIR = "reviews"
WORKBOOK_FILENAME = "review.xlsx"
EXPORT_MANIFEST_FILENAME = "export_manifest.json"

WORKBOOK_VERSION = "review-workbook-v2"
EXPORT_MANIFEST_VERSION = "review-export-v2"

SHEET_ASSIGNMENTS = "assignments"
SHEET_BOUNDARY = "boundary"
SHEET_TAXONOMY = "taxonomy"
SHEET_INFO = "info"

#: Schluessel der Zeile. Kammer, Committee- und Unitname stehen bewusst nicht
#: im Workbook: sie sind aus der ``unit_id`` ableitbar, und jede zusaetzliche
#: Spalte waere eine Stelle mehr, an der Excel und Korpus auseinanderlaufen
#: koennen. Den Klartext holt sich der Import aus dem Korpus.
CONTEXT_COLUMNS: tuple[str, ...] = ("unit_id",)
#: Die Werte, um die es geht. Leere ``industry_id`` heisst: diese Unit hat
#: keine Zuordnung.
VALUE_COLUMNS: tuple[str, ...] = (
    "industry_id", "confidence", "evidence_excerpt", "rationale_short",
    "no_mapping_reason",
)
ASSIGNMENT_SHEET_COLUMNS: tuple[str, ...] = CONTEXT_COLUMNS + VALUE_COLUMNS

#: Konfliktblatt; reine Uebersicht ohne Eingabefeld. Beruehren mehrere Units
#: derselben Committee-Familie dieselbe Industry, ist das haeufig richtig; wo
#: es das nicht ist, wird die betroffene Zeile im Blatt ``assignments``
#: bearbeitet. Ein zweiter Entscheidungsweg fuer denselben Sachverhalt waere
#: nur eine weitere Fehlerquelle.
BOUNDARY_SHEET_COLUMNS: tuple[str, ...] = (
    "family_id", "industry_id", "industry_name", "unit_count", "unit_ids",
)
#: Nachschlagewerk: die zulaessigen Industries des Laufsnapshots. Das Blatt
#: speist zugleich die Auswahlliste der Spalte ``industry_id``.
TAXONOMY_SHEET_COLUMNS: tuple[str, ...] = (
    "industry_id", "industry_name", "sector_id", "sector_name",
)

#: Unitstatus, die kein Reviewurteil tragen und deshalb keine Zeile bekommen.
UNDECIDED_STATUS: frozenset[str] = frozenset({"failed", "not_decided"})

#: Felder, deren Aenderung eine Zuordnung zur Handarbeit macht. Reihenfolge
#: ohne Bedeutung; verglichen wird gegen die Zeile des Laufs.
COMPARED_FIELDS: tuple[str, ...] = (
    "confidence", "evidence_excerpt", "rationale_short",
)

#: Spalten, deren Wert eine Kennung ist. Nur sie werden beim Lesen getrimmt.
STRIPPED_COLUMNS: frozenset[str] = frozenset({
    "unit_id", "industry_id", "confidence",
})


class ReviewError(MatcherError):
    """Fehler im Reviewpfad."""


def _blocked(code: str, obj: str, message: str, hint: str,
             exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> ReviewError:
    collector = ErrorCollector()
    collector.blocker(code, Stage.REVIEW, obj, message, hint, exit_code=exit_code)
    return ReviewError(collector.blockers, message)


def review_path(review_id: str, *parts: str, create_parents: bool = False) -> Path:
    return paths.artifact_path(REVIEWS_DIR, review_id, *parts,
                               create_parents=create_parents)


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _read_csv(path: Path, columns: Sequence[str]) -> tuple[dict[str, str], ...]:
    """Liest ein Artefakt und prueft seinen Spaltenvertrag."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _blocked(
            "REV_ARTIFACT_UNREADABLE", paths.relative_to_base(path),
            f"Das Artefakt ist nicht lesbar: {exc}.",
            "Die Runrevision muss vollstaendig geschrieben sein; "
            "'run execute' abschliessen.")
    # ``newline=""`` haelt eingebettete Zeilenumbrueche zusammen. Ueber
    # ``splitlines()`` gelesen wuerde ein mehrzeiliges Feld ohne seine
    # Umbrueche zurueckkommen - der Wert waere ein anderer als der
    # geschriebene, und der Vergleich gegen den Lauf schluege still fehl.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != tuple(columns):
        raise _blocked(
            "REV_ARTIFACT_CONTRACT", paths.relative_to_base(path),
            "Die Spalten des Artefakts weichen vom Vertrag ab.",
            "Artefakt aus derselben Programmversion erzeugen; von Hand "
            "geaenderte CSVs werden nicht gelesen.")
    rows: list[dict[str, str]] = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise _blocked(
                "REV_ARTIFACT_CONTRACT", paths.relative_to_base(path),
                "Eine Zeile des Artefakts hat eine andere Feldzahl als die "
                "Kopfzeile.",
                "Artefakt aus derselben Programmversion erzeugen; von Hand "
                "geaenderte CSVs werden nicht gelesen.")
        rows.append(dict(row))
    return tuple(rows)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExportedReview:
    """Ergebnis eines Exports."""

    review_id: str
    run_id: str
    workbook: Path
    manifest: Path
    rows: int
    boundary_rows: int
    #: Units ohne Urteil; sie stehen bewusst nicht im Workbook.
    incomplete_units: tuple[str, ...]

    def files(self) -> tuple[str, ...]:
        return (paths.relative_to_base(self.workbook),
                paths.relative_to_base(self.manifest))


def _assignment_row(source: Mapping[str, str]) -> dict[str, object]:
    """Eine Workbookzeile aus einer Artefaktzeile."""
    return {name: source.get(name, "") for name in ASSIGNMENT_SHEET_COLUMNS}


def workbook_rows(assignments: Sequence[Mapping[str, str]],
                  coverage: Sequence[Mapping[str, str]],
                  ) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    """Baut die Zeilen des Blatts ``assignments`` aus den Runartefakten.

    Eine ``mapped``-Unit bekommt eine Zeile je Zuordnung, eine
    ``no_mapping``-Unit genau eine Zeile ohne Industry. Units ohne Urteil
    bekommen keine Zeile: es gibt nichts zu reviewen, ihr Fehlen wird
    stattdessen im Exportmanifest ausgewiesen.
    """
    by_unit: dict[str, list[Mapping[str, str]]] = {}
    for row in assignments:
        by_unit.setdefault(row["unit_id"], []).append(row)

    rows: list[dict[str, object]] = []
    incomplete: list[str] = []
    for entry in coverage:
        unit_id = entry["unit_id"]
        if entry["status"] in UNDECIDED_STATUS:
            incomplete.append(unit_id)
            continue
        if entry["status"] == "no_mapping":
            rows.append(_assignment_row(entry))
            continue
        for source in by_unit.get(unit_id, ()):
            rows.append(_assignment_row(source))
    return tuple(rows), tuple(incomplete)


def _cell(sheet, row: int, column: int, value: object) -> None:
    """Schreibt eine reine Textzelle.

    Ohne den erzwungenen Typ wuerde ein Wert mit fuehrendem ``=`` als Formel
    gespeichert; Excel zeigte dann ein Rechenergebnis, und der Rueckimport
    laese einen anderen Wert als den exportierten.
    """
    target = sheet.cell(row=row, column=column, value=_text(value))
    target.data_type = "s"


def _write_sheet(sheet, columns: Sequence[str],
                 rows: Iterable[Mapping[str, object]]) -> None:
    for index, name in enumerate(columns, start=1):
        _cell(sheet, 1, index, name)
    for line, row in enumerate(rows, start=2):
        for index, name in enumerate(columns, start=1):
            _cell(sheet, line, index, row.get(name, ""))
    sheet.freeze_panes = "B2"


def _bind_industry_list(sheet, taxonomy_rows: int, data_rows: int) -> None:
    """Haengt die Auswahlliste der Industries an die Spalte ``industry_id``.

    Der Tippfehler ist der wahrscheinlichste Handfehler, und er faellt sonst
    erst beim Import auf. Die Liste warnt nur; sie sperrt nichts, damit
    Einfuegen aus der Zwischenablage weiter moeglich bleibt.
    """
    if taxonomy_rows < 1 or data_rows < 1:
        return
    column = get_column_letter(ASSIGNMENT_SHEET_COLUMNS.index("industry_id") + 1)
    rule = DataValidation(
        type="list", allow_blank=True, errorStyle="warning",
        formula1=f"{SHEET_TAXONOMY}!$A$2:$A${taxonomy_rows + 1}",
        error="Die Industry steht nicht im Snapshot dieses Laufs.",
        errorTitle="Unbekannte industry_id",
        prompt="Leer lassen heisst: diese Unit hat keine Zuordnung.",
        promptTitle="industry_id")
    sheet.add_data_validation(rule)
    rule.add(f"{column}2:{column}{data_rows + 1}")


def build_workbook(rows: Sequence[Mapping[str, object]],
                   boundary: Sequence[Mapping[str, object]],
                   taxonomy: Sequence[Mapping[str, object]],
                   info: Mapping[str, object]) -> Workbook:
    """Baut das Workbook; vier Blaetter, alles Text."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_ASSIGNMENTS
    _write_sheet(sheet, ASSIGNMENT_SHEET_COLUMNS, rows)
    _write_sheet(book.create_sheet(SHEET_BOUNDARY), BOUNDARY_SHEET_COLUMNS, boundary)
    _write_sheet(book.create_sheet(SHEET_TAXONOMY), TAXONOMY_SHEET_COLUMNS, taxonomy)
    _bind_industry_list(sheet, len(taxonomy), len(rows))

    facts = book.create_sheet(SHEET_INFO)
    _cell(facts, 1, 1, "key")
    _cell(facts, 1, 2, "value")
    for line, (key, value) in enumerate(sorted(info.items()), start=2):
        _cell(facts, line, 1, key)
        _cell(facts, line, 2, value)
    return book


def taxonomy_rows(snapshot) -> tuple[dict[str, object], ...]:
    """Das Nachschlageblatt; sortiert, damit die Auswahlliste stabil ist."""
    return tuple(
        {"industry_id": record.industry_id, "industry_name": record.industry_name,
         "sector_id": record.sector_id, "sector_name": record.sector_name}
        for record in sorted(snapshot.industries, key=lambda item: item.industry_id))


def export_manifest(*, review_id: str, run_id: str, manifest: Mapping[str, object],
                    rows: Sequence[Mapping[str, object]],
                    boundary: Sequence[Mapping[str, object]],
                    incomplete: Sequence[str]) -> dict[str, object]:
    """Bindung des Exports; der Import prueft jeden Wert daraus nach."""
    return {
        "version": EXPORT_MANIFEST_VERSION,
        "workbook_version": WORKBOOK_VERSION,
        "review_id": review_id,
        "run_id": run_id,
        "revision_id": run_id,
        "snapshot_id": manifest.get("snapshot_id", ""),
        "taxonomy_content_hash": manifest.get("taxonomy_content_hash", ""),
        "input_manifest_hash": manifest.get("input_manifest_hash", ""),
        "model": manifest.get("model", ""),
        "unit_count": manifest.get("unit_count", 0),
        "row_count": len(rows),
        "boundary_count": len(boundary),
        "incomplete_units": list(incomplete),
        "columns": {
            "assignments": list(ASSIGNMENT_SHEET_COLUMNS),
            "boundary": list(BOUNDARY_SHEET_COLUMNS),
            "taxonomy": list(TAXONOMY_SHEET_COLUMNS),
        },
        # Die exportierte Unitmenge. Fehlt eine Unit spaeter komplett im
        # Workbook, ist das eine Entfernung - und keine vergessene Unit.
        "units": sorted({_text(row["unit_id"]) for row in rows}),
    }


def _require_complete_run(run_id: str) -> Mapping[str, object]:
    """Stellt sicher, dass die Revision abgeschlossen ist, und liefert ihr Manifest."""
    if not artifacts.is_complete(run_id):
        raise _blocked(
            "REV_RUN_INCOMPLETE", run_id,
            "Die Runrevision hat keinen vollstaendigen Artefaktsatz.",
            f"'run execute --run-id {run_id}' bis zum Abschlussmarker "
            "durchlaufen lassen; ein Teilstand wird nicht reviewt.")
    manifest = workflow.load_manifest(run_id)
    if manifest is None:
        raise _blocked(
            "REV_MANIFEST_MISSING", run_id,
            "Zur Revision fehlt das Runmanifest.",
            "Ohne Manifest ist die Herkunft des Exports nicht belegbar; "
            "Revision neu planen und ausfuehren.")
    return manifest


def run_assignments(run_id: str) -> tuple[dict[str, str], ...]:
    """Die Long-CSV des Laufs; Vergleichsbasis fuer die Provenienz."""
    return _read_csv(workflow.mapping_path(run_id, artifacts.ASSIGNMENTS_FILENAME),
                     artifacts.ASSIGNMENT_COLUMNS)


def _require_run_snapshot(run_id: str, manifest: Mapping[str, object],
                          snapshot) -> None:
    """Der Export spiegelt einen Lauf; er nutzt dessen Taxonomiestand.

    Das Blatt ``taxonomy`` und die Auswahlliste kommen aus dem uebergebenen
    Snapshot. Waere er ein anderer als der des Laufs, stuenden im Workbook
    Industries, die der Lauf nie sehen konnte.
    """
    checks = (
        ("snapshot_id", snapshot.snapshot_id, manifest.get("snapshot_id")),
        ("taxonomy_content_hash", snapshot.taxonomy_content_hash,
         manifest.get("taxonomy_content_hash")),
    )
    for name, current, expected in checks:
        if _text(current) != _text(expected):
            raise _blocked(
                "REV_SNAPSHOT_MISMATCH", run_id,
                f"{name} des freigegebenen Snapshots weicht vom Lauf ab: "
                f"Lauf {expected!r}, jetzt {current!r}.",
                "Der Review gehoert zum Taxonomiestand des Laufs; Lauf mit dem "
                "aktuellen Snapshot neu ausfuehren oder den Stand des Laufs "
                "freigeben.")


def export_review(run_id: str, review_id: str, snapshot) -> ExportedReview:
    """Schreibt Workbook und Exportmanifest einer abgeschlossenen Revision."""
    manifest = _require_complete_run(run_id)
    _require_run_snapshot(run_id, manifest, snapshot)
    if review_path(review_id, WORKBOOK_FILENAME).exists():
        raise _blocked(
            "REV_EXPORT_EXISTS", review_id,
            "Zu dieser Review-ID gibt es bereits ein Workbook.",
            "Eine Reviewrevision wird nie ueberschrieben; neue --review-id "
            "waehlen.")

    assignments = run_assignments(run_id)
    coverage = _read_csv(
        workflow.mapping_path(run_id, artifacts.UNIT_COVERAGE_FILENAME),
        artifacts.UNIT_COVERAGE_COLUMNS)
    conflicts = _read_csv(
        workflow.mapping_path(run_id, artifacts.BOUNDARY_FILENAME),
        artifacts.BOUNDARY_COLUMNS)

    rows, incomplete = workbook_rows(assignments, coverage)
    boundary = tuple(dict(row) for row in conflicts)
    payload = export_manifest(review_id=review_id, run_id=run_id, manifest=manifest,
                              rows=rows, boundary=boundary, incomplete=incomplete)
    info = {
        "review_id": review_id,
        "run_id": run_id,
        "snapshot_id": payload["snapshot_id"],
        "taxonomy_content_hash": payload["taxonomy_content_hash"],
        "input_manifest_hash": payload["input_manifest_hash"],
        "model": payload["model"],
        "workbook_version": WORKBOOK_VERSION,
        "hinweis": ("Werte direkt aendern. Zeile loeschen entfernt die "
                    "Zuordnung, leere industry_id heisst keine Zuordnung, "
                    "eine neue Zeile mit unit_id fuegt eine hinzu. Zulaessige "
                    "Industries stehen im Blatt 'taxonomy'."),
    }

    workbook_target = review_path(review_id, WORKBOOK_FILENAME, create_parents=True)
    manifest_target = review_path(review_id, EXPORT_MANIFEST_FILENAME,
                                  create_parents=True)
    try:
        build_workbook(rows, boundary, taxonomy_rows(snapshot), info).save(workbook_target)
    except OSError as exc:
        raise _blocked(
            "REV_EXPORT_NOT_WRITTEN", paths.relative_to_base(workbook_target),
            f"Das Workbook konnte nicht geschrieben werden: {exc}.",
            "Schreibrecht unter data/committee_matcher_v2/ pruefen.")
    try:
        paths.write_text_atomic(
            manifest_target,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except (MatcherError, OSError) as exc:
        # Ohne Manifest ist das Workbook nicht rueckimportierbar; es bleibt
        # deshalb nicht als scheinbar gueltiger Export liegen.
        workbook_target.unlink(missing_ok=True)
        raise _blocked(
            "REV_EXPORT_NOT_WRITTEN", paths.relative_to_base(manifest_target),
            f"Das Exportmanifest konnte nicht geschrieben werden: {exc}.",
            "Schreibrecht unter data/committee_matcher_v2/ pruefen.")

    return ExportedReview(
        review_id=review_id, run_id=run_id, workbook=workbook_target,
        manifest=manifest_target, rows=len(rows), boundary_rows=len(boundary),
        incomplete_units=incomplete)


# ---------------------------------------------------------------------------
# Rueckimport
# ---------------------------------------------------------------------------

#: Spalten der reviewten Long-CSV; sie erweitert den Artefaktvertrag um die
#: Reviewherkunft und laesst die Callprovenienz manueller Zeilen leer.
REVIEWED_COLUMNS: tuple[str, ...] = artifacts.ASSIGNMENT_COLUMNS + ("review_id",)
REVIEWED_COVERAGE_COLUMNS: tuple[str, ...] = (
    "review_id", "run_id", "unit_id", "chamber", "committee_name", "unit_name",
    "unit_kind", "decision", "assignment_count", "no_mapping_reason",
    "broad_scope_exception", "broad_scope_reason", "manual_assignments",
    "review_status",
)
REVIEWED_ASSIGNMENTS_FILENAME = "reviewed_assignments.csv"
REVIEWED_COVERAGE_FILENAME = "reviewed_unit_coverage.csv"
IMPORT_MANIFEST_FILENAME = "import_manifest.json"
IMPORT_MANIFEST_VERSION = "review-import-v2"
#: Reviewstatus einer angenommenen Unit (Plan 2.8).
REVIEW_STATUS_REVIEWED = "reviewed"


@dataclass(frozen=True)
class EffectiveAssignment:
    """Eine Zuordnung nach dem Review, mit ihrer Herkunft."""

    unit_id: str
    industry_id: str
    confidence: str
    evidence_excerpt: str
    rationale_short: str
    source: str
    #: Zeile des Laufs, aus der die Zuordnung stammt; bei Handarbeit leer.
    origin: Mapping[str, str]


@dataclass(frozen=True)
class EffectiveUnit:
    """Reviewstand einer Unit."""

    unit_id: str
    decision: str
    no_mapping_reason: str
    broad_scope_exception: bool
    broad_scope_reason: str
    assignments: tuple[EffectiveAssignment, ...]


@dataclass(frozen=True)
class ImportedReview:
    """Ergebnis eines angenommenen Rueckimports."""

    units: int
    assignments: int
    manual_assignments: int
    no_mapping_units: int
    #: Aenderungsbilanz gegen den Lauf; sie ist der einzige Schutz davor, dass
    #: eine versehentlich geloeschte Zeile unbemerkt bleibt.
    removed: int
    added: int
    changed: int
    #: Geaenderte Gruende von Units, die vorher wie nachher ``no_mapping`` sind.
    changed_reasons: int
    files: tuple[str, ...]


def _reject(collector: ErrorCollector, code: str, obj: str, message: str,
            hint: str) -> None:
    """Ein abgelehnter Import ist Exit 7; er sammelt jeden Grund."""
    collector.blocker(code, Stage.REVIEW, obj, message, hint,
                      exit_code=ExitCode.REVIEW_IMPORT_REJECTED)


def load_export_manifest(review_id: str) -> Mapping[str, object]:
    target = review_path(review_id, EXPORT_MANIFEST_FILENAME)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _blocked(
            "REV_EXPORT_MANIFEST_MISSING", review_id,
            f"Das Exportmanifest der Reviewrevision fehlt oder ist unlesbar: {exc}.",
            "Ohne Exportmanifest ist kein Rueckimport moeglich; Workbook aus "
            "'review export' verwenden.")
    if not isinstance(payload, dict) or payload.get("version") != EXPORT_MANIFEST_VERSION:
        raise _blocked(
            "REV_EXPORT_MANIFEST_CONTRACT", review_id,
            "Das Exportmanifest traegt eine andere Vertragsversion.",
            f"Export mit derselben Programmversion erzeugen ({EXPORT_MANIFEST_VERSION}).")
    return payload


def read_workbook(path: Path, collector: ErrorCollector,
                  ) -> tuple[tuple[dict[str, str], ...], dict[str, str]]:
    """Liest die Blaetter ``assignments`` und ``info`` und lehnt Formeln ab.

    Eine Formel liefert beim Lesen ihren Ausdruck statt eines Werts. Sie still
    als Text zu uebernehmen wuerde einen Ausdruck zum Reviewurteil machen.

    Das Blatt ``info`` traegt kein Urteil, aber die Kennungen des Exports; es
    ist die einzige Stelle, an der die Datei selbst sagt, zu welchem Lauf und
    welcher Reviewrevision sie gehoert. Die Blaetter ``boundary`` und
    ``taxonomy`` werden nicht gelesen.
    """
    try:
        book = load_workbook(path, data_only=False)
    except Exception as exc:  # openpyxl wirft je nach Schaden unterschiedlich
        raise _blocked(
            "REV_WORKBOOK_UNREADABLE", paths.relative_to_base(path),
            f"Das Workbook ist nicht lesbar: {exc}.",
            "Datei aus 'review export' verwenden und als .xlsx speichern.")

    for name in (SHEET_ASSIGNMENTS, SHEET_INFO):
        if name not in book.sheetnames:
            raise _blocked(
                "REV_WORKBOOK_CONTRACT", name,
                f"Dem Workbook fehlt das Blatt '{name}'.",
                "Blaetter weder umbenennen noch entfernen.")
    rows = _sheet_rows(book[SHEET_ASSIGNMENTS], ASSIGNMENT_SHEET_COLUMNS, collector)
    return rows, _info_values(book[SHEET_INFO])


def _info_values(sheet) -> dict[str, str]:
    """Die Kennungen des Exports als Zuordnung; Kopfzeile uebersprungen."""
    values: dict[str, str] = {}
    for line in sheet.iter_rows(min_row=2, max_col=2):
        key = _text(line[0].value).strip()
        if key:
            values[key] = _text(line[1].value if len(line) > 1 else "").strip()
    return values


def _sheet_rows(sheet, columns: Sequence[str],
                collector: ErrorCollector) -> tuple[dict[str, str], ...]:
    header = tuple(_text(cell.value) for cell in sheet[1])
    if header != tuple(columns):
        raise _blocked(
            "REV_WORKBOOK_CONTRACT", sheet.title,
            "Die Spaltenkoepfe des Blatts weichen vom Export ab.",
            "Spalten weder umbenennen, verschieben noch entfernen.")
    rows: list[dict[str, str]] = []
    for line in sheet.iter_rows(min_row=2):
        values: dict[str, str] = {}
        for name, cell in zip(columns, line):
            if cell.data_type == "f":
                _reject(collector, "REV_FORMULA_CELL",
                        f"{sheet.title}!{cell.coordinate}",
                        "Die Zelle enthaelt eine Formel statt eines Werts.",
                        "Formel durch ihren Wert ersetzen (Einfuegen als Wert); "
                        "ein Ausdruck wird nie als Reviewurteil uebernommen.")
            value = _text(cell.value)
            # Nur Kennungen werden getrimmt: dort ist Randwhitespace immer ein
            # Tippfehler. Textfelder bleiben zeichengenau, sonst wuerde der
            # Import den exportierten Wert still veraendern und ihn trotzdem
            # als Modellwert ausweisen.
            values[name] = value.strip() if name in STRIPPED_COLUMNS else value
        if any(value.strip() for value in values.values()):
            rows.append(values)
    return tuple(rows)


def check_binding(manifest: Mapping[str, object], run_manifest: Mapping[str, object],
                  snapshot, input_manifest_hash: str, review_id: str,
                  expected_run_id: str, info: Mapping[str, str],
                  collector: ErrorCollector) -> None:
    """Stale Bindung blockiert den gesamten Import (Plan 2.8).

    Geprueft wird in drei Richtungen, weil drei Dinge auseinanderlaufen
    koennen: das Exportmanifest gegen den Lauf, der geladene Snapshot gegen
    beide, und das Workbook selbst gegen die Reviewrevision. Ohne die letzte
    Richtung liesse sich mit ``--workbook`` die Datei einer fremden Revision
    einlesen.
    """
    checks = (
        ("run_id", manifest.get("run_id"), expected_run_id),
        ("snapshot_id", manifest.get("snapshot_id"), run_manifest.get("snapshot_id")),
        ("snapshot_id/aktuell", manifest.get("snapshot_id"), snapshot.snapshot_id),
        ("taxonomy_content_hash", manifest.get("taxonomy_content_hash"),
         snapshot.taxonomy_content_hash),
        ("input_manifest_hash", manifest.get("input_manifest_hash"),
         input_manifest_hash),
        ("model", manifest.get("model"), run_manifest.get("model")),
        ("workbook/review_id", info.get("review_id"), review_id),
        ("workbook/run_id", info.get("run_id"), manifest.get("run_id")),
        ("workbook/workbook_version", info.get("workbook_version"),
         WORKBOOK_VERSION),
    )
    for name, exported, current in checks:
        if _text(exported) != _text(current):
            _reject(collector, "REV_STALE_BINDING", name,
                    f"{name} weicht vom Stand des Exports ab: "
                    f"exportiert {exported!r}, jetzt {current!r}.",
                    "Der Review gehoert zu genau einem Lauf; Workbook aus einem "
                    "neuen Export bearbeiten.")


def _confidence(value: str, obj: str, collector: ErrorCollector) -> str | None:
    """Confidence als Text; leer ist zulaessig, Unsinn nicht."""
    span = f"{MIN_CONFIDENCE}..{MAX_CONFIDENCE}"
    if not value:
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        _reject(collector, "REV_CONFIDENCE_INVALID", obj,
                f"'{value}' ist keine ganze Zahl.",
                f"Confidence als ganze Zahl {span} eintragen oder leer lassen.")
        return None
    if not MIN_CONFIDENCE <= number <= MAX_CONFIDENCE:
        _reject(collector, "REV_CONFIDENCE_INVALID", obj,
                f"Confidence {number} liegt ausserhalb von {span}.",
                f"Confidence als ganze Zahl {span} eintragen oder leer lassen.")
        return None
    return str(number)


def _origin_index(assignments: Sequence[Mapping[str, str]],
                  collector: ErrorCollector,
                  ) -> dict[tuple[str, str], Mapping[str, str]]:
    """Zeilen des Laufs, adressiert ueber Unit und Industry.

    Der Schluessel ist im Lauf eindeutig. Kaeme er doppelt vor, waere nicht
    entscheidbar, welche Zeile die Provenienz einer unveraenderten Zuordnung
    ist; still die letzte zu nehmen wuerde eine Herkunft erfinden.
    """
    index: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in assignments:
        if not row.get("industry_id"):
            continue
        key = (row["unit_id"], row["industry_id"])
        if key in index:
            _reject(collector, "REV_ORIGIN_DUPLICATE", "/".join(key),
                    "Die Long-CSV des Laufs fuehrt diese Zuordnung mehrfach.",
                    "Artefakte des Laufs sind widerspruechlich; Revision neu "
                    "ausfuehren statt sie zu reviewen.")
            continue
        index[key] = row
    return index


def _unchanged(row: Mapping[str, str], origin: Mapping[str, str]) -> bool:
    """Traegt die Zeile noch genau die Werte des Laufs?

    Verglichen wird zeichengenau. Nur die Confidence wird vorher getrimmt,
    weil sie als Kennung gelesen und auf ihre Zahl normalisiert wird.
    """
    for name in COMPARED_FIELDS:
        current = row[name]
        before = _text(origin.get(name))
        if name in STRIPPED_COLUMNS:
            current, before = current.strip(), before.strip()
        if current != before:
            return False
    return True


def effective_units(rows: Sequence[Mapping[str, str]],
                    exported_units: Sequence[str],
                    corpus: Mapping[str, object],
                    origins: Mapping[tuple[str, str], Mapping[str, str]],
                    coverage: Mapping[str, Mapping[str, str]],
                    collector: ErrorCollector) -> dict[str, EffectiveUnit]:
    """Wertet alle Zeilen zum Reviewstand je Unit aus.

    Eine exportierte Unit ohne Zeile im Workbook ist nicht verschwunden,
    sondern hat keine Zuordnung mehr - sonst wuerde eine geloeschte Zeile die
    Unit lautlos aus dem Ergebnis nehmen.
    """
    grouped: dict[str, list[dict[str, str]]] = {
        unit_id: [] for unit_id in exported_units if unit_id in corpus}
    for row in rows:
        unit_id = row["unit_id"]
        if not unit_id:
            _reject(collector, "REV_UNIT_MISSING", row.get("industry_id") or "Zeile",
                    "Der Zeile fehlt die unit_id.",
                    "unit_id der Zielunit eintragen; nur so ist eine Zuordnung "
                    "zuzuordnen.")
            continue
        if unit_id not in corpus:
            _reject(collector, "REV_UNIT_UNKNOWN", unit_id,
                    "Die unit_id kommt im Inputkorpus nicht vor.",
                    "unit_id zeichengenau aus dem Export uebernehmen.")
            continue
        grouped.setdefault(unit_id, []).append(dict(row))

    model_counts: dict[str, int] = {}
    for unit_id, _ in origins:
        model_counts[unit_id] = model_counts.get(unit_id, 0) + 1

    units: dict[str, EffectiveUnit] = {}
    for unit_id, entries in grouped.items():
        assignments: list[EffectiveAssignment] = []
        reason = ""
        reason_given = False
        seen: set[str] = set()
        for row in entries:
            industry_id = row["industry_id"]
            if not industry_id:
                # Die Zeile sagt: diese Unit hat keine Zuordnung. Ein
                # geleertes Feld ist eine Aussage und wird nicht aus dem Lauf
                # wieder aufgefuellt; nur die erste solche Zeile zaehlt.
                if not reason_given:
                    reason, reason_given = row["no_mapping_reason"], True
                continue
            obj = f"{unit_id}/{industry_id}"
            if industry_id in seen:
                _reject(collector, "REV_INDUSTRY_DUPLICATE", obj,
                        "Die Industry ist der Unit mehrfach zugeordnet.",
                        "Doppelte Zeile loeschen.")
                continue
            seen.add(industry_id)
            confidence = _confidence(row["confidence"], obj, collector)
            if confidence is None:
                continue
            origin = origins.get((unit_id, industry_id), {})
            unchanged = bool(origin) and _unchanged(row, origin)
            assignments.append(EffectiveAssignment(
                unit_id=unit_id, industry_id=industry_id, confidence=confidence,
                evidence_excerpt=row["evidence_excerpt"],
                rationale_short=row["rationale_short"],
                source="model" if unchanged else "manual",
                origin=origin if unchanged else {}))

        model_state = coverage.get(unit_id, {})
        if not assignments and not reason_given:
            # Keine Zeile mehr fuer die Unit: der Grund des Laufs gilt weiter.
            reason = _text(model_state.get("no_mapping_reason"))
        # Der Ausnahmegrund des Modells beschreibt dessen Zuordnungsmenge. Ist
        # sie nicht mehr dieselbe, waere er ein Fremdwert an einer Handzeile.
        untouched = (
            len(assignments) == model_counts.get(unit_id, 0)
            and all(entry.source == "model" for entry in assignments))
        units[unit_id] = EffectiveUnit(
            unit_id=unit_id,
            decision="mapped" if assignments else "no_mapping",
            no_mapping_reason=reason if not assignments else "",
            broad_scope_exception=len(assignments) > validation.MAX_ASSIGNMENTS_REGULAR,
            broad_scope_reason=(_text(model_state.get("broad_scope_reason"))
                                if untouched else ""),
            assignments=tuple(assignments))
    return units


def check_units(units: Mapping[str, EffectiveUnit], corpus: Mapping[str, object],
                snapshot, collector: ErrorCollector) -> None:
    """Prueft, was das Ergebnis unbrauchbar machen wuerde.

    Die Anzahlgrenzen des Modellvertrags werden hier nur gemeldet, nicht
    erzwungen: sie disziplinieren das Modell, waehrend der Review die Instanz
    ist, die ueber den Einzelfall entscheidet.
    """
    known_ids = snapshot.industry_ids()
    for unit_id in sorted(units):
        state = units[unit_id]
        limit = validation._unit_limit(corpus[unit_id])
        if len(state.assignments) > limit:
            collector.warning(
                "REV_ASSIGNMENTS_ABOVE_LIMIT", Stage.REVIEW, unit_id,
                f"{len(state.assignments)} Zuordnungen; der Modellvertrag "
                f"erlaubt hoechstens {limit}.",
                "Bewusst so gewollt: nichts zu tun. Sonst ueberzaehlige Zeilen "
                "im Blatt 'assignments' loeschen und erneut importieren.")
        for entry in state.assignments:
            if entry.industry_id not in known_ids:
                _reject(collector, "REV_UNKNOWN_INDUSTRY",
                        f"{unit_id}/{entry.industry_id}",
                        "Die industry_id kommt im Snapshot des Laufs nicht vor.",
                        "ID aus dem Blatt 'taxonomy' uebernehmen; Sektor und "
                        "Namen leitet der Import selbst ab.")


def _industry_record(snapshot, industry_id: str):
    for record in snapshot.industries:
        if record.industry_id == industry_id:
            return record
    return None


def reviewed_rows(review_id: str, run_id: str, units: Mapping[str, EffectiveUnit],
                  corpus: Mapping[str, object], snapshot,
                  ) -> tuple[dict[str, object], ...]:
    """Long-CSV des Reviewstands; Sektorwerte stammen aus dem Snapshot.

    Eine manuell entstandene Zeile bekommt keine Callprovenienz: sie hat keinen
    Modellaufruf hinter sich, und ein fremder Wert waere eine Falschaussage.
    """
    rows: list[dict[str, object]] = []
    for unit_id in sorted(units):
        state = units[unit_id]
        unit = corpus[unit_id]
        for entry in sorted(state.assignments, key=lambda item: item.industry_id):
            industry = _industry_record(snapshot, entry.industry_id)
            origin = entry.origin
            rows.append({
                "run_id": run_id,
                "revision_id": review_id,
                "unit_id": unit_id,
                "chamber": unit.chamber,
                "committee_name": unit.committee_name,
                "unit_name": unit.unit_name,
                "unit_kind": unit.kind.value,
                "input_basis": unit.input_basis.value,
                "mapping_input_hash": unit.mapping_input_hash or "",
                "decision": state.decision,
                "broad_scope_exception": "true" if state.broad_scope_exception else "false",
                "industry_id": entry.industry_id,
                "industry_name": industry.industry_name if industry else "",
                "sector_id": industry.sector_id if industry else "",
                "sector_name": industry.sector_name if industry else "",
                "confidence": entry.confidence,
                "evidence_excerpt": entry.evidence_excerpt,
                "rationale_short": entry.rationale_short,
                "assignment_source": entry.source,
                "broad_scope_reason": state.broad_scope_reason,
                "call_id": origin.get("call_id", ""),
                "provider": origin.get("provider", ""),
                "generation_id": origin.get("generation_id", ""),
                "from_repair": origin.get("from_repair", ""),
                "model": origin.get("model", ""),
                "taxonomy_content_hash": snapshot.taxonomy_content_hash,
                "snapshot_id": snapshot.snapshot_id,
                "prompt_id": origin.get("prompt_id", ""),
                "prompt_version": origin.get("prompt_version", ""),
                "prompt_sha256": origin.get("prompt_sha256", ""),
                "schema_name": origin.get("schema_name", ""),
                "schema_sha256": origin.get("schema_sha256", ""),
                "review_id": review_id,
            })
    return tuple(rows)


def reviewed_coverage_rows(review_id: str, run_id: str,
                           units: Mapping[str, EffectiveUnit],
                           corpus: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for unit_id in sorted(units):
        state = units[unit_id]
        unit = corpus[unit_id]
        rows.append({
            "review_id": review_id,
            "run_id": run_id,
            "unit_id": unit_id,
            "chamber": unit.chamber,
            "committee_name": unit.committee_name,
            "unit_name": unit.unit_name,
            "unit_kind": unit.kind.value,
            "decision": state.decision,
            "assignment_count": len(state.assignments),
            "no_mapping_reason": state.no_mapping_reason,
            "broad_scope_exception": "true" if state.broad_scope_exception else "false",
            "broad_scope_reason": state.broad_scope_reason,
            "manual_assignments": sum(1 for entry in state.assignments
                                      if entry.source == "manual"),
            "review_status": REVIEW_STATUS_REVIEWED,
        })
    return tuple(rows)


def _write_review_files(review_id: str,
                        files: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """Schreibt die Ergebnisdateien einer Revision - alle oder keine.

    Bricht eine Datei ab, waeren die bereits geschriebenen ein Reviewstand,
    den es nicht gibt; sie werden deshalb wieder entfernt.
    """
    written: list[Path] = []
    try:
        for filename, text in files:
            target = review_path(review_id, filename, create_parents=True)
            paths.write_text_atomic(target, text)
            written.append(target)
    except (MatcherError, OSError) as exc:
        for target in written:
            target.unlink(missing_ok=True)
        raise _blocked(
            "REV_IMPORT_NOT_WRITTEN", review_id,
            f"Die reviewten Artefakte konnten nicht geschrieben werden: {exc}.",
            "Schreibrecht unter data/committee_matcher_v2/ pruefen; die "
            "Reviewrevision ist unveraendert.")
    return tuple(paths.relative_to_base(target) for target in written)


def _balance(units: Mapping[str, EffectiveUnit],
             origins: Mapping[tuple[str, str], Mapping[str, str]],
             coverage: Mapping[str, Mapping[str, str]]) -> tuple[int, int, int, int]:
    """Aenderungsbilanz gegen den Lauf: entfernt, hinzugefuegt, geaendert, Gruende.

    Gezaehlt wird je Zuordnung, adressiert ueber Unit und Industry. Wer eine
    Industry gegen eine andere tauscht, sieht das deshalb als eine entfernte
    und eine neue Zuordnung - nicht als eine geaenderte.

    Der Grund einer ``no_mapping``-Unit haengt an keiner Zuordnung; ohne die
    vierte Zahl bliebe seine Aenderung im Bericht unsichtbar.
    """
    kept = {(entry.unit_id, entry.industry_id)
            for state in units.values() for entry in state.assignments}
    changed = sum(1 for state in units.values() for entry in state.assignments
                  if entry.source == "manual" and (entry.unit_id, entry.industry_id) in origins)
    added = sum(1 for key in kept if key not in origins)
    removed = sum(1 for key in origins if key not in kept)
    reasons = sum(
        1 for unit_id, state in units.items()
        if state.decision == "no_mapping"
        and _text(coverage.get(unit_id, {}).get("status")) == "no_mapping"
        and state.no_mapping_reason != _text(
            coverage.get(unit_id, {}).get("no_mapping_reason")))
    return removed, added, changed, reasons


def import_review(review_id: str, *, run_id: str, corpus: Mapping[str, object],
                  snapshot, run_manifest: Mapping[str, object],
                  input_manifest_hash: str, collector: ErrorCollector,
                  workbook: Path | None = None) -> ImportedReview | None:
    """Prueft das bearbeitete Workbook und schreibt die reviewten Artefakte.

    Der Import ist alles oder nichts: erst wenn keine Regel verletzt ist,
    entsteht eine Datei, und bricht das Schreiben ab, bleibt keine halbe
    Revision liegen. Ein abgelehnter Import laesst die Reviewrevision
    unveraendert.

    ``run_id`` ist der Lauf, den der Aufrufer meint. Er wird gegen das
    Exportmanifest geprueft, statt ihm entnommen zu werden - sonst berichtete
    die CLI ueber einen Lauf und schriebe die Zeilen eines anderen.
    """
    manifest = load_export_manifest(review_id)
    target = workbook or review_path(review_id, WORKBOOK_FILENAME)
    if reviewed_path_exists(review_id):
        raise _blocked(
            "REV_IMPORT_EXISTS", review_id,
            "Die Reviewrevision traegt bereits einen Importstand.",
            "Eine reviewte Revision wird nie ueberschrieben; neue Review-ID "
            "exportieren und dort importieren.")

    rows, info = read_workbook(target, collector)
    check_binding(manifest, run_manifest, snapshot, input_manifest_hash,
                  review_id, run_id, info, collector)

    origins = _origin_index(run_assignments(run_id), collector)
    coverage = {row["unit_id"]: row for row in _read_csv(
        workflow.mapping_path(run_id, artifacts.UNIT_COVERAGE_FILENAME),
        artifacts.UNIT_COVERAGE_COLUMNS)}
    # Die exportierte Unitmenge wird aus der Coverage des Laufs nachgerechnet,
    # nicht dem Manifest geglaubt. Nur so kann eine Unit nicht dadurch aus dem
    # Ergebnis fallen, dass sie im Sidecar fehlt.
    exported_units = [unit_id for unit_id, row in coverage.items()
                      if row["status"] not in UNDECIDED_STATUS]
    if sorted(exported_units) != sorted(str(unit_id) for unit_id
                                        in (manifest.get("units") or [])):
        _reject(collector, "REV_UNITS_MISMATCH", run_id,
                "Die entschiedene Unitmenge des Laufs weicht von der des "
                "Exports ab.",
                "Die Artefakte des Laufs haben sich seit dem Export geaendert; "
                "neu exportieren und dieses Workbook verwerfen.")

    units = effective_units(rows, exported_units, corpus, origins, coverage, collector)
    known = {unit_id: corpus[unit_id] for unit_id in units}
    check_units(units, known, snapshot, collector)
    if collector.has_blockers():
        return None

    assignments = sum(len(state.assignments) for state in units.values())
    manual = sum(1 for state in units.values() for entry in state.assignments
                 if entry.source == "manual")
    removed, added, changed, reasons = _balance(units, origins, coverage)
    payload = {
        "version": IMPORT_MANIFEST_VERSION,
        "review_id": review_id,
        "run_id": run_id,
        "snapshot_id": snapshot.snapshot_id,
        "taxonomy_content_hash": snapshot.taxonomy_content_hash,
        "input_manifest_hash": input_manifest_hash,
        "unit_count": len(units),
        "assignment_count": assignments,
        "manual_assignment_count": manual,
        "removed_assignments": removed,
        "added_assignments": added,
        "changed_assignments": changed,
        "changed_no_mapping_reasons": reasons,
        "no_mapping_units": sorted(unit_id for unit_id, state in units.items()
                                   if state.decision == "no_mapping"),
        "review_status": REVIEW_STATUS_REVIEWED,
        "files": [REVIEWED_ASSIGNMENTS_FILENAME, REVIEWED_COVERAGE_FILENAME],
    }
    # Das Importmanifest steht zuletzt: es ist der Abschlussmarker, an dem ein
    # spaeterer Lauf erkennt, dass die Revision reviewt ist.
    written = _write_review_files(review_id, (
        (REVIEWED_ASSIGNMENTS_FILENAME,
         artifacts.render_csv(REVIEWED_COLUMNS,
                              reviewed_rows(review_id, run_id, units, known, snapshot))),
        (REVIEWED_COVERAGE_FILENAME,
         artifacts.render_csv(REVIEWED_COVERAGE_COLUMNS,
                              reviewed_coverage_rows(review_id, run_id, units, known))),
        (IMPORT_MANIFEST_FILENAME,
         json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
    ))
    return ImportedReview(
        units=len(units),
        assignments=assignments, manual_assignments=manual,
        no_mapping_units=len(payload["no_mapping_units"]),
        removed=removed, added=added, changed=changed,
        changed_reasons=reasons, files=written)


def reviewed_path_exists(review_id: str) -> bool:
    """Traegt die Reviewrevision bereits einen Importstand?"""
    return review_path(review_id, IMPORT_MANIFEST_FILENAME).exists()
