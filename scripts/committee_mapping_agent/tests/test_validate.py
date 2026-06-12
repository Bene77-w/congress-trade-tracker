"""Phase-3-Tests: deterministische validate-Schicht."""
import pytest

from scripts.committee_mapping_agent import config, taxonomy, validate


@pytest.mark.parametrize("val,expected", [
    ("high", "high"), ("LOW", "low"), ("medium", "medium"),
    (0.95, "high"), (0.8, "high"), (0.79, "medium"), (0.5, "medium"),
    (0.49, "low"), (95, "high"), (60, "medium"), (10, "low"),
    ("0.9", "high"), ("0.3", "low"), ("quatsch", "low"), (True, "low"),
])
def test_normalize_confidence(val, expected):
    assert config.normalize_confidence(val) == expected


def _tax():
    return taxonomy.load_source()


def _row(sector, industry, rank=1, confidence="high", grounding="https://x — \"zitat\""):
    return {"sector": sector, "industry": industry, "rank": rank,
            "confidence": confidence, "grounding_source": grounding}


def test_drops_non_allowlist_pair():
    rows = [_row("Energy", "Fusion Reactors")]  # Industry existiert nicht
    clean, msgs = validate.validate_rows(rows, _tax(), "direct")
    assert clean == []
    assert any("Allow-Liste" in m for m in msgs)


def test_drops_wrong_sector_for_industry():
    rows = [_row("Technology", "Uranium")]  # Uranium ist Energy
    clean, _ = validate.validate_rows(rows, _tax(), "direct")
    assert clean == []


def test_keeps_valid_pair():
    rows = [_row("Energy", "Uranium")]
    clean, _ = validate.validate_rows(rows, _tax(), "direct")
    assert len(clean) == 1
    assert clean[0]["confidence"] == "high"  # direct erlaubt high


def test_grounding_quality_caps_confidence():
    rows = [_row("Energy", "Uranium", confidence="high")]
    clean, msgs = validate.validate_rows(rows, _tax(), "fallback")  # cap=medium
    assert clean[0]["confidence"] == "medium"
    assert any("gedeckelt" in m for m in msgs)

    clean2, _ = validate.validate_rows(rows, _tax(), "none")  # cap=low
    assert clean2[0]["confidence"] == "low"


def test_empty_grounding_caps_to_low():
    rows = [_row("Energy", "Uranium", confidence="high", grounding="")]
    clean, msgs = validate.validate_rows(rows, _tax(), "direct")
    assert clean[0]["confidence"] == "low"
    assert any("grounding_source leer" in m for m in msgs)


def test_invalid_rank_becomes_99():
    rows = [_row("Energy", "Uranium", rank="abc")]
    clean, _ = validate.validate_rows(rows, _tax(), "direct")
    assert clean[0]["rank"] == 99


def test_invalid_confidence_becomes_low():
    rows = [_row("Energy", "Uranium", confidence="sehr-sicher")]
    clean, _ = validate.validate_rows(rows, _tax(), "direct")
    assert clean[0]["confidence"] == "low"


def test_dedup_same_pair():
    rows = [_row("Energy", "Uranium"), _row("Energy", "Uranium", rank=2)]
    clean, msgs = validate.validate_rows(rows, _tax(), "direct")
    assert len(clean) == 1
    assert any("Duplikat" in m for m in msgs)


def test_idempotent():
    rows = [_row("Energy", "Uranium"), _row("Healthcare", "Biotechnology")]
    clean1, _ = validate.validate_rows(rows, _tax(), "direct")
    clean2, _ = validate.validate_rows(clean1, _tax(), "direct")
    assert clean1 == clean2
