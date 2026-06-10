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
