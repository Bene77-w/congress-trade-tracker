"""Providerneutrales Interface und OpenRouter-Adapter (Plan 2.6, P5).

Alle Tests sind netzwerkfrei: der SDK-Client wird als Testdouble injiziert.
Geprueft wird vor allem, dass der Adapter jeden Ausgang **fachlich uebersetzt**
statt SDK-Typen oder rohe Exceptions nach aussen zu lassen, und dass nur echte
Uebergangszustaende als wiederholbar gelten.
"""
from __future__ import annotations

import json

import pytest

from committee_matcher import provider
from committee_matcher.errors import ErrorCollector, Severity, Stage
from committee_matcher.settings_view import ProviderSettings
from committee_matcher.status import ExitCode

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}},
          "required": ["a"], "additionalProperties": False}


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
        quantizations=("fp8",),
    )
    values.update(overrides)
    return ProviderSettings(**values)


def _request(**overrides) -> provider.StructuredRequest:
    values = dict(
        call_id="batch-1",
        system_prompt="Systemregeln",
        user_content="Nutzdaten",
        schema_name="MappingBatchResult",
        json_schema=SCHEMA,
        max_output_tokens=4_000,
    )
    values.update(overrides)
    return provider.StructuredRequest(**values)


# -- SDK-Testdoubles --------------------------------------------------------
class _Message:
    def __init__(self, content, refusal=None):
        self.content = content
        self.refusal = refusal


class _Choice:
    def __init__(self, content, finish_reason="stop", refusal=None):
        self.message = _Message(content, refusal)
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt=120, completion=45, cost=0.0004):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.cost = cost


class _Attempt:
    def __init__(self, name):
        self.provider = name


class _Metadata:
    def __init__(self, *names):
        self.attempts = [_Attempt(n) for n in names]


class _Result:
    def __init__(self, content='{"a": "b"}', *, finish_reason="stop", refusal=None,
                 usage=None, metadata=None, choices=None, model="anbieter/modell-x"):
        self.choices = choices if choices is not None else [
            _Choice(content, finish_reason, refusal)]
        self.usage = usage if usage is not None else _Usage()
        self.openrouter_metadata = metadata
        self.model = model
        self.id = "gen-123"


class _Chat:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.kwargs = None

    def send(self, **kwargs):
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _Client:
    def __init__(self, result=None, error=None):
        self.chat = _Chat(result, error)


def _provider(result=None, error=None, settings=None):
    client = _Client(result, error)
    return provider.OpenRouterProvider(settings or _settings(), "key", client=client), client


def only(collector):
    blockers = [f for f in collector.findings if f.severity is Severity.BLOCKER]
    assert len(blockers) == 1, f"genau ein Blocker erwartet, {len(blockers)} erhalten"
    return blockers[0]


# -- Erfolgsfall ------------------------------------------------------------
def test_successful_call_is_translated_into_the_neutral_response():
    adapter, _ = _provider(_Result(metadata=_Metadata("deepinfra", "baidu")))
    response = adapter.complete(_request())
    assert response.payload == {"a": "b"}
    assert response.provider == "baidu", "der zuletzt geroutete Versuch zaehlt"
    assert response.model == "anbieter/modell-x"
    assert response.prompt_tokens == 120
    assert response.completion_tokens == 45
    assert response.total_tokens == 165
    assert response.cost_usd == pytest.approx(0.0004)
    assert response.generation_id == "gen-123"


def test_request_carries_the_gate_b_routing_conditions():
    adapter, client = _provider(_Result())
    adapter.complete(_request())
    sent = client.chat.kwargs
    preferences = sent["provider"]
    assert preferences.allow_fallbacks is True
    gesendet = preferences.model_dump(exclude_unset=True)
    assert "only" not in gesendet and "order" not in gesendet, \
        "die Endpointwahl bleibt bei OpenRouter"
    assert preferences.require_parameters is True
    assert preferences.zdr is True
    assert preferences.data_collection == "deny"
    assert preferences.quantizations == ["fp8"]
    assert preferences.max_price.prompt == "0.3"
    assert sent["response_format"].json_schema.strict is True
    assert sent["response_format"].json_schema.schema_ == SCHEMA
    assert sent["stream"] is False
    assert sent["timeout_ms"] == 300_000
    assert sent["max_tokens"] == 4_000
    assert sent["retries"] is not None, "SDK-Retries werden ausdruecklich abgeschaltet"


def test_missing_cost_stays_unknown_instead_of_becoming_zero():
    """cost_unknown ist ein eigener Zustand; der Adapter erfindet keine Zahl."""
    adapter, _ = _provider(_Result(usage=_Usage(cost=None)))
    assert adapter.complete(_request()).cost_usd is None


def test_missing_routing_metadata_leaves_the_provider_unknown():
    adapter, _ = _provider(_Result(metadata=None))
    assert adapter.complete(_request()).provider is None


def test_segmented_content_is_joined():
    adapter, _ = _provider(_Result(content=[{"text": '{"a": '}, {"text": '"b"}'}]))
    assert adapter.complete(_request()).payload == {"a": "b"}


