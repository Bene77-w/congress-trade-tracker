"""Complete offline contract tests for the shared LiteLLM layer."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from suspicious_trades.llm import client, tool_loop
from suspicious_trades.llm.client import (
    LLMValidationError,
    MissingAPIKeyError,
    call_json,
    complete,
    inject_cache_markers,
)
from suspicious_trades.llm.tool_loop import RunContext, StopRequested, call_with_tools
from suspicious_trades.llm.usage import (
    CostCapExceeded,
    UsageLog,
    UsageRecord,
    calculate_cost,
    extract_usage,
)
from suspicious_trades.tests.conftest import make_response, make_tool_call, make_usage


def _patch_settings(monkeypatch, settings):
    monkeypatch.setattr(client.config, "effective", lambda: settings)


def test_complete_maps_effort_and_cache_without_mutating_messages(
    monkeypatch, llm_settings
):
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []
    monkeypatch.setattr(
        client.litellm,
        "completion",
        lambda **kwargs: calls.append(kwargs) or make_response(),
    )
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "dynamic"},
    ]
    original = copy.deepcopy(messages)

    complete("worker", messages)

    assert messages == original
    assert calls[0]["model"] == "anthropic/test-model"
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[0]["num_retries"] == 2
    assert calls[0]["max_tokens"] == 128
    marked = [
        block
        for message in calls[0]["messages"]
        for block in message["content"]
        if block.get("cache_control")
    ]
    assert len(marked) == 2


def test_none_effort_and_disabled_cache_are_omitted(monkeypatch, llm_settings):
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    calls = []
    monkeypatch.setattr(
        client.litellm,
        "completion",
        lambda **kwargs: calls.append(kwargs) or make_response(),
    )
    messages = [{"role": "user", "content": "hello"}]
    complete("plain", messages)
    assert "reasoning_effort" not in calls[0]
    assert calls[0]["messages"] == messages


def test_moving_cache_marker_replaces_old_history_markers():
    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "s", "cache_control": {"type": "old"}}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "u", "cache_control": {"type": "old"}}],
        },
        {"role": "assistant", "content": "new"},
    ]
    marked = inject_cache_markers(messages)
    locations = [
        (index, block["cache_control"])
        for index, message in enumerate(marked)
        for block in message["content"]
        if "cache_control" in block
    ]
    assert locations == [
        (0, {"type": "ephemeral"}),
        (2, {"type": "ephemeral"}),
    ]
    assert len(locations) <= 4


def test_missing_api_key_is_clear(monkeypatch, llm_settings):
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
        complete("worker", [{"role": "user", "content": "x"}])


def test_transient_primary_failure_uses_one_openrouter_fallback(
    monkeypatch, llm_settings
):
    llm_settings["LLM_FALLBACK_ENABLED"] = True
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    calls = []

    class Timeout(Exception):
        pass

    def fake_completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise Timeout("temporary")
        return make_response()

    monkeypatch.setattr(client.litellm, "completion", fake_completion)
    complete("worker", [{"role": "system", "content": "x"}])
    assert [call["model"] for call in calls] == [
        "anthropic/test-model",
        "openrouter/anthropic/test-model",
    ]
    assert calls[1]["num_retries"] == 0
    assert not any(
        block.get("cache_control")
        for message in calls[1]["messages"]
        for block in message.get("content", [])
        if isinstance(block, dict)
    )


def test_usage_normalizes_anthropic_and_openai_cache_fields():
    anthropic = make_response(
        usage=make_usage(prompt=100, completion=20, cache_read=40, cache_write=10)
    )
    openai = make_response(
        usage=make_usage(prompt=80, completion=9, openai_cached=50)
    )
    assert extract_usage(anthropic) == UsageRecord(100, 20, 40, 10)
    assert extract_usage(openai) == UsageRecord(80, 9, 50, 0)


def test_cost_formula_matches_hand_calculation():
    tokens = UsageRecord(prompt=100, completion=20, cache_read=40, cache_write=10)
    prices = {"in": 5.0, "out": 25.0, "cache_read": 0.5, "cache_write": 6.25}
    expected = (50 * 5.0 + 20 * 25.0 + 40 * 0.5 + 10 * 6.25) / 1_000_000
    assert calculate_cost(tokens, prices) == pytest.approx(expected)


def test_usage_log_writes_jsonl_summary_and_enforces_cap(tmp_path, llm_settings):
    llm_settings["COST_CAP_USD_PER_RUN"] = 0.001
    llm_settings["PRICING"]["anthropic/test-model"] = {
        "in": 10.0,
        "out": 10.0,
        "cache_read": 10.0,
        "cache_write": 10.0,
    }
    ledger = UsageLog("run-1", runs_dir=tmp_path, settings=llm_settings)
    response = make_response(usage=make_usage(prompt=50, completion=50))
    ledger.record("worker", "anthropic/test-model", response)
    assert ledger.path.exists()
    assert json.loads(ledger.path.read_text().splitlines()[0])["turn"] is None
    assert json.loads(ledger.summary_path.read_text())["cost_usd_total"] == pytest.approx(
        0.001
    )
    with pytest.raises(CostCapExceeded):
        ledger.check_cap("worker")


def test_complete_checks_cost_cap_before_provider_call(
    monkeypatch, tmp_path, llm_settings
):
    llm_settings["COST_CAP_USD_PER_RUN"] = 0.0
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    provider_called = False

    def provider(**kwargs):
        nonlocal provider_called
        provider_called = True
        return make_response()

    monkeypatch.setattr(client.litellm, "completion", provider)
    ledger = UsageLog("cap", runs_dir=tmp_path, settings=llm_settings)
    with ledger.activate(), pytest.raises(CostCapExceeded):
        complete("worker", [{"role": "user", "content": "x"}])
    assert provider_called is False


def test_call_json_repairs_invalid_json_and_schema(monkeypatch, llm_settings):
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    responses = iter(
        [
            make_response("not-json"),
            make_response('{"name": 3}'),
            make_response('{"name": "valid"}'),
        ]
    )
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(client.litellm, "completion", provider)
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    assert call_json("worker", [{"role": "system", "content": "json"}], schema) == {
        "name": "valid"
    }
    assert len(calls) == 3
    assert all(call["response_format"] == {"type": "json_object"} for call in calls)


def test_call_json_raises_after_repairs(monkeypatch, llm_settings):
    _patch_settings(monkeypatch, llm_settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        client.litellm, "completion", lambda **kwargs: make_response("bad")
    )
    with pytest.raises(LLMValidationError):
        call_json("worker", [], {"type": "object"}, max_repair_attempts=1)


def test_tool_loop_executes_tools_and_finishes(monkeypatch, tool_call_factory):
    responses = iter(
        [
            make_response(
                tool_calls=[tool_call_factory("c1", "lookup", {"q": "x"})],
                finish_reason="tool_calls",
            ),
            make_response("final"),
        ]
    )
    monkeypatch.setattr(tool_loop, "complete", lambda *args, **kwargs: next(responses))
    seen = []
    context = RunContext("tools", stop_path=None)
    context.stop_path = SimpleNamespace(exists=lambda: False)
    result = call_with_tools(
        "worker",
        [{"role": "user", "content": "go"}],
        [{"type": "function", "function": {"name": "lookup"}}],
        lambda name, args: seen.append((name, args)) or "result",
        max_turns=3,
        run_ctx=context,
    )
    assert result.final_text == "final"
    assert result.turns == 2
    assert seen == [("lookup", {"q": "x"})]
    assert any(message["role"] == "tool" for message in result.messages)


def test_tool_loop_turn_cap_forces_no_tools_completion(monkeypatch):
    calls = []

    def provider(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            return make_response(
                tool_calls=[make_tool_call(f"c{len(calls)}", "noop", {})],
                finish_reason="tool_calls",
            )
        assert kwargs.get("tools") is None
        return make_response("forced final")

    monkeypatch.setattr(tool_loop, "complete", provider)
    context = SimpleNamespace(run_id="turns", usage_log=None, check_stop=lambda: False)
    result = call_with_tools(
        "worker",
        [{"role": "user", "content": "go"}],
        [],
        lambda name, args: "ok",
        max_turns=2,
        run_ctx=context,
    )
    assert result.final_text == "forced final"
    assert result.turns == 3
    assert len(calls) == 3


def test_tool_loop_checks_stop_before_every_call(monkeypatch):
    monkeypatch.setattr(
        tool_loop,
        "complete",
        lambda *args, **kwargs: pytest.fail("provider must not be called"),
    )
    context = SimpleNamespace(run_id="stop", usage_log=None, check_stop=lambda: True)
    with pytest.raises(StopRequested):
        call_with_tools(
            "worker", [], [], lambda name, args: "", max_turns=2, run_ctx=context
        )
