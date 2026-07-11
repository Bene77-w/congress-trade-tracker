"""Offline CLI and smoke orchestration tests."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from suspicious_trades import run


def _settings(tmp_path):
    return {
        "SMOKE_MODELS": ["deepseek/test", "openai/test"],
        "AGENT_SPECS": {},
        "SMOKE_MAX_OUTPUT_TOKENS": 16,
        "CACHE_CHECK_MAX_OUTPUT_TOKENS": 16,
        "CACHE_CHECK_TOOL_MAX_OUTPUT_TOKENS": 128,
        "CACHE_CHECK_MODEL": "anthropic/test",
        "CACHE_CHECK_SYSTEM_TOKENS": 5000,
        "CACHE_CHECK_PROVIDER_TO_LOCAL_TOKEN_RATIO": 2.0,
        "CACHE_CHECK_REQUIRED_TOOL_TURNS": 2,
        "SMOKE_SEARCH_QUERY": "Congress",
        "SMOKE_SEARCH_MAX_RESULTS": 1,
        "SMOKE_SEARCH_LOOKBACK_DAYS": 1,
        "SEARCH_API": {"depth": "basic"},
        "PRICING": {
            "deepseek/test": {
                "in": 1.0,
                "out": 1.0,
                "cache_read": 0.1,
                "cache_write": 1.0,
            },
            "openai/test": {
                "in": 1.0,
                "out": 1.0,
                "cache_read": 0.1,
                "cache_write": 1.0,
            },
        },
        "COST_CAP_ENABLED": True,
        "COST_CAP_USD_PER_RUN": 20.0,
        "DATA_DIR": tmp_path,
    }


def test_parser_registers_only_b1_smoke():
    args = run.build_parser().parse_args(
        ["smoke", "--cache-check", "--confirm-live-costs"]
    )
    assert args.command == "smoke"
    assert args.cache_check is True
    assert args.confirm_live_costs is True
    assert args.handler is run.smoke_command


def test_smoke_missing_keys_are_skips_not_failures(monkeypatch, tmp_path, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(run.config, "effective", lambda: settings)
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    exit_code = run.smoke_command(
        argparse.Namespace(cache_check=False, confirm_live_costs=True)
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[SKIP] deepseek/test" in output
    assert "[SKIP] openai/test" in output
    assert "[SKIP] tavily" in output
    assert "Offene B1-Live-Abnahmepunkte" in output
    assert f"Keys eintragen in: {run.config.PACKAGE_ROOT / '.env'}" in output


def test_model_smoke_calls_each_unique_available_model(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings["SMOKE_MODELS"].append("deepseek/test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(
        run,
        "complete",
        lambda role, messages, spec_override: calls.append(spec_override["model"])
        or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        ),
    )
    ledger = run.UsageLog("models", runs_dir=tmp_path, settings=settings)
    results = run._smoke_models(settings, ledger)
    assert calls == ["deepseek/test"]
    assert [(result.check, result.status) for result in results] == [
        ("deepseek/test", "PASS"),
        ("openai/test", "SKIP"),
    ]


def test_failed_model_smoke_reports_key_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings["SMOKE_MODELS"] = ["deepseek/test"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")

    def _raise(role, messages, spec_override):
        raise RuntimeError("boom")

    monkeypatch.setattr(run, "complete", _raise)
    ledger = run.UsageLog("models", runs_dir=tmp_path, settings=settings)
    results = run._smoke_models(settings, ledger)
    assert results[0].status == "FAIL"
    assert "key_source=" in results[0].detail


def test_tavily_smoke_calls_search_and_usage(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    seen = []

    class FakeTavily:
        def __init__(self, **kwargs):
            seen.append(("init", kwargs))

        def search(self, query, **kwargs):
            seen.append(("search", query, kwargs))
            return {"results": [{"url": "https://example.test"}]}

        def get_usage(self):
            seen.append(("usage",))
            return {"account": {"plan_usage": 1, "plan_limit": 100}}

    monkeypatch.setattr(run, "TavilySearchAPI", FakeTavily)
    result = run._smoke_tavily(settings, "run-1")
    assert result.status == "PASS"
    assert [row[0] for row in seen] == ["init", "search", "usage"]
    search_kwargs = seen[1][2]
    assert search_kwargs["max_results"] == 1
    assert "start_date" in search_kwargs and "end_date" in search_kwargs


def test_any_live_failure_makes_cli_nonzero(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(run.config, "effective", lambda: settings)
    monkeypatch.setattr(
        run,
        "_smoke_models",
        lambda settings, ledger: [run.SmokeResult("model", "FAIL", "boom")],
    )
    monkeypatch.setattr(
        run,
        "_smoke_tavily",
        lambda settings, run_id: run.SmokeResult("tavily", "SKIP", "key"),
    )
    assert run.smoke_command(
        argparse.Namespace(cache_check=False, confirm_live_costs=True)
    ) == 1


def test_live_smoke_requires_explicit_cost_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(
        run.config,
        "effective",
        lambda: pytest.fail("config/provider path must not start"),
    )
    assert run.smoke_command(argparse.Namespace(cache_check=True)) == 2
    assert "--confirm-live-costs" in capsys.readouterr().out


def test_access_blocked_is_visible_but_not_a_code_failure(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(run.config, "effective", lambda: settings)
    monkeypatch.setattr(
        run,
        "_smoke_models",
        lambda settings, ledger: [
            run.SmokeResult("gemini/test", "ACCESS_BLOCKED", "quota")
        ],
    )
    monkeypatch.setattr(
        run,
        "_smoke_tavily",
        lambda settings, run_id: run.SmokeResult("tavily", "PASS", "ok"),
    )
    assert run.smoke_command(
        argparse.Namespace(cache_check=False, confirm_live_costs=True)
    ) == 0


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (type("AuthenticationError", (Exception,), {})("bad"), "AUTH_FAIL"),
        (type("NotFoundError", (Exception,), {})("model not found"), "MODEL_CONFIG_FAIL"),
        (
            type("RateLimitError", (Exception,), {})
            ("RESOURCE_EXHAUSTED: quota exceeded; limit: 0"),
            "ACCESS_BLOCKED",
        ),
        (type("RateLimitError", (Exception,), {})("retry in 10 seconds"), "FAIL"),
        (
            type("PermissionDeniedError", (Exception,), {})
            ("model is not available in your region"),
            "ACCESS_BLOCKED",
        ),
        (type("Timeout", (Exception,), {})("timed out"), "FAIL"),
    ],
)
def test_provider_exception_classification(exc, expected):
    assert run._classify_exception(exc) == expected


def test_placeholder_key_is_auth_failure_without_provider_call(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings["SMOKE_MODELS"] = ["deepseek/test"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "<your-key>")
    monkeypatch.setattr(
        run,
        "complete",
        lambda *args, **kwargs: pytest.fail("provider must not be called"),
    )
    ledger = run.UsageLog("placeholder", runs_dir=tmp_path, settings=settings)
    result = run._smoke_models(settings, ledger)[0]
    assert result.status == "AUTH_FAIL"
    assert "<your-key>" not in result.detail


def test_cache_check_requires_hits_in_identical_call_and_tool_loop(
    monkeypatch, tmp_path
):
    settings = _settings(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    ledger = SimpleNamespace(records=[], activate=lambda: nullcontext())
    monkeypatch.setattr(run, "UsageLog", lambda *args, **kwargs: ledger)
    monkeypatch.setattr(
        run,
        "_cache_system_prompt",
        lambda model, target, ratio: "stable",
    )
    calls = []

    def fake_complete(role, messages, spec_override):
        calls.append((role, spec_override))
        cached = 0 if len(calls) == 1 else 100
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=5100,
                completion_tokens=1,
                cache_read_input_tokens=cached,
                cache_creation_input_tokens=0,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            )
        )

    def fake_tool_loop(role, messages, tools, executor, **kwargs):
        assert executor("cache_probe", {"step": 1}).startswith("ACK")
        assert executor("cache_probe", {"step": 2}).startswith("ACK")
        ledger.records.append({"cache_read": 5000})
        assert kwargs["run_ctx"].spec_override["model"] == "anthropic/test"
        assert kwargs["run_ctx"].spec_override["max_output_tokens"] == 128
        assert kwargs["max_turns"] == 2
        assert kwargs["required_tool_turns"] == 2
        return SimpleNamespace(turns=3, final_text="CACHE_OK")

    monkeypatch.setattr(run, "complete", fake_complete)
    monkeypatch.setattr(run, "call_with_tools", fake_tool_loop)
    result = run._cache_check(settings, "cache-run")
    assert result.status == "PASS"
    assert "identical_cache_read=100" in result.detail
    assert len(calls) == 2
