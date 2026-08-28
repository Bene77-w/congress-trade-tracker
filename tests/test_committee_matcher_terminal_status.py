"""Tests des Live-Terminalreporters (Terminalstatusplan S1 und S2)."""
from __future__ import annotations

import io
import json

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


from committee_matcher import paths, taxonomy_snapshot, taxonomy_source  # noqa: E402
from committee_matcher.errors import ErrorCollector  # noqa: E402

from tests.test_committee_matcher_taxonomy_p4 import (  # noqa: E402,F401
    FakeClient, artifact_sandbox, sectors)


def _snapshot_status(artifact_sandbox, **client_kwargs):
    """Fuehrt einen Snapshotlauf gegen das Testdouble und gibt die Streams."""
    out, err = io.StringIO(), io.StringIO()
    reporter = TerminalReporter(stdout=out, stderr=err)
    client = FakeClient(sectors(), **client_kwargs)
    return client, reporter, out, err


def test_snapshot_build_reports_sector_progress_and_result(artifact_sandbox):
    client, reporter, out, err = _snapshot_status(artifact_sandbox)
    taxonomy_snapshot.build_snapshot(
        ErrorCollector(), client, run_id="run-1", reporter=reporter)
    lines = out.getvalue().splitlines()
    assert "[START] taxonomy | run=run-1 | action=build | known=0" in lines
    # Jeder Sectorvorgang hat Start und Gegenstueck; 'done' beziffert den
    # Restumfang in der einzigen vorab bekannten Menge.
    assert "[START] taxonomy | sector=energy | done=1/2" in lines
    assert "[OK] taxonomy | sector=energy | industries=2" in lines
    assert "[START] taxonomy | sector=technology | done=2/2" in lines
    assert "[OK] taxonomy | sector=technology | industries=1" in lines
    assert any(line.startswith("[INFO] taxonomy | run=run-1 | checkpoint=")
               for line in lines), "Checkpointfortschritt bleibt sichtbar"
    assert err.getvalue() == ""


def test_a_successful_single_fetch_stays_silent(artifact_sandbox):
    """Nur Wiederholungen sind relevant; sonst stuende je Industry eine Zeile."""
    client, reporter, out, err = _snapshot_status(artifact_sandbox)
    taxonomy_snapshot.build_snapshot(
        ErrorCollector(), client, run_id="run-1", reporter=reporter)
    assert not [line for line in out.getvalue().splitlines()
                if "industry=" in line]


def test_a_retried_request_is_visible_without_its_payload():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("GEHEIMER ANTWORTTEXT")
        return "ok"

    out, err = io.StringIO(), io.StringIO()
    reporter = TerminalReporter(stdout=out, stderr=err)
    from tests.test_committee_matcher_taxonomy_p4 import OFFLINE

    client = taxonomy_source.YFinanceClient(
        OFFLINE, sleep=lambda _s: None, reporter=reporter)
    assert client._call("uranium", flaky) == "ok"
    assert "[WARN] taxonomy | object=uranium | retry=1/" in err.getvalue()
    assert "GEHEIMER ANTWORTTEXT" not in err.getvalue() + out.getvalue()


def test_an_exhausted_fetch_closes_its_start_with_an_error(artifact_sandbox):
    """Sonst bliebe der Vorgang im Livestatus offen und der gesicherte
    Wiederaufnahmepunkt stuende nirgends."""
    client, reporter, out, err = _snapshot_status(artifact_sandbox, fail_on="uranium")
    with pytest.raises(taxonomy_source.TaxonomyFetchError):
        taxonomy_snapshot.build_snapshot(
            ErrorCollector(), client, run_id="run-1", reporter=reporter)
    assert ("[ERROR] taxonomy | run=run-1 | code=TAX_FETCH_FAILED | known=1"
            in err.getvalue())


def test_snapshot_resume_reports_its_starting_point(artifact_sandbox):
    client, reporter, out, err = _snapshot_status(artifact_sandbox, fail_on="uranium")
    with pytest.raises(taxonomy_source.TaxonomyFetchError):
        taxonomy_snapshot.build_snapshot(
            ErrorCollector(), client, run_id="run-1", reporter=reporter)

    client, reporter, out, err = _snapshot_status(artifact_sandbox)
    taxonomy_snapshot.build_snapshot(
        ErrorCollector(), client, run_id="run-1", resume=True, reporter=reporter)
    assert "[START] taxonomy | run=run-1 | action=resume | known=1" in out.getvalue()


