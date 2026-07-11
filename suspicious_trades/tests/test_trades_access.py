"""Offline-Tests des Trades-Zugriffs: Staging-Reader, Fenster, Aggregate."""

from __future__ import annotations

from datetime import date

import pytest

from suspicious_trades import trades_access
from suspicious_trades.trades_access import StagingSchemaError

# Muss zum B3_TODAY der conftest-Fixtures passen.
B3_TODAY = date(2025, 2, 1)


def _staging_row(trade_id: str, **overrides) -> dict:
    row = {
        "trade_id": trade_id,
        "name": "Alice Buyer",
        "ticker": "AAA",
        "transaction_type": "Purchase",
        "transaction_date": "2025-01-05",
        "disclosure_date": "2025-01-06",
        "amount": "15000",
        "direction": "buy",
        "export_ts": "2025-02-01T10:00:00",
    }
    row.update(overrides)
    return row


def test_read_staging_valid(b3_settings, staging_writer):
    directory = trades_access.staged_trades_dir(b3_settings)
    staging_writer(directory, "trade_selection_a.csv", [_staging_row("t1"), _staging_row("t2")])
    frame = trades_access.read_staging_trades(b3_settings)
    assert set(frame["trade_id"]) == {"t1", "t2"}
    assert list(frame["direction"]) == ["buy", "buy"]


def test_read_staging_missing_required_column_aborts(b3_settings, staging_writer):
    directory = trades_access.staged_trades_dir(b3_settings)
    columns = ["trade_id", "name", "ticker", "transaction_type", "transaction_date", "amount"]
    staging_writer(directory, "broken.csv", [_staging_row("t1")], columns=columns)
    with pytest.raises(StagingSchemaError) as excinfo:
        trades_access.read_staging_trades(b3_settings)
    assert "disclosure_date" in str(excinfo.value)


def test_read_staging_derives_missing_direction(b3_settings, staging_writer):
    directory = trades_access.staged_trades_dir(b3_settings)
    rows = [
        _staging_row("t1", transaction_type="Sale (Full)", direction=""),
        _staging_row("t2", transaction_type="Purchase", direction=""),
    ]
    staging_writer(directory, "sel.csv", rows)
    frame = trades_access.read_staging_trades(b3_settings)
    directions = dict(zip(frame["trade_id"], frame["direction"]))
    assert directions == {"t1": "sell", "t2": "buy"}


def test_read_staging_dedup_latest_export_ts_wins(b3_settings, staging_writer):
    directory = trades_access.staged_trades_dir(b3_settings)
    staging_writer(
        directory,
        "old.csv",
        [_staging_row("t1", amount="100", export_ts="2025-02-01T09:00:00")],
    )
    staging_writer(
        directory,
        "new.csv",
        [_staging_row("t1", amount="999", export_ts="2025-02-02T09:00:00")],
    )
    frame = trades_access.read_staging_trades(b3_settings)
    assert len(frame) == 1
    assert frame.iloc[0]["amount"] == "999"


def test_read_staging_empty_dir_returns_empty(b3_settings):
    frame = trades_access.read_staging_trades(b3_settings)
    assert frame.empty


def test_window_mode_selects_by_date_field(b3_settings):
    b3_settings["TRADE_SOURCE"] = "window"
    frame = trades_access.read_window_trades(b3_settings, today=B3_TODAY)
    ids = set(frame["trade_id"])
    assert "t2" in ids  # 2025-01-20 im 14-Tage-Fenster
    assert "t1" not in ids  # 2025-01-05 ausserhalb
    assert "t4" not in ids  # 2024-12-15 ausserhalb


def test_load_trades_dispatch_window(b3_settings):
    b3_settings["TRADE_SOURCE"] = "window"
    frame = trades_access.load_trades(b3_settings, today=B3_TODAY)
    assert "direction" in frame.columns


def test_load_trades_unknown_source(b3_settings):
    b3_settings["TRADE_SOURCE"] = "nonsense"
    with pytest.raises(ValueError):
        trades_access.load_trades(b3_settings)


def test_taxonomy_vocabulary(b3_settings):
    vocab = trades_access.taxonomy_vocabulary(b3_settings)
    assert "Technology" in vocab["sectors"]
    assert "" not in vocab["sectors"]
    assert vocab["sectors"] == sorted(vocab["sectors"])


def test_member_aggregates(b3_settings):
    aggregates = trades_access.member_aggregates(b3_settings)
    alice = aggregates.set_index("name").loc["Alice Buyer"]
    assert alice["buys"] >= 2
    assert alice["trades"] >= alice["buys"] + alice["sells"]
    assert "AAA" in alice["tickers"]
