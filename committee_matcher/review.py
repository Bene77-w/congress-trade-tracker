"""Review-Workbook des Committee Matchers V2 (Plan 2.8, Phase P7d).

Der Export spiegelt genau eine abgeschlossene Runrevision in eine XLSX. Er
liest ausschliesslich die geschriebenen Artefakte, nie den Laufzustand: was
Bene reviewt, ist der Stand, der auch in den CSVs steht.

Zwei Eigenschaften sind Vertrag, nicht Komfort:

* ``model_*``-Spalten sind unveraenderlich. Das Exportmanifest haelt je Zeile
  einen Hash ueber genau diese Werte; der Rueckimport rechnet ihn nach und
  verlaesst sich nicht auf den Zellschutz von Excel.
* Jede Zelle wird als Textzelle geschrieben. Ohne den erzwungenen Zelltyp
  wuerde openpyxl einen Wert mit fuehrendem ``=`` als Formel typisieren; der
  Rueckimport bekaeme dann eine Formel statt des Modellwerts zu sehen.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from openpyxl import Workbook, load_workbook

from . import artifacts, paths, validation, workflow
from .errors import ErrorCollector, MatcherError, Stage
from .status import ExitCode

#: Reviewrevisionen liegen neben den Runrevisionen, nicht in ihnen: ein Review
#: darf die Artefakte des Laufs nicht beruehren.
REVIEWS_DIR = "reviews"
WORKBOOK_FILENAME = "review.xlsx"
EXPORT_MANIFEST_FILENAME = "export_manifest.json"

WORKBOOK_VERSION = "review-workbook-v1"
EXPORT_MANIFEST_VERSION = "review-export-v1"

SHEET_ASSIGNMENTS = "assignments"
SHEET_BOUNDARY = "boundary"
SHEET_INFO = "info"

#: Unveraenderliche Herkunft je Zeile. Reihenfolge ist Teil des Vertrags: der
#: Zeilenhash wird ueber genau diese Werte in genau dieser Folge gebildet.
MODEL_COLUMNS: tuple[str, ...] = (
    "model_unit_id", "model_chamber", "model_committee_name", "model_unit_name",
    "model_unit_kind", "model_decision", "model_industry_id", "model_industry_name",
    "model_sector_id", "model_sector_name", "model_confidence",
    "model_evidence_excerpt", "model_rationale_short", "model_broad_scope_exception",
    "model_broad_scope_reason", "model_no_mapping_reason", "model_assignment_source",
)
#: Editierbare Felder. ``review_action`` sagt, was mit der Zeile geschehen soll.
REVIEW_COLUMNS: tuple[str, ...] = (
    "review_action", "review_decision", "review_industry_id", "review_confidence",
    "review_evidence_excerpt", "review_rationale_short",
    "review_broad_scope_exception", "review_broad_scope_reason",
    "review_no_mapping_reason", "review_note",
)
ASSIGNMENT_SHEET_COLUMNS: tuple[str, ...] = ("row_id",) + MODEL_COLUMNS + REVIEW_COLUMNS

#: Konfliktblatt; ``review_action`` ist die einzige editierbare Spalte.
BOUNDARY_SHEET_COLUMNS: tuple[str, ...] = (
    "family_id", "industry_id", "industry_name", "unit_count", "unit_ids",
    "review_action", "review_note",
)

#: Erlaubte Werte der Aktionsspalten (Plan 2.8).
ROW_ACTIONS: tuple[str, ...] = ("keep", "edit", "delete", "add")
BOUNDARY_ACTIONS: tuple[str, ...] = ("keep_a", "keep_b", "remove_both",
                                     "legitimate_overlap")

#: Unitstatus, die kein Reviewurteil tragen und deshalb keine Zeile bekommen.
UNDECIDED_STATUS: frozenset[str] = frozenset({"failed", "not_decided"})


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


def row_hash(row: Mapping[str, object]) -> str:
    """Hash ueber die Modellwerte einer Zeile; Grundlage der Read-only-Pruefung."""
    payload = "\n".join(f"{name}={_text(row.get(name))}" for name in MODEL_COLUMNS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    lines = text.splitlines()
    rows = tuple(csv.DictReader(lines))
    header = tuple(next(csv.reader(lines), ()))
    if header != tuple(columns):
        raise _blocked(
            "REV_ARTIFACT_CONTRACT", paths.relative_to_base(path),
            "Die Spalten des Artefakts weichen vom Vertrag ab.",
            "Artefakt aus derselben Programmversion erzeugen; von Hand "
            "geaenderte CSVs werden nicht gelesen.")
    return rows


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


def _assignment_row(source: Mapping[str, str], row_id: str) -> dict[str, object]:
    return {
        "row_id": row_id,
        "model_unit_id": source["unit_id"],
        "model_chamber": source["chamber"],
        "model_committee_name": source["committee_name"],
        "model_unit_name": source["unit_name"],
        "model_unit_kind": source["unit_kind"],
        "model_decision": source["decision"],
        "model_industry_id": source.get("industry_id", ""),
        "model_industry_name": source.get("industry_name", ""),
        "model_sector_id": source.get("sector_id", ""),
        "model_sector_name": source.get("sector_name", ""),
        "model_confidence": source.get("confidence", ""),
        "model_evidence_excerpt": source.get("evidence_excerpt", ""),
        "model_rationale_short": source.get("rationale_short", ""),
        "model_broad_scope_exception": source.get("broad_scope_exception", ""),
        "model_broad_scope_reason": source.get("broad_scope_reason", ""),
        "model_no_mapping_reason": source.get("no_mapping_reason", ""),
        "model_assignment_source": source.get("assignment_source", ""),
    }


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
        status = entry["status"]
        if status in UNDECIDED_STATUS:
            incomplete.append(unit_id)
            continue
        if status == "no_mapping":
            rows.append(_assignment_row({**entry, "decision": "no_mapping"},
                                        f"r{len(rows) + 1:05d}"))
            continue
        for source in by_unit.get(unit_id, ()):
            rows.append(_assignment_row(source, f"r{len(rows) + 1:05d}"))
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


def build_workbook(rows: Sequence[Mapping[str, object]],
                   boundary: Sequence[Mapping[str, object]],
                   info: Mapping[str, object]) -> Workbook:
    """Baut das Workbook; drei Blaetter, alles Text."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_ASSIGNMENTS
    _write_sheet(sheet, ASSIGNMENT_SHEET_COLUMNS, rows)
    _write_sheet(book.create_sheet(SHEET_BOUNDARY), BOUNDARY_SHEET_COLUMNS, boundary)

    facts = book.create_sheet(SHEET_INFO)
    _cell(facts, 1, 1, "key")
    _cell(facts, 1, 2, "value")
    for line, (key, value) in enumerate(sorted(info.items()), start=2):
        _cell(facts, line, 1, key)
        _cell(facts, line, 2, value)
    return book


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
            "model": list(MODEL_COLUMNS),
            "review": list(REVIEW_COLUMNS),
            "boundary": list(BOUNDARY_SHEET_COLUMNS),
        },
        # Read-only-Pruefung des Rueckimports: Zeilen-ID -> Hash der Modellwerte.
        "rows": {str(row["row_id"]): row_hash(row) for row in rows},
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