def test_a_snapshot_module_without_a_reporter_stays_silent(artifact_sandbox, capsys):
    taxonomy_snapshot.build_snapshot(
        ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_an_aborted_snapshot_run_still_ends_with_one_report(sandbox, capsys,
                                                            monkeypatch):
    """Genau ein Abschlussbericht je Lauf, auch wenn der Abruf ausfaellt."""
    def failing(settings, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(taxonomy_source, "YFinanceClient", failing)
    assert cli.main(["snapshot", "build", "--run-id", "run-abbruch"]) == int(
        ExitCode.USER_ABORT)
    out = capsys.readouterr().out
    assert out.count("Committee Matcher V2 -") == 1
    assert "CLI_ABORTED" in out


# ---------------------------------------------------------------------------
# Integration in Mappinglauf und Review (Statusplan S4)
# ---------------------------------------------------------------------------

from dataclasses import replace  # noqa: E402

from committee_matcher import workflow  # noqa: E402

from tests.test_committee_matcher_run_cli_p7 import (  # noqa: E402,F401
    ScriptedProvider, _assignment, _provider_error, _requested_units,
    _single_unit_batches, execute_argv, plan_argv, scripted, unit_result,
)
from tests.test_committee_matcher_review_p7 import (  # noqa: E402,F401
    DistinctProvider, export_argv, import_argv, mapped_run, write_wide_baseline,
)

#: Kosten je Call im Skriptprovider; der Kostentest rechnet damit nach.
CALL_USD = 0.000012


def status_lines(text: str, prefix: str) -> list[str]:
    """Alle vollstaendigen Statuszeilen mit diesem Kopf.

    Geprueft wird zeilenweise, damit ein Feld nicht aus einer anderen Zeile
    stammen kann und die Anzahl der Meldungen mitgeprueft wird.
    """
    return [line for line in text.splitlines() if line.startswith(prefix)]


def fields_of(line: str) -> dict[str, str]:
    """Die Felder einer Statuszeile; ein Marker ohne Wert wird zu ``''``."""
    values: dict[str, str] = {}
    for part in line.split(" | ")[1:]:
        name, _, value = part.partition("=")
        values[name] = value
    return values


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
    starts = status_lines(out, "[START] mapping")
    finished = status_lines(out, "[OK] mapping | batch=")
    assert len(starts) == 1 and len(finished) == 1, "je Batch genau ein Paar"
    start, finish = fields_of(starts[0]), fields_of(finished[0])
    assert start["position"] == "1/1"
    assert start["units"] == "3"
    assert finish["batch"] == start["batch"], "der Abschluss nennt denselben Batch"
    assert finish["repairs"] == "0"
    assert float(finish["cost_usd"]) == pytest.approx(CALL_USD)


def test_each_unit_reports_its_industry_count(scripted, capsys):
    out, _ = run_scripted(scripted, capsys)
    units = status_lines(out, "[OK] mapping | unit=")
    assert len(units) == 3, "jede Einheit des Laufs bekommt genau eine Zeile"
    assert {fields_of(line)["industries"] for line in units} == {"1"}


def test_no_mapping_is_an_info_not_an_error(scripted, capsys):
    """Eine Einheit ohne Zuordnung ist eine Entscheidung, kein Fehlschlag."""
    out, err = run_scripted(scripted, capsys, decision="no_mapping")
    infos = status_lines(out, "[INFO] mapping | unit=")
    assert len(infos) == 3
    assert all("no_mapping" in fields_of(line) for line in infos)
    assert all(fields_of(line)["reason"] for line in infos)
    assert not status_lines(out + err, "[ERROR] mapping")


def test_the_run_closes_with_a_committee_summary(scripted, capsys):
    out, _ = run_scripted(scripted, capsys)
    summaries = status_lines(out, "[INFO] mapping | committee=")
    assert len(summaries) == 1, "eine Zusammenfassung je Committee"
    fields = fields_of(summaries[0])
    assert fields["units"] == "3"
    assert fields["mapped"] == "3"
    assert fields["no_mapping"] == "0"
    assert fields["failed"] == "0"
    assert fields["not_decided"] == "0"
    assert fields["industries"] == "1"


def test_every_booked_call_reports_its_cost(scripted, capsys):
    """Ohne die Zeile verschwaende ein teurer Reparaturcall in der Batchsumme."""
    out, _ = run_scripted(scripted, capsys)
    costs = status_lines(out, "[INFO] cost | call=")
    assert len(costs) == 1, "je gebuchtem Call genau eine Zeile"
    fields = fields_of(costs[0])
    assert fields["outcome"] == "charged"
    assert float(fields["call_usd"]) == pytest.approx(CALL_USD)
    assert float(fields["committed_usd"]) == pytest.approx(CALL_USD)


def test_a_free_call_gets_no_cost_line(scripted, capsys):
    """Ein vom Provider abgewiesener Call kostet nichts und meldet nichts."""
    out, _ = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=True, outcome_unknown=False)])
    costs = status_lines(out, "[INFO] cost | call=")
    assert len(costs) == 1, "nur der erfolgreiche zweite Call ist gebucht"
    assert fields_of(costs[0])["call"].startswith("retry1-")


