"""Tests des Review-Roundtrips des Committee Matchers V2 (P7d).

Netzwerkfrei: der Lauf wird mit dem Skriptprovider aus den P7c-Tests erzeugt,
der Export liest ausschliesslich die geschriebenen Artefakte.
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
                               review.SHEET_INFO]
    rows = rows_of(book[review.SHEET_ASSIGNMENTS])
    assert rows, "jede entschiedene Unit steht im Workbook"
    assert [cell.value for cell in book[review.SHEET_ASSIGNMENTS][1]] == list(
        review.ASSIGNMENT_SHEET_COLUMNS)
    assert all(row["review_action"] in (None, "") for row in rows)


def test_every_decided_unit_has_a_row(mapped_run):
    assert cli.main(export_argv()) in (0, 2)
    rows = rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])
    units = {row["model_unit_id"] for row in rows}
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
    assert all(row["model_decision"] == "no_mapping" for row in rows)
    assert all(not row["model_industry_id"] for row in rows)
    assert all(row["model_no_mapping_reason"] for row in rows)


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
    assert set(manifest["rows"]) == {row["row_id"] for row in rows}


def test_row_hash_covers_every_model_field(mapped_run):
    assert cli.main(export_argv()) in (0, 2)
    manifest = manifest_of()
    rows = rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])
    row = rows[0]
    assert review.row_hash(row) == manifest["rows"][row["row_id"]]
    for column in review.MODEL_COLUMNS:
        tampered = {**row, column: (row[column] or "") + "x"}
        assert review.row_hash(tampered) != manifest["rows"][row["row_id"]], column


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
    units = {row["model_unit_id"]
             for row in rows_of(workbook_of()[review.SHEET_ASSIGNMENTS])}
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


def add_row(path, **values) -> None:
    """Haengt eine neue Zeile ohne row_id an."""
    book = load_workbook(path)
    sheet = book[review.SHEET_ASSIGNMENTS]
    header = [cell.value for cell in sheet[1]]
    line = sheet.max_row + 1
    for name, value in values.items():
        cell = sheet.cell(row=line, column=header.index(name) + 1, value=value)
        cell.data_type = "s"
    book.save(path)


def reviewed_rows(review_id: str = "review-run-test") -> list[dict[str, str]]:
    import csv
    text = review.review_path(review_id, review.REVIEWED_ASSIGNMENTS_FILENAME
                              ).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def test_an_untouched_workbook_imports_unchanged(exported, capsys):
    assert cli.main(import_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert "reviewed_assignments.csv" in out
    rows = reviewed_rows()
    assert rows, "jede Zuordnung steht in der reviewten CSV"
    assert all(row["assignment_source"] == "model" for row in rows)
    assert all(row["review_action"] == "keep" for row in rows)
    manifest = json.loads(
        review.review_path("review-run-test", review.IMPORT_MANIFEST_FILENAME)
        .read_text(encoding="utf-8"))
    assert manifest["review_status"] == "reviewed"
    assert manifest["manual_assignment_count"] == 0


def test_a_changed_industry_needs_evidence_and_rationale(exported, capsys):
    edit(exported, 0, review_industry_id="gold")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_EVIDENCE_REQUIRED" in capsys.readouterr().out
    assert not review.reviewed_path_exists("review-run-test")


def test_a_changed_industry_with_evidence_becomes_manual(exported, capsys, sandbox):
    # Eine Industry, die im Lauf noch keine Unit traegt: so bleibt der Test
    # von der Boundary-Pruefung unabhaengig.
    industry = "oil-gas-midstream"
    edit(exported, 0, review_industry_id=industry,
         review_evidence_excerpt="Zustaendig fuer Bergbau",
         review_rationale_short="Reviewentscheidung")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    rows = reviewed_rows()
    changed = [row for row in rows if row["industry_id"] == industry]
    assert len(changed) == 1
    assert changed[0]["assignment_source"] == "manual"
    assert changed[0]["review_action"] == "edit"
    assert not changed[0]["call_id"], "eine manuelle Zeile erbt keine Callprovenienz"
    assert changed[0]["sector_id"], "Sektor stammt aus dem Snapshot"


def test_a_changed_confidence_needs_a_rationale(exported, capsys):
    edit(exported, 0, review_confidence="5")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_RATIONALE_REQUIRED" in capsys.readouterr().out


def test_a_deleted_assignment_needs_a_note(exported, capsys):
    edit(exported, 0, review_action="delete")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    out = capsys.readouterr().out
    assert "REV_DELETE_NOTE_REQUIRED" in out or "REV_NO_MAPPING_REASON_REQUIRED" in out


def test_switching_to_no_mapping_needs_a_reason_and_empty_fields(exported, capsys):
    edit(exported, 0, review_decision="no_mapping", review_industry_id="gold")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    out = capsys.readouterr().out
    assert "REV_NO_MAPPING_NOT_EMPTY" in out
    assert "REV_NO_MAPPING_REASON_REQUIRED" in out


def test_switching_to_no_mapping_is_accepted(exported, capsys):
    edit(exported, 0, review_decision="no_mapping",
         review_no_mapping_reason="keine direkte Zustaendigkeit")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    unit_id = load_workbook(exported)[review.SHEET_ASSIGNMENTS].cell(row=2, column=2).value
    rows = [row for row in reviewed_rows() if row["unit_id"] == unit_id]
    assert rows == [], "eine no_mapping-Unit traegt keine Zuordnung"
    coverage = review.review_path(
        "review-run-test", review.REVIEWED_COVERAGE_FILENAME).read_text(encoding="utf-8")
    assert "no_mapping" in coverage


def test_a_new_assignment_is_added_as_manual(exported, capsys):
    unit_id = load_workbook(exported)[review.SHEET_ASSIGNMENTS].cell(row=2, column=2).value
    add_row(exported, model_unit_id=unit_id, review_industry_id="gold",
            review_confidence="4", review_evidence_excerpt="Bergbauaufsicht",
            review_rationale_short="Reviewergaenzung")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    added = [row for row in reviewed_rows()
             if row["unit_id"] == unit_id and row["industry_id"] == "gold"]
    assert len(added) == 1
    assert added[0]["assignment_source"] == "manual"
    assert added[0]["review_action"] == "add"


def test_a_new_row_with_model_values_is_rejected(exported, capsys):
    unit_id = load_workbook(exported)[review.SHEET_ASSIGNMENTS].cell(row=2, column=2).value
    add_row(exported, model_unit_id=unit_id, model_industry_id="gold",
            review_industry_id="gold", review_confidence="4",
            review_evidence_excerpt="Beleg", review_rationale_short="Grund")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_NEW_ROW_NOT_EMPTY" in capsys.readouterr().out


def test_an_unknown_industry_is_rejected(exported, capsys):
    edit(exported, 0, review_industry_id="gibt-es-nicht",
         review_evidence_excerpt="Beleg", review_rationale_short="Grund")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_UNKNOWN_INDUSTRY" in capsys.readouterr().out


def test_a_changed_model_field_is_rejected(exported, capsys):
    edit(exported, 0, model_rationale_short="von Hand geaendert")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_MODEL_FIELD_CHANGED" in capsys.readouterr().out


def test_a_missing_row_is_rejected(exported, capsys):
    book = load_workbook(exported)
    book[review.SHEET_ASSIGNMENTS].delete_rows(2)
    book.save(exported)
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_ROW_MISSING" in capsys.readouterr().out


def test_a_duplicated_row_id_is_rejected(exported, capsys):
    book = load_workbook(exported)
    sheet = book[review.SHEET_ASSIGNMENTS]
    header = [cell.value for cell in sheet[1]]
    line = sheet.max_row + 1
    for column in range(1, len(header) + 1):
        cell = sheet.cell(row=line, column=column, value=sheet.cell(row=2, column=column).value)
        cell.data_type = "s"
    book.save(exported)
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_ROW_DUPLICATE" in capsys.readouterr().out


def test_a_formula_cell_is_rejected(exported, capsys):
    book = load_workbook(exported)
    book[review.SHEET_ASSIGNMENTS].cell(row=2, column=len(
        review.ASSIGNMENT_SHEET_COLUMNS), value="=1+1")
    book.save(exported)
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_FORMULA_CELL" in capsys.readouterr().out


def test_a_stale_snapshot_binding_blocks_the_import(exported, capsys):
    manifest_target = review.review_path("review-run-test",
                                         review.EXPORT_MANIFEST_FILENAME)
    payload = json.loads(manifest_target.read_text(encoding="utf-8"))
    payload["taxonomy_content_hash"] = "0" * 64
    manifest_target.write_text(json.dumps(payload), encoding="utf-8")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_STALE_BINDING" in capsys.readouterr().out


def test_a_rejected_import_writes_nothing(exported, capsys):
    edit(exported, 0, model_rationale_short="von Hand geaendert")
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
    import shutil
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


def boundary(path, row_index: int = 0, **values) -> None:
    book = load_workbook(path)
    sheet = book[review.SHEET_BOUNDARY]
    header = [cell.value for cell in sheet[1]]
    for name, value in values.items():
        cell = sheet.cell(row=row_index + 2, column=header.index(name) + 1,
                          value=value)
        cell.data_type = "s"
    book.save(path)


def test_the_export_lists_the_conflict(conflicted):
    rows = rows_of(load_workbook(conflicted)[review.SHEET_BOUNDARY])
    assert len(rows) == 1
    assert rows[0]["industry_id"] == "farm-products"
    assert rows[0]["unit_count"] == "3"
    assert not rows[0]["review_action"]


def test_an_open_conflict_blocks_the_import(conflicted, capsys):
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_BOUNDARY_OPEN" in capsys.readouterr().out


def test_legitimate_overlap_needs_a_note(conflicted, capsys):
    boundary(conflicted, review_action="legitimate_overlap")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_BOUNDARY_NOTE_REQUIRED" in capsys.readouterr().out


def test_legitimate_overlap_with_a_note_is_accepted(conflicted, capsys):
    boundary(conflicted, review_action="legitimate_overlap",
             review_note="Zustaendigkeit besteht tatsaechlich mehrfach.")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert len(reviewed_rows()) == 3


def test_keep_a_is_ambiguous_with_more_than_two_units(conflicted, capsys):
    boundary(conflicted, review_action="keep_a")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_BOUNDARY_ACTION_AMBIGUOUS" in capsys.readouterr().out


def test_remove_both_must_match_the_edited_slots(conflicted, capsys):
    boundary(conflicted, review_action="remove_both")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_BOUNDARY_ACTION_MISMATCH" in capsys.readouterr().out


def test_an_unknown_conflict_action_is_rejected(conflicted, capsys):
    boundary(conflicted, review_action="vielleicht")
    assert cli.main(import_argv()) == int(ExitCode.REVIEW_IMPORT_REJECTED)
    assert "REV_BOUNDARY_ACTION_INVALID" in capsys.readouterr().out


def test_a_conflict_action_documents_the_deletion(conflicted, capsys):
    """Eine durch die Konfliktaktion gedeckte Loeschung braucht keine Notiz."""
    for index in range(3):
        edit(conflicted, index, review_action="delete",
             review_decision="no_mapping",
             review_no_mapping_reason="Konflikt zugunsten keiner Unit geloest")
    boundary(conflicted, review_action="remove_both")
    assert cli.main(import_argv()) in (0, 2)
    capsys.readouterr()
    assert reviewed_rows() == []
    coverage = review.review_path(
        "review-run-test", review.REVIEWED_COVERAGE_FILENAME).read_text(encoding="utf-8")
    assert coverage.count("no_mapping") >= 3
