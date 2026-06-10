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

from tags.committee_tag import CommitteeTag  # noqa: E402

# Realer Walberg-Roster-String in F2 (s. tests/test_member_metrics.py).
WALBERG = ('Education And Workforce|Chairman:Early Childhood, Elementary, And '
           'Secondary Education|Ex Officio~Health, Employment, Labor, And '
           'Pensions|Ex Officio~Higher Education And Workforce Development|Ex '
           'Officio~Workforce Protections|Ex Officio;Natural Resources|:Indian '
           'And Insular Affairs|~Water, Wildlife And Fisheries|')


@pytest.fixture(scope='module')
def tag() -> CommitteeTag:
    t = CommitteeTag()
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
