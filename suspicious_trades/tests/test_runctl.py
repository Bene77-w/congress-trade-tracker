"""runctl-Logik: Status-Listing, Reconnect (PID tot/lebendig), Stop, mark_failed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from suspicious_trades.ui import runctl


@pytest.fixture
def ui_settings(tmp_path: Path) -> dict:
    return {
        "DATA_DIR": tmp_path,
        "RUNS_DIRNAME": "runs",
        "STATUS_POLL_SECONDS": 0.05,
        "STOP_GRACE_SECONDS": 120,
        "UI_LOG_TAIL_LINES": 3,
    }


def _write_status(settings: dict, run_id: str, payload: dict) -> Path:
    path = runctl.status_path(settings, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _status(run_id: str, *, state: str, pid: int, started: str) -> dict:
    return {
        "run_id": run_id,
        "pipeline": "test",
        "pid": pid,
        "state": state,
        "started_at": started,
        "finished_at": None,
        "stop_requested": False,
        "cost_usd_total": 0.0,
        "steps": [
            {"step": "a", "state": "done", "started_at": started, "finished_at": started,
             "error": None, "artifacts": [], "cost_usd": 0.0, "tokens": {}, "counts": {}},
            {"step": "b", "state": "running", "started_at": started, "finished_at": None,
             "error": None, "artifacts": [], "cost_usd": 0.0, "tokens": {}, "counts": {}},
            {"step": "c", "state": "pending", "started_at": None, "finished_at": None,
             "error": None, "artifacts": [], "cost_usd": 0.0, "tokens": {}, "counts": {}},
        ],
    }


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_list_runs_sorted_and_limited(ui_settings):
    _write_status(ui_settings, "r1", _status("r1", state="done", pid=1, started="2026-07-01T10:00:00"))
    _write_status(ui_settings, "r2", _status("r2", state="running", pid=os.getpid(), started="2026-07-02T10:00:00"))
    runs = runctl.list_runs(ui_settings)
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert [r["run_id"] for r in runctl.list_runs(ui_settings, limit=1)] == ["r2"]


def test_pid_alive_true_for_self_false_for_dead():
    assert runctl.pid_alive(os.getpid()) is True
    assert runctl.pid_alive(_dead_pid()) is False
    assert runctl.pid_alive(None) is False


def test_reconnect_info_alive(ui_settings):
    _write_status(
        ui_settings,
        "live",
        _status("live", state="running", pid=os.getpid(), started="2026-07-02T10:00:00"),
    )
    info = runctl.reconnect_info(ui_settings)
    assert info is not None
    assert info["run_id"] == "live"
    assert info["alive"] is True


def test_reconnect_info_dead_offers_mark_failed(ui_settings):
    _write_status(
        ui_settings,
        "tot",
        _status("tot", state="running", pid=_dead_pid(), started="2026-07-02T10:00:00"),
    )
    info = runctl.reconnect_info(ui_settings)
    assert info is not None
    assert info["alive"] is False

    # Kein Auto-Umschreiben: erst die explizite User-Aktion markiert failed.
    assert runctl.load_status(ui_settings, "tot")["state"] == "running"
    assert runctl.mark_failed(ui_settings, "tot") is True
    payload = runctl.load_status(ui_settings, "tot")
    assert payload["state"] == "failed"
    states = {step["step"]: step["state"] for step in payload["steps"]}
    assert states == {"a": "done", "b": "failed", "c": "aborted"}
    assert payload["finished_at"] is not None
    # Zweiter Aufruf ist ein No-Op (nicht mehr running).
    assert runctl.mark_failed(ui_settings, "tot") is False


def test_reconnect_info_none_without_running_run(ui_settings):
    _write_status(ui_settings, "done", _status("done", state="done", pid=1, started="2026-07-01T00:00:00"))
    assert runctl.reconnect_info(ui_settings) is None


def test_request_stop_and_grace(ui_settings):
    path = runctl.request_stop(ui_settings, "r1")
    assert path.exists()
    assert runctl.grace_elapsed(ui_settings, "r1") is False
    # Stop-Datei kuenstlich altern lassen -> Grace-Zeit abgelaufen.
    old = time.time() - 10_000
    os.utime(path, (old, old))
    assert runctl.grace_elapsed(ui_settings, "r1") is True
    # Ohne Stop-Datei keine Grace-Auswertung.
    assert runctl.grace_elapsed(ui_settings, "anders") is False


def test_usage_totals_and_log_tail(ui_settings):
    runs = runctl.runs_dir(ui_settings)
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "r1.usage.jsonl").write_text(
        '{"usd": 0.5, "prompt": 10, "completion": 5, "cache_read": 1, "cache_write": 2}\n'
        '{"usd": 0.25, "prompt": 4, "completion": 2, "cache_read": 0, "cache_write": 0, "estimated": true}\n',
        encoding="utf-8",
    )
    totals = runctl.llm_usage_totals(ui_settings, "r1")
    assert totals["usd"] == pytest.approx(0.75)
    assert totals["calls"] == 2
    assert totals["prompt"] == 14
    assert totals["estimated"] is True

    (runs / "r1.search_usage.jsonl").write_text(
        '{"credits": 2, "usage_reported": true}\n'
        '{"credits": null, "usage_reported": false}\n',
        encoding="utf-8",
    )
    search = runctl.search_usage_totals(ui_settings, "r1")
    assert search["credits"] == pytest.approx(2.0)
    assert search["calls"] == 2
    assert search["unreported"] == 1

    (runs / "r1.log").write_text("z1\nz2\nz3\nz4\nz5\n", encoding="utf-8")
    assert runctl.read_log_tail(ui_settings, "r1") == "z3\nz4\nz5"


def test_collect_live_snapshot(ui_settings):
    _write_status(
        ui_settings,
        "r9",
        _status("r9", state="running", pid=os.getpid(), started="2026-07-02T10:00:00"),
    )
    payload = runctl.collect_live(ui_settings, "r9")
    assert payload["run_id"] == "r9"
    assert payload["status"]["state"] == "running"
    assert payload["llm"]["usd"] == 0.0
    assert payload["search"]["calls"] == 0