def export_review(run_id: str, review_id: str) -> ExportedReview:
    """Schreibt Workbook und Exportmanifest einer abgeschlossenen Revision."""
    manifest = _require_complete_run(run_id)
    if review_path(review_id, WORKBOOK_FILENAME).exists():
        raise _blocked(
            "REV_EXPORT_EXISTS", review_id,
            "Zu dieser Review-ID gibt es bereits ein Workbook.",
            "Eine Reviewrevision wird nie ueberschrieben; neue --review-id "
            "waehlen.")

    assignments = _read_csv(
        workflow.mapping_path(run_id, artifacts.ASSIGNMENTS_FILENAME),
        artifacts.ASSIGNMENT_COLUMNS)
    coverage = _read_csv(
        workflow.mapping_path(run_id, artifacts.UNIT_COVERAGE_FILENAME),
        artifacts.UNIT_COVERAGE_COLUMNS)
    conflicts = _read_csv(
        workflow.mapping_path(run_id, artifacts.BOUNDARY_FILENAME),
        artifacts.BOUNDARY_COLUMNS)

    rows, incomplete = workbook_rows(assignments, coverage)
    boundary = tuple({**row, "review_action": "", "review_note": ""}
                     for row in conflicts)
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
        "hinweis": ("model_*-Spalten nicht aendern; Zeilen weder loeschen noch "
                    "umsortieren; neue Zuordnung als neue Zeile ohne row_id."),
    }

    workbook_target = review_path(review_id, WORKBOOK_FILENAME, create_parents=True)
    manifest_target = review_path(review_id, EXPORT_MANIFEST_FILENAME,
                                  create_parents=True)
    try:
        build_workbook(rows, boundary, info).save(workbook_target)
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

