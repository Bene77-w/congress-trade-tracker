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


# ---------------------------------------------------------------------------
# run execute
# ---------------------------------------------------------------------------

def _assignment(industry_id: str = "farm-products") -> dict:
    return {
        "industry_id": industry_id,
        "confidence": 3,
        "evidence_excerpt": "Zustaendig fuer",
        "rationale_short": "Kerngeschaeft der Industry.",
    }


def unit_result(unit_id: str, *, decision: str = "mapped",
                assignments=None, no_mapping_reason=None) -> dict:
    return {
        "unit_id": unit_id,
        "decision": decision,
        "no_mapping_reason": no_mapping_reason,
        "broad_scope_exception": False,
        "broad_scope_reason": None,
        "assignments": [] if decision == "no_mapping" else (
            assignments if assignments is not None else [_assignment()]),
    }


def batch_payload(request, *, decision: str = "mapped") -> dict:
    """Baut die Antwort zu einem Request aus den Unit-IDs seines Batches."""
    batch_id = request.call_id.split("-repair-")[0]
    if "-" in batch_id and batch_id.startswith("retry"):
        batch_id = batch_id.split("-", 1)[1]
    reason = "keine direkte Zustaendigkeit" if decision == "no_mapping" else None
    return {
        "requested_batch_id": batch_id,
        "units": [unit_result(unit_id, decision=decision, no_mapping_reason=reason)
                  for unit_id in _requested_units(request)],
    }


def _requested_units(request) -> list[str]:
    """Liest die Unit-IDs aus dem gerenderten Nutzdatenblock."""
    found = []
    for line in request.user_content.splitlines():
        if line.startswith("## unit_id:"):
            found.append(line.split(":", 1)[1].strip())
    return found


class ScriptedProvider:
    """Antwortet auf jeden Request mit einer gueltigen Batchantwort."""

    def __init__(self, *, cost_usd: float | None = 0.000012,
                 provider_name: str | None = "fake", errors=None,
                 decision: str = "mapped"):
        self.calls = []
        self._cost = cost_usd
        self._provider = provider_name
        self._errors = list(errors or [])
        self._decision = decision

    def complete(self, request):
        self.calls.append(request)
        if self._errors:
            error = self._errors.pop(0)
            if error is not None:
                raise error
        payload = batch_payload(request, decision=self._decision)
        return cli.provider.StructuredResponse(
            call_id=request.call_id,
            payload=payload,
            model="anbieter/modell",
            provider=self._provider,
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=self._cost,
            finish_reason="stop",
            generation_id=f"gen-{request.call_id}",
        )


@pytest.fixture
def scripted(sandbox):
    """Ersetzt den Providerrand durch ein Skript und plant den Lauf."""
    def install(**kwargs):
        adapter = ScriptedProvider(**kwargs)
        sandbox.monkeypatch.setattr(
            cli.provider.OpenRouterProvider, "create",
            classmethod(lambda cls, collector, settings: adapter))
        return adapter
    sandbox.install = install
    return sandbox


def execute_argv(run_id: str = "run-test") -> list[str]:
    return ["run", "execute", "--run-id", run_id, "--committee", "Committee on Testing"]


