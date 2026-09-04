"""Gate C des Integrationsplans: der V2-Adapter in `config/committee_mapping.py`.

Geprüft werden die vier Festlegungen, die diesen Weg von der Legacy-Kette
unterscheiden: kanonische Namen aus `units.match_key`, der Joint-Fallback aus
`roster_coverage`, die Parent-Core-Abgrenzung und die Dublettenwahl nach
`confidence`. Dazu, dass der Legacy-Weg weiter läuft — der Schattenvergleich
baut beide Mapper nebeneinander, und ein Adapter, der den alten Weg mit
abräumt, macht den Vergleich unmöglich.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import committee_mapping, committee_revision  # noqa: E402
from config.committee_mapping import CommitteeMapper, dedupe_entries  # noqa: E402


def _zuordnung(unit_id: str, chamber: str, committee: str, unit_name: str,
               unit_kind: str, sector: str, industry: str,
               confidence: int) -> committee_revision.Assignment:
    return committee_revision.Assignment(
        unit_id=unit_id, chamber=chamber, committee_name=committee,
        unit_name=unit_name, unit_kind=unit_kind,
        sector_id=sector.lower(), sector_name=sector,
        industry_id=industry.lower(), industry_name=industry,
        confidence=confidence)


def _revision(*zuordnungen) -> committee_revision.Revision:
    return committee_revision.Revision(
        review_id="map-test", path=Path("/dev/null"), scope="fullrun",
        snapshot_id="tax-test", unit_count=len(zuordnungen),
        assignments=tuple(zuordnungen))


@pytest.fixture()
def mapper_factory(monkeypatch):
    """Baut einen Mapper über eine gestellte Revision statt über die Ablage."""
    def bauen(*zuordnungen) -> CommitteeMapper:
        monkeypatch.setattr(committee_revision, "load_active_revision",
                            lambda settings: _revision(*zuordnungen))
        return CommitteeMapper()
    return bauen


ARMED_SERVICES = _zuordnung(
    "senate--armed-services", "senate", "Committee on Armed Services",
    "Committee on Armed Services", "committee_parent_core",
    "Industrials", "Aerospace & Defense", 4)

JUDICIARY = _zuordnung(
    "house--judiciary", "house", "Committee on the Judiciary",
    "Committee on the Judiciary", "committee_direct",
    "Technology", "Software - Infrastructure", 3)

VETERANS_SUB = _zuordnung(
    "house--veterans-affairs--health", "house", "Committee on Veterans' Affairs",
    "Subcommittee on Health", "subcommittee",
    "Healthcare", "Medical Care Facilities", 4)


# -- kanonische Namen ------------------------------------------------------
@pytest.mark.parametrize("geschrieben", [
    "Committee on Armed Services",
    "Armed Services",
    "  armed   services  ",
    "COMMITTEE ON ARMED SERVICES",
])
def test_der_committee_name_wird_kanonisiert(mapper_factory, geschrieben) -> None:
    """`units.match_key` auf beiden Seiten - keine zweite Normalisierung."""
    mapper = mapper_factory(ARMED_SERVICES)

    treffer = mapper.get_all_sectors_for_committee("Senate", geschrieben)

    assert [e["industry"] for e in treffer] == ["Aerospace & Defense"]


def test_der_artikel_nach_dem_praefix_faellt_weg(mapper_factory) -> None:
    """'Committee on the Judiciary' und 'Judiciary' sind dieselbe Unit."""
    mapper = mapper_factory(JUDICIARY)

    assert mapper.get_all_sectors_for_committee("House", "Judiciary")
    assert mapper.get_all_sectors_for_committee(
        "House", "Committee on the Judiciary")


def test_typografische_apostrophe_trennen_keine_units(mapper_factory) -> None:
    """Roster und Mapping schreiben 'Veterans’ Affairs' verschieden."""
    mapper = mapper_factory(VETERANS_SUB)

    assert mapper.get_sectors_for_subcommittee(
        "House", "Committee on Veterans’ Affairs", "Subcommittee on Health")


def test_das_subcommittee_praefix_faellt_weg(mapper_factory) -> None:
    mapper = mapper_factory(VETERANS_SUB)

    assert mapper.get_sectors_for_subcommittee(
        "House", "Committee on Veterans' Affairs", "Health")


# -- Joint-Fallback --------------------------------------------------------
def test_joint_trifft_beide_kammern(mapper_factory) -> None:
    """Der Matcher fuehrt 4 Joint-Units; die Trade-Seite kennt nur die Kammer."""
    joint = _zuordnung("joint--economic", "joint", "Joint Economic Committee",
                       "Joint Economic Committee", "committee_direct",
                       "Financial Services", "Banks - Diversified", 3)
    mapper = mapper_factory(joint)

    for kammer in ("House", "Senate"):
        treffer = mapper.get_all_sectors_for_committee(
            kammer, "Joint Economic Committee")
        assert [e["industry"] for e in treffer] == ["Banks - Diversified"]


def test_joint_trifft_beide_kammern_auch_im_subcommittee(mapper_factory) -> None:
    joint_sub = _zuordnung("joint--taxation--rules", "joint",
                           "Joint Committee on Taxation",
                           "Subcommittee on Rules", "subcommittee",
                           "Financial Services", "Capital Markets", 2)
    mapper = mapper_factory(joint_sub)

    assert mapper.get_sectors_for_subcommittee(
        "House", "Joint Committee on Taxation", "Subcommittee on Rules")


def test_die_eigene_kammer_geht_vor_joint(mapper_factory) -> None:
    """Nur wenn die Kammer selbst nichts hat, greift der Fallback."""
    eigen = _zuordnung("senate--economic", "senate", "Economic Committee",
                       "Economic Committee", "committee_direct",
                       "Energy", "Oil & Gas E&P", 4)
    joint = _zuordnung("joint--economic", "joint", "Economic Committee",
                       "Economic Committee", "committee_direct",
                       "Technology", "Semiconductors", 2)
    mapper = mapper_factory(eigen, joint)

    treffer = mapper.get_all_sectors_for_committee("Senate", "Economic Committee")

    assert [e["industry"] for e in treffer] == ["Oil & Gas E&P"]


# -- Parent-Core-Abgrenzung ------------------------------------------------
def test_subcommittees_fuellen_den_committee_lookup_nicht_auf(
        mapper_factory) -> None:
    """Die Legacy-Aggregation entfaellt: sie verwaesserte den Parent-Core.

    Der Matcher erzeugt zu einem Committee mit Subcommittees eigens eine
    `committee_parent_core`-Unit. Wuerden die Subcommittee-Sektoren dort
    zusaetzlich einlaufen, staende im Committee-Lookup etwas, das niemand
    gemappt hat.
    """
    kern = _zuordnung("house--veterans-affairs", "house",
                      "Committee on Veterans' Affairs",
                      "Committee on Veterans' Affairs", "committee_parent_core",
                      "Consumer Defensive", "Education & Training Services", 4)
    mapper = mapper_factory(kern, VETERANS_SUB)

    committee = mapper.get_all_sectors_for_committee(
        "House", "Committee on Veterans' Affairs")

    assert [e["industry"] for e in committee] == ["Education & Training Services"]
    # Das Subcommittee bleibt ueber seinen eigenen Lookup erreichbar.
    assert [e["industry"] for e in mapper.get_sectors_for_subcommittee(
        "House", "Committee on Veterans' Affairs", "Subcommittee on Health")] \
        == ["Medical Care Facilities"]


def test_ein_committee_ohne_eigene_zuordnung_bleibt_leer(mapper_factory) -> None:
    mapper = mapper_factory(VETERANS_SUB)

    assert mapper.get_all_sectors_for_committee(
        "House", "Committee on Veterans' Affairs") == []


# -- Dublettenwahl nach confidence -----------------------------------------
def test_bei_dubletten_gewinnt_die_hoehere_konfidenz(mapper_factory) -> None:
    """`Sector_Rank` ist weg; seine Aufgabe hat `confidence` uebernommen."""
    schwach = _zuordnung("joint--x", "joint", "X Committee", "X Committee",
                         "committee_direct", "Energy", "Oil & Gas E&P", 2)
    stark = _zuordnung("house--x", "house", "X Committee", "X Committee",
                       "committee_direct", "Energy", "Oil & Gas E&P", 4)
    mapper = mapper_factory(stark, schwach)

    treffer = mapper.get_all_sectors_for_committee("House", "X Committee")

    assert len(treffer) == 1
    assert treffer[0]["confidence"] == 4


def test_die_dublettenwahl_haengt_nicht_an_der_reihenfolge() -> None:
    hoch = {"sector": "Energy", "industry": "Oil & Gas E&P", "confidence": 4}
    niedrig = {"sector": "Energy", "industry": "Oil & Gas E&P", "confidence": 1}

    assert dedupe_entries([niedrig, hoch])[0]["confidence"] == 4
    assert dedupe_entries([hoch, niedrig])[0]["confidence"] == 4


def test_ohne_numerische_konfidenz_entscheidet_weiter_der_rang() -> None:
    """Legacy-Eintraege tragen 'Hoch' als Wort und einen Sector_Rank."""
    primaer = {"sector": "Energy", "industry": "Oil & Gas E&P", "rank": 1,
               "confidence": "Hoch"}
    sekundaer = {"sector": "Energy", "industry": "Oil & Gas E&P", "rank": 3,
                 "confidence": "Hoch"}

    assert dedupe_entries([sekundaer, primaer])[0]["rank"] == 1


def test_verschiedene_industries_bleiben_nebeneinander() -> None:
    eintraege = [
        {"sector": "Energy", "industry": "Oil & Gas E&P", "confidence": 3},
        {"sector": "Energy", "industry": "Uranium", "confidence": 2},
    ]

    assert len(dedupe_entries(eintraege)) == 2


def test_die_rueckgabe_ist_eine_kopie(mapper_factory) -> None:
    """Der Aufrufer haengt Rollen an die Eintraege; der Lookup bleibt sauber."""
    mapper = mapper_factory(ARMED_SERVICES)

    erste = mapper.get_all_sectors_for_committee("Senate", "Armed Services")
    erste[0]["committee_role"] = "Chairman"
    zweite = mapper.get_all_sectors_for_committee("Senate", "Armed Services")

    assert "committee_role" not in zweite[0]


# -- der Legacy-Weg bleibt begehbar ----------------------------------------
def test_mit_pfad_liest_der_mapper_weiter_die_legacy_csv(tmp_path) -> None:
    """Der Schattenvergleich baut beide Staende nebeneinander."""
    csv = tmp_path / "legacy.csv"
    csv.write_text(
        "Chamber,Committee,Subcommittee,Sector,Industry,Sector_Rank,Konfidenz\n"
        "House,Armed Services,Readiness,Industrials,Aerospace & Defense,1,Hoch\n",
        encoding="utf-8")

    mapper = CommitteeMapper(mapping_path=str(csv))

    assert mapper.revision is None
    treffer = mapper.get_sectors_for_subcommittee(
        "House", "Armed Services", "Readiness")
    assert treffer[0]["industry"] == "Aerospace & Defense"
    assert treffer[0]["rank"] == 1


def test_der_legacy_weg_aggregiert_weiter_seine_subcommittees(tmp_path) -> None:
    """Die Parent-Core-Abgrenzung gilt fuer V2, nicht rueckwirkend."""
    csv = tmp_path / "legacy.csv"
    csv.write_text(
        "Chamber,Committee,Subcommittee,Sector,Industry,Sector_Rank,Konfidenz\n"
        "House,Armed Services,Readiness,Industrials,Aerospace & Defense,1,Hoch\n",
        encoding="utf-8")

    mapper = CommitteeMapper(mapping_path=str(csv))

    assert mapper.get_all_sectors_for_committee("House", "Armed Services")


# -- der Ausweis -----------------------------------------------------------
def test_der_mapper_weist_seine_revision_aus(mapper_factory) -> None:
    mapper = mapper_factory(ARMED_SERVICES, VETERANS_SUB)

    assert mapper.revision.review_id == "map-test"
    assert mapper.get_statistics() == {
        "total_mappings": 2, "unique_subcommittees": 1, "unique_committees": 1}


def test_der_chamber_wert_der_tradeseite_wird_kleingeschrieben(
        mapper_factory) -> None:
    """Die Trade-Seite liefert 'House', der Matcher fuehrt 'house'."""
    mapper = mapper_factory(ARMED_SERVICES)

    assert mapper.get_all_sectors_for_committee("SENATE", "Armed Services")
    assert mapper.get_all_sectors_for_committee(" senate ", "Armed Services")


# -- Parent-Core additiv (Option A) ----------------------------------------
class _StubMapper:
    """Mapper mit gestellten Antworten; `revision` markiert den V2-Weg."""

    def __init__(self, committee, subcommittee, revision=object()):
        self._committee = committee
        self._subcommittee = subcommittee
        self.revision = revision

    def get_all_sectors_for_committee(self, chamber, committee):
        return [e.copy() for e in self._committee]

    def get_sectors_for_subcommittee(self, chamber, committee, subcommittee):
        return [e.copy() for e in self._subcommittee]


KERN = [{"sector": "Consumer Defensive", "industry": "Farm Products",
         "confidence": 4}]
SUB = [{"sector": "Consumer Defensive", "industry": "Packaged Foods",
        "confidence": 3}]

#: Ein Mitglied im Ausschuss mit einem Unterausschuss-Sitz, dort Vorsitz.
ROSTER = "Agriculture|:Livestock|Chairman"


def _tag(monkeypatch, mapper, additiv=True):
    from tags.committee_tag import CommitteeTag
    monkeypatch.setattr(committee_mapping.settings,
                        "COMMITTEE_PARENT_CORE_ADDITIVE", additiv,
                        raising=False)
    tag = CommitteeTag(mapper=mapper)
    tag.match_mode = "sector_or_industry"
    return tag


def test_der_parent_core_gilt_auch_bei_unterausschuss_sitz(monkeypatch) -> None:
    """Sonst erreichten die 31 Parent-Core-Units fast niemanden."""
    tag = _tag(monkeypatch, _StubMapper(KERN, SUB))

    ergebnis = tag.calculate_relevance(
        ROSTER, "House", ticker_sector="Consumer Defensive",
        ticker_industry="Farm Products")

    industries = {e["industry"] for e in ergebnis["insider_sectors"]}
    assert industries == {"Farm Products", "Packaged Foods"}


def test_die_unterausschuss_rolle_faerbt_nicht_auf_den_kern_ab(
        monkeypatch) -> None:
    """Der Vorsitz im Unterausschuss ist keine Rolle im Ausschuss.

    Geprueft an einem Ticker, den nur der Kern trifft: traegt der
    Kerneintrag die Rolle des Unterausschusses mit, stuende dessen Bonus in
    den Rollenkeys, obwohl der Unterausschuss den Trade gar nicht beruehrt.
    """
    fremd = [{"sector": "Technology", "industry": "Semiconductors",
              "confidence": 3}]
    tag = _tag(monkeypatch, _StubMapper(KERN, fremd))

    ergebnis = tag.calculate_relevance(
        "Agriculture|Chairman:Livestock|Chairman", "House",
        ticker_sector="Consumer Defensive", ticker_industry="Farm Products")

    kern = [e for e in ergebnis["insider_sectors"]
            if e["industry"] == "Farm Products"][0]
    assert kern["subcommittee_role"] == ""
    assert kern["subcommittee"] == ""
    assert ergebnis["ex_officio_role_keys"] == "committee_chairman"


def test_abgeschaltet_bleibt_es_beim_bisherigen_verhalten(monkeypatch) -> None:
    tag = _tag(monkeypatch, _StubMapper(KERN, SUB), additiv=False)

    ergebnis = tag.calculate_relevance(
        ROSTER, "House", ticker_sector="Consumer Defensive",
        ticker_industry="Packaged Foods")

    assert {e["industry"] for e in ergebnis["insider_sectors"]} == {"Packaged Foods"}


def test_der_legacy_mapper_bleibt_unberuehrt(monkeypatch) -> None:
    """Dort aggregiert der Committee-Lookup die Unterausschuesse selbst.

    Dieselbe Regel bedeutete auf dem alten Weg etwas anderes und veraenderte
    den Stand, gegen den der Schattenvergleich misst.
    """
    tag = _tag(monkeypatch, _StubMapper(KERN, SUB, revision=None))

    ergebnis = tag.calculate_relevance(
        ROSTER, "House", ticker_sector="Consumer Defensive",
        ticker_industry="Packaged Foods")

    assert tag.parent_core_additive is False
    assert {e["industry"] for e in ergebnis["insider_sectors"]} == {"Packaged Foods"}
