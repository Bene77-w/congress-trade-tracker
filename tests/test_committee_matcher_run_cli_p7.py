"""Tests der Run-CLI des Committee Matchers V2 (P7c).

Netzwerkfrei: der Providerrand ist in jedem Test entweder verboten oder durch
``FakeProvider`` ersetzt. Corpus, Artefaktwurzel und Settings liegen unter
``tmp_path``; das Repository wird nur lesend fuer Prompts benutzt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import cli, input_corpus, paths, taxonomy_snapshot, workflow  # noqa: E402
from committee_matcher.settings_view import load_settings  # noqa: E402
from committee_matcher.status import ExitCode  # noqa: E402

from tests.test_committee_matcher_input_units import build_corpus, make_markdown  # noqa: E402

#: Zwei Industries genuegen; geprueft wird der Ablauf, nicht die Taxonomie.
INDUSTRIES = [
    {
        "industry_id": "agricultural-inputs",
        "industry_name": "Agricultural Inputs",
        "sector_id": "basic-materials",
        "sector_name": "Basic Materials",
        "description": "Duenger, Saatgut und Pflanzenschutz.",
    },
    {
        "industry_id": "farm-products",
        "industry_name": "Farm Products",
        "sector_id": "consumer-defensive",
        "sector_name": "Consumer Defensive",
        "description": "Erzeugung und Vermarktung von Agrarprodukten.",
    },
]


def settings_module(**overrides):
    """Vollstaendige Settings eines Laufs; Werte wie nach Gate B."""
    values = {
        "COMMITTEE_MATCHER_MAX_FILE_BYTES": 512_000,
        "COMMITTEE_MATCHER_MAX_FRONTMATTER_BYTES": 32_000,
        "COMMITTEE_MATCHER_MIN_EVIDENCE_CHARS": 60,
        "COMMITTEE_MATCHER_YFINANCE_REQUEST_PAUSE_S": 1.5,
        "COMMITTEE_MATCHER_YFINANCE_MAX_RETRIES": 3,
        "COMMITTEE_MATCHER_YFINANCE_CHECKPOINT_EVERY": 10,
        "COMMITTEE_MATCHER_BATCH_SIZE": 3,
        "COMMITTEE_MATCHER_WORKFLOW_RETRIES": 2,
        "COMMITTEE_MATCHER_MAX_REPAIR_ATTEMPTS": 1,
        "COMMITTEE_MATCHER_CHARS_PER_TOKEN": 3.0,
        "COMMITTEE_MATCHER_COST_GUARD": True,
        "COMMITTEE_MATCHER_MODEL": "anbieter/modell",
        "COMMITTEE_MATCHER_QUANTIZATIONS": ("fp8", "bf16"),
        "COMMITTEE_MATCHER_DATA_COLLECTION": "deny",
        "COMMITTEE_MATCHER_ZDR": True,
        "COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK": {"prompt": 3.0, "completion": 15.0},
        "COMMITTEE_MATCHER_ENDPOINT_CONTEXT_TOKENS": 200_000,
        "COMMITTEE_MATCHER_MAX_OUTPUT_TOKENS": 8_000,
        "COMMITTEE_MATCHER_REASONING_RESERVE_TOKENS": 4_000,
        "COMMITTEE_MATCHER_REQUEST_TIMEOUT_S": 120.0,
        "COMMITTEE_MATCHER_MAX_RUN_COST_USD": 5.0,
        "COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD": 5.0,
        "COMMITTEE_MATCHER_MAX_QUALIFICATION_COST_USD": 5.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def write_baseline(*, run_id: str = "run-taxonomy") -> str:
    """Legt eine freigegebene Taxonomie an und liefert ihren Content-Hash."""
    digest = taxonomy_snapshot.hash_records(INDUSTRIES)
    target = taxonomy_snapshot.baseline_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "content_version": taxonomy_snapshot.TAXONOMY_CONTENT_VERSION,
                "approved_run_id": run_id,
                "taxonomy_content_hash": digest,
                "yfinance_version": "0.2.99",
                "industries": INDUSTRIES,
            },
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return digest


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Corpus, Artefaktwurzel, Settings und ein verbotener Providerrand."""
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path / "artifacts")
    root = build_corpus(tmp_path, {
        "house_testing.md": make_markdown(
            sections=("Subcommittee on Livestock", "Subcommittee on Nutrition")),
    })
    original = input_corpus.load_documents
    monkeypatch.setattr(
        cli.input_corpus, "load_documents",
        lambda collector, offline, input_root=root: original(collector, offline, root),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(settings_module()))

    def forbidden(*args, **kwargs):
        raise AssertionError("run plan darf keinen Provider aufbauen")

    monkeypatch.setattr(cli.provider.OpenRouterProvider, "create", forbidden)
    write_baseline()
    return SimpleNamespace(root=root, tmp_path=tmp_path, monkeypatch=monkeypatch)


def plan_argv(run_id: str = "run-test", *extra: str) -> list[str]:
    return ["run", "plan", "--run-id", run_id, "--committee",
            "Committee on Testing", *extra]


# ---------------------------------------------------------------------------
# run plan
# ---------------------------------------------------------------------------

