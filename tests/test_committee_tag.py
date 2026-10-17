"""Tests fuer tags/committee_tag.py — CommitteeTag.calculate_relevance (Phase 3).

Pinnt die F2-Integration des zentralen Parsers, die zwei expliziten Rollen-
Ebenen-Felder (committee_role/subcommittee_role) und Befund A (Artikel-Strip
in committee_mapping._normalize). Laeuft gegen das echte Mapping-CSV
(data/mappings/subcommittee_sector_industry_mapping.csv, im Repo committet).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from config.committee_mapping import CommitteeMapper  # noqa: E402
from tags.committee_tag import (  # noqa: E402
    CommitteeTag,
    role_keys_for_entries,
)

#: Diese Datei prueft die Tag-Logik gegen das Legacy-Mapping und nennt seinen
#: Pfad seit P3 ausdruecklich: ohne Pfad laedt der Mapper die aktive
#: Mappingrevision des Matchers, und die kennt diese Ausschuesse nicht.
LEGACY_CSV = str(ROOT / settings.DATA_MAPPINGS_PATH
                 / settings.COMMITTEE_MAPPING_FILE)

# Realer Walberg-Roster-String in F2 (s. tests/test_member_metrics.py).
WALBERG = ('Education And Workforce|Chairman:Early Childhood, Elementary, And '
           'Secondary Education|Ex Officio~Health, Employment, Labor, And '
           'Pensions|Ex Officio~Higher Education And Workforce Development|Ex '
           'Officio~Workforce Protections|Ex Officio;Natural Resources|:Indian '
           'And Insular Affairs|~Water, Wildlife And Fisheries|')


@pytest.fixture(scope='module')
def tag() -> CommitteeTag:
    t = CommitteeTag(mapper=CommitteeMapper(mapping_path=LEGACY_CSV))
    # Match-Modus fixieren, damit der Test nicht von settings abhaengt.
    t.match_mode = 'sector_or_industry'
    return t


def test_walberg_trailing_sub_sectors_present(tag):
    """Der alte Parser flachte ab und verlor trailing Subs (Walberg 4 statt 20
    Treffer). Mit F2 muessen die Sektoren des LETZTEN Subs des ZWEITEN
    Committees ('Water, Wildlife And Fisheries') vorhanden sein."""
    res = tag.calculate_relevance(WALBERG, 'House')
    subs_seen = {s['subcommittee'] for s in res['insider_sectors']}
    assert 'Water, Wildlife And Fisheries' in subs_seen
    assert 'Workforce Protections' in subs_seen
    # ('Indian And Insular Affairs' hat KEINE Zeile im Mapping-CSV —
    #  inhaltliche Mapping-Luecke, kein Parser-Thema.)


def test_role_fields_carry_both_levels(tag):
    """Jeder Sektor-Eintrag traegt committee_role UND subcommittee_role
    (statt des frueheren vermischten 'role')."""
    res = tag.calculate_relevance(WALBERG, 'House')
    assert res['insider_sectors'], 'Mapping lieferte keine Sektoren'
    for entry in res['insider_sectors']:
        assert 'committee_role' in entry
        assert 'subcommittee_role' in entry
        assert 'role' not in entry

    edu = [e for e in res['insider_sectors']
           if e['committee'] == 'Education And Workforce']
    assert edu, 'Education And Workforce ohne Mapping-Treffer'
    assert all(e['committee_role'] == 'Chairman' for e in edu)
    assert all(e['subcommittee_role'] == 'Ex Officio' for e in edu)

    nat = [e for e in res['insider_sectors']
           if e['committee'] == 'Natural Resources']
    assert nat, 'Natural Resources ohne Mapping-Treffer'
    assert all(e['committee_role'] == '' for e in nat)


def test_committee_without_subs_maps_committee_wide(tag):
    """Committee ohne Subs (F2: kein ':') -> committee-weite Sektoren,
    subcommittee_role leer, subcommittee leer."""
    res = tag.calculate_relevance('Committee on the Judiciary|Chairman',
                                  'Senate')
    assert res['insider_sectors'], 'Befund-A-Fix greift nicht (Judiciary leer)'
    for entry in res['insider_sectors']:
        assert entry['committee_role'] == 'Chairman'
        assert entry['subcommittee_role'] == ''
        assert entry['subcommittee'] == ''


def test_befund_a_article_stripped_for_subcommittee_lookup(tag):
    """Befund A: 'Committee on the Judiciary' muss das Mapping 'Judiciary'
    treffen — auch auf Subcommittee-Ebene."""
    res = tag.calculate_relevance(
        'Committee on the Judiciary|:'
        'Subcommittee on Privacy, Technology, and the Law|Chair', 'Senate')
    assert res['insider_sectors'], 'Judiciary-Sub ohne Sektoren (Befund A)'
    assert all(e['subcommittee_role'] == 'Chair'
               for e in res['insider_sectors'])


def test_relevant_sectors_match_carries_role_fields(tag):
    """Match-Pass: relevante Treffer behalten beide Rollen-Felder +
    match_type."""
    res = tag.calculate_relevance(
        'Natural Resources|:Water, Wildlife And Fisheries|Chair', 'House',
        ticker_sector='Utilities',
        ticker_industry='Utilities - Regulated Water')
    assert res['match_count'] >= 1
    for entry in res['relevant_sectors']:
        assert entry['match_type'] in ('full', 'sector')
        assert entry['committee_role'] == ''
        assert entry['subcommittee_role'] == 'Chair'


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
        'Natural Resources|Chairman:Water, Wildlife And Fisheries|Chair',
        'House',
        ticker_sector='Utilities',
        ticker_industry='Utilities - Regulated Water')
    assert res['match_count'] >= 1
    assert res['ex_officio_role_keys'] == \
        'committee_chairman;subcommittee_chair'


def test_ex_officio_role_keys_empty_without_leadership(tag):
    res = tag.calculate_relevance(
        'Natural Resources|:Water, Wildlife And Fisheries|', 'House',
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
    'Alpha' rollenlos mit Rank 1, 'Beta' (Sub-Chair) mit Rank 2. Die Dedup
    behaelt den rollenlosen Rank-1-Eintrag."""

    def get_sectors_for_subcommittee(self, chamber, committee, subcommittee):
        if committee == 'Alpha':
            return [{'sector': 'Technology', 'industry': 'Software',
                     'rank': 1, 'confidence': 'Hoch'}]
        if committee == 'Beta':
            return [{'sector': 'Technology', 'industry': 'Software',
                     'rank': 2, 'confidence': 'Hoch'}]
        return []

    def get_all_sectors_for_committee(self, chamber, committee):
        return []


def test_role_keys_survive_dedup_displacement():
    """Prae-Dedup-Garantie: verdraengt die (sector, industry)-Dedup den
    Fuehrungsrollen-Eintrag (rollenlos Rank 1 schlaegt Sub-Chair Rank 2),
    muss der Role-Key trotzdem in ex_officio_role_keys stehen."""
    t = CommitteeTag(mapper=_FakeDedupMapper())
    t.match_mode = 'sector_or_industry'
    res = t.calculate_relevance(
        'Alpha|:SubA|;Beta|:SubB|Chair', 'House',
        ticker_sector='Technology', ticker_industry='Software')
    # Dedup behaelt nur den Alpha-Eintrag (Rank 1, rollenlos) ...
    assert len(res['relevant_sectors']) == 1
    assert res['relevant_sectors'][0]['committee'] == 'Alpha'
    assert res['relevant_sectors'][0]['subcommittee_role'] == ''
    # ... aber der Sub-Chair-Key von Beta bleibt erhalten.
    assert res['ex_officio_role_keys'] == 'subcommittee_chair'
