"""Offline-Tests fuer Defaults und config.effective()."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from suspicious_trades import config


def _write_overrides(tmp_path, monkeypatch, payload) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "config_overrides.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_data_dir_is_derived_from_top_level_package() -> None:
    assert config.PACKAGE_ROOT == Path(config.__file__).resolve().parent
    assert config.PROJECT_ROOT == config.PACKAGE_ROOT.parent
    assert config.DATA_DIR == config.PROJECT_ROOT / "data" / "suspicious_trades"


def test_effective_deep_merges_dicts(tmp_path, monkeypatch) -> None:
    _write_overrides(
        tmp_path,
        monkeypatch,
        {
            "SEARCH_API": {
                "map": {"limit": 12},
                "crawl": {"max_depth": 1},
            },
            "TAVILY": {"credit_reserve": 350},
        },
    )

    effective = config.effective(reload=True)

    assert effective["SEARCH_API"]["map"] == {
        "max_depth": 2,
        "max_breadth": 50,
        "limit": 12,
    }
    assert effective["SEARCH_API"]["crawl"]["max_depth"] == 1
    assert effective["SEARCH_API"]["crawl"]["limit"] == 30
    assert effective["TAVILY"]["credit_reserve"] == 350
    assert effective["TAVILY"]["monthly_credit_budget"] == 4000


def test_effective_replaces_lists_wholly(tmp_path, monkeypatch) -> None:
    _write_overrides(
        tmp_path,
        monkeypatch,
        {"FEDREG_DOC_TYPES": ["RULE"]},
    )

    effective = config.effective(reload=True)

    assert effective["FEDREG_DOC_TYPES"] == ["RULE"]


def test_unknown_keys_and_type_conflicts_warn_and_are_ignored(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    _write_overrides(
        tmp_path,
        monkeypatch,
        {
            "DOES_NOT_EXIST": True,
            "SEARCH_API": {
                "max_results": "many",
                "map": {"unknown_cap": 5},
            },
        },
    )

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        effective = config.effective(reload=True)

    assert "DOES_NOT_EXIST" not in effective
    assert effective["SEARCH_API"]["max_results"] == 15
    assert "unknown_cap" not in effective["SEARCH_API"]["map"]
    assert "unknown top-level key" in caplog.text
    assert "type conflict" in caplog.text
    assert "SEARCH_API.map.unknown_cap" in caplog.text


def test_unknown_agent_role_warns_and_is_ignored(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    _write_overrides(
        tmp_path,
        monkeypatch,
        {
            "AGENT_SPECS": {
                "invented_role": {
                    "model": "anthropic/claude-opus-4-8",
                }
            }
        },
    )

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        effective = config.effective(reload=True)

    assert effective["AGENT_SPECS"] == {}
    assert "unknown AGENT_SPECS role" in caplog.text
    assert "invented_role" in caplog.text


def test_unpriced_model_is_allowed_with_warning(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(
        config,
        "AGENT_SPECS",
        {
            "researcher": {
                "model": "anthropic/claude-opus-4-8",
                "effort": "high",
            }
        },
    )
    _write_overrides(
        tmp_path,
        monkeypatch,
        {"AGENT_SPECS": {"researcher": {"model": "custom/acme-model"}}},
    )

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        effective = config.effective(reload=True)

    assert effective["AGENT_SPECS"]["researcher"]["model"] == "custom/acme-model"
    assert "has no PRICING entry" in caplog.text
    assert "model remains enabled" in caplog.text


def test_effective_is_memoized_until_explicit_reload(tmp_path, monkeypatch) -> None:
    _write_overrides(tmp_path, monkeypatch, {"TRADE_WINDOW_DAYS": 21})
    first = config.effective(reload=True)

    (tmp_path / "config_overrides.json").write_text(
        json.dumps({"TRADE_WINDOW_DAYS": 42}),
        encoding="utf-8",
    )
    memoized = config.effective()
    reloaded = config.effective(reload=True)

    assert first["TRADE_WINDOW_DAYS"] == 21
    assert memoized["TRADE_WINDOW_DAYS"] == 21
    assert reloaded["TRADE_WINDOW_DAYS"] == 42


def test_callers_cannot_mutate_the_effective_memo(tmp_path, monkeypatch) -> None:
    _write_overrides(tmp_path, monkeypatch, {})
    first = config.effective(reload=True)
    first["SEARCH_API"]["map"]["limit"] = 1

    second = config.effective()

    assert second["SEARCH_API"]["map"]["limit"] == 100


def test_invalid_override_document_warns_and_uses_defaults(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "config_overrides.json").write_text("[]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        effective = config.effective(reload=True)

    assert effective["TRADE_WINDOW_DAYS"] == config.TRADE_WINDOW_DAYS
    assert "top level must be an object" in caplog.text


def test_grok_pricing_is_authoritative_not_estimated() -> None:
    assert config.PRICING["xai/grok-4.5"] == {
        "in": 2.0,
        "out": 6.0,
        "cache_read": 0.5,
        "cache_write": 2.0,
    }
