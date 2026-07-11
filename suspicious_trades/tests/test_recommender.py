"""Offline-Tests des Empfehlungs-Ledgers und Mark-to-Market."""

from __future__ import annotations

from datetime import date
import math

from suspicious_trades.recommendations import recommender
from suspicious_trades.recommendations.recommender import (
    RECOMMENDATION_COLUMNS,
    add_recommendations,
    compute_eval_until,
    load_ledger,
    update_tracking,
)

ISSUE = date(2025, 1, 1)
NOW = date(2025, 1, 31)  # 30 Tage nach Ausgabe (AAA-Close 130, CCC-Close 65)


def _candidate(**overrides) -> dict:
    row = {
        "ticker": "AAA",
        "direction": "buy",
        "rationale": "test",
        "dossier_path": "outputs/run/bericht.md#aaa",
        "rank": 1,
        "issued_date": ISSUE.isoformat(),
    }
    row.update(overrides)
    return row


def test_add_recommendation_gold_buy(b3_settings, price_source):
    new = add_recommendations(
        b3_settings, [_candidate()], today=NOW, run_id="r1", price_source=price_source
    )
    row = new.iloc[0]
    assert list(new.columns) == RECOMMENDATION_COLUMNS
    assert row["ref_price_at_issue"] == 100.0
    assert math.isclose(row["hypothetical_return_pct"], 30.0)
    assert row["eval_status"] == "tracking"
    assert row["price_as_of"] == "2025-01-31"
    assert row["rec_id"] == "rec-r1-001"


def test_add_recommendation_sell_sign(b3_settings, price_source):
    new = add_recommendations(
        b3_settings,
        [_candidate(ticker="CCC", direction="sell")],
        today=NOW,
        price_source=price_source,
    )
    row = new.iloc[0]
    assert row["ref_price_at_issue"] == 50.0
    assert math.isclose(row["hypothetical_return_pct"], -30.0)


def test_add_recommendation_price_missing(b3_settings, price_source):
    new = add_recommendations(
        b3_settings, [_candidate(ticker="DDD")], today=NOW, price_source=price_source
    )
    row = new.iloc[0]
    assert row["eval_status"] == "price_missing"
    assert math.isnan(row["hypothetical_return_pct"])
    assert row["price_as_of"] == ""


def test_freezing_after_eval_until(b3_settings, price_source):
    b3_settings["EVAL_MODE"] = "horizon"
    b3_settings["EVAL_HORIZON_DAYS"] = 10  # eval_until = 2025-01-11 (Close 110)
    new = add_recommendations(
        b3_settings, [_candidate()], today=NOW, price_source=price_source
    )
    row = new.iloc[0]
    assert row["eval_until"] == "2025-01-11"
    assert row["eval_status"] == "final"
    assert math.isclose(row["hypothetical_return_pct"], 10.0)
    assert row["price_as_of"] == "2025-01-11"


def test_compute_eval_until_modes():
    horizon_settings = {"EVAL_MODE": "horizon", "EVAL_HORIZON_DAYS": 30, "EVAL_MAX_DAYS": 180}
    assert compute_eval_until(ISSUE, horizon_settings) == date(2025, 1, 31)

    outcome_settings = {"EVAL_MODE": "outcome", "EVAL_OUTCOME_BUFFER_DAYS": 5, "EVAL_MAX_DAYS": 180}
    assert compute_eval_until(
        ISSUE, outcome_settings, event_outcome_date=date(2025, 3, 1)
    ) == date(2025, 3, 6)
    # Ohne Outcome greift die harte Obergrenze.
    assert compute_eval_until(ISSUE, outcome_settings) == date(2025, 6, 30)
    # Outcome + Buffer wird bei der Obergrenze gedeckelt.
    capped = {"EVAL_MODE": "outcome", "EVAL_OUTCOME_BUFFER_DAYS": 5, "EVAL_MAX_DAYS": 10}
    assert compute_eval_until(
        ISSUE, capped, event_outcome_date=date(2025, 12, 1)
    ) == date(2025, 1, 11)


def test_update_tracking_only_tracking_rows(b3_settings, price_source):
    add_recommendations(
        b3_settings,
        [_candidate(), _candidate(ticker="DDD")],
        today=date(2025, 1, 11),  # AAA-Close 110 -> 10%
        run_id="r1",
        price_source=price_source,
    )
    updated = update_tracking(b3_settings, today=NOW, price_source=price_source)
    assert updated == 1  # nur die AAA-tracking-Zeile, DDD bleibt price_missing
    ledger = load_ledger(b3_settings)
    aaa = ledger[ledger["ticker"] == "AAA"].iloc[0]
    assert math.isclose(aaa["hypothetical_return_pct"], 30.0)
    ddd = ledger[ledger["ticker"] == "DDD"].iloc[0]
    assert ddd["eval_status"] == "price_missing"


def test_add_recommendations_persists_schema(b3_settings, price_source):
    add_recommendations(
        b3_settings, [_candidate()], today=NOW, run_id="r1", price_source=price_source
    )
    ledger = load_ledger(b3_settings)
    assert list(ledger.columns) == RECOMMENDATION_COLUMNS
    assert recommender.recommendations_path(b3_settings).is_file()


def test_load_price_lookup_is_monkeypatch_seam(b3_settings, price_lookup, monkeypatch):
    monkeypatch.setattr(recommender, "_load_price_lookup", lambda settings: price_lookup)
    new = add_recommendations(b3_settings, [_candidate()], today=NOW, run_id="r1")
    assert new.iloc[0]["ref_price_at_issue"] == 100.0
