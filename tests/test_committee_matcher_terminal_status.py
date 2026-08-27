"""Tests des Live-Terminalreporters (Terminalstatusplan S1 und S2)."""
from __future__ import annotations

import io

import pytest

from committee_matcher.errors import (
    MAX_FIELD_CHARS,
    SILENT_REPORTER,
    Stage,
    StatusKind,
    TerminalReporter,
    status_line,
)


def _reporter() -> tuple[TerminalReporter, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return TerminalReporter(stdout=out, stderr=err), out, err


def test_status_line_format_matches_contract():
    line = status_line(StatusKind.START, Stage.MAPPING, committee="House Agriculture", units=6)
    assert line == "[START] mapping | committee=House Agriculture | units=6"


def test_none_value_renders_marker_without_value():
    line = status_line(
        StatusKind.INFO, Stage.MAPPING, unit="Nutrition", no_mapping=None, reason="keine Zustaendigkeit"
    )
    assert line == "[INFO] mapping | unit=Nutrition | no_mapping | reason=keine Zustaendigkeit"


@pytest.mark.parametrize("count", [1, 2, 5])
def test_unit_result_reports_industry_count(count):
    reporter, out, err = _reporter()
    reporter.ok(Stage.MAPPING, unit="livestock", industries=count)
    assert out.getvalue() == f"[OK] mapping | unit=livestock | industries={count}\n"
    assert err.getvalue() == ""


def test_committee_summary_carries_all_counters():
    line = status_line(
        StatusKind.OK,
        Stage.MAPPING,
        committee="House Agriculture",
        mapped=5,
        no_mapping=1,
        failed=0,
        industries=8,
    )
    assert line == (
        "[OK] mapping | committee=House Agriculture | mapped=5 | no_mapping=1 | failed=0 | industries=8"
    )


def test_warnings_and_errors_go_to_stderr():
    reporter, out, err = _reporter()
    reporter.warn(Stage.TAXONOMY, industry="uranium", retry="2/3", network_timeout=None)
    reporter.error(Stage.PROVIDER, unit="house-agriculture-livestock", code="API_TIMEOUT")
    assert out.getvalue() == ""
    assert err.getvalue().splitlines() == [
        "[WARN] taxonomy | industry=uranium | retry=2/3 | network_timeout",
        "[ERROR] provider | unit=house-agriculture-livestock | code=API_TIMEOUT",
    ]


def test_status_and_wait_go_to_stdout():
    reporter, out, err = _reporter()
    reporter.wait(Stage.TAXONOMY, run="run-2026-08-20", awaiting_taxonomy_approval=None)
    assert out.getvalue() == "[WAIT] taxonomy | run=run-2026-08-20 | awaiting_taxonomy_approval\n"
    assert err.getvalue() == ""


@pytest.mark.parametrize("name", ["api_key", "apikey", "openrouter_key", "auth_token", "secret", "password"])
def test_secret_field_names_are_rejected(name):
    with pytest.raises(ValueError):
        status_line(StatusKind.INFO, Stage.PROVIDER, **{name: "sk-live-value"})


def test_long_values_are_truncated_visibly():
    text = "x" * 500
    line = status_line(StatusKind.INFO, Stage.PROVIDER, response=text)
    value = line.split("response=", 1)[1]
    assert value.endswith("...")
    assert len(value) == MAX_FIELD_CHARS
    assert text not in line


def test_values_stay_on_one_line_and_keep_the_separator_unique():
    line = status_line(StatusKind.INFO, Stage.MAPPING, note="erste\nzweite | dritte")
    assert line == "[INFO] mapping | note=erste zweite / dritte"


def test_float_values_render_without_exponent():
    assert status_line(StatusKind.OK, Stage.COST, cost_usd=0.000018).endswith("cost_usd=0.000018")
    assert status_line(StatusKind.OK, Stage.COST, cost_usd=0.0).endswith("cost_usd=0")


def test_disabled_reporter_stays_silent_but_returns_the_line():
    out, err = io.StringIO(), io.StringIO()
    reporter = TerminalReporter(enabled=False, stdout=out, stderr=err)
    assert reporter.ok(Stage.MAPPING, unit="livestock", industries=2)
    assert reporter.error(Stage.PROVIDER, code="API_TIMEOUT")
    assert out.getvalue() == ""
    assert err.getvalue() == ""


def test_silent_reporter_is_disabled():
    assert SILENT_REPORTER.enabled is False


# ---------------------------------------------------------------------------
# Integration in die providerfreien Aktionen (Statusplan S3)
# ---------------------------------------------------------------------------

from committee_matcher import cli  # noqa: E402
from committee_matcher.status import ExitCode  # noqa: E402

from tests.test_committee_matcher_run_cli_p7 import sandbox  # noqa: E402,F401


def test_input_validate_reports_start_and_result(sandbox, capsys):
    cli.main(["input", "validate"])
    captured = capsys.readouterr()
    assert "[START] input | action=validate" in captured.out
    assert "[INFO] input | files=" in captured.out
    assert "units=3" in captured.out + captured.err


def test_roster_validate_reports_its_coverage(sandbox, capsys):
    cli.main(["roster", "validate"])
    captured = capsys.readouterr()
    assert "[START] roster | action=validate" in captured.out
    assert "without_roster=" in captured.out + captured.err


def test_snapshot_approve_without_a_snapshot_reports_an_error(sandbox, capsys):
    assert cli.main(["snapshot", "approve", "--run-id", "run-ohne"]) != int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "[START] taxonomy | run=run-ohne | action=approve" in captured.out
    assert "[ERROR] taxonomy | run=run-ohne | approval=abgelehnt" in captured.err


# ---------------------------------------------------------------------------
# Integration in Mappinglauf und Review (Statusplan S4)
# ---------------------------------------------------------------------------

from dataclasses import replace  # noqa: E402

from tests.test_committee_matcher_run_cli_p7 import (  # noqa: E402,F401
    ScriptedProvider, _assignment, _provider_error, execute_argv, plan_argv,
    scripted, unit_result,
)
from tests.test_committee_matcher_review_p7 import (  # noqa: E402,F401
    DistinctProvider, export_argv, import_argv, mapped_run, write_wide_baseline,
)


def run_scripted(scripted, capsys, **kwargs) -> tuple[str, str]:
    """Plant und fuehrt den Lauf aus; liefert die Ausgabe der Ausfuehrung."""
    scripted.install(**kwargs)
    assert cli.main(plan_argv()) in (0, 2)
    capsys.readouterr()
    cli.main(execute_argv())
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_each_batch_reports_start_and_finish(scripted, capsys):
    out, _ = run_scripted(scripted, capsys)
    assert "[START] mapping | run=run-test | batch=" in out
    assert "position=1/" in out, "der Batch nennt seine Stelle im Lauf"
    assert "[OK] mapping | batch=" in out
    assert "repairs=0" in out


def test_each_unit_reports_its_industry_count(scripted, capsys):
    out, _ = run_scripted(scripted, capsys)
    assert "[OK] mapping | unit=" in out
    assert "industries=1" in out


def test_no_mapping_is_an_info_not_an_error(scripted, capsys):
    """Eine Einheit ohne Zuordnung ist eine Entscheidung, kein Fehlschlag."""
    out, err = run_scripted(scripted, capsys, decision="no_mapping")
    assert "[INFO] mapping | unit=" in out
    assert "no_mapping" in out
    assert "[ERROR] mapping" not in out + err


def test_the_run_closes_with_a_committee_summary(scripted, capsys):
    out, _ = run_scripted(scripted, capsys)
    assert "[INFO] mapping | committee=" in out
    for field in ("units=", "mapped=", "no_mapping=", "failed=", "industries="):
        assert field in out


def test_every_call_reports_the_cost(scripted, capsys):
    """Ohne die Zeile waere die Kostenentwicklung erst nach dem Batch sichtbar."""
    out, _ = run_scripted(scripted, capsys)
    assert "[INFO] cost | call=" in out
    assert "outcome=charged" in out
    assert "committed_usd=" in out


def test_an_open_outcome_reports_its_reservation(scripted, capsys):
    out, _ = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=True, outcome_unknown=True)])
    assert "outcome=unknown" in out
    assert "[INFO] cost | call=" in out


