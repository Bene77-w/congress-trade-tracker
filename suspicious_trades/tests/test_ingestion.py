"""Offline-Tests der vier Ingestion-Adapter (Feld-Mapping und Filter)."""

from __future__ import annotations

from datetime import date

from suspicious_trades.ingestion import (
    CongressGovAdapter,
    FederalRegisterAdapter,
    SamGovAdapter,
    WarGovRssAdapter,
    is_award_notice,
)

TODAY = date(2026, 7, 11)


# -- Congress.gov ------------------------------------------------------------
def test_congress_maps_and_filters_lookback(ingest_settings):
    adapter = CongressGovAdapter(ingest_settings)
    candidates = adapter.ingest(today=TODAY)

    by_id = {c.source_id: c for c in candidates}
    # hr-119-999 liegt vor dem Lookback-Fenster und faellt heraus.
    assert set(by_id) == {"hr-119-1", "s-119-50", "hjres-119-7"}

    ndaa = by_id["hr-119-1"]
    assert ndaa.event_id == "congress_gov:hr-119-1"
    assert ndaa.event_class == "legislation"
    # event_date = introduced_date, update_date = updateDate — nie vermischt.
    assert ndaa.event_date == "2026-06-20"
    assert ndaa.update_date == "2026-07-05"
    assert ndaa.expected_outcome_date is None
    assert ndaa.url == "https://www.congress.gov/bill/119th-congress/house-bill/1"
    assert ndaa.raw_payload["policy_area"] == "Armed Forces and National Security"


def test_congress_lookback_is_configurable(ingest_settings):
    ingest_settings["INGEST_LOOKBACK_DAYS"] = 120
    candidates = CongressGovAdapter(ingest_settings).ingest(today=TODAY)
    # Groesseres Fenster nimmt den alten Bill wieder auf.
    assert "hr-119-999" in {c.source_id for c in candidates}


def test_congress_floor_actions_reach_payload(ingest_settings):
    candidates = CongressGovAdapter(ingest_settings).ingest(today=TODAY)
    ndaa = next(c for c in candidates if c.source_id == "hr-119-1")
    floor = [a for a in ndaa.raw_payload["actions"] if a["type"] == "Floor"]
    assert floor and floor[0]["roll_call"]


# -- Federal Register --------------------------------------------------------
def test_federal_register_filters_and_maps(ingest_settings, read_fixture):
    raw = read_fixture("federal_register", "documents.json")
    candidates = FederalRegisterAdapter(ingest_settings).parse(raw, today=TODAY)

    by_id = {c.source_id: c for c in candidates}
    # Der Notice-Typ wird verworfen; nur RULE/PRORULE bleiben.
    assert set(by_id) == {"2026-14001", "2026-09000"}

    rule = by_id["2026-14001"]
    assert rule.raw_payload["type"] == "RULE"
    assert rule.raw_payload["rin"] == ["2060-AV12"]
    assert rule.event_date == "2026-07-01"
    assert rule.expected_outcome_date == "2026-07-05"

    prorule = by_id["2026-09000"]
    assert prorule.raw_payload["type"] == "PRORULE"
    assert prorule.expected_outcome_date == "2026-07-20"


def test_federal_register_doc_types_configurable(ingest_settings, read_fixture):
    ingest_settings["FEDREG_DOC_TYPES"] = ["RULE"]
    raw = read_fixture("federal_register", "documents.json")
    candidates = FederalRegisterAdapter(ingest_settings).parse(raw, today=TODAY)
    assert {c.source_id for c in candidates} == {"2026-14001"}


# -- war.gov RSS -------------------------------------------------------------
def test_war_gov_threshold_and_amount_parsing(ingest_settings, read_fixture_text):
    raw = read_fixture_text("war_gov", "contracts.xml")
    candidates = WarGovRssAdapter(ingest_settings).parse(raw, today=TODAY)

    # Drei Contracts, einer unter 50 Mio USD -> zwei Events.
    assert len(candidates) == 2
    amounts = sorted(c.raw_payload["amount_usd"] for c in candidates)
    assert amounts == [490_000_000.0, 1_200_000_000.0]

    guid = "https://www.defense.gov/News/Contracts/Contract/Article/4001/"
    # Laufende Contract-Nr. bleibt am Absatz haengen (1 und 2), 3 faellt weg.
    assert {c.source_id for c in candidates} == {f"{guid}#1", f"{guid}#2"}
    for candidate in candidates:
        assert candidate.event_class == "defense_contract"
        assert candidate.event_date == "2026-07-08"
        assert candidate.raw_payload["branch"] == "ARMY"


def test_war_gov_threshold_is_configurable(ingest_settings, read_fixture_text):
    ingest_settings["WAR_GOV_MIN_CONTRACT_USD"] = 1_000_000
    raw = read_fixture_text("war_gov", "contracts.xml")
    candidates = WarGovRssAdapter(ingest_settings).parse(raw, today=TODAY)
    assert len(candidates) == 3


# -- SAM.gov -----------------------------------------------------------------
def test_sam_solicitations_map(ingest_settings, read_fixture):
    raw = read_fixture("sam_gov", "opportunities.json")
    candidates = SamGovAdapter(ingest_settings).parse(raw, today=TODAY)

    by_id = {c.source_id: c for c in candidates}
    assert set(by_id) == {"sol-0001", "sol-0002"}
    sol = by_id["sol-0001"]
    assert sol.event_class == "procurement"
    assert sol.raw_payload["solicitationNumber"] == "SPRTA1-26-R-0007"
    assert sol.expected_outcome_date == "2026-06-30"
    assert not is_award_notice(sol.raw_payload)


def test_sam_award_is_detected(ingest_settings, read_fixture):
    raw = read_fixture("sam_gov", "award.json")
    candidates = SamGovAdapter(ingest_settings).parse(raw, today=TODAY)
    award = candidates[0]
    assert is_award_notice(award.raw_payload)
    assert award.raw_payload["award"]["date"] == "2026-07-08"
    assert award.raw_payload["solicitationNumber"] == "SPRTA1-26-R-0007"
