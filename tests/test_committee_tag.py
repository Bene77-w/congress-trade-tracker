"""Tests fuer tags/committee_tag.py — CommitteeTag.calculate_relevance.

Pinnt die F2-Integration des zentralen Parsers, die zwei expliziten Rollen-
Ebenen-Felder (committee_role/subcommittee_role), die eigene Zustaendigkeit
des Vollausschusses und die Dublettenwahl. Laeuft gegen die eingecheckten
Referenztabellen (data/reference/, 119. Kongress).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tags.committee_tag import (  # noqa: E402
    CommitteeTag,
    dedupe_entries,
    role_keys_for_entries,
)
from utils.reference_data import ReferenceData  # noqa: E402

# F2-String aus den Referenztabellen (Tim Walberg, 119. Kongress).
WALBERG = ('Committee on Education and Workforce|Chairman;'
           'Committee on Natural Resources|:Water, Wildlife and Fisheries|'
           '~Indian and Insular Affairs|')
WATER = 'Water, Wildlife and Fisheries'


@pytest.fixture(scope='module')
def tag() -> CommitteeTag:
    t = CommitteeTag(mapper=ReferenceData().mapper('119'))
    # Match-Modus fixieren, damit der Test nicht von settings abhaengt.
    t.match_mode = 'sector_or_industry'
    return t


def test_trailing_sub_sectors_present(tag):
    """Der alte Parser flachte ab und verlor trailing Subs. Mit F2 muessen die
    Sektoren des LETZTEN Subs des ZWEITEN Committees vorhanden sein."""
    res = tag.calculate_relevance(WALBERG, 'House')
    subs_seen = {s['subcommittee'] for s in res['insider_sectors']}
    assert {'Indian and Insular Affairs', WATER} <= subs_seen


def test_role_fields_carry_both_levels(tag):
    """Jeder Sektor-Eintrag traegt committee_role UND subcommittee_role
    (statt des frueheren vermischten 'role')."""
    res = tag.calculate_relevance(
        f'Committee on Natural Resources|Chairman:{WATER}|Ex Officio', 'House')
    assert res['insider_sectors'], 'Mapping lieferte keine Sektoren'
    for entry in res['insider_sectors']:
        assert entry['committee_role'] == 'Chairman'
        assert 'role' not in entry
    sub = [e for e in res['insider_sectors'] if e['subcommittee'] == WATER]
    assert sub and all(e['subcommittee_role'] == 'Ex Officio' for e in sub)


def test_committee_without_subs_maps_committee_wide(tag):
    """Committee ohne Subs (F2: kein ':') -> committee-weite Sektoren,
    subcommittee_role leer, subcommittee leer."""
    res = tag.calculate_relevance('Committee on the Judiciary|Chairman',
                                  'Senate')
    assert res['insider_sectors']
    for entry in res['insider_sectors']:
        assert entry['committee_role'] == 'Chairman'
        assert entry['subcommittee_role'] == ''
        assert entry['subcommittee'] == ''


def test_relevant_sectors_match_carries_role_fields(tag):
    """Match-Pass: relevante Treffer behalten beide Rollen-Felder +
    match_type."""
    res = tag.calculate_relevance(
        f'Committee on Natural Resources|:{WATER}|Chair', 'House',
        ticker_sector='Utilities',
        ticker_industry='Utilities - Regulated Water')
    assert res['match_count'] >= 1
    for entry in res['relevant_sectors']:
        assert entry['match_type'] in ('full', 'sector')
        assert entry['committee_role'] == ''
        assert entry['subcommittee_role'] == 'Chair'


# ---------------------------------------------------------------------------
# Eigene Zustaendigkeit des Vollausschusses
# ---------------------------------------------------------------------------

class _StubMapper:
    """Mapper mit gestellten Antworten, ohne weitere Attribute."""

    def __init__(self, committee, subcommittee):
        self._committee = committee
        self._subcommittee = subcommittee

    def get_all_sectors_for_committee(self, chamber, committee):
        return [e.copy() for e in self._committee]

    def get_sectors_for_subcommittee(self, chamber, committee, subcommittee):
        return [e.copy() for e in self._subcommittee]


KERN = [{'sector': 'Consumer Defensive', 'industry': 'Farm Products',
         'confidence': 4}]
SUB = [{'sector': 'Consumer Defensive', 'industry': 'Packaged Foods',
        'confidence': 3}]


def _stub_tag(committee, subcommittee) -> CommitteeTag:
    t = CommitteeTag(mapper=_StubMapper(committee, subcommittee))
    t.match_mode = 'sector_or_industry'
    return t


def test_der_vollausschuss_gilt_auch_bei_unterausschuss_sitz():
    """Sonst erreichte die Zustaendigkeit des Ausschusses nur Mitglieder
    ohne Unterausschuss-Sitz."""
    res = _stub_tag(KERN, SUB).calculate_relevance(
        'Agriculture|:Livestock|Chairman', 'House')
    assert {e['industry'] for e in res['insider_sectors']} == {
        'Farm Products', 'Packaged Foods'}


def test_die_unterausschuss_rolle_faerbt_nicht_auf_den_kern_ab():
    """Geprueft an einem Ticker, den nur der Kern trifft: truege der
    Kerneintrag die Rolle des Unterausschusses, stuende dessen Bonus in den
    Rollenkeys, obwohl der Unterausschuss den Trade nicht beruehrt."""
    fremd = [{'sector': 'Technology', 'industry': 'Semiconductors',
              'confidence': 3}]
    res = _stub_tag(KERN, fremd).calculate_relevance(
        'Agriculture|Chairman:Livestock|Chairman', 'House',
        ticker_sector='Consumer Defensive', ticker_industry='Farm Products')
    kern = [e for e in res['insider_sectors']
            if e['industry'] == 'Farm Products'][0]
    assert kern['subcommittee_role'] == ''
    assert kern['subcommittee'] == ''
    assert res['ex_officio_role_keys'] == 'committee_chairman'


# ---------------------------------------------------------------------------
# Dublettenwahl
# ---------------------------------------------------------------------------

def test_die_dublettenwahl_haengt_nicht_an_der_reihenfolge():
    hoch = {'sector': 'Energy', 'industry': 'Oil & Gas E&P', 'confidence': 4}
    niedrig = {'sector': 'Energy', 'industry': 'Oil & Gas E&P', 'confidence': 1}
    assert dedupe_entries([niedrig, hoch])[0]['confidence'] == 4
    assert dedupe_entries([hoch, niedrig])[0]['confidence'] == 4


def test_verschiedene_industries_bleiben_nebeneinander():
    eintraege = [
        {'sector': 'Energy', 'industry': 'Oil & Gas E&P', 'confidence': 3},
        {'sector': 'Energy', 'industry': 'Uranium', 'confidence': 2},
    ]
    assert len(dedupe_entries(eintraege)) == 2


# ---------------------------------------------------------------------------
# Ex-Officio-Role-Keys (Phase 4 — Basis des rollen-differenzierten Bonus)
# ---------------------------------------------------------------------------

def test_role_keys_mapping_levels_and_aliases():
    """Ebenen-abhaengiges Mapping inkl. Cochairman->Chairman-Key und
    Ex Officio als eigener Key pro Ebene."""
    assert role_keys_for_entries([
        {'committee_role': 'Chairman', 'subcommittee_role': ''},
    ]) == ['committee_chairman']
    assert role_keys_for_entries([
        {'committee_role': 'Cochairman', 'subcommittee_role': ''},
    ]) == ['committee_chairman']
    assert role_keys_for_entries([
        {'committee_role': 'Ex Officio', 'subcommittee_role': ''},
    ]) == ['committee_ex_officio']
    assert role_keys_for_entries([
        {'committee_role': '', 'subcommittee_role': 'Vice Chair'},
    ]) == ['subcommittee_vice_chair']
    assert role_keys_for_entries([
        {'committee_role': '', 'subcommittee_role': 'Ranking Member'},
    ]) == ['subcommittee_ranking_member']
    assert role_keys_for_entries([
        {'committee_role': 'Chairman', 'subcommittee_role': 'Ex Officio'},
    ]) == ['committee_chairman', 'subcommittee_ex_officio']
    # Unbekannte/leere Rollen -> kein Key
    assert role_keys_for_entries([
        {'committee_role': '', 'subcommittee_role': ''},
        {'committee_role': 'Mystery Role', 'subcommittee_role': ''},
    ]) == []


def test_ex_officio_role_keys_end_to_end(tag):
    """Treffer mit Committee-Chairman + Sub-Chair -> beide Keys, sortiert."""
    res = tag.calculate_relevance(
        f'Committee on Natural Resources|Chairman:{WATER}|Chair', 'House',
        ticker_sector='Utilities',
        ticker_industry='Utilities - Regulated Water')
    assert res['match_count'] >= 1
    assert res['ex_officio_role_keys'] == \
        'committee_chairman;subcommittee_chair'


def test_ex_officio_role_keys_empty_without_leadership(tag):
    res = tag.calculate_relevance(
        f'Committee on Natural Resources|:{WATER}|', 'House',
        ticker_sector='Utilities',
        ticker_industry='Utilities - Regulated Water')
    assert res['match_count'] >= 1
    assert res['ex_officio_role_keys'] == ''


def test_ex_officio_role_keys_empty_without_ticker_info(tag):
    """Ohne Ticker-Sektor/Industry gibt es keine relevanten Treffer ->
    keine Role-Keys."""
    res = tag.calculate_relevance(WALBERG, 'House')
    assert res['ex_officio_role_keys'] == ''


class _FakeDedupMapper:
    """Zwei Committees mappen dieselbe (Sector, Industry)-Kombination:
    'Alpha' rollenlos mit Konfidenz 3, 'Beta' (Sub-Chair) mit Konfidenz 2.
    Die Dedup behaelt den rollenlosen Eintrag."""

    def get_sectors_for_subcommittee(self, chamber, committee, subcommittee):
        if committee == 'Alpha':
            return [{'sector': 'Technology', 'industry': 'Software',
                     'confidence': 3}]
        if committee == 'Beta':
            return [{'sector': 'Technology', 'industry': 'Software',
                     'confidence': 2}]
        return []

    def get_all_sectors_for_committee(self, chamber, committee):
        return []


def test_role_keys_survive_dedup_displacement():
    """Prae-Dedup-Garantie: verdraengt die (sector, industry)-Dedup den
    Fuehrungsrollen-Eintrag, muss der Role-Key trotzdem in
    ex_officio_role_keys stehen."""
    t = CommitteeTag(mapper=_FakeDedupMapper())
    t.match_mode = 'sector_or_industry'
    res = t.calculate_relevance(
        'Alpha|:SubA|;Beta|:SubB|Chair', 'House',
        ticker_sector='Technology', ticker_industry='Software')
    # Dedup behaelt nur den Alpha-Eintrag (hoehere Konfidenz, rollenlos) ...
    assert len(res['relevant_sectors']) == 1
    assert res['relevant_sectors'][0]['committee'] == 'Alpha'
    assert res['relevant_sectors'][0]['subcommittee_role'] == ''
    # ... aber der Sub-Chair-Key von Beta bleibt erhalten.
    assert res['ex_officio_role_keys'] == 'subcommittee_chair'