def test_a_rejected_call_reports_its_retry_on_stderr(scripted, capsys):
    _, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=True, outcome_unknown=False)])
    assert "[WARN] provider | batch=" in err
    assert "retry=1/" in err


def test_a_final_provider_error_names_its_code(scripted, capsys):
    _, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=False, outcome_unknown=False)])
    assert "[ERROR] provider | batch=" in err
    assert "code=PROV_TEST" in err


class RepairedProvider(ScriptedProvider):
    """Erster Call verletzt den Vertrag, der Reparaturcall liefert gueltig."""

    def complete(self, request):
        response = super().complete(request)
        if "-repair-" in request.call_id:
            return response
        payload = dict(response.payload)
        payload["units"] = [
            unit_result(unit["unit_id"], assignments=[_assignment("gibt-es-nicht")])
            for unit in payload["units"]
        ]
        return replace(response, payload=payload)


def test_a_repair_attempt_is_reported(scripted, capsys):
    adapter = RepairedProvider()
    scripted.monkeypatch.setattr(
        cli.provider.OpenRouterProvider, "create",
        classmethod(lambda cls, collector, settings: adapter))
    assert cli.main(plan_argv()) in (0, 2)
    capsys.readouterr()
    cli.main(execute_argv())
    captured = capsys.readouterr()
    assert "[WARN] mapping | batch=" in captured.err
    assert "repair=1" in captured.err
    assert "open_violations=" in captured.err


def test_review_export_reports_start_and_result(mapped_run, capsys):
    assert cli.main(export_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert "[START] review | run=run-test | review=review-run-test" in out
    assert "[OK] review | review=review-run-test | rows=" in out


def test_review_import_reports_start_and_balance(mapped_run, capsys):
    assert cli.main(export_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(import_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert "[START] review | run=run-test | review=review-run-test | action=import" in out
    assert "[OK] review | review=review-run-test | units=" in out
    for field in ("assignments=", "manual=", "removed=", "added=", "changed="):
        assert field in out