def test_manifest_entry_never_contains_the_api_key():
    adapter, _ = _provider(_Result())
    entry = adapter.complete(_request()).manifest_entry()
    assert "key" not in json.dumps(entry).lower().replace("call_id", "")
    assert set(entry) == {"call_id", "model", "provider", "prompt_tokens",
                          "completion_tokens", "cost_usd", "finish_reason",
                          "generation_id"}


# -- Fehlerklassifikation ---------------------------------------------------
class _Named(Exception):
    """Erzeugt Exceptions mit den Klassennamen des SDK."""


def _sdk_error(name: str) -> Exception:
    return type(name, (_Named,), {})("Testfall")


@pytest.mark.parametrize("name", provider.RETRYABLE_ERRORS)
def test_transient_sdk_errors_are_retryable(name):
    adapter, _ = _provider(error=_sdk_error(name))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.retryable is True
    assert exc.value.findings[0].exit_code is ExitCode.PROVIDER_ERROR
    assert exc.value.findings[0].stage is Stage.PROVIDER


@pytest.mark.parametrize("name,code", [
    ("UnauthorizedResponseError", "PROV_AUTH_FAILED"),
    ("ForbiddenResponseError", "PROV_AUTH_FAILED"),
    ("PaymentRequiredResponseError", "PROV_NO_CREDIT"),
    ("BadRequestResponseError", "PROV_REQUEST_REJECTED"),
    ("UnprocessableEntityResponseError", "PROV_REQUEST_REJECTED"),
    ("PayloadTooLargeResponseError", "PROV_PAYLOAD_TOO_LARGE"),
])
def test_permanent_sdk_errors_are_not_retried(name, code):
    adapter, _ = _provider(error=_sdk_error(name))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.retryable is False
    assert exc.value.findings[0].code == code


def test_unknown_sdk_error_defaults_to_not_retryable():
    """Fail closed: ein unbekannter Fehler wird nicht auf Verdacht wiederholt."""
    adapter, _ = _provider(error=_sdk_error("VoelligNeuerFehler"))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.retryable is False
    assert exc.value.findings[0].code == "PROV_CALL_FAILED"


def test_auth_hint_points_at_the_env_file():
    """Der Key gehoert in .env - nie in die per UI editierbaren Settings."""
    adapter, _ = _provider(error=_sdk_error("UnauthorizedResponseError"))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    hint = exc.value.findings[0].hint
    assert ".env" in hint
    assert "settings.py" not in hint


# -- Fachliche Ausgaenge ----------------------------------------------------
def test_refusal_is_not_retried():
    adapter, _ = _provider(_Result(refusal="Das mache ich nicht."))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.findings[0].code == "PROV_REFUSED"
    assert exc.value.retryable is False


def test_truncated_answer_blocks_instead_of_being_repaired_silently():
    adapter, _ = _provider(_Result(content='{"a": "b', finish_reason="length"))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.findings[0].code == "PROV_TRUNCATED"
    assert exc.value.retryable is False
    assert "MAX_OUTPUT_TOKENS" in exc.value.findings[0].hint


def test_invalid_json_is_retryable_and_names_the_diagnosis():
    adapter, _ = _provider(_Result(content="kein json"))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.findings[0].code == "PROV_INVALID_JSON"
    assert exc.value.retryable is True
    assert "provider check" in exc.value.findings[0].hint


def test_json_array_is_rejected_because_the_contract_demands_an_object():
    adapter, _ = _provider(_Result(content='[{"a": "b"}]'))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.findings[0].code == "PROV_INVALID_JSON"


def test_empty_choices_are_retryable():
    adapter, _ = _provider(_Result(choices=[]))
    with pytest.raises(provider.ProviderCallError) as exc:
        adapter.complete(_request())
    assert exc.value.findings[0].code == "PROV_EMPTY_RESPONSE"
    assert exc.value.retryable is True


# -- create(): Vorbedingungen vor dem ersten Call ---------------------------
def test_create_blocks_without_api_key(monkeypatch):
    monkeypatch.delenv(provider.API_KEY_ENV, raising=False)
    collector = ErrorCollector()
    assert provider.OpenRouterProvider.create(collector, _settings()) is None
    finding = only(collector)
    assert finding.code == "PROV_NO_API_KEY"
    assert finding.exit_code is ExitCode.PREFLIGHT_BLOCKED
    assert ".env" in finding.hint


def test_create_treats_a_blank_key_as_missing(monkeypatch):
    monkeypatch.setenv(provider.API_KEY_ENV, "   ")
    collector = ErrorCollector()
    assert provider.OpenRouterProvider.create(collector, _settings()) is None
    assert only(collector).code == "PROV_NO_API_KEY"


