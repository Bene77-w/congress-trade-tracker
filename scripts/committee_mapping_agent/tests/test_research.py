"""Phase-2-Tests (netzwerkfrei): Loader, Normalisierung, Cache, Tool-Specs."""
import json

from scripts.committee_mapping_agent import committees, config, research
from scripts.committee_mapping_agent.research import COMMITTEE_KEY
from scripts.committee_mapping_agent.tests.conftest import FakeClient, make_response


# ---------------------------------------------------------------------------
# committees-Loader
# ---------------------------------------------------------------------------
def test_load_committees_house_has_subcommittees():
    data = committees.load_committees("House")
    assert "Agriculture" in data
    assert len(data["Agriculture"]) >= 5
    # Reihenfolge erhalten, keine Duplikate.
    assert len(data["Agriculture"]) == len(set(data["Agriculture"]))


def test_slugify_stable():
    assert committees.slugify("Energy and Commerce") == "energy_and_commerce"
    assert committees.slugify("  A/B, C  ") == "a_b_c"
    assert committees.slugify("") == "x"


# ---------------------------------------------------------------------------
# _strip_fences / _normalize
# ---------------------------------------------------------------------------
def test_strip_fences():
    assert research._strip_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert research._strip_fences('{"a":1}') == '{"a":1}'


def test_normalize_fills_missing_and_caps_grounding():
    raw = {"jurisdiction": {
        COMMITTEE_KEY: {
            "text": "x", "source_url": "https://energy.house.gov",
            "quote": "all matters relating to energy research and development",
            "grounding_quality": "direct",
        },
        "Sub A": {  # zu kurzes Zitat -> Deckel auf none
            "text": "y", "source_url": "u", "quote": "kurz",
            "grounding_quality": "direct",
        },
        # 'Sub B' fehlt komplett -> wird als none ergaenzt
    }}
    out = research._normalize(raw, ["Sub A", "Sub B"])
    assert out[COMMITTEE_KEY]["grounding_quality"] == "direct"
    assert out["Sub A"]["grounding_quality"] == "none"  # gedeckelt
    assert out["Sub B"]["grounding_quality"] == "none"  # ergaenzt
    assert set(out.keys()) == {COMMITTEE_KEY, "Sub A", "Sub B"}


def test_normalize_invalid_quality_to_none():
    raw = {"jurisdiction": {COMMITTEE_KEY: {
        "text": "x", "source_url": "u",
        "quote": "ein ausreichend langes woertliches Zitat hier",
        "grounding_quality": "super-sicher",
    }}}
    out = research._normalize(raw, [])
    assert out[COMMITTEE_KEY]["grounding_quality"] == "none"


# ---------------------------------------------------------------------------
# Tool-Specs
# ---------------------------------------------------------------------------
def test_tool_specs_present():
    names = {t["function"]["name"] for t in research.TOOLS}
    assert names == {"web_search", "fetch_page"}
    assert set(research.TOOL_HANDLERS) == names


# ---------------------------------------------------------------------------
# research_committee end-to-end (Mock-Client, kein Netz)
# ---------------------------------------------------------------------------
def test_research_committee_writes_cache_and_reuses(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_RESEARCH", str(tmp_path / "research"))

    final = json.dumps({"jurisdiction": {
        COMMITTEE_KEY: {
            "text": "Energieforschung etc.",
            "source_url": "https://science.house.gov/jurisdiction-and-rules",
            "quote": "all matters relating to energy research, development, and demonstration",
            "grounding_quality": "direct",
        },
        "Energy": {
            "text": "Energie-Subcommittee",
            "source_url": "https://science.house.gov/jurisdiction-and-rules",
            "quote": "jurisdiction over the following subject matters: all matters relating to energy",
            "grounding_quality": "direct",
        },
    }})
    # Erste Runde: ein Call (finaler Text, keine Tool-Calls).
    client = FakeClient([make_response(content=final)])
    result = research.research_committee(
        "House", "Science, Space, and Technology", ["Energy"], client=client,
    )
    assert result["jurisdiction"][COMMITTEE_KEY]["grounding_quality"] == "direct"
    assert result["jurisdiction"]["Energy"]["grounding_quality"] == "direct"
    assert len(client.calls) == 1
    assert research.cache_path("House", "Science, Space, and Technology")

    # Zweite Runde: Cache-Hit -> kein weiterer Client-Call noetig.
    client2 = FakeClient([])  # wuerde bei Aufruf werfen
    cached = research.research_committee(
        "House", "Science, Space, and Technology", ["Energy"], client=client2,
    )
    assert cached["committee"] == result["committee"]
    assert len(client2.calls) == 0
