"""Phase-1-Tests: Prefix byte-stabil, Allow-Listen-Checks, enrich-Reconstruction."""
import json

from scripts.committee_mapping_agent import config, taxonomy
from scripts.committee_mapping_agent.tests.conftest import FakeClient, make_response


def test_prefix_block_is_byte_stable():
    src = taxonomy.load_source()
    a = taxonomy.build_prefix_block(src)
    b = taxonomy.build_prefix_block(src)
    assert a == b
    assert isinstance(a, str)
    # Bekannter Sektor + Industry muessen vorkommen.
    assert "## Technology" in a
    assert "Semiconductors" in a


def test_allow_list_checks():
    src = taxonomy.load_source()
    assert taxonomy.is_valid_sector(src, "Energy")
    assert not taxonomy.is_valid_sector(src, "Crypto")
    # korrektes Paar
    assert taxonomy.is_valid_industry(src, "Energy", "Uranium")
    # richtige Industry, falscher Sektor -> ungueltig
    assert not taxonomy.is_valid_industry(src, "Technology", "Uranium")
    # unbekannte Industry
    assert not taxonomy.is_valid_industry(src, "Energy", "Fusion Reactors")


def test_industries_for_sector_nonempty():
    src = taxonomy.load_source()
    inds = taxonomy.industries_for_sector(src, "Healthcare")
    assert "Biotechnology" in inds
    assert taxonomy.industries_for_sector(src, "Nonexistent") == []


def test_enrich_preserves_canonical_set(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAXONOMY_ENRICHED", str(tmp_path / "enriched.json"))
    src = taxonomy.load_source()
    n_sectors = len(src["sectors"])

    # Pro Sektor eine Mock-Response. Absichtlich: eine Industry weggelassen,
    # eine halluziniert -> darf die kanonische Menge NICHT veraendern.
    def scripted():
        out = []
        for s in src["sectors"]:
            canonical = [taxonomy._industry_name(i) for i in s["industries"]]
            industries = [{"industry": canonical[0], "description": "desc0"}]
            industries.append({"industry": "Halluzinierte Industry", "description": "x"})
            out.append(make_response(json.dumps({
                "sector_description": f"Beschreibung {s['sector']}",
                "industries": industries,
            })))
        return out

    client = FakeClient(scripted())
    enriched = taxonomy.enrich(client=client)

    assert len(enriched["sectors"]) == n_sectors
    for s_src, s_enr in zip(src["sectors"], enriched["sectors"]):
        canonical = [taxonomy._industry_name(i) for i in s_src["industries"]]
        got = [i["industry"] for i in s_enr["industries"]]
        assert got == canonical  # exakt die kanonische Menge, gleiche Reihenfolge
        assert s_enr["description"].startswith("Beschreibung")
        # erste Industry hat Beschreibung, fehlende -> leer
        assert s_enr["industries"][0]["description"] == "desc0"
        assert s_enr["industries"][1]["description"] == ""

    # Datei wurde geschrieben + ist valides JSON.
    written = json.loads((tmp_path / "enriched.json").read_text(encoding="utf-8"))
    assert written == enriched


def test_prefix_block_includes_descriptions_when_enriched():
    data = {"sectors": [{
        "sector": "Energy", "description": "Energiesektor.",
        "industries": [{"industry": "Uranium", "description": "Uranabbau."}],
    }]}
    block = taxonomy.build_prefix_block(data)
    assert "## Energy — Energiesektor." in block
    assert "- Uranium: Uranabbau." in block