def test_execute_maps_every_unit_and_writes_the_artifact_set(scripted, capsys):
    adapter = scripted.install()
    assert cli.main(plan_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(execute_argv()) in (0, 2)
    out = capsys.readouterr().out

    assert len(adapter.calls) == 1
    state = workflow.load_state("run-test")
    assert state.state.value == "awaiting_manual_review"
    assert {record.status.value for record in state.units.values()} == {"mapped"}
    for record in state.units.values():
        assert record.call_id and record.provider == "fake"
        assert record.generation_id.startswith("gen-")
        assert record.from_repair is False
    for name in ("assignments_long.csv", "unit_coverage.csv", "cost_report.json",
                 "artifacts_manifest.json"):
        assert workflow.mapping_path("run-test", name).exists(), name
    assert "[OK] mapping | unit=" in out
    assert "Kosten:   0.000012 USD gebucht" in out


def test_execute_without_a_plan_refuses_to_call(scripted, capsys):
    adapter = scripted.install()
    assert cli.main(execute_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "RUN_NOT_PLANNED" in capsys.readouterr().out
    assert adapter.calls == []


def test_audit_trail_and_cost_report_cover_every_call(scripted, capsys):
    scripted.install()
    cli.main(plan_argv())
    cli.main(execute_argv())
    capsys.readouterr()
    calls_dir = workflow.mapping_path("run-test", "calls")
    audits = sorted(path.name for path in calls_dir.glob("*.json"))
    assert audits and all(not name.endswith(".intent.json") for name in audits)
    report = json.loads(
        workflow.mapping_path("run-test", "cost_report.json").read_text(encoding="utf-8"))
    assert report["calls"] == 1
    assert report["audited_calls"] == 1


def test_no_mapping_is_a_decision_and_not_a_failure(scripted, capsys):
    scripted.install(decision="no_mapping")
    cli.main(plan_argv())
    capsys.readouterr()
    assert cli.main(execute_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert "RUN_UNITS_FAILED" not in out
    assert "[INFO] mapping | unit=" in out and "no_mapping" in out
    state = workflow.load_state("run-test")
    assert {record.status.value for record in state.units.values()} == {"no_mapping"}
    rows = workflow.mapping_path("run-test", "assignments_long.csv").read_text(
        encoding="utf-8").splitlines()
    assert len(rows) == 1, "no_mapping erzeugt keine Zuordnungszeile"


def test_budget_stops_the_run_before_the_first_call(scripted, capsys):
    adapter = scripted.install()
    cli.main(plan_argv())
    capsys.readouterr()
    scripted.monkeypatch.setattr(
        cli, "load_settings",
        lambda: load_settings(settings_module(COMMITTEE_MATCHER_MAX_RUN_COST_USD=0.000001)),
    )
    assert cli.main(execute_argv()) == int(ExitCode.COST_BLOCKED)
    assert adapter.calls == []
    assert not workflow.mapping_path("run-test", "assignments_long.csv").exists()


def test_missing_call_provenance_fails_the_unit_instead_of_inventing_one(scripted, capsys):
    scripted.install(provider_name=None)
    cli.main(plan_argv())
    capsys.readouterr()
    assert cli.main(execute_argv()) == int(ExitCode.FAILED_MAPPING_UNITS)
    out = capsys.readouterr().out
    assert "RUN_UNITS_FAILED" in out
    state = workflow.load_state("run-test")
    assert {record.status.value for record in state.units.values()} == {"failed"}


def test_unreported_cost_is_reserved_and_reported(scripted, capsys):
    scripted.install(cost_usd=None)
    cli.main(plan_argv())
    capsys.readouterr()
    assert cli.main(execute_argv()) == int(ExitCode.SUCCESS_WITH_WARNINGS)
    out = capsys.readouterr().out
    assert "COST_UNKNOWN_REPORTED" in out
    assert "reserviert" in out


def test_a_finished_revision_is_not_executed_twice(scripted, capsys):
    scripted.install()
    cli.main(plan_argv())
    cli.main(execute_argv())
    capsys.readouterr()
    assert cli.main(execute_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "RUN_REVISION_COMPLETE" in capsys.readouterr().out


def _provider_error(*, retryable: bool, outcome_unknown: bool):
    return cli.provider._call_failure(
        "PROV_TEST", "call", "Testfehler.", "Testhinweis.",
        retryable=retryable, outcome_unknown=outcome_unknown)


def _single_unit_batches(monkeypatch):
    """Ein Batch je Unit; nur so ist ein Teilabbruch beobachtbar."""
    monkeypatch.setattr(
        cli, "load_settings",
        lambda: load_settings(settings_module(COMMITTEE_MATCHER_BATCH_SIZE=1)))


def test_resume_reuses_paid_units_and_sends_only_the_open_batches(scripted, capsys):
    _single_unit_batches(scripted.monkeypatch)
    first = scripted.install(errors=[None, _provider_error(
        retryable=False, outcome_unknown=False)])
    cli.main(plan_argv())
    capsys.readouterr()
    assert cli.main(execute_argv()) == int(ExitCode.PROVIDER_ERROR)
    assert len(first.calls) == 2
    state = workflow.load_state("run-test")
    assert len(state.units) == 1
    assert not workflow.mapping_path("run-test", "assignments_long.csv").exists()
    assert "RUN_ARTIFACTS_DEFERRED" in capsys.readouterr().out

    second = scripted.install()
    assert cli.main(execute_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert len(second.calls) == 2, "die bezahlte Unit wird nicht erneut gesendet"
    assert "[INFO] workflow | run=run-test | pending=2 | cached=1" in out
    assert workflow.mapping_path("run-test", "artifacts_manifest.json").exists()


def test_a_rejected_call_is_repeated_under_a_new_call_id(scripted, capsys):
    adapter = scripted.install(errors=[_provider_error(
        retryable=True, outcome_unknown=False)])
    cli.main(plan_argv())
    capsys.readouterr()
    assert cli.main(execute_argv()) in (0, 2)
    out = capsys.readouterr()
    assert len(adapter.calls) == 2
    assert adapter.calls[1].call_id.startswith("retry1-")
    assert "[WARN] provider" in out.err
    audits = sorted(path.name for path
                    in workflow.mapping_path("run-test", "calls").glob("*.json"))
    assert len(audits) == 1, "nur der erfolgreiche Call traegt eine Auditdatei"


def test_an_open_outcome_is_never_repeated(scripted, capsys):
    adapter = scripted.install(errors=[_provider_error(
        retryable=True, outcome_unknown=True)])
    cli.main(plan_argv())
    capsys.readouterr()
    # Der Providerfehler hat nach EXIT_CODE_PRIORITY Vorrang; der offene
    # Ausgang wird daneben als eigener Befund ausgewiesen.
    assert cli.main(execute_argv()) == int(ExitCode.PROVIDER_ERROR)
    out = capsys.readouterr().out
    assert len(adapter.calls) == 1, "ein offener Ausgang wird nie erneut gesendet"
    assert "RUN_OUTCOME_UNKNOWN" in out
    state = workflow.load_state("run-test")
    assert state.state.value == "outcome_unknown"
    assert len(workflow.open_intents("run-test")) == 1
