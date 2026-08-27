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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from openpyxl import Workbook

from . import artifacts, paths, workflow
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