#: Reviewfelder, die eine Zeile inhaltlich veraendern. ``review_note`` und
#: ``review_action`` gehoeren nicht dazu: eine Notiz allein aendert kein Urteil.
EDIT_FIELDS: tuple[str, ...] = (
    "review_decision", "review_industry_id", "review_confidence",
    "review_evidence_excerpt", "review_rationale_short",
    "review_broad_scope_exception", "review_broad_scope_reason",
    "review_no_mapping_reason",
)
#: Felder, die eine ``no_mapping``-Zeile zwingend leer laesst (Plan 2.8).
NO_MAPPING_MUST_BE_EMPTY: tuple[str, ...] = (
    "review_industry_id", "review_confidence", "review_evidence_excerpt",
    "review_rationale_short", "review_broad_scope_exception",
    "review_broad_scope_reason",
)
#: Spalten der reviewten Long-CSV; sie erweitert den Artefaktvertrag um die
#: Reviewherkunft und laesst die Callprovenienz manueller Zeilen leer.
REVIEWED_COLUMNS: tuple[str, ...] = artifacts.ASSIGNMENT_COLUMNS + (
    "review_id", "review_action", "review_note")
REVIEWED_COVERAGE_COLUMNS: tuple[str, ...] = (
    "review_id", "run_id", "unit_id", "chamber", "committee_name", "unit_name",
    "unit_kind", "decision", "assignment_count", "no_mapping_reason",
    "broad_scope_exception", "broad_scope_reason", "manual_assignments",
    "review_status",
)
REVIEWED_ASSIGNMENTS_FILENAME = "reviewed_assignments.csv"
REVIEWED_COVERAGE_FILENAME = "reviewed_unit_coverage.csv"
IMPORT_MANIFEST_FILENAME = "import_manifest.json"
IMPORT_MANIFEST_VERSION = "review-import-v1"
#: Reviewstatus einer angenommenen Unit (Plan 2.8).
REVIEW_STATUS_REVIEWED = "reviewed"


@dataclass(frozen=True)
class EffectiveAssignment:
    """Eine Zuordnung nach dem Review, mit ihrer Herkunft."""

    unit_id: str
    industry_id: str
    confidence: int
    evidence_excerpt: str
    rationale_short: str
    source: str
    action: str
    note: str
    #: Herkunft des Modelldrafts; bei manuellen Zeilen leer.
    origin: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectiveUnit:
    """Reviewstand einer Unit."""

    unit_id: str
    decision: str
    no_mapping_reason: str
    broad_scope_exception: bool
    broad_scope_reason: str
    assignments: tuple[EffectiveAssignment, ...]
    #: Industry-IDs, die der Review entfernt hat; die Boundary-Pruefung
    #: vergleicht die gemeldete Aktion mit genau dieser Menge.
    removed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ImportedReview:
    """Ergebnis eines angenommenen Rueckimports."""

    review_id: str
    run_id: str
    units: int
    assignments: int
    manual_assignments: int
    no_mapping_units: int
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


def read_workbook(path: Path, collector: ErrorCollector
                  ) -> tuple[tuple[dict[str, str], ...], tuple[dict[str, str], ...]]:
    """Liest beide Blaetter als Text und lehnt jede Formelzelle ab.

    Eine Formel liefert beim Lesen ihren Ausdruck statt eines Werts. Sie still
    als Text zu uebernehmen wuerde einen Ausdruck zum Reviewurteil machen.
    """
    try:
        book = load_workbook(path, data_only=False)
    except Exception as exc:  # openpyxl wirft je nach Schaden unterschiedlich
        raise _blocked(
            "REV_WORKBOOK_UNREADABLE", paths.relative_to_base(path),
            f"Das Workbook ist nicht lesbar: {exc}.",
            "Datei aus 'review export' verwenden und als .xlsx speichern.")

    sheets: dict[str, tuple[dict[str, str], ...]] = {}
    for name, columns in ((SHEET_ASSIGNMENTS, ASSIGNMENT_SHEET_COLUMNS),
                          (SHEET_BOUNDARY, BOUNDARY_SHEET_COLUMNS)):
        if name not in book.sheetnames:
            raise _blocked(
                "REV_WORKBOOK_CONTRACT", name,
                f"Dem Workbook fehlt das Blatt '{name}'.",
                "Blaetter weder umbenennen noch entfernen.")
        sheets[name] = _sheet_rows(book[name], columns, collector)
    return sheets[SHEET_ASSIGNMENTS], sheets[SHEET_BOUNDARY]


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
            values[name] = _text(cell.value).strip()
        if any(values.values()):
            rows.append(values)
    return tuple(rows)


def check_binding(manifest: Mapping[str, object], run_manifest: Mapping[str, object],
                  snapshot, input_manifest_hash: str,
                  collector: ErrorCollector) -> None:
    """Stale Bindung blockiert den gesamten Import (Plan 2.8)."""
    checks = (
        ("snapshot_id", manifest.get("snapshot_id"), run_manifest.get("snapshot_id")),
        ("taxonomy_content_hash", manifest.get("taxonomy_content_hash"),
         snapshot.taxonomy_content_hash),
        ("input_manifest_hash", manifest.get("input_manifest_hash"),
         input_manifest_hash),
        ("model", manifest.get("model"), run_manifest.get("model")),
    )
    for name, exported, current in checks:
        if _text(exported) != _text(current):
            _reject(collector, "REV_STALE_BINDING", name,
                    f"{name} weicht vom Stand des Exports ab: "
                    f"exportiert {exported!r}, jetzt {current!r}.",
                    "Der Review gehoert zu genau einem Lauf; Workbook aus einem "
                    "neuen Export bearbeiten.")


