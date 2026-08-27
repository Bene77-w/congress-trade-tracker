"""Tests des Review-Roundtrips des Committee Matchers V2 (P7d).

Netzwerkfrei: der Lauf wird mit dem Skriptprovider aus den P7c-Tests erzeugt,
der Export liest ausschliesslich die geschriebenen Artefakte.

Das Workbook ist eine Arbeitstabelle: geprueft wird deshalb, dass eine
gewoehnliche Handaenderung ohne Zusatzeintrag ankommt und dass der Import
selbst ausrechnet, was geschehen ist.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import cli, paths, review, taxonomy_snapshot, workflow  # noqa: E402
from committee_matcher.status import ExitCode  # noqa: E402

from tests.test_committee_matcher_run_cli_p7 import (  # noqa: E402
    INDUSTRIES, ScriptedProvider, execute_argv, plan_argv, sandbox, scripted,  # noqa: F401
)

#: Vier Industries: nur so kann jede Unit eine eigene bekommen und der
#: Standardfall bleibt ohne Boundary-Konflikt.
WIDE_INDUSTRIES = INDUSTRIES + [
    {
        "industry_id": "gold",
        "industry_name": "Gold",
        "sector_id": "basic-materials",
        "sector_name": "Basic Materials",
        "description": "Foerderung und Verarbeitung von Gold.",
    },
    {
        "industry_id": "oil-gas-midstream",
        "industry_name": "Oil Gas Midstream",
        "sector_id": "energy",
        "sector_name": "Energy",
        "description": "Transport und Lagerung von Oel und Gas.",
    },
]
INDUSTRY_POOL = tuple(entry["industry_id"] for entry in WIDE_INDUSTRIES)


def write_wide_baseline(run_id: str = "run-taxonomy") -> str:
    """Freigegebene Taxonomie mit vier Industries."""
    digest = taxonomy_snapshot.hash_records(WIDE_INDUSTRIES)
    target = taxonomy_snapshot.baseline_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "content_version": taxonomy_snapshot.TAXONOMY_CONTENT_VERSION,
            "approved_run_id": run_id,
            "taxonomy_content_hash": digest,
            "yfinance_version": "0.2.99",
            "industries": WIDE_INDUSTRIES,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return digest


class DistinctProvider(ScriptedProvider):
    """Gibt jeder Unit eine eigene Industry; erzeugt keinen Boundary-Konflikt."""

    def complete(self, request):
        response = super().complete(request)
        payload = dict(response.payload)
        payload["units"] = [
            {**unit, "assignments": [{
                "industry_id": INDUSTRY_POOL[index % len(INDUSTRY_POOL)],
                "confidence": 3,
                "evidence_excerpt": "Zustaendig fuer",
                "rationale_short": "Kerngeschaeft der Industry.",
            }]}
            for index, unit in enumerate(payload["units"])
        ]
        return replace(response, payload=payload)


def export_argv(run_id: str = "run-test", review_id: str | None = None) -> list[str]:
    argv = ["review", "export", "--run-id", run_id]
    if review_id is not None:
        argv += ["--review-id", review_id]
    return argv


@pytest.fixture
def mapped_run(scripted, capsys):
    """Ein vollstaendig gemappter Lauf mit geschriebenem Artefaktsatz."""
    write_wide_baseline()
    scripted.monkeypatch.setattr(
        cli.provider.OpenRouterProvider, "create",
        classmethod(lambda cls, collector, settings: DistinctProvider()))
    assert cli.main(plan_argv()) in (0, 2)
    assert cli.main(execute_argv()) in (0, 2)
    capsys.readouterr()
    return scripted


def workbook_of(review_id: str = "review-run-test"):
    return load_workbook(review.review_path(review_id, review.WORKBOOK_FILENAME))


def manifest_of(review_id: str = "review-run-test") -> dict:
    return json.loads(
        review.review_path(review_id, review.EXPORT_MANIFEST_FILENAME)
        .read_text(encoding="utf-8"))


def rows_of(sheet) -> list[dict[str, str]]:
    header = [cell.value for cell in sheet[1]]
    return [dict(zip(header, [cell.value for cell in row]))
            for row in sheet.iter_rows(min_row=2)]


# ---------------------------------------------------------------------------
# review export
# ---------------------------------------------------------------------------

def test_export_writes_workbook_and_manifest(mapped_run, capsys):
    assert cli.main(export_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert "review-run-test" in out
    book = workbook_of()
    assert book.sheetnames == [review.SHEET_ASSIGNMENTS, review.SHEET_BOUNDARY,
                               review.SHEET_TAXONOMY, review.SHEET_INFO]
    assert rows_of(book[review.SHEET_ASSIGNMENTS]), "jede entschiedene Unit steht drin"
    assert [cell.value for cell in book[review.SHEET_ASSIGNMENTS][1]] == list(
        review.ASSIGNMENT_SHEET_COLUMNS)


def test_the_sheet_has_no_control_columns(mapped_run):
    """Wer reviewt, aendert Werte - er fuellt kein Formular aus."""
    assert cli.main(export_argv()) in (0, 2)
    header = [cell.value for cell in workbook_of()[review.SHEET_ASSIGNMENTS][1]]
    assert not [name for name in header
                if name.startswith(("review_", "model_")) or name == "row_id"]


def test_every_decided_unit_has_a_row(mapped_run):
    assert cli.main(export_argv()) in (0, 2)
    rows = rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])
    units = {row["unit_id"] for row in rows}
    coverage = workflow.mapping_path("run-test", "unit_coverage.csv").read_text(
        encoding="utf-8").splitlines()[1:]
    decided = {line.split(",")[2] for line in coverage}
    assert units == decided


def test_no_mapping_becomes_one_row_without_industry(scripted, capsys):
    scripted.install(decision="no_mapping")
    assert cli.main(plan_argv()) in (0, 2)
    assert cli.main(execute_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(export_argv()) in (0, 2)
    rows = rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])
    assert len(rows) == 3, "eine Zeile je Unit, keine Zuordnungszeile"
    assert all(not row["industry_id"] for row in rows)
    assert all(row["no_mapping_reason"] for row in rows)


def test_the_taxonomy_sheet_lists_the_snapshot(mapped_run):
    """Ohne Nachschlagewerk muesste Bene die zulaessigen IDs raten."""
    assert cli.main(export_argv()) in (0, 2)
    rows = rows_of(workbook_of()[review.SHEET_TAXONOMY])
    assert {row["industry_id"] for row in rows} == set(INDUSTRY_POOL)
    assert all(row["sector_id"] for row in rows)


def test_manifest_binds_run_snapshot_and_input(mapped_run):
    assert cli.main(export_argv()) in (0, 2)
    manifest = manifest_of()
    run_manifest = workflow.load_manifest("run-test")
    assert manifest["version"] == review.EXPORT_MANIFEST_VERSION
    assert manifest["run_id"] == "run-test"
    for field in ("snapshot_id", "taxonomy_content_hash", "input_manifest_hash",
                  "model"):
        assert manifest[field] == run_manifest[field]
    rows = rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])
    assert set(manifest["units"]) == {row["unit_id"] for row in rows}


def test_a_value_starting_with_equals_stays_text(mapped_run):
    """Excel darf einen Modellwert nicht als Formel auswerten."""
    target = workflow.mapping_path("run-test", "assignments_long.csv")
    text = target.read_text(encoding="utf-8")
    text = text.replace("Zustaendig fuer", "=Zustaendig fuer", 1)
    target.write_text(text, encoding="utf-8")
    assert cli.main(export_argv()) in (0, 2)
    sheet = workbook_of()[review.SHEET_ASSIGNMENTS]
    values = [cell.value for row in sheet.iter_rows(min_row=2) for cell in row]
    formulas = [cell for row in sheet.iter_rows(min_row=2) for cell in row
                if cell.data_type == "f"]
    assert not formulas, "keine Zelle wird als Formel gespeichert"
    assert any(str(value).startswith("=Zustaendig") for value in values)


def test_export_refuses_an_incomplete_run(scripted, capsys):
    scripted.install()
    assert cli.main(plan_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "REV_RUN_INCOMPLETE" in capsys.readouterr().out


def test_export_never_overwrites_a_review_revision(mapped_run, capsys):
    assert cli.main(export_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "REV_EXPORT_EXISTS" in capsys.readouterr().out


def test_export_without_a_run_id_refuses(sandbox, capsys):
    assert cli.main(["review", "export"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "CLI_RUN_ID_REQUIRED" in capsys.readouterr().out


def test_a_tampered_artifact_contract_blocks_the_export(mapped_run, capsys):
    target = workflow.mapping_path("run-test", "assignments_long.csv")
    lines = target.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("unit_id", "unit", 1)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "REV_ARTIFACT_CONTRACT" in capsys.readouterr().out


def test_units_without_a_decision_are_named_and_left_out(mapped_run, capsys):
    """Eine Unit ohne Urteil wird nicht stillschweigend zum Reviewposten."""
    target = workflow.mapping_path("run-test", "unit_coverage.csv")
    lines = target.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace(",mapped,", ",failed,", 1)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(export_argv()) == int(ExitCode.SUCCESS_WITH_WARNINGS)
    out = capsys.readouterr().out
    assert "REV_UNITS_WITHOUT_DECISION" in out
    manifest = manifest_of()
    assert len(manifest["incomplete_units"]) == 1
    units = {row["unit_id"] for row in rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])}
    assert manifest["incomplete_units"][0] not in units


def test_the_review_revision_lies_beside_the_run(mapped_run):
    assert cli.main(export_argv("run-test", "review-a")) in (0, 2)
    target = review.review_path("review-a", review.WORKBOOK_FILENAME)
    assert paths.is_within(paths.ARTIFACT_ROOT / "reviews", target)
    assert not workflow.mapping_path("run-test", review.WORKBOOK_FILENAME).exists()


# ---------------------------------------------------------------------------
# review import
# ---------------------------------------------------------------------------

def import_argv(run_id: str = "run-test", review_id: str | None = None,
                workbook: str | None = None) -> list[str]:
    argv = ["review", "import", "--run-id", run_id]
    if review_id is not None:
        argv += ["--review-id", review_id]
    if workbook is not None:
        argv += ["--workbook", workbook]
    return argv


@pytest.fixture
def exported(mapped_run, capsys):
    """Ein exportiertes Workbook, bereit zur Bearbeitung."""
    assert cli.main(export_argv()) in (0, 2)
    capsys.readouterr()
    return review.review_path("review-run-test", review.WORKBOOK_FILENAME)


def edit(path, row_index: int, **values) -> None:
    """Aendert Zellen einer Datenzeile des Blatts 'assignments'."""
    book = load_workbook(path)
    sheet = book[review.SHEET_ASSIGNMENTS]
    header = [cell.value for cell in sheet[1]]
    for name, value in values.items():
        cell = sheet.cell(row=row_index + 2, column=header.index(name) + 1,
                          value=value)
        cell.data_type = "s"
    book.save(path)


def delete_row(path, row_index: int = 0) -> None:
    """Loescht eine Datenzeile, wie es in Excel von Hand geschieht."""
    book = load_workbook(path)
    book[review.SHEET_ASSIGNMENTS].delete_rows(row_index + 2)
    book.save(path)


def add_row(path, **values) -> None:
    """Haengt eine neue Zeile an."""
    book = load_workbook(path)
    sheet = book[review.SHEET_ASSIGNMENTS]
    header = [cell.value for cell in sheet[1]]
    line = sheet.max_row + 1
    for name, value in values.items():
        cell = sheet.cell(row=line, column=header.index(name) + 1, value=value)
        cell.data_type = "s"
    book.save(path)


def cell_of(path, row_index: int, name: str) -> str:
    sheet = load_workbook(path)[review.SHEET_ASSIGNMENTS]
    header = [cell.value for cell in sheet[1]]
    return sheet.cell(row=row_index + 2, column=header.index(name) + 1).value


def reviewed_rows(review_id: str = "review-run-test") -> list[dict[str, str]]:
    text = review.review_path(review_id, review.REVIEWED_ASSIGNMENTS_FILENAME
                              ).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def import_manifest(review_id: str = "review-run-test") -> dict:
    return json.loads(
        review.review_path(review_id, review.IMPORT_MANIFEST_FILENAME)
        .read_text(encoding="utf-8"))


def test_an_untouched_workbook_imports_unchanged(exported, capsys):
    assert cli.main(import_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert "reviewed_assignments.csv" in out
    rows = reviewed_rows()
    assert rows, "jede Zuordnung steht in der reviewten CSV"
    assert all(row["assignment_source"] == "model" for row in rows)
    assert all(row["call_id"] for row in rows), "Callprovenienz bleibt erhalten"
    manifest = import_manifest()
    assert manifest["review_status"] == "reviewed"
    assert manifest["manual_assignment_count"] == 0
    assert (manifest["changed_assignments"], manifest["added_assignments"],
            manifest["removed_assignments"]) == (0, 0, 0)


def test_a_changed_industry_becomes_manual(exported, capsys):
    """Eine geaenderte Industry braucht keinen Zusatzeintrag."""
    edit(exported, 0, industry_id="oil-gas-midstream")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    changed = [row for row in reviewed_rows()
               if row["industry_id"] == "oil-gas-midstream"]
    assert len(changed) == 1
    assert changed[0]["assignment_source"] == "manual"
    assert not changed[0]["call_id"], "eine manuelle Zeile erbt keine Callprovenienz"
    assert changed[0]["sector_id"], "Sektor stammt aus dem Snapshot"


def test_a_changed_confidence_becomes_manual(exported, capsys):
    unit_id = cell_of(exported, 0, "unit_id")
    edit(exported, 0, confidence="5")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    row = [entry for entry in reviewed_rows() if entry["unit_id"] == unit_id][0]
    assert row["confidence"] == "5"
    assert row["assignment_source"] == "manual"
    assert import_manifest()["changed_assignments"] == 1


def test_a_changed_rationale_becomes_manual(exported, capsys):
    edit(exported, 0, rationale_short="Reviewbegruendung")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert import_manifest()["changed_assignments"] == 1


def test_a_deleted_row_removes_the_assignment(exported, capsys):
    unit_id = cell_of(exported, 0, "unit_id")
    delete_row(exported, 0)
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert not [row for row in reviewed_rows() if row["unit_id"] == unit_id]
    manifest = import_manifest()
    assert manifest["removed_assignments"] == 1
    assert unit_id in manifest["no_mapping_units"], (
        "die Unit bleibt im Ergebnis, nur ohne Zuordnung")


def test_an_empty_industry_means_no_mapping(exported, capsys):
    unit_id = cell_of(exported, 0, "unit_id")
    edit(exported, 0, industry_id="", no_mapping_reason="keine direkte Zustaendigkeit")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert not [row for row in reviewed_rows() if row["unit_id"] == unit_id]
    coverage = review.review_path(
        "review-run-test", review.REVIEWED_COVERAGE_FILENAME).read_text(encoding="utf-8")
    assert "keine direkte Zustaendigkeit" in coverage


def test_a_removal_without_a_reason_is_accepted(exported, capsys):
    """Kein Pflichtfeld: der Reviewgrund steht im Kopf des Reviewers."""
    delete_row(exported, 0)
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert review.reviewed_path_exists("review-run-test")


def test_a_new_assignment_is_added_as_manual(exported, capsys):
    unit_id = cell_of(exported, 0, "unit_id")
    add_row(exported, unit_id=unit_id, industry_id="gold", confidence="4",
            evidence_excerpt="Bergbauaufsicht", rationale_short="Reviewergaenzung")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    added = [row for row in reviewed_rows()
             if row["unit_id"] == unit_id and row["industry_id"] == "gold"]
    assert len(added) == 1
    assert added[0]["assignment_source"] == "manual"
    assert import_manifest()["added_assignments"] == 1


def test_a_new_assignment_without_evidence_is_accepted(exported, capsys):
    """Der Mensch belegt nicht gegenueber dem Programm."""
    unit_id = cell_of(exported, 0, "unit_id")
    add_row(exported, unit_id=unit_id, industry_id="gold")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    added = [row for row in reviewed_rows()
             if row["unit_id"] == unit_id and row["industry_id"] == "gold"]
    assert len(added) == 1
    assert added[0]["confidence"] == ""


def test_reordered_rows_stay_model(exported, capsys):
    """Umsortieren ist keine Aenderung; der Abgleich laeuft ueber die Werte."""
    book = load_workbook(exported)
    sheet = book[review.SHEET_ASSIGNMENTS]
    first = [cell.value for cell in sheet[2]]
    last = [cell.value for cell in sheet[sheet.max_row]]
    for index, value in enumerate(last, start=1):
        sheet.cell(row=2, column=index, value=value).data_type = "s"
    for index, value in enumerate(first, start=1):
        sheet.cell(row=sheet.max_row, column=index, value=value).data_type = "s"
    book.save(exported)
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert import_manifest()["manual_assignment_count"] == 0


def test_more_assignments_than_the_limit_only_warn(exported, capsys):
    """Die Anzahlgrenze diszipliniert das Modell, nicht den Reviewer."""
    unit_id = cell_of(exported, 0, "unit_id")
    for industry in ("gold", "oil-gas-midstream", "farm-products"):
        if industry != cell_of(exported, 0, "industry_id"):
            add_row(exported, unit_id=unit_id, industry_id=industry)
    assert cli.main(import_argv()) == int(ExitCode.SUCCESS_WITH_WARNINGS)
    assert "REV_ASSIGNMENTS_ABOVE_LIMIT" in capsys.readouterr().out
    assert review.reviewed_path_exists("review-run-test")


def test_an_unknown_industry_is_rejected(exported, capsys):
    edit(exported, 0, industry_id="gibt-es-nicht")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_UNKNOWN_INDUSTRY" in capsys.readouterr().out


def test_an_unknown_unit_is_rejected(exported, capsys):
    add_row(exported, unit_id="house--gibt-es-nicht", industry_id="gold")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_UNIT_UNKNOWN" in capsys.readouterr().out


def test_a_row_without_a_unit_is_rejected(exported, capsys):
    add_row(exported, industry_id="gold")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_UNIT_MISSING" in capsys.readouterr().out


def test_the_same_industry_twice_is_rejected(exported, capsys):
    unit_id = cell_of(exported, 0, "unit_id")
    industry = cell_of(exported, 0, "industry_id")
    add_row(exported, unit_id=unit_id, industry_id=industry)
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_INDUSTRY_DUPLICATE" in capsys.readouterr().out


def test_an_unreadable_confidence_is_rejected(exported, capsys):
    edit(exported, 0, confidence="hoch")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_CONFIDENCE_INVALID" in capsys.readouterr().out


def test_a_formula_cell_is_rejected(exported, capsys):
    book = load_workbook(exported)
    book[review.SHEET_ASSIGNMENTS].cell(row=2, column=len(
        review.ASSIGNMENT_SHEET_COLUMNS), value="=1+1")
    book.save(exported)
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_FORMULA_CELL" in capsys.readouterr().out


def test_a_changed_header_is_rejected(exported, capsys):
    book = load_workbook(exported)
    book[review.SHEET_ASSIGNMENTS].cell(row=1, column=6, value="branche")
    book.save(exported)
    assert cli.main(import_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "REV_WORKBOOK_CONTRACT" in capsys.readouterr().out


def test_a_stale_snapshot_binding_blocks_the_import(exported, capsys):
    manifest_target = review.review_path("review-run-test",
                                         review.EXPORT_MANIFEST_FILENAME)
    payload = json.loads(manifest_target.read_text(encoding="utf-8"))
    payload["taxonomy_content_hash"] = "0" * 64
    manifest_target.write_text(json.dumps(payload), encoding="utf-8")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_STALE_BINDING" in capsys.readouterr().out


def test_a_rejected_import_writes_nothing(exported, capsys):
    edit(exported, 0, industry_id="gibt-es-nicht")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    capsys.readouterr()
    assert not review.review_path(
        "review-run-test", review.REVIEWED_ASSIGNMENTS_FILENAME).exists()
    assert not review.reviewed_path_exists("review-run-test")


def test_an_imported_revision_is_never_overwritten(exported, capsys):
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(import_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "REV_IMPORT_EXISTS" in capsys.readouterr().out


def test_the_workbook_may_come_from_another_path(exported, capsys, tmp_path):
    copy = tmp_path / "bearbeitet.xlsx"
    shutil.copy(exported, copy)
    exported.unlink()
    assert cli.main(import_argv(workbook=str(copy))) in (0, 2)
    assert "reviewed_assignments.csv" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Boundary-Konflikte
# ---------------------------------------------------------------------------

@pytest.fixture
def conflicted(scripted, capsys):
    """Ein Lauf, in dem alle Units dieselbe Industry tragen."""
    write_wide_baseline()
    scripted.install()
    assert cli.main(plan_argv()) in (0, 2)
    assert cli.main(execute_argv()) in (0, 2)
    assert cli.main(export_argv()) in (0, 2)
    capsys.readouterr()
    return review.review_path("review-run-test", review.WORKBOOK_FILENAME)


def test_the_export_lists_the_conflict(conflicted):
    rows = rows_of(load_workbook(conflicted)[review.SHEET_BOUNDARY])
    assert len(rows) == 1
    assert rows[0]["industry_id"] == "farm-products"
    assert rows[0]["unit_count"] == "3"


def test_the_conflict_sheet_has_no_input_column(conflicted):
    header = [cell.value for cell in load_workbook(conflicted)[review.SHEET_BOUNDARY][1]]
    assert header == list(review.BOUNDARY_SHEET_COLUMNS)
    assert not [name for name in header if name.startswith("review_")]


def test_an_untouched_conflict_is_accepted(conflicted, capsys):
    """Ueberlappende Zustaendigkeiten sind der Regelfall, keine Rueckfrage."""
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert len(reviewed_rows()) == 3


def test_a_conflict_is_resolved_by_deleting_rows(conflicted, capsys):
    """Wer die Ueberlappung nicht will, loescht die Zeilen."""
    for _ in range(3):
        delete_row(conflicted, 0)
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert reviewed_rows() == []
    coverage = review.review_path(
        "review-run-test", review.REVIEWED_COVERAGE_FILENAME).read_text(encoding="utf-8")
    assert coverage.count("no_mapping") >= 3