def test_create_runs_the_routing_preflight_before_any_call(monkeypatch):
    """Verbindliche Integrationsanforderung aus P5: erst pruefen, dann zahlen."""
    monkeypatch.setenv(provider.API_KEY_ENV, "sk-test")
    monkeypatch.setattr(provider, "fetch_endpoints", lambda model: {})
    collector = ErrorCollector()
    assert provider.OpenRouterProvider.create(collector, _settings()) is None
    finding = only(collector)
    assert finding.code == "PROV_MODEL_UNKNOWN"
    assert finding.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_create_succeeds_when_key_and_endpoint_are_available(monkeypatch):
    monkeypatch.setenv(provider.API_KEY_ENV, "sk-test")
    monkeypatch.setattr(provider, "fetch_endpoints", lambda model: {"data": {"endpoints": [{
        "tag": "a/fp8", "quantization": "fp8", "context_length": 1_000_000,
        "pricing": {"prompt": "0.00000008", "completion": "0.00000018"},
        "supported_parameters": ["structured_outputs"],
    }]}})
    collector = ErrorCollector()
    adapter = provider.OpenRouterProvider.create(
        collector, _settings(), client=_Client(_Result()))
    assert collector.findings == []
    assert adapter is not None
    assert adapter.complete(_request()).payload == {"a": "b"}


# -- FakeProvider -----------------------------------------------------------
def test_fake_provider_replays_its_script_in_order():
    fake = provider.FakeProvider([{"n": 1}, {"n": 2}])
    assert fake.complete(_request(call_id="a")).payload == {"n": 1}
    assert fake.complete(_request(call_id="b")).payload == {"n": 2}
    assert [c.call_id for c in fake.calls] == ["a", "b"]
    assert fake.remaining == 0


def test_fake_provider_can_raise_a_scripted_failure():
    boom = _call_error()
    fake = provider.FakeProvider([boom, {"n": 2}])
    with pytest.raises(provider.ProviderCallError):
        fake.complete(_request())
    assert fake.complete(_request()).payload == {"n": 2}


def test_fake_provider_refuses_an_unscripted_call():
    fake = provider.FakeProvider([])
    with pytest.raises(AssertionError):
        fake.complete(_request())


def test_fake_provider_satisfies_the_neutral_interface():
    fake = provider.FakeProvider([{"n": 1}])
    response = fake.complete(_request())
    assert isinstance(response, provider.StructuredResponse)
    assert response.cost_usd == 0.0


def _call_error() -> provider.ProviderCallError:
    return provider._call_failure(
        "PROV_CALL_FAILED", "batch-1", "Testfall", "Testfall", retryable=True)


# -- CLI: Contract-Smoke ----------------------------------------------------
@pytest.fixture()
def artifact_sandbox(tmp_path, monkeypatch):
    """Leitet die Artefaktwurzel um, damit CLI-Laeufe nichts ins Repo schreiben."""
    from committee_matcher import paths

    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    return tmp_path


def _catalog_with_one_endpoint(model):
    return {"data": {"endpoints": [{
        "tag": "a/fp8", "quantization": "fp8", "context_length": 1_000_000,
        "pricing": {"prompt": "0.00000008", "completion": "0.00000018"},
        "supported_parameters": ["structured_outputs"],
    }]}}


def test_cli_smoke_reports_routing_and_cost(capsys, artifact_sandbox, monkeypatch):
    from committee_matcher import cli

    monkeypatch.setenv(provider.API_KEY_ENV, "sk-test")
    monkeypatch.setattr(provider, "fetch_endpoints", _catalog_with_one_endpoint)
    monkeypatch.setattr(
        provider.OpenRouterProvider, "_build_client",
        staticmethod(lambda api_key: _Client(_Result(
            content='{"ok": true, "echo": "contract-smoke"}',
            metadata=_Metadata("deepinfra")))))

    assert cli.main(["provider", "smoke"]) == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "Schema eingehalten" in out
    assert "deepinfra" in out
    assert "Kosten 0.000400 USD" in out


def test_cli_smoke_stops_before_the_call_without_a_key(capsys, artifact_sandbox,
                                                       monkeypatch):
    from committee_matcher import cli

    monkeypatch.delenv(provider.API_KEY_ENV, raising=False)

    def _fail(model):  # pragma: no cover - darf nie laufen
        raise AssertionError("ohne Key darf der Katalog nicht abgefragt werden")

    monkeypatch.setattr(provider, "fetch_endpoints", _fail)
    assert cli.main(["provider", "smoke"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "PROV_NO_API_KEY" in out
    assert "nicht ausgefuehrt" in out


def test_cli_smoke_maps_a_call_failure_to_exit_nine(capsys, artifact_sandbox,
                                                    monkeypatch):
    from committee_matcher import cli

    monkeypatch.setenv(provider.API_KEY_ENV, "sk-test")
    monkeypatch.setattr(provider, "fetch_endpoints", _catalog_with_one_endpoint)
    monkeypatch.setattr(
        provider.OpenRouterProvider, "_build_client",
        staticmethod(lambda api_key: _Client(error=_sdk_error("UnauthorizedResponseError"))))

    assert cli.main(["provider", "smoke"]) == int(ExitCode.PROVIDER_ERROR)
    out = capsys.readouterr().out
    assert "PROV_AUTH_FAILED" in out
    assert "fehlgeschlagen" in out
