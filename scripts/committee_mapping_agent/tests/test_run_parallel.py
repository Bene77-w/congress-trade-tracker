"""Tests: paralleler Task-Runner des CLI-Orchestrators (_run_parallel)."""
import argparse
import json
import threading
import time

import pytest

from scripts.committee_mapping_agent import config, llm_client, review, run, taxonomy


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch):
    # _run_parallel waermt den Client-Singleton vor -> im Test stubben.
    monkeypatch.setattr(llm_client, "get_client", lambda: object())


def test_all_tasks_executed_and_no_failures():
    done = []
    lock = threading.Lock()

    def worker(ch, comm, unit):
        with lock:
            done.append((ch, comm, unit))

    tasks = [("House", "Science", f"unit{i}") for i in range(10)]
    failures = run._run_parallel("Map", tasks, worker, max_workers=4)
    assert failures == []
    assert sorted(done) == sorted(tasks)


def test_single_failure_does_not_abort_others():
    done = []
    lock = threading.Lock()

    def worker(ch, comm, unit):
        if unit == "unit3":
            raise RuntimeError("kaputt")
        with lock:
            done.append(unit)

    tasks = [("House", "Science", f"unit{i}") for i in range(6)]
    failures = run._run_parallel("Map", tasks, worker, max_workers=3)
    assert len(failures) == 1
    assert failures[0][0][2] == "unit3"
    assert "kaputt" in failures[0][1]
    assert len(done) == 5  # alle uebrigen liefen durch


def test_tasks_actually_run_concurrently():
    """Mit n Workern dauern n langsame Tasks ~1x statt ~n x so lange."""
    barrier = threading.Barrier(4, timeout=5)

    def worker(ch, comm, unit):
        barrier.wait()  # haengt, bis 4 Tasks GLEICHZEITIG laufen
        time.sleep(0.01)

    tasks = [("House", "Science", f"unit{i}") for i in range(4)]
    start = time.monotonic()
    failures = run._run_parallel("Map", tasks, worker, max_workers=4)
    assert failures == []  # Barrier waere bei sequentieller Abarbeitung getimeoutet
    assert time.monotonic() - start < 5


def test_empty_tasks_no_client_init(monkeypatch):
    def _boom():
        raise AssertionError("Client darf bei leerer Task-Liste nicht erzeugt werden")
    monkeypatch.setattr(llm_client, "get_client", _boom)
    assert run._run_parallel("Map", [], lambda: None, max_workers=2) == []


def test_cmd_review_disabled_passthrough_without_client(tmp_path, monkeypatch):
    """REVIEW_ENABLED=False: cmd_review schreibt Pass-Through-Caches und
    initialisiert NIE den LLM-Client (laeuft also auch ohne API-Key)."""
    monkeypatch.setattr(config, "REVIEW_ENABLED", False)
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))

    def _boom():
        raise AssertionError("Client darf bei deaktiviertem Review nicht erzeugt werden")
    monkeypatch.setattr(llm_client, "get_client", _boom)

    monkeypatch.setattr(
        run, "_select", lambda ch, co: iter([("House", "Science", ["Energy"])]))
    rpath = tmp_path / "research.json"
    rpath.write_text(json.dumps({"jurisdiction": {"Energy": {
        "text": "x", "source_url": "https://x", "quote": "energy research",
        "grounding_quality": "direct"}}}), encoding="utf-8")
    monkeypatch.setattr(run.research, "cache_path", lambda ch, co: str(rpath))
    mpath = tmp_path / "mapping.json"
    mpath.write_text(json.dumps({
        "chamber": "House", "committee": "Science", "unit_key": "Energy",
        "grounding_quality": "direct",
        "rows": [{"sector": "Energy", "industry": "Uranium", "rank": 1,
                  "confidence": "high", "grounding_source": "https://x"}],
    }), encoding="utf-8")
    monkeypatch.setattr(run.mapping, "cache_path", lambda ch, co, u: str(mpath))
    monkeypatch.setattr(run.taxonomy, "load", taxonomy.load_source)

    rc = run.cmd_review(argparse.Namespace(chamber=None, committee=None,
                                           force=False))
    assert rc == 0
    with open(review.cache_path("House", "Science"), encoding="utf-8") as fh:
        out = json.load(fh)
    assert out["llm_reviewed"] is False
    assert out["reviewed_rows"][0]["review_flag"] == "not_reviewed"
