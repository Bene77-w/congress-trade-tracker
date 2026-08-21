"""Routing-Preflight des Providerpfads (Gate-B-Beschluss).

Alle Tests sind netzwerkfrei: der Katalogabruf wird als Funktion injiziert.
Geprueft wird, dass jede Ursache einer leeren Endpointmenge **getrennt**
gemeldet wird - im Terminal soll stehen, welches Kriterium geleert hat und
welche Stellschraube ohne Eingriff in die Workflowlogik hilft.
"""
from __future__ import annotations

import pytest

from committee_matcher import cli, paths, provider
from committee_matcher.errors import ErrorCollector, Severity, Stage
from committee_matcher.settings_view import ProviderSettings
from committee_matcher.status import ExitCode


@pytest.fixture()
def artifact_sandbox(tmp_path, monkeypatch):
    """Leitet die Artefaktwurzel um, damit CLI-Laeufe nichts ins Repo schreiben."""
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    return tmp_path


def _settings(**overrides) -> ProviderSettings:
    values = dict(
        model="anbieter/modell",
        data_collection="deny",
        zdr=True,
        max_price_usd_per_mtok={"prompt": 0.30, "completion": 0.90},
        endpoint_context_tokens=200_000,
        max_output_tokens=8_000,
        reasoning_reserve_tokens=24_000,
        request_timeout_s=300.0,
        quantizations=("fp8", "bf16", "fp16"),
    )
    values.update(overrides)
    return ProviderSettings(**values)


def _endpoint(tag, *, quant="fp8", prompt=0.08, completion=0.18, schema=True):
    return {
        "tag": tag,
        "quantization": quant,
        "context_length": 1_000_000,
        "pricing": {"prompt": str(prompt / 1_000_000), "completion": str(completion / 1_000_000)},
        "supported_parameters": (
            ["response_format", "structured_outputs"] if schema else ["response_format"]
        ),
    }


def _catalog(*endpoints):
    return lambda model: {"data": {"endpoints": list(endpoints)}}


def only(collector):
    blockers = [f for f in collector.findings if f.severity is Severity.BLOCKER]
    assert len(blockers) == 1, f"genau ein Blocker erwartet, {len(blockers)} erhalten"
    return blockers[0]


# -- Erfolgsfall -----------------------------------------------------------
def test_eligible_endpoints_survive_every_filter():
    collector = ErrorCollector()
    report = provider.check_routing(collector, _settings(), fetch=_catalog(
        _endpoint("guenstig/fp8", prompt=0.08),
        _endpoint("teuer/fp8", prompt=0.50),
        _endpoint("ohne-schema/fp8", schema=False),
        _endpoint("falsche-quant/fp4", quant="fp4"),
    ))
    assert collector.findings == []
    assert report is not None
    assert [e.tag for e in report.eligible] == ["guenstig/fp8"]
    assert report.total == 4
    assert report.cheapest.prompt_usd_per_mtok == pytest.approx(0.08)


def test_no_quantization_filter_keeps_every_capable_endpoint():
    collector = ErrorCollector()
    report = provider.check_routing(
        collector, _settings(quantizations=None), fetch=_catalog(
            _endpoint("a/fp4", quant="fp4"),
            _endpoint("b/bf16", quant="bf16"),
        ))
    assert {e.tag for e in report.eligible} == {"a/fp4", "b/bf16"}


# -- Jede Leerungsursache getrennt -----------------------------------------
def test_unknown_model_blocks_before_any_call():
    collector = ErrorCollector()
    assert provider.check_routing(collector, _settings(), fetch=lambda m: {}) is None
    finding = only(collector)
    assert finding.code == "PROV_MODEL_UNKNOWN"
    assert finding.stage is Stage.PROVIDER
    assert finding.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_model_without_structured_output_demands_a_model_change():
    collector = ErrorCollector()
    assert provider.check_routing(collector, _settings(), fetch=_catalog(
        _endpoint("a/fp8", schema=False),
        _endpoint("b/bf16", quant="bf16", schema=False),
    )) is None
    finding = only(collector)
    assert finding.code == "PROV_NO_STRUCTURED_OUTPUT"
    assert "COMMITTEE_MATCHER_MODEL" in finding.hint
    assert "Workflowlogik" in finding.hint