def test_an_open_outcome_reports_its_reservation(scripted, capsys):
    out, _ = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=True, outcome_unknown=True)])
    costs = status_lines(out, "[INFO] cost | call=")
    assert len(costs) == 1
    fields = fields_of(costs[0])
    assert fields["outcome"] == "unknown"
    assert float(fields["call_usd"]) > 0, "der Worst Case bleibt reserviert"
    assert float(fields["committed_usd"]) == pytest.approx(float(fields["call_usd"]))


def test_a_rejected_call_reports_its_retry_on_stderr(scripted, capsys):
    _, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=True, outcome_unknown=False)])
    retries = status_lines(err, "[WARN] provider | batch=")
    assert len(retries) == 1
    fields = fields_of(retries[0])
    assert fields["retry"].startswith("1/")
    assert fields["reason"] == "PROV_TEST"


def test_a_final_provider_error_names_its_code(scripted, capsys):
    _, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=False, outcome_unknown=False)])
    errors = status_lines(err, "[ERROR] provider | batch=")
    assert len(errors) == 1
    assert fields_of(errors[0])["code"] == "PROV_TEST"


def test_an_aborted_batch_closes_its_frame(scripted, capsys):
    """Ohne Abschlusszeile bliebe der START ohne Gegenstueck."""
    out, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=False, outcome_unknown=False)])
    assert len(status_lines(out, "[START] mapping | run=")) == 1
    aborted = status_lines(err, "[ERROR] mapping | batch=")
    assert len(aborted) == 1
    fields = fields_of(aborted[0])
    assert "aborted" in fields
    assert fields["open_units"] == "3"
    open_units = status_lines(err, "[WARN] mapping | unit=")
    assert len(open_units) == 3, "jede offene Einheit wird benannt"
    assert all("not_decided" in fields_of(line) for line in open_units)
    named = {fields_of(line)["unit"] for line in open_units}
    assert len(named) == 3, "keine Einheit wird doppelt gemeldet"
    summary = fields_of(status_lines(err, "[WARN] mapping | committee=")[0])
    assert summary["failed"] == "0", "offen ist nicht gescheitert"
    assert summary["not_decided"] == "3"


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


class FailingRepairProvider(ScriptedProvider):
    """Erster Call verletzt den Vertrag, der Reparaturcall scheitert."""

    def complete(self, request):
        if "-repair-" in request.call_id:
            self.calls.append(request)
            raise _provider_error(retryable=True, outcome_unknown=False)
        response = super().complete(request)
        payload = dict(response.payload)
        payload["units"] = [
            unit_result(unit["unit_id"], assignments=[_assignment("gibt-es-nicht")])
            for unit in payload["units"]
        ]
        return replace(response, payload=payload)


def install(scripted, adapter):
    scripted.monkeypatch.setattr(
        cli.provider.OpenRouterProvider, "create",
        classmethod(lambda cls, collector, settings: adapter))
    assert cli.main(plan_argv()) in (0, 2)
    return adapter


def test_a_repair_attempt_is_reported(scripted, capsys):
    install(scripted, RepairedProvider())
    capsys.readouterr()
    cli.main(execute_argv())
    captured = capsys.readouterr()
    repairs = status_lines(captured.err, "[WARN] mapping | batch=")
    assert len(repairs) == 1
    fields = fields_of(repairs[0])
    assert fields["repair"] == "1"
    assert "-repair-1" in fields["call"]
    assert int(fields["open_violations"]) >= 1
    assert fields_of(status_lines(captured.out, "[OK] mapping | batch=")[0])[
        "repairs"] == "1"


