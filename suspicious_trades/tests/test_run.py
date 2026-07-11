"""Offline CLI and smoke orchestration tests."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from types import SimpleNamespace

from suspicious_trades import run


def _settings(tmp_path):
    return {
        "SMOKE_MODELS": ["deepseek/test", "openai/test"],
        "AGENT_SPECS": {},
        "SMOKE_MAX_OUTPUT_TOKENS": 16,
        "CACHE_CHECK_MAX_OUTPUT_TOKENS": 16,
        "CACHE_CHECK_MODEL": "anthropic/test",
        "CACHE_CHECK_SYSTEM_TOKENS": 5000,
        "CACHE_CHECK_TOOL_TURNS": 3,
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
    args = run.build_parser().parse_args(["smoke", "--cache-check"])
    assert args.command == "smoke"
    assert args.cache_check is True
    assert args.handler is run.smoke_command


def test_smoke_missing_keys_are_skips_not_failures(monkeypatch, tmp_path, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(run.config, "effective", lambda: settings)
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    exit_code = run.smoke_command(argparse.Namespace(cache_check=False))
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[SKIP] deepseek/test" in output
    assert "[SKIP] openai/test" in output
    assert "[SKIP] tavily" in output
    assert "Offene B1-Live-Abnahmepunkte" in output


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
    assert run.smoke_command(argparse.Namespace(cache_check=False)) == 1


def test_cache_check_requires_hits_in_identical_call_and_tool_loop(
    monkeypatch, tmp_path
):
    settings = _settings(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    ledger = SimpleNamespace(records=[], activate=lambda: nullcontext())
    monkeypatch.setattr(run, "UsageLog", lambda *args, **kwargs: ledger)
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
        ledger.records.append({"cache_read": 5000})
        assert kwargs["run_ctx"].spec_override["model"] == "anthropic/test"
        return SimpleNamespace(turns=3)

    monkeypatch.setattr(run, "complete", fake_complete)
    monkeypatch.setattr(run, "call_with_tools", fake_tool_loop)
    result = run._cache_check(settings, "cache-run")
    assert result.status == "PASS"
    assert "identical_cache_read=100" in result.detail
    assert len(calls) == 2