def check_read_only(rows: Sequence[Mapping[str, str]],
                    manifest: Mapping[str, object],
                    collector: ErrorCollector) -> None:
    """Prueft die Modellspalten gegen die Hashes des Exportmanifests."""
    expected = dict(manifest.get("rows") or {})
    seen: set[str] = set()
    for row in rows:
        row_id = row["row_id"]
        if not row_id:
            continue
        if row_id in seen:
            _reject(collector, "REV_ROW_DUPLICATE", row_id,
                    "Die Zeilen-ID kommt mehrfach vor.",
                    "Kopierte Zeile: row_id der Kopie leeren; eine neue "
                    "Zuordnung ist eine Zeile ohne row_id.")
            continue
        seen.add(row_id)
        if row_id not in expected:
            _reject(collector, "REV_ROW_UNKNOWN", row_id,
                    "Die Zeilen-ID steht nicht im Exportmanifest.",
                    "row_id nicht von Hand vergeben; neue Zeilen bleiben ohne ID.")
            continue
        if row_hash(row) != expected[row_id]:
            _reject(collector, "REV_MODEL_FIELD_CHANGED", row_id,
                    "Mindestens eine model_*-Spalte dieser Zeile wurde veraendert.",
                    "Modellwerte bleiben unveraendert; Korrekturen gehoeren in "
                    "die review_*-Spalten.")
    for row_id in sorted(set(expected) - seen):
        _reject(collector, "REV_ROW_MISSING", row_id,
                "Eine exportierte Zeile fehlt im Workbook.",
                "Zeilen werden nie geloescht; zum Entfernen einer Zuordnung "
                "review_action=delete setzen.")


def _flagged(value: str) -> bool:
    return value.strip().lower() in ("true", "wahr", "1", "ja", "x")


def _row_action(row: Mapping[str, str], collector: ErrorCollector) -> str:
    """Bestimmt die Aktion einer Zeile; leer heisst 'unveraendert'."""
    given = row["review_action"].strip().lower()
    edited = any(row[name] for name in EDIT_FIELDS)
    if not row["row_id"]:
        if given not in ("", "add"):
            _reject(collector, "REV_ACTION_INVALID", "neue Zeile",
                    f"Eine Zeile ohne row_id traegt die Aktion '{given}'.",
                    "Neue Zeilen sind immer 'add'; Feld leer lassen oder 'add' "
                    "eintragen.")
        return "add"
    if given and given not in ROW_ACTIONS:
        _reject(collector, "REV_ACTION_INVALID", row["row_id"],
                f"'{given}' ist keine zulaessige Aktion.",
                f"Zulaessig: {', '.join(ROW_ACTIONS)}.")
        return "keep"
    if given == "add":
        _reject(collector, "REV_ACTION_INVALID", row["row_id"],
                "Eine exportierte Zeile kann nicht 'add' sein.",
                "Neue Zuordnung als neue Zeile ohne row_id eintragen.")
        return "keep"
    if given == "keep" and edited:
        _reject(collector, "REV_ACTION_CONTRADICTS_EDIT", row["row_id"],
                "Die Zeile ist als 'keep' markiert, traegt aber geaenderte "
                "review_*-Werte.",
                "Aktion auf 'edit' setzen oder die Reviewfelder leeren.")
        return "edit"
    if given:
        return given
    return "edit" if edited else "keep"


def _confidence(value: str, obj: str, collector: ErrorCollector) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        _reject(collector, "REV_CONFIDENCE_INVALID", obj,
                f"'{value}' ist keine ganze Zahl.",
                "Confidence als ganze Zahl 1..5 eintragen.")
        return None
    if not 1 <= number <= 5:
        _reject(collector, "REV_CONFIDENCE_INVALID", obj,
                f"Confidence {number} liegt ausserhalb von 1..5.",
                "Confidence als ganze Zahl 1..5 eintragen.")
        return None
    return number


def _effective_value(row: Mapping[str, str], field_name: str) -> str:
    """Reviewwert, sonst Modellwert."""
    return row[f"review_{field_name}"] or row[f"model_{field_name}"]