def test_a_failing_repair_is_still_reported(scripted, capsys):
    """Scheitert der Reparaturcall, gaebe es sonst keine Spur des Versuchs."""
    install(scripted, FailingRepairProvider())
    capsys.readouterr()
    cli.main(execute_argv())
    captured = capsys.readouterr()
    repairs = status_lines(captured.err, "[WARN] mapping | batch=")
    assert len(repairs) == 1, "der Versuch wird vor dem Senden gemeldet"
    assert "-repair-1" in fields_of(repairs[0])["call"]
    assert status_lines(captured.err, "[ERROR] provider | batch=")


def test_an_exception_after_a_paid_call_keeps_the_cost_visible(
        scripted, capsys, monkeypatch):
    """Der Abschlussbericht darf einen bezahlten Lauf nie providerfrei nennen."""
    scripted.install()
    assert cli.main(plan_argv()) in (0, 2)
    capsys.readouterr()

    def failing(self, *args, **kwargs):
        raise OSError("Datentraeger voll")

    monkeypatch.setattr(workflow.AuditedProvider, "_persist", failing)
    assert cli.main(execute_argv()) == int(ExitCode.INTERNAL_ERROR)
    captured = capsys.readouterr()
    assert "RUN_ABORTED_AFTER_CALL" in captured.out
    assert "providerfreie Phase" not in captured.out
    assert f"Kosten: {CALL_USD:.6f} USD" in captured.out
    assert "[ERROR] workflow" in captured.err
    aborted = status_lines(captured.err, "[ERROR] mapping | batch=")
    assert len(aborted) == 1, "auch der Ausnahmepfad schliesst seinen Batch"
    assert fields_of(aborted[0])["open_units"] == "3"
    assert len(status_lines(captured.err, "[WARN] mapping | unit=")) == 3


def test_the_run_ends_at_a_human_gate_as_wait(scripted, capsys):
    out, _ = run_scripted(scripted, capsys)
    gates = status_lines(out, "[WAIT] workflow")
    assert len(gates) == 1
    fields = fields_of(gates[0])
    assert fields["state"] == "awaiting_manual_review"
    assert fields["mapped"] == "3"


def test_review_export_reports_start_and_result(mapped_run, capsys):
    assert cli.main(export_argv()) in (0, 2)
    out = capsys.readouterr().out
    assert status_lines(out, "[START] review | run=run-test | review=review-run-test")
    results = status_lines(out, "[OK] review | review=review-run-test")
    assert len(results) == 1
    assert int(fields_of(results[0])["rows"]) > 0


def test_review_import_reports_start_and_balance(mapped_run, capsys):
    assert cli.main(export_argv()) in (0, 2)
    capsys.readouterr()
    assert cli.main(import_argv()) in (0, 2)
    out = capsys.readouterr().out
    starts = status_lines(out, "[START] review | run=run-test")
    assert len(starts) == 1 and fields_of(starts[0])["action"] == "import"
    results = status_lines(out, "[OK] review | review=review-run-test")
    assert len(results) == 1
    fields = fields_of(results[0])
    assert int(fields["units"]) > 0
    assert fields["manual"] == "0"
    assert (fields["removed"], fields["added"], fields["changed"]) == ("0", "0", "0")


class ResumeProvider(ScriptedProvider):
    """Erste Einheit scheitert fachlich, die zweite bricht den Lauf ab.

    Danach traegt die erste Einheit einen ``failed``-Eintrag im Laufzustand und
    steht trotzdem wieder in ``pending`` - genau der Fall, in dem eine Zaehlung
    ueber den Laufzustand die offene Einheit verschluckt.
    """

    def __init__(self):
        super().__init__()
        self.first_unit: str | None = None

    def complete(self, request):
        units = _requested_units(request)
        if self.first_unit is None:
            self.first_unit = units[0]
        if units[0] == self.first_unit:
            response = super().complete(request)
            payload = dict(response.payload)
            payload["units"] = [
                unit_result(unit_id, assignments=[_assignment("gibt-es-nicht")])
                for unit_id in units
            ]
            return replace(response, payload=payload)
        self.calls.append(request)
        raise _provider_error(retryable=False, outcome_unknown=False)


