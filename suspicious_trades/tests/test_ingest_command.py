"""Tests der Ingestion-Orchestrierung, Quellen-Schalter und des CLI-Subcommands."""

from __future__ import annotations

import argparse
from datetime import date

from suspicious_trades import run
from suspicious_trades.ingestion import (
    CongressGovAdapter,
    FederalRegisterAdapter,
    SamGovAdapter,
    WarGovRssAdapter,
    base as ingestion_base,
    run_ingestion,
)
from suspicious_trades.ingestion.base import EventCandidate
from suspicious_trades.registry import (
    EventRegistry,
    STATUS_CLOSED,
    STATUS_EXPIRED,
    STATUS_OPEN,
    STATUS_OUTCOME_KNOWN,
)

TODAY = date(2026, 7, 11)


class _FakeAdapter:
    def __init__(self, key: str, candidates=None, error: Exception | None = None):
        self.CONFIG_KEY = key
        self._candidates = candidates or []
        self._error = error

    def ingest(self, *, today):
        if self._error is not None:
            raise self._error
        return self._candidates


def _candidate(source: str, source_id: str) -> EventCandidate:
    return EventCandidate(
        source=source, source_id=source_id, event_class="legislation",
        title="x", event_date=None, update_date=None,
        expected_outcome_date=None, url=None, raw_payload={},
    )


def test_disabled_source_is_skipped(ingest_settings):
    ingest_settings["INGEST_SOURCES_ENABLED"]["federal_register"] = False
    adapters = [
        _FakeAdapter("congress_gov", [_candidate("congress_gov", "a")]),
        _FakeAdapter("federal_register", [_candidate("federal_register", "b")]),
    ]
    result = run_ingestion(ingest_settings, today=TODAY, adapters=adapters)

    assert "federal_register" in result.skipped
    assert result.per_source == {"congress_gov": 1}
    assert [c.source for c in result.candidates] == ["congress_gov"]


def test_failing_source_does_not_abort_others(ingest_settings):
    adapters = [
        _FakeAdapter("congress_gov", error=RuntimeError("boom")),
        _FakeAdapter("sam_gov", [_candidate("sam_gov", "ok")]),
    ]
    result = run_ingestion(ingest_settings, today=TODAY, adapters=adapters)

    assert "congress_gov" in result.errors
    assert result.per_source["sam_gov"] == 1
    assert len(result.candidates) == 1


def _patched_adapters(ingest_settings, read_fixture, read_fixture_text, *, sam_file):
    """Reale Adapter mit netzfrei ersetzten fetch()-Methoden."""

    congress = CongressGovAdapter(ingest_settings)  # liest Fixture-Cache direkt
    federal = FederalRegisterAdapter(ingest_settings)
    federal.fetch = lambda: read_fixture("federal_register", "documents.json")
    war = WarGovRssAdapter(ingest_settings)
    war.fetch = lambda: read_fixture_text("war_gov", "contracts.xml")
    sam = SamGovAdapter(ingest_settings)
    sam.fetch = lambda: read_fixture("sam_gov", sam_file)
    return [congress, federal, war, sam]


def test_ingest_command_end_to_end(
    monkeypatch, ingest_settings, read_fixture, read_fixture_text, capsys
):
    adapters = _patched_adapters(
        ingest_settings, read_fixture, read_fixture_text, sam_file="opportunities.json"
    )
    monkeypatch.setattr(run.config, "effective", lambda: ingest_settings)
    monkeypatch.setattr(ingestion_base, "build_adapters", lambda settings: adapters)

    exit_code = run.ingest_command(argparse.Namespace(today="2026-07-11"))
    assert exit_code == 0

    registry_path = ingest_settings["DATA_DIR"] / "events.jsonl"
    assert registry_path.exists()
    events = EventRegistry(registry_path, ingest_settings).load()

    # Congress: Floor-Vote -> outcome_known, Public Law -> outcome_known, offen bleibt offen.
    assert events["congress_gov:hr-119-1"]["status"] == STATUS_OUTCOME_KNOWN
    assert events["congress_gov:hjres-119-7"]["status"] == STATUS_OUTCOME_KNOWN
    assert events["congress_gov:s-119-50"]["status"] == STATUS_OPEN
    # Federal Register: RULE effektiv -> outcome_known, PRORULE via RULE -> outcome_known.
    assert events["federal_register:2026-14001"]["status"] == STATUS_OUTCOME_KNOWN
    assert events["federal_register:2026-09000"]["status"] == STATUS_OUTCOME_KNOWN
    # war.gov: sofortiges Outcome (Announcement).
    assert all(
        e["status"] == STATUS_OUTCOME_KNOWN
        for eid, e in events.items()
        if eid.startswith("war_gov_rss:")
    )
    # SAM: offene Solicitation bleibt offen, alte Presolicitation ist expired.
    assert events["sam_gov:sol-0001"]["status"] == STATUS_OPEN
    assert events["sam_gov:sol-0002"]["status"] == STATUS_EXPIRED

    output = capsys.readouterr().out
    assert "[registry]" in output


def test_ingest_command_award_closes_solicitation(
    monkeypatch, ingest_settings, read_fixture, read_fixture_text
):
    monkeypatch.setattr(run.config, "effective", lambda: ingest_settings)

    first = _patched_adapters(
        ingest_settings, read_fixture, read_fixture_text, sam_file="opportunities.json"
    )
    monkeypatch.setattr(ingestion_base, "build_adapters", lambda settings: first)
    run.ingest_command(argparse.Namespace(today="2026-06-05"))

    second = _patched_adapters(
        ingest_settings, read_fixture, read_fixture_text, sam_file="award.json"
    )
    monkeypatch.setattr(ingestion_base, "build_adapters", lambda settings: second)
    run.ingest_command(argparse.Namespace(today="2026-07-11"))

    registry_path = ingest_settings["DATA_DIR"] / "events.jsonl"
    events = EventRegistry(registry_path, ingest_settings).load()
    assert "sam_gov:awd-0001" not in events
    assert events["sam_gov:sol-0001"]["status"] == STATUS_OUTCOME_KNOWN
    assert events["sam_gov:sol-0001"]["actual_outcome_date"] == "2026-07-08"


def test_build_parser_registers_ingest():
    parser = run.build_parser()
    args = parser.parse_args(["ingest", "--today", "2026-07-11"])
    assert args.handler is run.ingest_command
    assert args.today == "2026-07-11"