def test_quantization_filter_names_itself_when_it_empties_the_set():
    collector = ErrorCollector()
    assert provider.check_routing(collector, _settings(), fetch=_catalog(
        _endpoint("a/fp4", quant="fp4"),
        _endpoint("b/int8", quant="int8"),
    )) is None
    finding = only(collector)
    assert finding.code == "PROV_NO_ENDPOINT"
    assert "QUANTIZATIONS" in finding.hint
    # Der Befund nennt beide Seiten: gewuenscht und tatsaechlich vorhanden.
    assert "fp8" in finding.message and "fp4" in finding.message


def test_price_cap_reports_the_cheapest_available_endpoint():
    collector = ErrorCollector()
    assert provider.check_routing(collector, _settings(), fetch=_catalog(
        _endpoint("a/fp8", prompt=1.20, completion=3.00),
        _endpoint("b/fp8", prompt=0.95, completion=2.50),
    )) is None
    finding = only(collector)
    assert finding.code == "PROV_NO_ENDPOINT"
    assert "0.950" in finding.message
    assert "MAX_PRICE" in finding.hint


def test_unreachable_catalog_is_its_own_finding():
    def boom(model):
        raise provider.CatalogUnavailable("HTTP 503")

    collector = ErrorCollector()
    assert provider.check_routing(collector, _settings(), fetch=boom) is None
    finding = only(collector)
    assert finding.code == "PROV_CATALOG_UNREACHABLE"
    assert "503" in finding.message


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {"endpoints": "x"}}, []],
                         ids=["leer", "data-null", "endpoints-string", "liste"])
def test_malformed_catalog_answers_do_not_crash(payload):
    collector = ErrorCollector()
    assert provider.check_routing(collector, _settings(), fetch=lambda m: payload) is None
    assert only(collector).code == "PROV_MODEL_UNKNOWN"


# -- Der Fall, den der Preflight nicht sehen kann ---------------------------
def test_routing_failure_points_at_zdr_because_the_catalog_hides_it():
    collector = ErrorCollector()
    provider.routing_failure_finding(collector, _settings(), "No endpoints found")
    finding = only(collector)
    assert finding.code == "PROV_NO_ENDPOINT"
    assert "zdr=True" in finding.message
    assert "COMMITTEE_MATCHER_ZDR" in finding.hint
    assert finding.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_hint_names_both_safe_adjustments():
    """Modellwechsel und ZDR sind reine Settings - sie beruehren keinen Ablauf."""
    assert "COMMITTEE_MATCHER_MODEL" in provider.ADJUSTMENT_HINT
    assert "COMMITTEE_MATCHER_ZDR" in provider.ADJUSTMENT_HINT
    assert "Workflowlogik" in provider.ADJUSTMENT_HINT


# -- CLI -------------------------------------------------------------------
def test_cli_reports_the_eligible_endpoints(capsys, artifact_sandbox, monkeypatch):
    monkeypatch.setattr(provider, "fetch_endpoints", _catalog(
        _endpoint("guenstig/fp8"), _endpoint("ausgeschlossen/fp4", quant="fp4")))
    assert cli.main(["provider", "check"]) == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "1 von 2 zulaessig" in out
    assert "guenstig/fp8" in out
    assert "ZDR" in out, "der ungeprueft bleibende Teil muss im Bericht stehen"


def test_cli_aborts_with_exit_four_when_nothing_is_eligible(capsys, artifact_sandbox, monkeypatch):
    monkeypatch.setattr(provider, "fetch_endpoints", _catalog(
        _endpoint("a/fp8", schema=False)))
    assert cli.main(["provider", "check"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "PROV_NO_STRUCTURED_OUTPUT" in out
    assert "keiner zulaessig" in out
