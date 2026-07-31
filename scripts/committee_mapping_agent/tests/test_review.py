"""Phase-4-Tests: Review-Agent (Mock) + rowid-Helfer."""
import json

from scripts.committee_mapping_agent import config, review, taxonomy
from scripts.committee_mapping_agent.rowid import (
    COMMITTEE_KEY,
    mapping_id,
    resolve_subcommittee_label,
)
from scripts.committee_mapping_agent.tests.conftest import FakeClient, make_response


def test_resolve_subcommittee_label():
    assert resolve_subcommittee_label("Budget", COMMITTEE_KEY) == "Budget"
    assert resolve_subcommittee_label("Agriculture", "Forestry") == "Forestry"


def test_mapping_id_stable_and_distinct():
    a = mapping_id("House", "Energy", "Energy", "Energy", "Uranium")
    b = mapping_id("House", "Energy", "Energy", "Energy", "Uranium")
    c = mapping_id("House", "Energy", "Energy", "Energy", "Gold")
    assert a == b and a != c
    assert len(a) == 10


def _jurisdiction():
    return {
        "Energy": {"text": "Energie", "source_url": "https://x",
                   "quote": "all matters relating to energy research and development",
                   "grounding_quality": "direct"},
    }


def _mapping_results():
    return [{
        "chamber": "House", "committee": "Science", "unit_key": "Energy",
        "grounding_quality": "direct",
        "rows": [
            {"sector": "Energy", "industry": "Uranium", "rank": 1,
             "confidence": "high",
             "grounding_source": "https://x — \"zitat\""},
            {"sector": "Energy", "industry": "Thermal Coal", "rank": 1,
             "confidence": "high",
             "grounding_source": "https://x — \"zitat\""},
        ],
    }]


def test_review_attaches_flags_and_sets_confidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    monkeypatch.setattr(config, "REVIEW_ENABLED", True)
    tax = taxonomy.load_source()

    resp = make_response(json.dumps({
        "row_reviews": [
            {"index": 0, "review_flag": "ok",
             "reviewed_confidence": "high", "reason": "passt"},
            {"index": 1, "review_flag": "confidence_too_high",
             "reviewed_confidence": "low", "reason": "schwach"},
        ],
        "missing": [
            {"unit": "Energy", "sector": "Utilities",
             "industry": "Utilities - Renewable", "reason": "Erneuerbare"},
        ],
    }))
    client = FakeClient([resp])

    result = review.review_committee(
        "House", "Science", _jurisdiction(), _mapping_results(), tax, client=client,
    )
    rows = result["reviewed_rows"]
    assert len(rows) == 2
    assert rows[0]["review_flag"] == "ok"
    assert rows[0]["confidence"] == "high"          # unveraendert uebernommen
    assert rows[1]["review_flag"] == "confidence_too_high"
    assert rows[1]["confidence"] == "low"           # Reviewer-Urteil ersetzt
    assert rows[1]["review_reason"].startswith("Konfidenz high -> low.")
    assert rows[0]["subcommittee"] == "Energy"
    assert rows[0]["mapping_id"] == mapping_id("House", "Science", "Energy",
                                               "Energy", "Uranium")
    assert len(result["missing"]) == 1


def test_reviewed_confidence_capped_by_grounding(tmp_path, monkeypatch):
    """Reviewer sagt high, aber grounding=fallback -> Deckel auf medium."""
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    monkeypatch.setattr(config, "REVIEW_ENABLED", True)
    tax = taxonomy.load_source()
    jurisdiction = {"Energy": {"text": "x", "source_url": "https://govinfo.gov",
                               "quote": "z", "grounding_quality": "fallback"}}
    mapping_results = [{
        "chamber": "House", "committee": "Science", "unit_key": "Energy",
        "grounding_quality": "fallback",
        "rows": [{"sector": "Energy", "industry": "Uranium", "rank": 1,
                  "confidence": "medium",
                  "grounding_source": "https://govinfo.gov — \"z\""}],
    }]
    resp = make_response(json.dumps({"row_reviews": [
        {"index": 0, "review_flag": "confidence_too_low",
         "reviewed_confidence": "high", "reason": "klar"}], "missing": []}))
    client = FakeClient([resp])
    result = review.review_committee(
        "House", "Science", jurisdiction, mapping_results, tax, client=client,
    )
    assert result["reviewed_rows"][0]["confidence"] == "medium"  # gedeckelt


