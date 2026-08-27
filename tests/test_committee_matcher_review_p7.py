"""Tests des Review-Roundtrips des Committee Matchers V2 (P7d).

Netzwerkfrei: der Lauf wird mit dem Skriptprovider aus den P7c-Tests erzeugt,
der Export liest ausschliesslich die geschriebenen Artefakte.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import cli, paths, review, workflow  # noqa: E402
from committee_matcher.status import ExitCode  # noqa: E402

from tests.test_committee_matcher_run_cli_p7 import (  # noqa: E402
    execute_argv, plan_argv, sandbox, scripted,  # noqa: F401
)


def export_argv(run_id: str = "run-test", review_id: str | None = None) -> list[str]:
    argv = ["review", "export", "--run-id", run_id]
    if review_id is not None:
        argv += ["--review-id", review_id]
    return argv


@pytest.fixture
def mapped_run(scripted, capsys):
    """Ein vollstaendig gemappter Lauf mit geschriebenem Artefaktsatz."""
    scripted.install()
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
