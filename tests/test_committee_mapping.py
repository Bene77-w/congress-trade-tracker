"""Tests fuer config/committee_mapping.py — CommitteeMapper-Loader.

Pinnt insbesondere das Committee-Level-Zeilen-Verhalten: Zeilen mit leerem
Subcommittee sind bewusste Committee-weite Mappings (Joint/Select ohne Subs)
und muessen ins Committee-Level-Dict einfliessen statt verworfen zu werden.
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


CSV_HEADER = 'Chamber,Committee,Subcommittee,Sector,Industry,Sector_Rank,Konfidenz\n'


def _mapper_for(tmp_path: Path, rows: list[str]) -> CommitteeMapper:
    csv_path = tmp_path / 'mapping.csv'
    csv_path.write_text(CSV_HEADER + ''.join(r + '\n' for r in rows),
                        encoding='utf-8')
    return CommitteeMapper(mapping_path=str(csv_path))


def test_subcommittee_rows_land_in_both_lookups(tmp_path):
    m = _mapper_for(tmp_path, [
        'House,Alpha,SubA,Technology,Software,1,Hoch',
    ])
    assert m.get_sectors_for_subcommittee('House', 'Alpha', 'SubA')
    assert m.get_all_sectors_for_committee('House', 'Alpha')


def test_committee_level_row_lands_only_in_committee_lookup(tmp_path):
    """Leeres Subcommittee = Committee-weite Zeile -> Committee-Lookup ja,
    Subcommittee-Lookup (auch mit leerem Namen) nein."""
    m = _mapper_for(tmp_path, [
        'House,Joint Economic Committee,,Financial Services,Capital Markets,1,Mittel',
    ])
    result = m.get_all_sectors_for_committee('House', 'Joint Economic Committee')
    assert [(e['sector'], e['industry']) for e in result] == \
        [('Financial Services', 'Capital Markets')]
    assert m.get_sectors_for_subcommittee(
        'House', 'Joint Economic Committee', '') == []
    assert ('house', 'joint economic committee', '') not in m.mapping


def test_committee_level_rows_merge_with_sub_aggregation(tmp_path):
    """Committee mit Subs UND expliziter Top-up-Zeile: Aggregation liefert
    beide; Dedup auf (sector, industry) mit bestem Rank bleibt erhalten."""
    m = _mapper_for(tmp_path, [
        'House,Alpha,SubA,Technology,Software,2,Hoch',
        'House,Alpha,,Consumer Cyclical,Auto Manufacturers,1,Mittel',
        'House,Alpha,,Technology,Software,1,Mittel',  # gleiche Kombi, besserer Rank
    ])
    result = m.get_all_sectors_for_committee('House', 'Alpha')
    by_key = {(e['sector'], e['industry']): e for e in result}
    assert set(by_key) == {('Technology', 'Software'),
                           ('Consumer Cyclical', 'Auto Manufacturers')}
    assert by_key[('Technology', 'Software')]['rank'] == 1
    # Subcommittee-Lookup bleibt granular (nur die Sub-Zeile, Rank 2).
    sub = m.get_sectors_for_subcommittee('House', 'Alpha', 'SubA')
    assert len(sub) == 1 and sub[0]['rank'] == 2


def test_lookup_collapses_inner_whitespace(tmp_path):
    """Quelldaten-Doppel-Leerzeichen (Senate-XML SSBK-Sub) duerfen keinen
    stillen Lookup-Miss erzeugen — _normalize kollabiert innere Whitespaces."""
    m = _mapper_for(tmp_path, [
        'Senate,"Banking, Housing, and Urban Affairs",'
        'Subcommittee on National Security and International Trade and Finance,'
        'Financial Services,Banks - Diversified,1,Mittel',
    ])
    assert m.get_sectors_for_subcommittee(
        'Senate', 'Banking,  Housing, and Urban Affairs',
        'Subcommittee on National Security and  International Trade and Finance')


def test_rows_without_committee_still_skipped(tmp_path):
    m = _mapper_for(tmp_path, [
        'House,,SubA,Technology,Software,1,Hoch',
        ',Alpha,SubA,Technology,Software,1,Hoch',
    ])
    assert m.mapping == {}
    assert m.committee_mapping == {}


def test_real_csv_joint_and_select_committees_covered():
    """Regression gegen das echte Mapping-CSV: die zuvor verworfenen
    Committee-Level-Zeilen (Joint/Select) liefern jetzt Sektoren."""
    # Der Pfad steht seit P3 ausdruecklich hier: ohne ihn laedt der Mapper
    # die aktive Mappingrevision, und dieser Test misst die Legacy-CSV.
    m = CommitteeMapper(mapping_path=str(
        ROOT / settings.DATA_MAPPINGS_PATH / settings.COMMITTEE_MAPPING_FILE))
    for committee in ('Joint Economic Committee',
                      'Joint Committee On Taxation',
                      'Permanent Select Committee On Intelligence'):
        assert m.get_all_sectors_for_committee('House', committee), committee


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