def test_plan_writes_the_manifest_and_stays_provider_free(sandbox, capsys):
    assert cli.main(plan_argv()) in (int(ExitCode.SUCCESS), int(ExitCode.SUCCESS_WITH_WARNINGS))
    manifest = workflow.load_manifest("run-test")
    assert manifest is not None
    assert manifest["unit_count"] == 3
    assert manifest["selection"]["committee"] == "Committee on Testing"
    out = capsys.readouterr().out
    assert "Kosten: 0.00 USD (providerfreie Phase" in out
    assert "[START] workflow | run=run-test | action=plan" in out


def test_plan_reports_scope_and_worst_case_cost(sandbox, capsys):
    cli.main(plan_argv())
    out = capsys.readouterr().out
    assert "Umfang:   3 von 3 Units" in out
    assert "Worst Case" in out
    assert "[OK] cost | run=run-test" in out


def test_plan_without_a_target_refuses_to_run(sandbox, capsys):
    assert cli.main(["run", "plan", "--run-id", "run-test"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "CLI_RUN_TARGET_REQUIRED" in capsys.readouterr().out
    assert workflow.load_manifest("run-test") is None


def test_plan_without_a_run_id_refuses_to_run(sandbox, capsys):
    assert cli.main(["run", "plan", "--committee", "Committee on Testing"]) == int(
        ExitCode.PREFLIGHT_BLOCKED)
    assert "CLI_RUN_ID_REQUIRED" in capsys.readouterr().out


def test_unknown_unit_id_blocks_instead_of_shrinking_the_scope(sandbox, capsys):
    argv = ["run", "plan", "--run-id", "run-test", "--unit-id", "gibt-es-nicht"]
    assert cli.main(argv) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "CLI_RUN_TARGET_UNKNOWN" in out
    assert workflow.load_manifest("run-test") is None


def test_target_without_any_match_blocks(sandbox, capsys):
    argv = ["run", "plan", "--run-id", "run-test", "--committee", "Committee on Nothing"]
    assert cli.main(argv) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "CLI_RUN_TARGET_EMPTY" in capsys.readouterr().out


def test_missing_taxonomy_approval_waits_instead_of_mapping(sandbox, capsys):
    taxonomy_snapshot.baseline_path().unlink()
    assert cli.main(plan_argv()) == int(ExitCode.AWAITING_TAXONOMY_APPROVAL)
    out = capsys.readouterr().out
    assert "TAX_BASELINE_MISSING" in out
    assert "[WAIT] taxonomy | run=run-test | awaiting_taxonomy_approval" in out


def test_plan_beyond_the_budget_is_refused_before_any_call(sandbox, capsys):
    sandbox.monkeypatch.setattr(
        cli, "load_settings",
        lambda: load_settings(settings_module(COMMITTEE_MATCHER_MAX_RUN_COST_USD=0.000001)),
    )
    assert cli.main(plan_argv()) == int(ExitCode.COST_BLOCKED)
    out = capsys.readouterr().out
    assert "COST_BUDGET_EXCEEDED" in out
    assert workflow.load_manifest("run-test") is None


def test_replanning_the_same_conditions_is_allowed(sandbox, capsys):
    assert cli.main(plan_argv()) in (int(ExitCode.SUCCESS), int(ExitCode.SUCCESS_WITH_WARNINGS))
    capsys.readouterr()
    assert cli.main(plan_argv()) in (int(ExitCode.SUCCESS), int(ExitCode.SUCCESS_WITH_WARNINGS))
    assert "RUN_MANIFEST_DRIFT" not in capsys.readouterr().out


def test_replanning_with_another_scope_needs_a_new_revision(sandbox, capsys):
    cli.main(plan_argv())
    capsys.readouterr()
    units = [unit for unit in _units(sandbox)]
    argv = ["run", "plan", "--run-id", "run-test", "--unit-id", units[0].unit_id]
    assert cli.main(argv) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "RUN_MANIFEST_DRIFT" in capsys.readouterr().out


def test_finished_revision_is_never_overwritten(sandbox, capsys):
    for name in ("assignments_long.csv", "unit_coverage.csv", "boundary_conflicts.csv",
                 "roster_coverage.csv", "cost_report.json"):
        workflow.mapping_path("run-test", name, create_parents=True).write_text(
            "", encoding="utf-8")
    workflow.mapping_path("run-test", "artifacts_manifest.json", create_parents=True).write_text(
        json.dumps({
            "version": "artifact-index-v1",
            "run_id": "run-test",
            "files": [{"name": name} for name in (
                "assignments_long.csv", "unit_coverage.csv", "boundary_conflicts.csv",
                "roster_coverage.csv", "cost_report.json")],
        }),
        encoding="utf-8",
    )
    assert cli.main(plan_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "RUN_REVISION_COMPLETE" in capsys.readouterr().out


def _units(sandbox):
    from committee_matcher import units as unit_module
    from committee_matcher.errors import ErrorCollector
    collector = ErrorCollector()
    result = input_corpus.load_documents(
        collector, load_settings(settings_module()).offline, sandbox.root)
    return unit_module.build_units(collector, result.documents)
