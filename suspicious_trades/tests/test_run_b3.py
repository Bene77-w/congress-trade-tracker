"""Offline-Tests der B3-Subcommands recommend und ledger-update."""

from __future__ import annotations

import argparse
import json

from suspicious_trades import config, run
from suspicious_trades.recommendations import load_ledger, recommender
from suspicious_trades.recommendations.recommender import RECOMMENDATION_COLUMNS


def _prepare(monkeypatch, b3_settings, price_lookup):
    monkeypatch.setattr(config, "effective", lambda *a, **k: b3_settings)
    monkeypatch.setattr(recommender, "_load_price_lookup", lambda settings: price_lookup)


def _write_input(tmp_path, candidates):
    path = tmp_path / "recs.json"
    path.write_text(json.dumps(candidates), encoding="utf-8")
    return path


def test_recommend_subcommand_writes_schema_valid_csv(
    monkeypatch, tmp_path, b3_settings, price_lookup
):
    _prepare(monkeypatch, b3_settings, price_lookup)
    input_path = _write_input(
        tmp_path,
        [
            {
                "ticker": "AAA",
                "direction": "buy",
                "rationale": "dummy",
                "dossier_path": "outputs/run/bericht.md#aaa",
                "rank": 1,
                "issued_date": "2025-01-01",
            }
        ],
    )
    args = argparse.Namespace(input=str(input_path), today="2025-01-31", run_id="cli1")
    rc = run.recommend_command(args)
    assert rc == 0

    ledger = load_ledger(b3_settings)
    assert list(ledger.columns) == RECOMMENDATION_COLUMNS
    assert len(ledger) == 1
    status_path = b3_settings["DATA_DIR"] / "runs" / "cli1.status.json"
    payload = json.loads(status_path.read_text())
    assert payload["state"] == "done"
    assert payload["steps"][0]["counts"]["recommendations_new"] == 1
    assert payload["steps"][0]["artifacts"] == ["recommendations.csv"]


def test_recommend_missing_input_returns_error(monkeypatch, b3_settings, price_lookup):
    _prepare(monkeypatch, b3_settings, price_lookup)
    args = argparse.Namespace(input="/nonexistent/x.json", today=None, run_id=None)
    assert run.recommend_command(args) == 1


def test_ledger_update_subcommand(
    monkeypatch, tmp_path, b3_settings, price_lookup
):
    _prepare(monkeypatch, b3_settings, price_lookup)
    input_path = _write_input(
        tmp_path,
        [{"ticker": "AAA", "direction": "buy", "issued_date": "2025-01-01"}],
    )
    run.recommend_command(
        argparse.Namespace(input=str(input_path), today="2025-01-11", run_id="cli1")
    )
    args = argparse.Namespace(today="2025-01-31", run_id="cliupd")
    rc = run.ledger_update_command(args)
    assert rc == 0
    status_path = b3_settings["DATA_DIR"] / "runs" / "cliupd.status.json"
    payload = json.loads(status_path.read_text())
    assert payload["steps"][0]["counts"]["tracking_updated"] == 1


def test_parser_registers_b3_subcommands():
    parser = run.build_parser()
    args = parser.parse_args(["recommend", "--input", "x.json"])
    assert args.handler is run.recommend_command
    args = parser.parse_args(["ledger-update"])
    assert args.handler is run.ledger_update_command
