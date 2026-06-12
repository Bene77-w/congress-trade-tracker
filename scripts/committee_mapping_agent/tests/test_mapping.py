"""Phase-3-Tests: hierarchisches Mapping (Mock) + Sektor-Block byte-stabil."""
import json

from scripts.committee_mapping_agent import config, mapping, taxonomy
from scripts.committee_mapping_agent.tests.conftest import FakeClient, make_response


def test_sector_block_byte_stable_and_isolated():
    src = taxonomy.load_source()
    a = taxonomy.build_sector_block(src, "Energy")
    b = taxonomy.build_sector_block(src, "Energy")
    assert a == b
    assert "Uranium" in a
    # Industries fremder Sektoren duerfen NICHT enthalten sein.
    assert "Biotechnology" not in a
    assert "Semiconductors" not in a


def test_map_unit_two_stage_assembles_and_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAPPING", str(tmp_path / "mapping"))
    tax = taxonomy.load_source()

    entry = {
        "text": "Energieforschung und -entwicklung.",
        "source_url": "https://science.house.gov/jurisdiction-and-rules",
        "quote": "all matters relating to energy research, development, and demonstration",
        "grounding_quality": "direct",
    }

    # Stufe 1: ein Sektor (Energy). Stufe 2: zwei Industries, davon eine
    # halluziniert (gehoert nicht zu Energy) -> muss rausfallen.
    stage1 = make_response(json.dumps({"sectors": [
        {"sector": "Energy", "rank": 1, "confidence": "high", "rationale": "Energieforschung"}
    ]}))
    stage2 = make_response(json.dumps({"industries": [
        {"industry": "Uranium", "confidence": "high", "rationale": "Kernenergie"},
        {"industry": "Biotechnology", "confidence": "high", "rationale": "halluziniert"},
    ]}))
    client = FakeClient([stage1, stage2])

    result = mapping.map_unit(
        "House", "Science, Space, and Technology", "Energy", entry, tax,
        client=client,
    )
    rows = result["rows"]
    # Nur die valide Energy/Uranium-Zeile bleibt.
    assert len(rows) == 1
    assert rows[0]["sector"] == "Energy"
    assert rows[0]["industry"] == "Uranium"
    assert rows[0]["rank"] == 1
    assert rows[0]["confidence"] == "high"  # direct -> high erlaubt
    assert "science.house.gov" in rows[0]["grounding_source"]
    assert result["grounding_quality"] == "direct"
    # 2 LLM-Calls: Stufe 1 + Stufe 2 (ein Sektor).
    assert len(client.calls) == 2


def test_map_unit_caps_confidence_for_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAPPING", str(tmp_path / "mapping"))
    tax = taxonomy.load_source()
    entry = {"text": "x", "source_url": "https://govinfo.gov",
             "quote": "to the Committee on Energy and Commerce shall be referred",
             "grounding_quality": "fallback"}
    stage1 = make_response(json.dumps({"sectors": [
        {"sector": "Energy", "rank": 1, "confidence": "high", "rationale": "r"}]}))
    stage2 = make_response(json.dumps({"industries": [
        {"industry": "Uranium", "confidence": "high", "rationale": "r"}]}))
    client = FakeClient([stage1, stage2])
    result = mapping.map_unit("House", "Energy and Commerce", "__committee__",
                              entry, tax, client=client)
    # fallback -> Deckel medium
    assert result["rows"][0]["confidence"] == "medium"


def test_map_unit_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAPPING", str(tmp_path / "mapping"))
    tax = taxonomy.load_source()
    entry = {"text": "x", "source_url": "u",
             "quote": "ein ausreichend langes zitat fuer den test hier",
             "grounding_quality": "none"}
    stage1 = make_response(json.dumps({"sectors": []}))
    client = FakeClient([stage1])
    mapping.map_unit("House", "Budget", "__committee__", entry, tax, client=client)
    assert len(client.calls) == 1

    client2 = FakeClient([])  # darf nicht aufgerufen werden
    mapping.map_unit("House", "Budget", "__committee__", entry, tax, client=client2)
    assert len(client2.calls) == 0


def _direct_entry():
    return {"text": "Energieforschung.", "source_url": "https://science.house.gov",
            "quote": "all matters relating to energy research and development",
            "grounding_quality": "direct"}


def test_retry_fills_empty_on_second_attempt(tmp_path, monkeypatch):
    """Leerer 1. Versuch (grounding=direct) -> Retry liefert valide Zeile."""
    monkeypatch.setattr(config, "CACHE_MAPPING", str(tmp_path / "mapping"))
    monkeypatch.setattr(config, "MAP_EMPTY_BACKOFF", (0, 0, 0, 0, 0))
    tax = taxonomy.load_source()
    empty1 = make_response(json.dumps({"sectors": []}))           # Versuch 1: leer
    stage1 = make_response(json.dumps({"sectors": [               # Versuch 2: Treffer
        {"sector": "Energy", "rank": 1, "confidence": "high", "rationale": "r"}]}))
    stage2 = make_response(json.dumps({"industries": [
        {"industry": "Uranium", "confidence": "high", "rationale": "r"}]}))
    client = FakeClient([empty1, stage1, stage2])

    result = mapping.map_unit("House", "Science", "Energy", _direct_entry(),
                              tax, client=client)
    assert result["no_mapping"] is False
    assert result["attempts"] == 2
    assert len(result["rows"]) == 1
    assert result["rows"][0]["industry"] == "Uranium"
    assert len(client.calls) == 3  # leer(1) + stage1+stage2(2)


def test_retry_exhausts_then_no_mapping(tmp_path, monkeypatch):
    """6 leere Versuche (1 + 5 Retries) -> no_mapping True, rows leer."""
    monkeypatch.setattr(config, "CACHE_MAPPING", str(tmp_path / "mapping"))
    monkeypatch.setattr(config, "MAP_EMPTY_BACKOFF", (0, 0, 0, 0, 0))
    tax = taxonomy.load_source()
    empties = [make_response(json.dumps({"sectors": []})) for _ in range(6)]
    client = FakeClient(empties)

    result = mapping.map_unit("House", "Science", "Energy", _direct_entry(),
                              tax, client=client)
    assert result["no_mapping"] is True
    assert result["attempts"] == 6
    assert result["rows"] == []
    assert len(client.calls) == 6


def test_no_retry_when_grounding_not_direct(tmp_path, monkeypatch):
    """grounding=none + leer -> KEIN Retry, sofort no_mapping (nur 1 Call)."""
    monkeypatch.setattr(config, "CACHE_MAPPING", str(tmp_path / "mapping"))
    monkeypatch.setattr(config, "MAP_EMPTY_BACKOFF", (0, 0, 0, 0, 0))
    tax = taxonomy.load_source()
    entry = {"text": "x", "source_url": "u", "quote": "kein nexus",
             "grounding_quality": "none"}
    client = FakeClient([make_response(json.dumps({"sectors": []}))])

    result = mapping.map_unit("House", "Budget", "__committee__", entry, tax,
                              client=client)
    assert result["no_mapping"] is True
    assert result["attempts"] == 1
    assert len(client.calls) == 1
