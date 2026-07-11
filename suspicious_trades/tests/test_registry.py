"""Offline-Tests der Ereignis-Registry: Lebenszyklus und Outcome-Regeln."""

from __future__ import annotations

from datetime import date
from typing import Any

from suspicious_trades.ingestion import EventCandidate
from suspicious_trades.registry import (
    EventRegistry,
    STATUS_CLOSED,
    STATUS_EXPIRED,
    STATUS_OPEN,
    STATUS_OUTCOME_KNOWN,
)

TODAY = date(2026, 7, 11)


def make_registry(ingest_settings) -> EventRegistry:
    path = ingest_settings["DATA_DIR"] / ingest_settings["EVENTS_FILENAME"]
    return EventRegistry(path, ingest_settings)


def candidate(
    source: str,
    source_id: str,
    event_class: str,
    *,
    title: str = "Example",
    event_date: str | None = None,
    update_date: str | None = None,
    expected_outcome_date: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> EventCandidate:
    return EventCandidate(
        source=source,
        source_id=source_id,
        event_class=event_class,
        title=title,
        event_date=event_date,
        update_date=update_date,
        expected_outcome_date=expected_outcome_date,
        url=None,
        raw_payload=raw_payload or {},
    )


# -- Upsert / Dedup ----------------------------------------------------------
def test_reingest_updates_instead_of_duplicating(ingest_settings):
    registry = make_registry(ingest_settings)

    first = candidate(
        "congress_gov", "hr-119-1", "legislation",
        title="Old title", update_date="2026-07-01", raw_payload={"a": 1},
    )
    stats = registry.update([first], today=TODAY)
    assert stats.new_events == 1 and stats.updated == 0

    second = candidate(
        "congress_gov", "hr-119-1", "legislation",
        title="New title", update_date="2026-07-05", raw_payload={"b": 2},
    )
    stats = registry.update([second], today=date(2026, 7, 12))

    events = registry.load()
    assert len(events) == 1
    record = events["congress_gov:hr-119-1"]
    assert stats.new_events == 0 and stats.updated == 1
    assert record["title"] == "New title"
    assert record["update_date"] == "2026-07-05"
    # first_seen bleibt erhalten, Payload wird gemergt.
    assert record["first_seen"] == "2026-07-11"
    assert record["raw_payload"] == {"a": 1, "b": 2}


# -- Outcome-Regeln ----------------------------------------------------------
def test_defense_contract_immediate_outcome(ingest_settings):
    registry = make_registry(ingest_settings)
    cand = candidate(
        "war_gov_rss", "guid#1", "defense_contract",
        event_date="2026-07-08", update_date="2026-07-08",
    )
    registry.update([cand], today=TODAY)
    record = registry.load()["war_gov_rss:guid#1"]
    assert record["status"] == STATUS_OUTCOME_KNOWN
    assert record["actual_outcome_date"] == "2026-07-08"


def test_legislation_floor_vote_outcome(ingest_settings):
    registry = make_registry(ingest_settings)
    cand = candidate(
        "congress_gov", "hr-119-1", "legislation",
        event_date="2026-06-20",
        raw_payload={
            "actions": [
                {"date": "2026-07-05", "type": "Floor", "roll_call": [{"rollNumber": 312}]}
            ]
        },
    )
    registry.update([cand], today=TODAY)
    record = registry.load()["congress_gov:hr-119-1"]
    assert record["status"] == STATUS_OUTCOME_KNOWN
    assert record["actual_outcome_date"] == "2026-07-05"


def test_legislation_floor_without_roll_call_stays_open(ingest_settings):
    registry = make_registry(ingest_settings)
    cand = candidate(
        "congress_gov", "hr-119-2", "legislation",
        raw_payload={"actions": [{"date": "2026-07-05", "type": "Floor", "roll_call": []}]},
    )
    registry.update([cand], today=TODAY)
    assert registry.load()["congress_gov:hr-119-2"]["status"] == STATUS_OPEN


def test_legislation_public_law_outcome(ingest_settings):
    registry = make_registry(ingest_settings)
    cand = candidate(
        "congress_gov", "hjres-119-7", "legislation",
        raw_payload={
            "actions": [],
            "latest_action": {"actionDate": "2026-07-08", "text": "Became Public Law No: 119-45."},
        },
    )
    registry.update([cand], today=TODAY)
    record = registry.load()["congress_gov:hjres-119-7"]
    assert record["status"] == STATUS_OUTCOME_KNOWN
    assert record["actual_outcome_date"] == "2026-07-08"


def test_regulation_rule_effective_reached(ingest_settings):
    registry = make_registry(ingest_settings)
    cand = candidate(
        "federal_register", "2026-14001", "regulation",
        event_date="2026-07-01",
        raw_payload={"type": "RULE", "effective_on": "2026-07-05", "rin": ["2060-AV12"]},
    )
    registry.update([cand], today=TODAY)
    record = registry.load()["federal_register:2026-14001"]
    assert record["status"] == STATUS_OUTCOME_KNOWN
    assert record["actual_outcome_date"] == "2026-07-05"


def test_regulation_rule_not_yet_effective_stays_open(ingest_settings):
    registry = make_registry(ingest_settings)
    cand = candidate(
        "federal_register", "2026-14001", "regulation",
        event_date="2026-07-01",
        raw_payload={"type": "RULE", "effective_on": "2026-07-05", "rin": ["2060-AV12"]},
    )
    registry.update([cand], today=date(2026, 7, 3))
    assert registry.load()["federal_register:2026-14001"]["status"] == STATUS_OPEN


def test_regulation_prorule_outcome_via_later_rule(ingest_settings):
    registry = make_registry(ingest_settings)
    prorule = candidate(
        "federal_register", "2026-09000", "regulation",
        event_date="2026-05-20",
        raw_payload={"type": "PRORULE", "rin": ["2060-AV12"]},
    )
    rule = candidate(
        "federal_register", "2026-14001", "regulation",
        event_date="2026-07-01",
        raw_payload={"type": "RULE", "effective_on": "2026-12-31", "rin": ["2060-AV12"]},
    )
    registry.update([prorule, rule], today=TODAY)
    record = registry.load()["federal_register:2026-09000"]
    assert record["status"] == STATUS_OUTCOME_KNOWN
    # actual_outcome_date = Veroeffentlichungsdatum der spaeteren RULE.
    assert record["actual_outcome_date"] == "2026-07-01"
    # Die RULE selbst bleibt offen (effective_on in der Zukunft).
    assert registry.load()["federal_register:2026-14001"]["status"] == STATUS_OPEN


def test_procurement_award_matches_open_solicitation(ingest_settings):
    registry = make_registry(ingest_settings)
    solicitation = candidate(
        "sam_gov", "sol-0001", "procurement",
        event_date="2026-06-01", expected_outcome_date="2026-06-30",
        raw_payload={"type": "Solicitation", "solicitationNumber": "SPRTA1-26-R-0007",
                     "responseDeadLine": "2026-06-30"},
    )
    registry.update([solicitation], today=date(2026, 6, 5))
    assert registry.load()["sam_gov:sol-0001"]["status"] == STATUS_OPEN

    award = candidate(
        "sam_gov", "awd-0001", "procurement",
        event_date="2026-07-08",
        raw_payload={"type": "Award Notice", "solicitationNumber": "SPRTA1-26-R-0007",
                     "award": {"date": "2026-07-08", "amount": "75000000",
                               "awardee": "Acme Defense LLC"}},
    )
    stats = registry.update([award], today=TODAY)

    events = registry.load()
    # Die Award-Notice erzeugt KEIN eigenes Event.
    assert "sam_gov:awd-0001" not in events
    record = events["sam_gov:sol-0001"]
    assert record["status"] == STATUS_OUTCOME_KNOWN
    assert record["actual_outcome_date"] == "2026-07-08"
    assert stats.outcomes_detected == 1


def test_procurement_award_without_solicitation_is_skipped(ingest_settings):
    registry = make_registry(ingest_settings)
    award = candidate(
        "sam_gov", "awd-9999", "procurement",
        event_date="2026-07-08",
        raw_payload={"type": "Award Notice", "solicitationNumber": "UNKNOWN-1",
                     "award": {"date": "2026-07-08"}},
    )
    stats = registry.update([award], today=TODAY)
    assert registry.load() == {}
    assert stats.outcomes_detected == 0


def test_procurement_expires_after_timeout(ingest_settings):
    registry = make_registry(ingest_settings)
    solicitation = candidate(
        "sam_gov", "sol-0002", "procurement",
        event_date="2025-10-01", expected_outcome_date="2025-11-15",
        raw_payload={"type": "Presolicitation", "solicitationNumber": "N00019-26-R-0099",
                     "responseDeadLine": "2025-11-15"},
    )
    stats = registry.update([solicitation], today=TODAY)
    record = registry.load()["sam_gov:sol-0002"]
    assert record["status"] == STATUS_EXPIRED
    assert stats.expired == 1


# -- Lebenszyklus-Abschluss --------------------------------------------------
def test_outcome_known_closes_after_configured_days(ingest_settings):
    registry = make_registry(ingest_settings)
    # Defense-Contract mit weit zurueckliegendem Announcement.
    cand = candidate(
        "war_gov_rss", "guid#old", "defense_contract",
        event_date="2026-01-01", update_date="2026-01-01",
    )
    stats = registry.update([cand], today=TODAY)
    record = registry.load()["war_gov_rss:guid#old"]
    # Sofort outcome_known, dann > 120 Tage alt -> closed im selben Lauf.
    assert record["status"] == STATUS_CLOSED
    assert stats.closed == 1


def test_close_after_days_is_configurable(ingest_settings):
    ingest_settings["EVENT_CLOSE_AFTER_DAYS"] = 365
    registry = make_registry(ingest_settings)
    cand = candidate(
        "war_gov_rss", "guid#recentish", "defense_contract",
        event_date="2026-01-01", update_date="2026-01-01",
    )
    registry.update([cand], today=TODAY)
    # Groesseres Fenster -> bleibt outcome_known.
    assert registry.load()["war_gov_rss:guid#recentish"]["status"] == STATUS_OUTCOME_KNOWN