def _assignment_from_row(row: Mapping[str, str], action: str,
                         collector: ErrorCollector) -> EffectiveAssignment | None:
    """Baut die Zuordnung einer Zeile aus Modell- und Reviewwerten."""
    unit_id = row["model_unit_id"]
    obj = row["row_id"] or f"{unit_id} (neu)"
    industry_id = _effective_value(row, "industry_id")
    if not industry_id:
        _reject(collector, "REV_INDUSTRY_MISSING", obj,
                "Der Zeile fehlt eine industry_id.",
                "industry_id aus der Taxonomie eintragen oder die Zeile mit "
                "review_action=delete entfernen.")
        return None
    confidence = _confidence(_effective_value(row, "confidence"), obj, collector)
    evidence = _effective_value(row, "evidence_excerpt")
    rationale = _effective_value(row, "rationale_short")
    for name, value in (("evidence_excerpt", evidence), ("rationale_short", rationale)):
        if not value:
            _reject(collector, "REV_FIELD_REQUIRED", obj,
                    f"Der Zeile fehlt {name}.",
                    "Jede Zuordnung nennt Beleg und Begruendung.")
    if confidence is None or not evidence or not rationale:
        return None

    changed_industry = bool(row["review_industry_id"]
                            and row["review_industry_id"] != row["model_industry_id"])
    new_row = action == "add" or row["model_decision"] == "no_mapping"
    if (changed_industry or new_row) and not (row["review_evidence_excerpt"]
                                              and row["review_rationale_short"]):
        _reject(collector, "REV_EVIDENCE_REQUIRED", obj,
                "Eine neue oder geaenderte Industry verlangt review_evidence_excerpt "
                "und review_rationale_short.",
                "Beleg und Begruendung des Reviews eintragen; die Modellwerte "
                "gelten fuer die urspruengliche Industry.")
        return None
    if (not changed_industry and not new_row and row["review_confidence"]
            and row["review_confidence"] != row["model_confidence"]
            and not row["review_rationale_short"]):
        _reject(collector, "REV_RATIONALE_REQUIRED", obj,
                "Eine geaenderte Confidence verlangt review_rationale_short.",
                "Begruendung der Aenderung eintragen.")
        return None

    manual = action in ("edit", "add")
    return EffectiveAssignment(
        unit_id=unit_id, industry_id=industry_id, confidence=confidence,
        evidence_excerpt=evidence, rationale_short=rationale,
        source="manual" if manual else "model",
        action=action, note=row["review_note"],
        origin=dict(row) if row["row_id"] else {})


def _no_mapping_checks(row: Mapping[str, str], action: str,
                       collector: ErrorCollector) -> str:
    """Prueft eine ``no_mapping``-Zeile und liefert ihren Grund."""
    obj = row["row_id"] or row["model_unit_id"]
    reason = row["review_no_mapping_reason"] or row["model_no_mapping_reason"]
    if action != "keep" and not row["review_no_mapping_reason"]:
        _reject(collector, "REV_NO_MAPPING_REASON_REQUIRED", obj,
                "Der Wechsel auf no_mapping verlangt review_no_mapping_reason.",
                "Grund eintragen; ohne Grund bleibt die Zuordnung bestehen.")
    for name in NO_MAPPING_MUST_BE_EMPTY:
        if row[name]:
            _reject(collector, "REV_NO_MAPPING_NOT_EMPTY", obj,
                    f"no_mapping verlangt ein leeres {name}.",
                    "Industry-, Confidence-, Evidence-, Rationale- und "
                    "Broad-Scope-Reviewfelder leeren.")
    if not reason:
        _reject(collector, "REV_NO_MAPPING_REASON_REQUIRED", obj,
                "Der Zeile fehlt ein Grund fuer no_mapping.",
                "review_no_mapping_reason eintragen.")
    return reason


