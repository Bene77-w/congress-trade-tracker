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
             "confidence": "high", "ai_comment": "c",
             "grounding_source": "https://x — \"zitat\""},
            {"sector": "Energy", "industry": "Thermal Coal", "rank": 1,
             "confidence": "high", "ai_comment": "c",
             "grounding_source": "https://x — \"zitat\""},
        ],
    }]


def test_review_attaches_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    tax = taxonomy.load_source()

    resp = make_response(json.dumps({
        "row_reviews": [
            {"index": 0, "review_flag": "ok", "reason": "passt"},
            {"index": 1, "review_flag": "confidence_too_high", "reason": "schwach"},
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
    assert rows[1]["review_flag"] == "confidence_too_high"
    assert rows[0]["subcommittee"] == "Energy"
    assert rows[0]["mapping_id"] == mapping_id("House", "Science", "Energy",
                                               "Energy", "Uranium")
    assert len(result["missing"]) == 1


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


def test_review_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_REVIEW", str(tmp_path / "review"))
    tax = taxonomy.load_source()
    resp = make_response(json.dumps({"row_reviews": [
        {"index": 0, "review_flag": "ok", "reason": "x"},
        {"index": 1, "review_flag": "ok", "reason": "x"}], "missing": []}))
    client = FakeClient([resp])
    review.review_committee("House", "Science", _jurisdiction(),
                            _mapping_results(), tax, client=client)
    client2 = FakeClient([])
    review.review_committee("House", "Science", _jurisdiction(),
                            _mapping_results(), tax, client=client2)
    assert len(client2.calls) == 0
