"""Offline-Tests des Live-Status-Writers, der Stop-Mechanik und des Treibers."""

from __future__ import annotations

import json

from suspicious_trades import status as status_mod
from suspicious_trades.status import RunStatus, StepOutcome, run_pipeline


def _make_status(b3_settings, steps=("ingest", "recommend")) -> RunStatus:
    return RunStatus("run-1", "weekly", list(steps), settings=b3_settings)


def test_prefill_pending_and_atomic_file(b3_settings):
    status = _make_status(b3_settings)
    assert status.path.is_file()
    payload = json.loads(status.path.read_text())
    assert payload["state"] == "running"
    assert payload["stop_requested"] is False
    assert [s["step"] for s in payload["steps"]] == ["ingest", "recommend"]
    assert all(s["state"] == "pending" for s in payload["steps"])
    assert payload["variant"] == status.settings["DATA_DIR"].name


def test_step_transitions_and_counts(b3_settings):
    status = _make_status(b3_settings, steps=("ingest",))
    status.start_step("ingest")
    status.finish_step(
        "ingest",
        artifacts=["events.jsonl"],
        cost_usd=1.25,
        tokens={"prompt": 10, "completion": 5},
        counts={"candidates_total": 42},
    )
    status.finish_run("done")
    payload = json.loads(status.path.read_text())
    step = payload["steps"][0]
    assert step["state"] == "done"
    assert step["artifacts"] == ["events.jsonl"]
    assert step["counts"] == {"candidates_total": 42}
    assert step["tokens"]["prompt"] == 10
    assert payload["cost_usd_total"] == 1.25
    assert payload["state"] == "done"
    assert payload["finished_at"] is not None


def test_run_pipeline_done(b3_settings):
    status = _make_status(b3_settings, steps=("a", "b"))
    calls: list[str] = []

    def make(name):
        def _fn():
            calls.append(name)
            return StepOutcome(counts={"done": name})
        return _fn

    final = run_pipeline(status, [("a", make("a")), ("b", make("b"))])
    assert final == "done"
    assert calls == ["a", "b"]
    payload = json.loads(status.path.read_text())
    assert all(s["state"] == "done" for s in payload["steps"])


def test_stop_file_aborts_between_steps(b3_settings):
    status = _make_status(b3_settings, steps=("a", "b"))
    ran: list[str] = []

    def step_a():
        ran.append("a")
        status.request_stop()  # Stop wird zwischen den Steps sichtbar
        return StepOutcome()

    def step_b():
        ran.append("b")
        return StepOutcome()

    final = run_pipeline(status, [("a", step_a), ("b", step_b)])
    assert final == "aborted"
    assert ran == ["a"]  # b lief nie
    payload = json.loads(status.path.read_text())
    assert payload["state"] == "aborted"
    assert payload["stop_requested"] is True
    states = {s["step"]: s["state"] for s in payload["steps"]}
    assert states["a"] == "done"
    assert states["b"] == "aborted"


def test_run_pipeline_failure(b3_settings):
    status = _make_status(b3_settings, steps=("a",))

    def boom():
        raise RuntimeError("kaputt")

    final = run_pipeline(status, [("a", boom)])
    assert final == "failed"
    payload = json.loads(status.path.read_text())
    assert payload["state"] == "failed"
    assert payload["steps"][0]["state"] == "failed"
    assert "kaputt" in payload["steps"][0]["error"]


def test_run_context_shares_stop_path(b3_settings):
    status = _make_status(b3_settings, steps=("a",))
    context = status.make_run_context()
    assert context.stop_path == status.stop_path
    assert context.check_stop() is False
    status.request_stop()
    assert context.check_stop() is True
