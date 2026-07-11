"""Offline-Tests des User-Hinweis-Speichers (Anhaengen, Konsum, Archiv)."""

from __future__ import annotations

from suspicious_trades import hints


def test_append_and_open(b3_settings):
    hints.append_hint(b3_settings, "Erste News", ticker="AAA")
    hints.append_hint(b3_settings, "Zweite News", topic="energy")
    stored = hints.load_hints(b3_settings)
    assert len(stored) == 2
    assert stored[0]["ticker"] == "AAA"
    assert stored[0]["consumed_by_run"] is None
    assert stored[0]["archived"] is False
    assert len(hints.open_hints(b3_settings)) == 2


def test_unique_hint_ids(b3_settings):
    a = hints.append_hint(b3_settings, "a", created_at="2025-01-01T00:00:00")
    b = hints.append_hint(b3_settings, "b", created_at="2025-01-01T00:00:00")
    assert a["hint_id"] != b["hint_id"]


def test_consume_marks_and_excludes(b3_settings):
    hints.append_hint(b3_settings, "one")
    hints.append_hint(b3_settings, "two")
    marked = hints.consume_hints(b3_settings, "run-42", consumed_at="2025-02-01T00:00:00")
    assert marked == 2
    assert hints.open_hints(b3_settings) == []
    stored = hints.load_hints(b3_settings)
    assert all(h["consumed_by_run"] == "run-42" for h in stored)
    # Zweiter Konsum markiert nichts mehr.
    assert hints.consume_hints(b3_settings, "run-43") == 0


def test_consume_specific_ids(b3_settings):
    a = hints.append_hint(b3_settings, "one")
    hints.append_hint(b3_settings, "two")
    marked = hints.consume_hints(b3_settings, "run-1", hint_ids=[a["hint_id"]])
    assert marked == 1
    remaining = {h["text"] for h in hints.open_hints(b3_settings)}
    assert remaining == {"two"}


def test_archived_excluded_from_injection(b3_settings):
    a = hints.append_hint(b3_settings, "one")
    hints.append_hint(b3_settings, "two")
    assert hints.archive_hint(b3_settings, a["hint_id"]) is True
    open_texts = {h["text"] for h in hints.select_for_injection(b3_settings)}
    assert open_texts == {"two"}
    assert hints.load_hints(b3_settings)[0]["archived"] is True