def test_review_empty_rows_no_call(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    tax = taxonomy.load_source()
    client = FakeClient([])  # darf nicht aufgerufen werden
    result = review.review_committee(
        "House", "Budget", {}, [{"unit_key": "__committee__", "rows": []}],
        tax, client=client,
    )
    assert result["reviewed_rows"] == []
    assert len(client.calls) == 0


def test_review_lists_no_mapping_unit_as_placeholder(tmp_path, monkeypatch):
    """Eine no_mapping-Einheit erscheint als Platzhalter-Zeile (leer, geflaggt)."""
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    tax = taxonomy.load_source()
    jurisdiction = {"__committee__": {"text": "x", "source_url": "https://b.gov",
                                      "quote": "", "grounding_quality": "none"}}
    mapping_results = [{
        "chamber": "House", "committee": "Budget", "unit_key": "__committee__",
        "grounding_quality": "none", "rows": [], "no_mapping": True, "attempts": 1,
    }]
    client = FakeClient([])  # keine echten Zeilen -> kein Review-Call
    result = review.review_committee(
        "House", "Budget", jurisdiction, mapping_results, tax, client=client,
    )
    rows = result["reviewed_rows"]
    assert len(rows) == 1
    assert rows[0]["review_flag"] == "no_mapping"
    assert rows[0]["sector"] == "" and rows[0]["industry"] == ""
    assert rows[0]["subcommittee"] == "Budget"  # __committee__ -> Committee-Name
    assert rows[0]["mapping_id"] == mapping_id("House", "Budget", "Budget", "", "")
    assert len(client.calls) == 0


def test_review_disabled_passthrough(tmp_path, monkeypatch):
    """REVIEW_ENABLED=False -> kein LLM-Call, Zeilen unveraendert,
    flag 'not_reviewed', no_mapping-Platzhalter bleibt, missing leer."""
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    monkeypatch.setattr(config, "REVIEW_ENABLED", False)
    tax = taxonomy.load_source()
    mapping_results = _mapping_results() + [{
        "chamber": "House", "committee": "Science", "unit_key": "Space",
        "grounding_quality": "none", "rows": [], "no_mapping": True, "attempts": 1,
    }]
    client = FakeClient([])  # darf nicht aufgerufen werden
    result = review.review_committee(
        "House", "Science", _jurisdiction(), mapping_results, tax, client=client,
    )
    assert len(client.calls) == 0
    assert result["llm_reviewed"] is False
    assert result["missing"] == []
    rows = result["reviewed_rows"]
    assert len(rows) == 3  # 2 gemappte + 1 no_mapping-Platzhalter
    assert rows[0]["review_flag"] == "not_reviewed"
    assert rows[0]["confidence"] == "high"   # Mapping-Konfidenz unveraendert
    assert rows[0]["review_reason"] == ""
    assert rows[2]["review_flag"] == "no_mapping"


def test_review_passthrough_cache_redone_when_enabled(tmp_path, monkeypatch):
    """Pass-Through-Cache + Schalter wieder an -> LLM-Review trotz Cache."""
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    tax = taxonomy.load_source()
    monkeypatch.setattr(config, "REVIEW_ENABLED", False)
    review.review_committee("House", "Science", _jurisdiction(),
                            _mapping_results(), tax, client=FakeClient([]))

    monkeypatch.setattr(config, "REVIEW_ENABLED", True)
    resp = make_response(json.dumps({"row_reviews": [
        {"index": 0, "review_flag": "ok", "reviewed_confidence": "high",
         "reason": "x"},
        {"index": 1, "review_flag": "ok", "reviewed_confidence": "medium",
         "reason": "x"}], "missing": []}))
    client = FakeClient([resp])
    result = review.review_committee("House", "Science", _jurisdiction(),
                                     _mapping_results(), tax, client=client)
    assert len(client.calls) == 1          # Cache wurde NICHT zurueckgegeben
    assert result["llm_reviewed"] is True
    assert result["reviewed_rows"][0]["review_flag"] == "ok"


def test_review_llm_cache_kept_when_disabled(tmp_path, monkeypatch):
    """Echter Review-Cache + Schalter aus -> Cache behalten, kein Re-Run."""
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    monkeypatch.setattr(config, "REVIEW_ENABLED", True)
    tax = taxonomy.load_source()
    resp = make_response(json.dumps({"row_reviews": [
        {"index": 0, "review_flag": "ok", "reviewed_confidence": "high",
         "reason": "x"},
        {"index": 1, "review_flag": "confidence_too_high",
         "reviewed_confidence": "low", "reason": "x"}], "missing": []}))
    review.review_committee("House", "Science", _jurisdiction(),
                            _mapping_results(), tax, client=FakeClient([resp]))

    monkeypatch.setattr(config, "REVIEW_ENABLED", False)
    client2 = FakeClient([])
    result = review.review_committee("House", "Science", _jurisdiction(),
                                     _mapping_results(), tax, client=client2)
    assert len(client2.calls) == 0
    assert result["llm_reviewed"] is True  # bezahltes LLM-Urteil bleibt
    assert result["reviewed_rows"][1]["review_flag"] == "confidence_too_high"


def test_review_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    monkeypatch.setattr(config, "REVIEW_ENABLED", True)
    tax = taxonomy.load_source()
    resp = make_response(json.dumps({"row_reviews": [
        {"index": 0, "review_flag": "ok", "reviewed_confidence": "high",
         "reason": "x"},
        {"index": 1, "review_flag": "ok", "reviewed_confidence": "high",
         "reason": "x"}], "missing": []}))
    client = FakeClient([resp])
    review.review_committee("House", "Science", _jurisdiction(),
                            _mapping_results(), tax, client=client)
    client2 = FakeClient([])
    review.review_committee("House", "Science", _jurisdiction(),
                            _mapping_results(), tax, client=client2)
    assert len(client2.calls) == 0