def effective_units(rows: Sequence[Mapping[str, str]], known_units: Mapping[str, object],
                    collector: ErrorCollector) -> dict[str, EffectiveUnit]:
    """Wertet alle Zeilen zum Reviewstand je Unit aus."""
    grouped: dict[str, list[tuple[Mapping[str, str], str]]] = {}
    for row in rows:
        unit_id = row["model_unit_id"]
        action = _row_action(row, collector)
        if not unit_id:
            _reject(collector, "REV_UNIT_MISSING", row["row_id"] or "neue Zeile",
                    "Der Zeile fehlt die Unit-ID.",
                    "model_unit_id der Zielunit eintragen; nur so ist eine neue "
                    "Zuordnung zuzuordnen.")
            continue
        if unit_id not in known_units:
            _reject(collector, "REV_UNIT_UNKNOWN", unit_id,
                    "Die Unit gehoert nicht zur exportierten Revision.",
                    "Unitzeilen duerfen weder fehlen noch hinzukommen.")
            continue
        if action == "add":
            leftover = [name for name in MODEL_COLUMNS
                        if name != "model_unit_id" and row[name]]
            if leftover:
                _reject(collector, "REV_NEW_ROW_NOT_EMPTY", unit_id,
                        "Eine neue Zeile traegt Modellwerte: "
                        f"{', '.join(sorted(leftover))}.",
                        "In einer neuen Zeile nur model_unit_id und die "
                        "review_*-Felder fuellen.")
                continue
        grouped.setdefault(unit_id, []).append((row, action))

    units: dict[str, EffectiveUnit] = {}
    for unit_id, entries in grouped.items():
        assignments: list[EffectiveAssignment] = []
        removed: set[str] = set()
        decisions: set[str] = set()
        reason = ""
        broad_scope = False
        broad_reason = ""
        for row, action in entries:
            # Eine neue Zeile hat keinen Modellwert; sie ist immer eine Zuordnung.
            decision = (row["review_decision"] or row["model_decision"]
                        or ("mapped" if action == "add" else "")).strip().lower()
            if decision not in ("mapped", "no_mapping"):
                _reject(collector, "REV_DECISION_INVALID", row["row_id"] or unit_id,
                        f"'{decision}' ist keine zulaessige Entscheidung.",
                        "Zulaessig sind 'mapped' und 'no_mapping'.")
                continue
            if action == "delete":
                removed.add(row["model_industry_id"])
                # Faellt mit dieser Zeile die letzte Zuordnung, steht der Grund
                # fuer no_mapping in genau ihr - nirgends sonst.
                reason = row["review_no_mapping_reason"] or reason
                continue
            if decision == "no_mapping":
                decisions.add("no_mapping")
                reason = _no_mapping_checks(row, action, collector) or reason
                if row["model_industry_id"]:
                    removed.add(row["model_industry_id"])
                continue
            decisions.add("mapped")
            entry = _assignment_from_row(row, action, collector)
            if entry is not None:
                assignments.append(entry)
            if _flagged(row["review_broad_scope_exception"]
                        or row["model_broad_scope_exception"]):
                broad_scope = True
                broad_reason = (row["review_broad_scope_reason"]
                                or row["model_broad_scope_reason"] or broad_reason)

        if "no_mapping" in decisions and assignments:
            _reject(collector, "REV_UNIT_DECISION_CONFLICT", unit_id,
                    "Die Unit traegt no_mapping und zugleich Zuordnungen.",
                    "Entweder alle Zuordnungen entfernen oder no_mapping "
                    "zuruecknehmen.")
        decision = "no_mapping" if ("no_mapping" in decisions and not assignments) else (
            "mapped" if assignments else "no_mapping")
        if decision == "no_mapping" and not reason:
            _reject(collector, "REV_NO_MAPPING_REASON_REQUIRED", unit_id,
                    "Die Unit hat keine Zuordnung mehr, aber keinen Grund.",
                    "review_no_mapping_reason in der Zeile der Unit eintragen "
                    "oder eine Zuordnung behalten.")
        units[unit_id] = EffectiveUnit(
            unit_id=unit_id, decision=decision, no_mapping_reason=reason,
            broad_scope_exception=broad_scope and decision == "mapped",
            broad_scope_reason=broad_reason,
            assignments=tuple(assignments), removed=frozenset(removed))
    return units


def check_units(units: Mapping[str, EffectiveUnit], corpus: Mapping[str, object],
                snapshot, collector: ErrorCollector) -> None:
    """Erzwingt dieselben 0/2/5- und Broad-Scope-Regeln wie beim Modell."""
    known_ids = snapshot.industry_ids()
    for unit_id in sorted(units):
        state = units[unit_id]
        unit = corpus[unit_id]
        limit = validation._unit_limit(unit)
        if len(state.assignments) > limit:
            _reject(collector, "REV_TOO_MANY_ASSIGNMENTS", unit_id,
                    f"{len(state.assignments)} Zuordnungen, zulaessig sind "
                    f"hoechstens {limit}.",
                    "Zuordnungen mit review_action=delete entfernen; die Grenze "
                    "gilt fuer Modell und Review gleich.")
        if state.broad_scope_exception and limit == validation.MAX_ASSIGNMENTS_REGULAR:
            _reject(collector, "REV_BROAD_SCOPE_NOT_ALLOWED", unit_id,
                    f"Die Broad-Scope-Ausnahme ist fuer Unittyp {unit.kind.value} "
                    f"mit Basis {unit.input_basis.value} nicht zulaessig.",
                    "Ausnahme zuruecknehmen und auf hoechstens "
                    f"{validation.MAX_ASSIGNMENTS_REGULAR} Zuordnungen kuerzen.")
        if state.broad_scope_exception and not state.broad_scope_reason:
            _reject(collector, "REV_BROAD_SCOPE_REASON_REQUIRED", unit_id,
                    "Die Broad-Scope-Ausnahme verlangt eine Begruendung.",
                    "review_broad_scope_reason eintragen.")
        seen: set[str] = set()
        for entry in state.assignments:
            if entry.industry_id in seen:
                _reject(collector, "REV_INDUSTRY_DUPLICATE",
                        f"{unit_id}/{entry.industry_id}",
                        "Die Industry ist der Unit mehrfach zugeordnet.",
                        "Doppelte Zeile mit review_action=delete entfernen.")
            seen.add(entry.industry_id)
            if entry.industry_id not in known_ids:
                _reject(collector, "REV_UNKNOWN_INDUSTRY",
                        f"{unit_id}/{entry.industry_id}",
                        "Die industry_id kommt im Snapshot des Laufs nicht vor.",
                        "ID zeichengenau aus der Taxonomie uebernehmen; Sektor "
                        "und Namen leitet der Import selbst ab.")


