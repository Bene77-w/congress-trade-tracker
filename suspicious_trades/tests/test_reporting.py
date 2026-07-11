"""Offline-Tests des Output-Kontrakts (Persistenz + Budget-Check)."""

from __future__ import annotations

from suspicious_trades.reporting import contract
from suspicious_trades.reporting.contract import persist_outputs


def test_persist_outputs_writes_both_docs(b3_settings):
    result = persist_outputs(
        b3_settings,
        "run-1",
        recommendation_md="# Ranking\n1. AAA buy",
        report_md="# Bericht\nKausalitaet ...",
    )
    assert result.recommendation_path.is_file()
    assert result.report_path.is_file()
    assert result.recommendation_path.name == "empfehlung.md"
    assert result.report_path.name == "bericht.md"
    assert result.recommendation_path.read_text().startswith("# Ranking")
    assert result.recommendation_budget.within_budget is True


def test_budget_flag_when_over_limit(b3_settings):
    b3_settings["SUMMARY_MAX_TOKENS"] = 3
    result = persist_outputs(
        b3_settings,
        "run-2",
        recommendation_md="word " * 50,
        report_md="ok",
    )
    assert result.recommendation_budget.within_budget is False
    assert result.recommendation_budget.tokens > result.recommendation_budget.limit


def test_count_tokens_heuristic():
    assert contract.count_tokens("") == 0
    assert contract.count_tokens("one two three") >= 3


def test_outputs_dir_layout(b3_settings):
    directory = contract.outputs_dir(b3_settings, "run-x")
    assert directory.name == "run-x"
    assert directory.parent.name == "outputs"
