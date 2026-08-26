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