def check_boundary(rows: Sequence[Mapping[str, str]],
                   units: Mapping[str, EffectiveUnit],
                   collector: ErrorCollector) -> set[tuple[str, str]]:
    """Prueft die Konfliktaktionen gegen die tatsaechlich bearbeiteten Slots.

    Eine leere Aktion ist der Regelfall und kein Befund: getrennte
    Zustaendigkeiten derselben Committee-Familie beruehren dieselbe Industry
    haeufig zu Recht. Das Konfliktblatt ist damit eine Uebersicht, keine
    Pflichtliste - entschieden werden muss nur, wo tatsaechlich etwas wegfaellt.

    Liefert die Paare (Unit, Industry), deren Loeschung durch eine
    Konfliktaktion bereits dokumentiert ist; sie brauchen keine Notiz.
    """
    documented: set[tuple[str, str]] = set()
    for row in rows:
        industry_id = row["industry_id"]
        members = [part for part in row["unit_ids"].split(artifacts.CELL_SEPARATOR)
                   if part]
        obj = f"{row['family_id']}/{industry_id}"
        action = row["review_action"].strip().lower()
        if not action:
            continue  # Default: Ueberlappung bleibt, siehe Docstring
        if action not in BOUNDARY_ACTIONS:
            _reject(collector, "REV_BOUNDARY_ACTION_INVALID", obj,
                    f"'{action}' ist keine zulaessige Konfliktaktion.",
                    f"Zulaessig: {', '.join(BOUNDARY_ACTIONS)}.")
            continue

        holders = {unit_id for unit_id in members
                   if any(entry.industry_id == industry_id
                          for entry in units.get(unit_id, EffectiveUnit(
                              unit_id, "no_mapping", "", False, "", ())).assignments)}
        if action == "legitimate_overlap":
            if set(members) != holders:
                _reject(collector, "REV_BOUNDARY_ACTION_MISMATCH", obj,
                        "'legitimate_overlap' verlangt, dass jede beteiligte Unit "
                        "die Industry behaelt.",
                        "Entweder alle Zuordnungen behalten oder eine andere "
                        "Aktion waehlen.")
            continue
        if action == "remove_both":
            if holders:
                _reject(collector, "REV_BOUNDARY_ACTION_MISMATCH", obj,
                        "'remove_both' verlangt, dass keine beteiligte Unit die "
                        f"Industry behaelt; sie steht noch bei: "
                        f"{', '.join(sorted(holders))}.",
                        "Zuordnungen mit review_action=delete entfernen.")
            documented.update((unit_id, industry_id) for unit_id in members)
            continue
        if len(members) != 2:
            _reject(collector, "REV_BOUNDARY_ACTION_AMBIGUOUS", obj,
                    f"'{action}' ist nur bei genau zwei beteiligten Units "
                    f"eindeutig; hier sind es {len(members)}.",
                    "'remove_both' oder 'legitimate_overlap' waehlen und die "
                    "verbleibenden Slots direkt bearbeiten.")
            continue
        keeper = members[0] if action == "keep_a" else members[1]
        loser = members[1] if action == "keep_a" else members[0]
        if holders != {keeper}:
            _reject(collector, "REV_BOUNDARY_ACTION_MISMATCH", obj,
                    f"'{action}' verlangt die Industry genau bei {keeper}; "
                    f"vorhanden bei: {', '.join(sorted(holders)) or 'keiner Unit'}.",
                    f"Zuordnung bei {loser} mit review_action=delete entfernen "
                    f"und bei {keeper} behalten.")
        documented.add((loser, industry_id))
    return documented


