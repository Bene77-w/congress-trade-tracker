"""Evaluationsrevisionen des Committee Matchers V2 (Plan P9).

Die Evaluation misst den Produktivpfad (``run plan|execute``) gegen das
Entwicklungs-Gold-Set. Damit das Ergebnis etwas ueber genau diesen Pfad
aussagt, friert ``evaluate export`` alle ergebnisbestimmenden Bedingungen
**vor** dem Labeln ein: Taxonomiestand, Stichprobe, Inputhashes, Prompt- und
Schemahashes, Routing, Kontextgrenzen, Variantenmatrix und Budget. Der Import
der Labels und der spaetere Prueflauf pruefen jeden dieser Werte nach; weicht
einer ab, ist es eine andere Evaluation und bekommt eine eigene Revision.

Zwei Abgrenzungen sind bewusst:

- Das Manifest entsteht aus ``workflow.build_manifest``, nicht aus einem
  zweiten Bauweg. Ein eigener Bauweg koennte andere Bedingungen einfrieren als
  der Lauf spaeter verwendet, und der Vergleich waere wertlos.
- ``batching.batch_size`` faellt aus dem Manifest heraus: die Batchgroesse ist
  in der Evaluation die Variante und steht in ``variants.batch_sizes``. Bliebe
  der Settingswert stehen, widerspraeche er jeder Variante ausser einer.

Keine Revision wird ueberschrieben, und weder Prompttext noch Zugangsdaten
gelangen in ein Artefakt.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from openpyxl import load_workbook

from . import paths, workflow
from .errors import ErrorCollector, MatcherError, Stage
from .models import MatcherUnit
from .status import ExitCode, WorkflowState

#: Revisionsverzeichnis der Evaluationen, neben ``runs`` und ``reviews``.
EVALUATIONS_DIR = "evaluations"
#: Ablage der Stichprobe und der Labels des Entwicklungs-Gold-Sets.
GOLDSET_DIR = "goldset"
SAMPLE_FILENAME = "sample_units.json"
SAMPLE_VERSION = "goldset-sample-v1"

EXPORT_MANIFEST_FILENAME = "export_manifest.json"
EXPORT_MANIFEST_VERSION = "evaluation-export-v1"

#: Kopffelder, die jede Stichprobenzeile tragen muss.
SAMPLE_UNIT_FIELDS = ("unit_id", "chamber", "committee_name", "unit_name",
                      "input_basis")


class EvaluationError(MatcherError):
    """Fehler im Evaluationspfad."""


def _blocked(code: str, obj: str, message: str, hint: str,
             exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> EvaluationError:
    collector = ErrorCollector()
    collector.blocker(code, Stage.EVALUATION, obj, message, hint,
                      exit_code=exit_code)
    return EvaluationError(collector.blockers, message)


def _reject(collector: ErrorCollector, code: str, obj: str, message: str,
            hint: str, exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> None:
    collector.blocker(code, Stage.EVALUATION, obj, message, hint,
                      exit_code=exit_code)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evaluation_path(eval_id: str, *parts: str,
                    create_parents: bool = False) -> Path:
    return paths.artifact_path(EVALUATIONS_DIR, eval_id, *parts,
                               create_parents=create_parents)


def sample_path() -> Path:
    return paths.artifact_path(GOLDSET_DIR, SAMPLE_FILENAME)


# ---------------------------------------------------------------------------
# Stichprobe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SampleUnit:
    """Eine Unit der Gold-Set-Stichprobe.

    ``focus`` aus der Stichprobendatei wird bewusst nicht uebernommen: der
    Wert nennt die erwartete Fallabdeckung und duerfte nie in die Naehe eines
    Aufgabenpakets geraten.
    """

    unit_id: str
    chamber: str
    committee_name: str
    unit_name: str
    input_basis: str


@dataclass(frozen=True)
class GoldsetSample:
    """Die eingefrorene Stichprobe des Entwicklungs-Gold-Sets."""

    version: str
    expected_unit_count: int
    units: tuple[SampleUnit, ...]

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self.units)


def load_sample(collector: ErrorCollector) -> GoldsetSample | None:
    """Liest die Stichprobe und prueft ihren Vertrag.

    Gibt ``None`` zurueck, sobald die Datei fehlt oder nicht mehr die
    freigegebene Stichprobe beschreibt: gegen eine stillschweigend veraenderte
    Stichprobe zu messen waere schlimmer als gar nicht zu messen.
    """
    target = sample_path()
    if not target.exists():
        _reject(collector, "EVAL_SAMPLE_MISSING",
                paths.relative_to_base(target),
                "Die Stichprobe des Entwicklungs-Gold-Sets fehlt.",
                "Stichprobe aus organisation/committee_matcher_goldset_stichprobe.md "
                "wiederherstellen; ohne sie gibt es keine Evaluationsmenge.")
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _reject(collector, "EVAL_SAMPLE_UNREADABLE",
                paths.relative_to_base(target),
                f"Die Stichprobe ist nicht lesbar: {exc.__class__.__name__}.",
                "Datei sichten; die Evaluation liest sie unveraendert ein.")
        return None
    if not isinstance(payload, dict):
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                "Die Stichprobe ist kein JSON-Objekt.",
                "Datei gegen das Format 'goldset-sample-v1' pruefen.")
        return None
    if payload.get("sample_version") != SAMPLE_VERSION:
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                f"sample_version={payload.get('sample_version')!r} statt "
                f"{SAMPLE_VERSION!r}.",
                "Stichprobe der freigegebenen Version verwenden; eine andere "
                "Version beschreibt eine andere Evaluationsmenge.")
        return None

    rows = payload.get("units")
    if not isinstance(rows, list) or not rows:
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                "'units' fehlt oder ist leer.",
                "Stichprobe wiederherstellen; eine leere Menge misst nichts.")
        return None

    units: list[SampleUnit] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=1):
        obj = f"units[{position}]"
        if not isinstance(row, dict):
            _reject(collector, "EVAL_SAMPLE_CONTRACT", obj,
                    "Eintrag ist kein Objekt.",
                    "Stichprobe gegen das Format 'goldset-sample-v1' pruefen.")
            continue
        missing = [field for field in SAMPLE_UNIT_FIELDS if not row.get(field)]
        if missing:
            _reject(collector, "EVAL_SAMPLE_CONTRACT", obj,
                    f"Eintrag ohne {', '.join(missing)}.",
                    "Fehlende Kopffelder ergaenzen; sie belegen, welche Unit "
                    "gemeint ist.")
            continue
        unit_id = str(row["unit_id"]).strip()
        if unit_id in seen:
            _reject(collector, "EVAL_SAMPLE_DUPLICATE", unit_id,
                    "Diese Unit steht mehrfach in der Stichprobe.",
                    "Doppelten Eintrag entfernen; sonst zaehlte die Unit "
                    "doppelt in jede Metrik.")
            continue
        seen.add(unit_id)
        units.append(SampleUnit(
            unit_id=unit_id,
            chamber=str(row["chamber"]).strip(),
            committee_name=str(row["committee_name"]).strip(),
            unit_name=str(row["unit_name"]).strip(),
            input_basis=str(row["input_basis"]).strip(),
        ))

    expected = payload.get("expected_unit_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                f"expected_unit_count={expected!r} ist keine positive Zahl.",
                "Sollzahl der Stichprobe eintragen; sie ist die Gegenprobe "
                "zur Zeilenzahl.")
        return None
    if expected != len(rows):
        _reject(collector, "EVAL_SAMPLE_COUNT",
                paths.relative_to_base(target),
                f"Die Stichprobe nennt {expected} Units, enthaelt aber "
                f"{len(rows)}.",
                "Stichprobe wiederherstellen; eine unbemerkt gekuerzte Menge "
                "verzerrt jede Metrik.")
        return None
    if collector.has_blockers():
        return None
    return GoldsetSample(version=SAMPLE_VERSION, expected_unit_count=expected,
                         units=tuple(units))


def select_units(collector: ErrorCollector, units: Sequence[MatcherUnit],
                 sample: GoldsetSample) -> tuple[MatcherUnit, ...] | None:
    """Bildet die Stichprobe auf die gebauten Units ab.

    Geprueft werden auch die Kopffelder: eine Unit-ID kann stabil bleiben,
    waehrend Kammer, Committee oder Name sich geaendert haben - dann meint die
    Stichprobe nicht mehr dieselbe Unit.
    """
    known = {unit.unit_id: unit for unit in units}
    selected: list[MatcherUnit] = []
    for entry in sample.units:
        unit = known.get(entry.unit_id)
        if unit is None:
            _reject(collector, "EVAL_SAMPLE_UNIT_UNKNOWN", entry.unit_id,
                    "Diese Unit der Stichprobe kommt in der Sollmenge nicht vor.",
                    "Stichprobe gegen 'input validate' abgleichen; eine "
                    "fehlende Unit macht die Evaluationsmenge unvollstaendig.")
            continue
        drift = [
            f"{name}: Stichprobe {expected!r}, Sollmenge {current!r}"
            for name, expected, current in (
                ("chamber", entry.chamber, unit.chamber),
                ("committee_name", entry.committee_name, unit.committee_name),
                ("unit_name", entry.unit_name, unit.unit_name),
                ("input_basis", entry.input_basis, str(unit.input_basis.value)),
            )
            if expected != current
        ]
        if drift:
            _reject(collector, "EVAL_SAMPLE_UNIT_DRIFT", entry.unit_id,
                    "Die Unit hat sich seit der Stichprobenziehung geaendert: "
                    + "; ".join(drift) + ".",
                    "Stichprobe neu ziehen und neu labeln; gegen eine "
                    "veraenderte Unit gemessene Labels sind ungueltig.")
            continue
        if not unit.mapping_input_hash:
            _reject(collector, "EVAL_UNIT_UNBOUND", entry.unit_id,
                    "Der Unit fehlt der mapping_input_hash.",
                    "'input validate' ausfuehren; ohne Inputhash ist die Unit "
                    "an keinen Textstand gebunden.")
            continue
        selected.append(unit)
    if collector.has_blockers():
        return None
    return tuple(selected)


# ---------------------------------------------------------------------------
# Revision
# ---------------------------------------------------------------------------

def revision_taken(eval_id: str) -> str | None:
    """Nennt den Grund, aus dem die Kennung nicht mehr frei ist, sonst ``None``.

    Geprueft wird auch die gleichnamige Runrevision: der Export legt seinen
    frischen Snapshot unter ``runs/<eval_id>/`` ab, und ein fremder Lauf
    duerfte dabei nichts verlieren.
    """
    if evaluation_path(eval_id, EXPORT_MANIFEST_FILENAME).exists():
        return "traegt bereits ein Exportmanifest"
    if workflow.manifest_path(eval_id).exists():
        return "ist bereits als Runrevision belegt"
    return None


def assert_revision_free(collector: ErrorCollector, eval_id: str) -> bool:
    """Blockiert eine belegte Kennung; ``True`` heisst 'darf schreiben'."""
    reason = revision_taken(eval_id)
    if reason is None:
        return True
    _reject(collector, "EVAL_REVISION_TAKEN", eval_id,
            f"Die Kennung {reason}.",
            "Neue --eval-id verwenden; eine Evaluationsrevision wird nie "
            "ueberschrieben.")
    return False


def build_export_manifest(*, eval_id: str, settings, provider_settings,
                          prompts_used, taxonomy_content_hash: str,
                          snapshot_id: str, input_manifest_hash: str,
                          targets: Sequence[MatcherUnit],
                          sample: GoldsetSample,
                          budget_usd: float,
                          json_schema: Mapping | None = None,
                          ) -> dict[str, object]:
    """Friert die Evaluationsbedingungen ein.

    Die Grundlage ist das Runmanifest: der Prueflauf entsteht spaeter aus
    denselben Feldern, und nur deshalb ist die Bindung pruefbar.
    """
    base = dict(workflow.build_manifest(
        run_id=eval_id,
        settings=settings,
        provider_settings=provider_settings,
        prompts_used=prompts_used,
        taxonomy_content_hash=taxonomy_content_hash,
        snapshot_id=snapshot_id,
        input_manifest_hash=input_manifest_hash,
        unit_count=len(targets),
        budget_usd=budget_usd,
        json_schema=json_schema,
        selection={"chamber": None, "committee": None,
                   "unit_ids": sorted(unit.unit_id for unit in targets)},
    ))
    base.pop("version", None)
    base.pop("run_id", None)
    batching = dict(base.get("batching") or {})
    batching.pop("batch_size", None)
    base["batching"] = batching
    offline = settings.offline
    return {
        "version": EXPORT_MANIFEST_VERSION,
        "eval_id": eval_id,
        "created_at": _now_iso(),
        # Der Ausgang steht im Manifest und nicht in einer zweiten Datei: so
        # entsteht die Revision in einem einzigen atomaren Schreibvorgang und
        # ein Abbruch kann keinen halben Zustand hinterlassen. Den Wechsel
        # nach 'pending' belegt spaeter die Existenz der Labeldatei.
        "state": WorkflowState.AWAITING_DEVELOPMENT_GOLDSET.value,
        "sample_version": sample.version,
        # Die Zuordnung Unit -> Textstand. Aendert sich ein Jurisdiktionstext
        # nach dem Labeln, faellt genau diese Unit auf und nicht der ganze Lauf.
        "mapping_input_hashes": {
            unit.unit_id: unit.mapping_input_hash for unit in
            sorted(targets, key=lambda item: item.unit_id)
        },
        "variants": {
            "batch_sizes": list(offline.evaluation_batch_sizes),
            "repetitions": offline.evaluation_repetitions,
            "max_repetitions": offline.evaluation_max_repetitions,
        },
        **base,
    }


def write_export_manifest(eval_id: str, manifest: Mapping[str, object]) -> Path:
    target = evaluation_path(eval_id, EXPORT_MANIFEST_FILENAME,
                             create_parents=True)
    return paths.write_text_atomic(
        target, json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n")


def load_export_manifest(eval_id: str) -> dict | None:
    """Liest das Exportmanifest; fehlt oder faellt es aus dem Vertrag, ``None``."""
    target = evaluation_path(eval_id, EXPORT_MANIFEST_FILENAME)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != EXPORT_MANIFEST_VERSION:
        return None
    if data.get("eval_id") != eval_id:
        return None
    return data


def require_export_manifest(eval_id: str) -> dict:
    """Wie ``load_export_manifest``, blockiert aber statt ``None`` zu liefern."""
    manifest = load_export_manifest(eval_id)
    if manifest is None:
        raise _blocked(
            "EVAL_EXPORT_MANIFEST_MISSING", eval_id,
            "Zur Evaluationsrevision fehlt ein brauchbares Exportmanifest.",
            f"'evaluate export --eval-id {eval_id}' ausfuehren; ohne Manifest "
            "ist die Herkunft der Labels nicht belegbar.")
    return manifest


# ---------------------------------------------------------------------------
# Bindung an den Prueflauf
# ---------------------------------------------------------------------------

#: Felder, in denen ein Prueflauf mit der Evaluationsrevision uebereinstimmen
#: muss. Es ist bewusst dieselbe Liste wie beim Resume eines Laufs, ohne
#: ``selection`` und ``unit_count``: die Evaluation misst dieselbe Unitmenge,
#: aber der Prueflauf darf sie in mehreren Teillaeufen abarbeiten.
BOUND_FIELDS = ("taxonomy_content_hash", "input_manifest_hash", "snapshot_id",
                "model", "routing", "prompts", "schema", "context",
                "cache_key_version", "batching")


def binding_conflicts(manifest: Mapping[str, object],
                      run_manifest: Mapping[str, object]) -> tuple[str, ...]:
    """Felder, in denen ein Lauf von der Evaluationsrevision abweicht.

    ``batching`` wird ohne ``batch_size`` verglichen: die Batchgroesse ist in
    der Evaluation die Variante, und beide Varianten sind gueltig. Jedes
    andere Feld von ``batching`` gehoert dagegen zur Bindung.
    """
    conflicts: list[str] = []
    for name in BOUND_FIELDS:
        expected, current = manifest.get(name), run_manifest.get(name)
        if name == "batching":
            expected = {key: value for key, value in dict(expected or {}).items()
                        if key != "batch_size"}
            current = {key: value for key, value in dict(current or {}).items()
                       if key != "batch_size"}
        if expected != current:
            conflicts.append(name)
    return tuple(conflicts)


def variant_allowed(manifest: Mapping[str, object], batch_size: int) -> bool:
    """Prueft eine Batchgroesse gegen die eingefrorene Variantenmatrix."""
    variants = dict(manifest.get("variants") or {})
    return batch_size in list(variants.get("batch_sizes") or [])


# ---------------------------------------------------------------------------
# Gold-Set-Import
# ---------------------------------------------------------------------------

GOLDSET_FILENAME = "goldset.json"
GOLDSET_VERSION = "development-goldset-v1"

#: Blatt und Spalten der Labeldatei. Bewusst als Literale gefuehrt und nicht
#: aus ``review.py`` importiert: die Uebereinstimmung mit dem Reviewvertrag ist
#: gewollt, die Kopplung an den Pruefling nicht. Aenderte der Reviewpfad seine
#: Leseregeln, duerfte sich das Gold-Set nicht mitaendern.
LABEL_SHEET = "labels"
LABEL_COLUMNS: tuple[str, ...] = (
    "unit_id", "industry_id", "confidence", "evidence_excerpt",
    "rationale_short", "no_mapping_reason",
)
#: Spalten, deren Wert eine Kennung ist. Nur sie werden beim Lesen getrimmt;
#: Textfelder bleiben zeichengenau, damit das Zitat belegbar bleibt.
STRIPPED_LABEL_COLUMNS: frozenset[str] = frozenset({
    "unit_id", "industry_id", "confidence",
})
MIN_CONFIDENCE = 1
MAX_CONFIDENCE = 4

#: Vorgabe des Labelwerkzeugs (``scripts/committee_matcher_goldset_label.py``).
LABEL_WORKBOOK_NAME = "goldset_review.xlsx"

DECISION_MAPPED = "mapped"
DECISION_NO_MAPPING = "no_mapping"


def label_workbook_path() -> Path:
    return paths.artifact_path(GOLDSET_DIR, LABEL_WORKBOOK_NAME)


def goldset_path(eval_id: str, *, create_parents: bool = False) -> Path:
    return evaluation_path(eval_id, GOLDSET_FILENAME, create_parents=create_parents)


def _rejected(collector: ErrorCollector, code: str, obj: str, message: str,
              hint: str) -> None:
    """Ein abgelehnter Labelimport ist Exit 7; er sammelt jeden Grund."""
    collector.blocker(code, Stage.EVALUATION, obj, message, hint,
                      exit_code=ExitCode.REVIEW_IMPORT_REJECTED)


def _label_text(value: object) -> str:
    return "" if value is None else str(value)


def _row(values: Mapping[str, object]) -> dict[str, str]:
    return {
        name: (_label_text(values.get(name)).strip()
               if name in STRIPPED_LABEL_COLUMNS
               else _label_text(values.get(name)))
        for name in LABEL_COLUMNS
    }


def _read_label_csv(path: Path) -> tuple[dict[str, str], ...]:
    text = path.read_text(encoding="utf-8")
    # ``newline=""`` haelt eingebettete Zeilenumbrueche zusammen; ein
    # mehrzeiliges Zitat waere sonst ein anderer Text als der gelabelte.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != LABEL_COLUMNS:
        raise _blocked(
            "EVAL_LABELS_CONTRACT", paths.relative_to_base(path),
            "Die Spalten der Labeldatei weichen vom Vertrag ab.",
            f"Spalten in der Reihenfolge {', '.join(LABEL_COLUMNS)} fuehren.",
            exit_code=ExitCode.REVIEW_IMPORT_REJECTED)
    rows: list[dict[str, str]] = []
    for line in reader:
        if None in line or any(value is None for value in line.values()):
            raise _blocked(
                "EVAL_LABELS_CONTRACT", paths.relative_to_base(path),
                "Eine Zeile hat eine andere Feldzahl als die Kopfzeile.",
                "Datei mit dem Labelwerkzeug erzeugen; von Hand geaenderte "
                "CSVs werden nicht gelesen.",
                exit_code=ExitCode.REVIEW_IMPORT_REJECTED)
        rows.append(_row(line))
    return tuple(rows)


def _read_label_workbook(path: Path,
                         collector: ErrorCollector) -> tuple[dict[str, str], ...]:
    """Liest das Blatt ``labels`` und lehnt Formelzellen ab.

    Eine Formel liefert beim Lesen ihren Ausdruck statt eines Werts. Sie still
    als Text zu uebernehmen wuerde einen Ausdruck zum Gold-Label machen.
    """
    try:
        book = load_workbook(path, data_only=False)
    except Exception as exc:  # openpyxl wirft je nach Schaden unterschiedlich
        raise _blocked(
            "EVAL_LABELS_UNREADABLE", paths.relative_to_base(path),
            f"Die Labeldatei ist nicht lesbar: {exc}.",
            "Datei des Labelwerkzeugs verwenden und als .xlsx speichern.",
            exit_code=ExitCode.REVIEW_IMPORT_REJECTED)
    if LABEL_SHEET not in book.sheetnames:
        raise _blocked(
            "EVAL_LABELS_CONTRACT", LABEL_SHEET,
            f"Der Labeldatei fehlt das Blatt '{LABEL_SHEET}'.",
            "Blaetter weder umbenennen noch entfernen.",
            exit_code=ExitCode.REVIEW_IMPORT_REJECTED)
    sheet = book[LABEL_SHEET]
    header = tuple(_label_text(cell.value) for cell in sheet[1])
    if header != LABEL_COLUMNS:
        raise _blocked(
            "EVAL_LABELS_CONTRACT", LABEL_SHEET,
            "Die Spaltenkoepfe des Blatts weichen vom Vertrag ab.",
            "Spalten weder umbenennen, verschieben noch entfernen.",
            exit_code=ExitCode.REVIEW_IMPORT_REJECTED)

    rows: list[dict[str, str]] = []
    for line in sheet.iter_rows(min_row=2):
        values: dict[str, object] = {}
        for name, cell in zip(LABEL_COLUMNS, line):
            if cell.data_type == "f":
                _rejected(collector, "EVAL_LABEL_FORMULA_CELL",
                          f"{LABEL_SHEET}!{cell.coordinate}",
                          "Die Zelle enthaelt eine Formel statt eines Werts.",
                          "Formel durch ihren Wert ersetzen (Einfuegen als "
                          "Wert); ein Ausdruck wird nie als Gold-Label "
                          "uebernommen.")
            values[name] = cell.value
        row = _row(values)
        if any(value.strip() for value in row.values()):
            rows.append(row)
    return tuple(rows)


def read_labels(path: Path, collector: ErrorCollector) -> tuple[dict[str, str], ...]:
    """Liest die Labeldatei; XLSX und CSV tragen denselben Spaltenvertrag."""
    if not path.exists():
        raise _blocked(
            "EVAL_LABELS_MISSING", paths.relative_to_base(path),
            "Die Labeldatei existiert nicht.",
            "Pfad mit --labels angeben oder das Gold-Set mit "
            "scripts/committee_matcher_goldset_label.py erzeugen.",
            exit_code=ExitCode.REVIEW_IMPORT_REJECTED)
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _read_label_workbook(path, collector)
    if suffix == ".csv":
        return _read_label_csv(path)
    raise _blocked(
        "EVAL_LABELS_CONTRACT", paths.relative_to_base(path),
        f"Dateiendung '{suffix or path.name}' ist kein Labelformat.",
        "Labels als .xlsx oder .csv uebergeben.",
        exit_code=ExitCode.REVIEW_IMPORT_REJECTED)


@dataclass(frozen=True)
class GoldAssignment:
    """Ein von Hand vergebenes Gold-Label."""

    industry_id: str
    confidence: str
    evidence_excerpt: str
    rationale_short: str


@dataclass(frozen=True)
class GoldUnit:
    """Der Gold-Stand einer Unit."""

    unit_id: str
    decision: str
    no_mapping_reason: str
    assignments: tuple[GoldAssignment, ...]


def _label_confidence(value: str, obj: str,
                      collector: ErrorCollector) -> str | None:
    """Confidence als Text; leer ist zulaessig, Unsinn nicht."""
    span = f"{MIN_CONFIDENCE}..{MAX_CONFIDENCE}"
    if not value:
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        _rejected(collector, "EVAL_LABEL_CONFIDENCE_INVALID", obj,
                  f"'{value}' ist keine ganze Zahl.",
                  f"Confidence als ganze Zahl {span} eintragen oder leer lassen.")
        return None
    if not MIN_CONFIDENCE <= number <= MAX_CONFIDENCE:
        _rejected(collector, "EVAL_LABEL_CONFIDENCE_INVALID", obj,
                  f"Confidence {number} liegt ausserhalb von {span}.",
                  f"Confidence als ganze Zahl {span} eintragen oder leer lassen.")
        return None
    return str(number)


def build_goldset(rows: Sequence[Mapping[str, str]], *,
                  expected_units: Sequence[str], industry_ids: frozenset[str],
                  collector: ErrorCollector) -> tuple[GoldUnit, ...] | None:
    """Verdichtet die Zeilen zum Gold-Stand je Unit.

    Abgelehnt wird nur, was das Ergebnis unbrauchbar macht. Jede Ablehnung
    sammelt der Collector: ein Labeldurchgang soll alle Fehler auf einmal
    nennen und nicht bei jedem Import einen weiteren.
    """
    expected = list(expected_units)
    grouped: dict[str, list[Mapping[str, str]]] = {unit_id: [] for unit_id in expected}
    for position, row in enumerate(rows, start=2):
        unit_id = row["unit_id"]
        if not unit_id:
            _rejected(collector, "EVAL_LABEL_UNIT_MISSING", f"Zeile {position}",
                      "Der Zeile fehlt die unit_id.",
                      "unit_id der gelabelten Unit eintragen; ohne sie ist die "
                      "Zeile keiner Unit zuzuordnen.")
            continue
        if unit_id not in grouped:
            _rejected(collector, "EVAL_LABEL_UNIT_UNKNOWN", unit_id,
                      "Die unit_id gehoert nicht zur Stichprobe dieser "
                      "Evaluationsrevision.",
                      "unit_id zeichengenau aus der Stichprobe uebernehmen; "
                      "eine fremde Unit verzerrt jede Metrik.")
            continue
        grouped[unit_id].append(row)

    units: list[GoldUnit] = []
    for unit_id in expected:
        entries = grouped[unit_id]
        if not entries:
            _rejected(collector, "EVAL_LABEL_UNIT_ABSENT", unit_id,
                      "Zu dieser Unit der Stichprobe gibt es keine Zeile.",
                      "Unit labeln; eine fehlende Unit waere kein 'no_mapping', "
                      "sondern eine Luecke im Gold-Set.")
            continue
        assignments: list[GoldAssignment] = []
        reasons: list[str] = []
        seen: set[str] = set()
        for row in entries:
            # Der Grund wird unabhaengig von der Zeilenart gesammelt: er in
            # einer Zuordnungszeile ist genauso widerspruechlich wie in einer
            # eigenen, und beides muss auffallen.
            if row["no_mapping_reason"].strip():
                reasons.append(row["no_mapping_reason"])
            industry_id = row["industry_id"]
            if not industry_id:
                continue
            obj = f"{unit_id}/{industry_id}"
            if industry_id not in industry_ids:
                _rejected(collector, "EVAL_LABEL_INDUSTRY_UNKNOWN", obj,
                          "Die industry_id kommt im freigegebenen Snapshot "
                          "nicht vor.",
                          "industry_id aus dem Blatt 'taxonomy' uebernehmen; "
                          "Gross- und Kleinschreibung zaehlt.")
                continue
            if industry_id in seen:
                _rejected(collector, "EVAL_LABEL_INDUSTRY_DUPLICATE", obj,
                          "Die Industry ist der Unit mehrfach zugeordnet.",
                          "Doppelte Zeile loeschen; sonst zaehlte dasselbe "
                          "Paar mehrfach in Precision und Recall.")
                continue
            seen.add(industry_id)
            confidence = _label_confidence(row["confidence"], obj, collector)
            if confidence is None:
                continue
            assignments.append(GoldAssignment(
                industry_id=industry_id, confidence=confidence,
                evidence_excerpt=row["evidence_excerpt"],
                rationale_short=row["rationale_short"]))

        reason = next((text for text in reasons if text.strip()), "")
        if assignments and any(text.strip() for text in reasons):
            _rejected(collector, "EVAL_LABEL_CONTRADICTION", unit_id,
                      "Die Unit traegt Zuordnungen und zugleich einen "
                      "no_mapping_reason.",
                      "Entweder die Zuordnungen loeschen oder den Grund; "
                      "beides zusammen ist kein entscheidbares Gold-Label.")
            continue
        if not assignments and not reason.strip():
            _rejected(collector, "EVAL_LABEL_REASON_MISSING", unit_id,
                      "Die Unit hat keine Zuordnung und keinen Grund.",
                      "no_mapping_reason eintragen; ohne ihn ist nicht "
                      "unterscheidbar, ob die Unit bewusst leer blieb.")
            continue
        units.append(GoldUnit(
            unit_id=unit_id,
            decision=DECISION_MAPPED if assignments else DECISION_NO_MAPPING,
            no_mapping_reason="" if assignments else reason,
            assignments=tuple(assignments)))

    if collector.has_blockers():
        return None
    return tuple(units)


def check_label_binding(manifest: Mapping[str, object], *, snapshot,
                        input_manifest_hash: str,
                        units: Mapping[str, MatcherUnit],
                        collector: ErrorCollector) -> None:
    """Stale Bindung lehnt den gesamten Import ab.

    Gelabelt wurde gegen einen bestimmten Taxonomie- und Textstand. Hat sich
    einer davon seither geaendert, beschreiben die Labels nicht mehr das, was
    der Prueflauf sehen wird.
    """
    checks = (
        ("snapshot_id", manifest.get("snapshot_id"), snapshot.snapshot_id),
        ("taxonomy_content_hash", manifest.get("taxonomy_content_hash"),
         snapshot.taxonomy_content_hash),
        ("input_manifest_hash", manifest.get("input_manifest_hash"),
         input_manifest_hash),
    )
    for name, expected, current in checks:
        if _label_text(expected) != _label_text(current):
            _rejected(collector, "EVAL_STALE_BINDING", name,
                      f"{name} weicht vom Stand des Exports ab: eingefroren "
                      f"{expected!r}, jetzt {current!r}.",
                      "Neue Evaluationsrevision exportieren und gegen sie "
                      "labeln; Labels aus einem anderen Stand werden nicht "
                      "uebernommen.")
    for unit_id, expected in dict(manifest.get("mapping_input_hashes") or {}).items():
        unit = units.get(unit_id)
        current = unit.mapping_input_hash if unit is not None else None
        if _label_text(expected) != _label_text(current):
            _rejected(collector, "EVAL_STALE_BINDING", f"mapping_input_hash/{unit_id}",
                      "Der Text dieser Unit hat sich seit dem Export "
                      "geaendert.",
                      "Neue Evaluationsrevision exportieren und die Unit neu "
                      "labeln; das Label gehoert zu einem anderen Text.")


def goldset_payload(eval_id: str, manifest: Mapping[str, object],
                    units: Sequence[GoldUnit], *,
                    source: Path, source_sha256: str,
                    row_count: int) -> dict[str, object]:
    """Das angenommene Gold-Set samt Labelprovenienz."""
    return {
        "version": GOLDSET_VERSION,
        "eval_id": eval_id,
        "created_at": _now_iso(),
        "state": WorkflowState.PENDING.value,
        "snapshot_id": manifest.get("snapshot_id", ""),
        "taxonomy_content_hash": manifest.get("taxonomy_content_hash", ""),
        "input_manifest_hash": manifest.get("input_manifest_hash", ""),
        "sample_version": manifest.get("sample_version", ""),
        "labels": {
            "source": paths.relative_to_base(source),
            "sha256": source_sha256,
            "rows": row_count,
        },
        "unit_count": len(units),
        "mapped_units": sum(1 for unit in units if unit.decision == DECISION_MAPPED),
        "no_mapping_units": sum(1 for unit in units
                                if unit.decision == DECISION_NO_MAPPING),
        "assignment_count": sum(len(unit.assignments) for unit in units),
        "units": [
            {
                "unit_id": unit.unit_id,
                "decision": unit.decision,
                "no_mapping_reason": unit.no_mapping_reason,
                "assignments": [
                    {
                        "industry_id": entry.industry_id,
                        "confidence": entry.confidence,
                        "evidence_excerpt": entry.evidence_excerpt,
                        "rationale_short": entry.rationale_short,
                    }
                    for entry in unit.assignments
                ],
            }
            for unit in units
        ],
    }


def write_goldset(eval_id: str, payload: Mapping[str, object]) -> Path:
    return paths.write_text_atomic(
        goldset_path(eval_id, create_parents=True),
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n")


def load_goldset(eval_id: str) -> dict | None:
    """Liest das Gold-Set der Revision; unbrauchbar wie fehlend behandelt."""
    target = goldset_path(eval_id)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != GOLDSET_VERSION:
        return None
    if data.get("eval_id") != eval_id:
        return None
    return data


def revision_state(eval_id: str) -> WorkflowState | None:
    """Der Zustand der Revision, abgeleitet aus ihren Dateien.

    Der Zustand steht nirgends fuer sich: das Exportmanifest eroeffnet die
    Revision, das Gold-Set fuehrt sie weiter. Eine eigene Zustandsdatei koennte
    von beiden abweichen.
    """
    if load_export_manifest(eval_id) is None:
        return None
    if goldset_path(eval_id).exists():
        return WorkflowState.PENDING
    return WorkflowState.AWAITING_DEVELOPMENT_GOLDSET