def test_an_aborted_resume_batch_still_names_its_unit(scripted, capsys, monkeypatch):
    """Eine gescheiterte Einheit laeuft erneut; ihr Abbruch bleibt sichtbar."""
    _single_unit_batches(monkeypatch)
    first = install(scripted, ResumeProvider())
    capsys.readouterr()
    cli.main(execute_argv())
    capsys.readouterr()
    assert first.first_unit is not None
    state = json.loads(
        workflow.state_path("run-test").read_text(encoding="utf-8"))
    entry = [unit for unit in state["units"]
             if unit["unit_id"] == first.first_unit]
    assert entry and entry[0]["status"] == "failed", (
        "Vorbedingung: die Einheit steht als gescheitert im Laufzustand und "
        "laeuft trotzdem erneut")

    install(scripted, ScriptedProvider(errors=[
        _provider_error(retryable=False, outcome_unknown=False)]))
    capsys.readouterr()
    cli.main(execute_argv())
    err = capsys.readouterr().err
    aborted = status_lines(err, "[ERROR] mapping | batch=")
    assert len(aborted) == 1
    assert fields_of(aborted[0])["open_units"] == "1"
    open_units = status_lines(err, "[WARN] mapping | unit=")
    assert [fields_of(line)["unit"] for line in open_units] == [first.first_unit]


def test_each_repair_call_reports_its_own_cost(scripted, capsys):
    """Ohne eigene Zeile verschwaende der Reparaturcall in der Batchsumme."""
    install(scripted, RepairedProvider())
    capsys.readouterr()
    cli.main(execute_argv())
    out = capsys.readouterr().out
    costs = status_lines(out, "[INFO] cost | call=")
    assert len(costs) == 2, "Erstcall und Reparaturcall zaehlen einzeln"
    assert sum("-repair-" in fields_of(line)["call"] for line in costs) == 1
    assert float(fields_of(costs[-1])["committed_usd"]) == pytest.approx(2 * CALL_USD)


class InterruptingProvider(ScriptedProvider):
    """Der zweite Call wird mitten im Senden abgebrochen."""

    def complete(self, request):
        if len(self.calls) >= 1:
            self.calls.append(request)
            raise KeyboardInterrupt
        return super().complete(request)


def test_an_interrupted_call_stays_reserved_and_visible(scripted, capsys, monkeypatch):
    """Ein Abbruch mitten im Call kann bezahlt sein; er gilt als offener Ausgang."""
    _single_unit_batches(monkeypatch)
    install(scripted, InterruptingProvider())
    capsys.readouterr()
    assert cli.main(execute_argv()) == int(ExitCode.USER_ABORT)
    captured = capsys.readouterr()
    assert "RUN_ABORTED_BY_USER" in captured.out
    assert "providerfreie Phase" not in captured.out
    costs = status_lines(captured.out, "[INFO] cost | call=")
    assert [fields_of(line)["outcome"] for line in costs] == ["charged", "unknown"]
    assert "[ERROR] workflow" in captured.err
    assert status_lines(captured.err, "[ERROR] mapping | batch=")


# ---------------------------------------------------------------------------
# Abnahme des Statusvertrags (Statusplan S5)
# ---------------------------------------------------------------------------

from tests.test_committee_matcher_run_cli_p7 import (  # noqa: E402,F401
    INDUSTRIES, api_key,
)
from committee_matcher import taxonomy_snapshot  # noqa: E402

#: Fuenf Industries; nur mit ihnen ist die Obergrenze des Modellvertrags
#: ueberhaupt erreichbar.
FIVE_INDUSTRIES = INDUSTRIES + [
    {
        "industry_id": f"branche-{index}",
        "industry_name": f"Branche {index}",
        "sector_id": "industrials",
        "sector_name": "Industrials",
        "description": f"Testbranche {index}.",
    }
    for index in range(1, 4)
]


def write_baseline(records, run_id: str = "run-taxonomy") -> None:
    """Legt eine freigegebene Taxonomie mit genau diesen Industries an."""
    target = taxonomy_snapshot.baseline_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "content_version": taxonomy_snapshot.TAXONOMY_CONTENT_VERSION,
            "approved_run_id": run_id,
            "taxonomy_content_hash": taxonomy_snapshot.hash_records(records),
            "yfinance_version": "0.2.99",
            "industries": records,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