def check_delete_notes(rows: Sequence[Mapping[str, str]],
                       documented: set[tuple[str, str]],
                       collector: ErrorCollector) -> None:
    """Eine geloeschte Zuordnung braucht einen Grund (Plan 2.8)."""
    for row in rows:
        if row["review_action"].strip().lower() != "delete":
            continue
        key = (row["model_unit_id"], row["model_industry_id"])
        if row["review_note"] or key in documented:
            continue
        _reject(collector, "REV_DELETE_NOTE_REQUIRED", row["row_id"],
                "Die geloeschte Zuordnung hat keine Notiz.",
                "review_note eintragen oder den Grund ueber eine Boundary-Aktion "
                "dokumentieren.")


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
            model_row = entry.source == "model"
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
                "call_id": origin.get("call_id", "") if model_row else "",
                "provider": origin.get("provider", "") if model_row else "",
                "generation_id": origin.get("generation_id", "") if model_row else "",
                "from_repair": origin.get("from_repair", "") if model_row else "",
                "model": origin.get("model", "") if model_row else "",
                "taxonomy_content_hash": snapshot.taxonomy_content_hash,
                "snapshot_id": snapshot.snapshot_id,
                "prompt_id": origin.get("prompt_id", "") if model_row else "",
                "prompt_version": origin.get("prompt_version", "") if model_row else "",
                "prompt_sha256": origin.get("prompt_sha256", "") if model_row else "",
                "schema_name": origin.get("schema_name", "") if model_row else "",
                "schema_sha256": origin.get("schema_sha256", "") if model_row else "",
                "review_id": review_id,
                "review_action": entry.action,
                "review_note": entry.note,
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


def _write_review_file(review_id: str, filename: str, text: str) -> str:
    target = review_path(review_id, filename, create_parents=True)
    paths.write_text_atomic(target, text)
    return paths.relative_to_base(target)


def import_review(review_id: str, *, corpus: Mapping[str, object], snapshot,
                  run_manifest: Mapping[str, object], input_manifest_hash: str,
                  collector: ErrorCollector, workbook: Path | None = None,
                  ) -> ImportedReview | None:
    """Prueft das bearbeitete Workbook und schreibt die reviewten Artefakte.

    Der Import ist alles oder nichts: erst wenn keine Regel verletzt ist,
    entsteht eine Datei. Ein abgelehnter Import laesst die Reviewrevision
    unveraendert.
    """
    manifest = load_export_manifest(review_id)
    run_id = str(manifest.get("run_id", ""))
    target = workbook or review_path(review_id, WORKBOOK_FILENAME)
    if reviewed_path_exists(review_id):
        raise _blocked(
            "REV_IMPORT_EXISTS", review_id,
            "Die Reviewrevision traegt bereits einen Importstand.",
            "Eine reviewte Revision wird nie ueberschrieben; neue Review-ID "
            "exportieren und dort importieren.")

    rows, boundary = read_workbook(target, collector)
    check_binding(manifest, run_manifest, snapshot, input_manifest_hash, collector)
    check_read_only(rows, manifest, collector)

    exported_units = {str(unit_id) for unit_id in
                      (row.get("model_unit_id") for row in rows) if unit_id}
    known = {unit_id: corpus[unit_id] for unit_id in exported_units if unit_id in corpus}
    units = effective_units(rows, known, collector)
    check_units(units, known, snapshot, collector)
    documented = check_boundary(boundary, units, collector)
    check_delete_notes(rows, documented, collector)
    if collector.has_blockers():
        return None

    written = [
        _write_review_file(
            review_id, REVIEWED_ASSIGNMENTS_FILENAME,
            artifacts.render_csv(REVIEWED_COLUMNS,
                                 reviewed_rows(review_id, run_id, units, known, snapshot))),
        _write_review_file(
            review_id, REVIEWED_COVERAGE_FILENAME,
            artifacts.render_csv(REVIEWED_COVERAGE_COLUMNS,
                                 reviewed_coverage_rows(review_id, run_id, units, known))),
    ]
    assignments = sum(len(state.assignments) for state in units.values())
    manual = sum(1 for state in units.values() for entry in state.assignments
                 if entry.source == "manual")
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
        "no_mapping_units": sorted(unit_id for unit_id, state in units.items()
                                   if state.decision == "no_mapping"),
        "review_status": REVIEW_STATUS_REVIEWED,
        "files": [Path(name).name for name in written],
    }
    written.append(_write_review_file(
        review_id, IMPORT_MANIFEST_FILENAME,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"))
    return ImportedReview(
        review_id=review_id, run_id=run_id, units=len(units),
        assignments=assignments, manual_assignments=manual,
        no_mapping_units=len(payload["no_mapping_units"]),
        files=tuple(written))


def reviewed_path_exists(review_id: str) -> bool:
    """Traegt die Reviewrevision bereits einen Importstand?"""
    return review_path(review_id, IMPORT_MANIFEST_FILENAME).exists()