class CountingProvider(ScriptedProvider):
    """Ordnet jeder Einheit ``count`` Industries zu, soweit ihr Typ das zulaesst.

    Der uebergeordnete Zustaendigkeitskern eines Committees traegt nach dem
    Modellvertrag hoechstens zwei Zuordnungen; er bekommt deshalb zwei, auch
    wenn der Fall fuenf prueft.
    """

    #: Ein Subcommittee traegt drei Segmente in seiner Kennung.
    SUBCOMMITTEE_SEGMENTS = 3
    #: Woertliche Ausschnitte aus dem Zustaendigkeitstext eines Subcommittees.
    #: Die Broad-Scope-Ausnahme verlangt je Zuordnung eine eigene Evidence.
    EVIDENCE = (
        "Zustaendig fuer", "dessen Aufsicht", "sowie alle daran",
        "anschliessenden Pruefverfahren", "alle daran anschliessenden",
    )

    def __init__(self, count: int, pool):
        super().__init__()
        self.count = count
        self.pool = list(pool)

    def _count_for(self, unit_id: str) -> int:
        wide = len(unit_id.split("--")) >= self.SUBCOMMITTEE_SEGMENTS
        return self.count if wide else min(self.count, 2)

    def complete(self, request):
        response = super().complete(request)
        payload = dict(response.payload)
        units = []
        for unit in payload["units"]:
            count = self._count_for(unit["unit_id"])
            broad = count > 2
            assignments = []
            for position, industry_id in enumerate(self.pool[:count]):
                entry = _assignment(industry_id)
                if broad:
                    entry["evidence_excerpt"] = self.EVIDENCE[position]
                    entry["rationale_short"] = (
                        f"Eigener Kernbereich {position + 1} dieser Einheit.")
                assignments.append(entry)
            units.append({
                **unit_result(unit["unit_id"], assignments=assignments),
                "broad_scope_exception": broad,
                "broad_scope_reason": ("Mehrere getrennte Kernbereiche."
                                       if broad else None),
            })
        payload["units"] = units
        return replace(response, payload=payload)


@pytest.mark.parametrize("count", [1, 2, 5])
def test_the_unit_line_counts_the_industries_of_the_run(scripted, capsys, count):
    """Die gemeldete Zahl folgt der Antwort, ueber die ganze Rubrik hinweg."""
    write_baseline(FIVE_INDUSTRIES)
    pool = [record["industry_id"] for record in FIVE_INDUSTRIES]
    install(scripted, CountingProvider(count, pool))
    capsys.readouterr()
    cli.main(execute_argv())
    out = capsys.readouterr().out
    units = status_lines(out, "[OK] mapping | unit=")
    assert len(units) == 3, "jede Einheit bekommt ihre Zeile"
    counted = {fields_of(line)["unit"]: fields_of(line)["industries"]
               for line in units}
    wide = {unit_id: value for unit_id, value in counted.items()
            if len(unit_id.split("--")) >= 3}
    assert wide and set(wide.values()) == {str(count)}
    summary = fields_of(status_lines(out, "[INFO] mapping | committee=")[0])
    assert summary["industries"] == str(count), "die Familie zaehlt eindeutige IDs"


def test_a_missing_approval_waits_instead_of_running(sandbox, capsys):
    """Das Freigabegate ist ein Wartezustand, kein Fehler."""
    taxonomy_snapshot.baseline_path().unlink()
    assert cli.main(plan_argv()) == int(ExitCode.AWAITING_TAXONOMY_APPROVAL)
    captured = capsys.readouterr()
    gates = status_lines(captured.out, "[WAIT] taxonomy")
    assert len(gates) == 1
    assert "awaiting_taxonomy_approval" in fields_of(gates[0])
    assert not status_lines(captured.err, "[ERROR] taxonomy")


def test_the_api_key_never_reaches_the_output(scripted, capsys, api_key):
    """Ein Geheimnis darf weder in eine Statuszeile noch in den Bericht."""
    out, err = run_scripted(scripted, capsys)
    assert "sk-test" not in out + err


def test_exactly_one_report_closes_a_successful_run(scripted, capsys):
    out, err = run_scripted(scripted, capsys)
    assert (out + err).count("Exit-Code:") == 1
    assert out.count("Committee Matcher V2 - run execute") == 1


def test_exactly_one_report_closes_a_failed_run(scripted, capsys):
    out, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=False, outcome_unknown=False)])
    assert (out + err).count("Exit-Code:") == 1
    assert out.count("Committee Matcher V2 - run execute") == 1


def test_the_streams_stay_separated_over_a_whole_run(scripted, capsys):
    """stdout traegt den Fortschritt, stderr ausschliesslich Warnung und Fehler."""
    out, err = run_scripted(scripted, capsys, errors=[
        _provider_error(retryable=True, outcome_unknown=False)])
    assert not [line for line in out.splitlines()
                if line.startswith(("[WARN]", "[ERROR]"))]
    assert not [line for line in err.splitlines()
                if line.startswith(("[START]", "[OK]", "[INFO]", "[WAIT]"))]
    assert status_lines(err, "[WARN] provider"), "die Warnung ist wirklich gefallen"
