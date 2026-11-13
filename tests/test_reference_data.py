"""Tests für utils/reference_data.py gegen die eingecheckten Tabellen unter data/reference/."""
import pandas as pd
import pytest

from tags.committee_tag import CommitteeTag
from utils.committee_parser import parse_committees_full
from utils.reference_data import (ReferenceData, STATUS_MATCHED, STATUS_NO_MEMBERSHIP,
                                  STATUS_NOT_COVERED)


@pytest.fixture(scope='module')
def ref():
    return ReferenceData()


def _trades(rows):
    df = pd.DataFrame(rows, columns=['bio_guide_id', 'transaction_date', 'chamber'])
    df['transaction_date'] = pd.to_datetime(df['transaction_date'])
    df['Member_Party'], df['Member_State'] = 'Democratic', 'California'
    return df


def test_three_states_are_distinguishable(ref):
    out = ref.assign(_trades([
        ('K000389', '2017-05-01', 'House'),    # Ro Khanna vor dem 118. Kongress
        ('K000389', '2025-06-01', 'House'),
        ('K000389', '2023-06-01', 'House'),    # 118 übernimmt die Daten des 119
        ('P000197', '2025-06-01', 'House'),    # Nancy Pelosi: kein Ausschuss im 119.
        ('K000389', None, 'House'),
    ]))
    assert list(out['Member_Committee_Status']) == [
        STATUS_NOT_COVERED, STATUS_MATCHED, STATUS_MATCHED, STATUS_NO_MEMBERSHIP, STATUS_NOT_COVERED]
    assert list(out['Member_Congress']) == ['', '119', '118', '119', '']
    assert out['Member_Committees_Full'][0] == '' and out['Member_Committees_Full'][3] == ''
    assert out['Member_Committees_Full'][1] == out['Member_Committees_Full'][2] != ''
    assert set(out['Member_Party']) == {'Democratic'} and set(out['Member_State']) == {'California'}
    assert list(out['Member_ExOfficio']) == ['', 'false', 'false', '', '']


def test_committees_string_is_f2_with_level_roles(ref):
    text, ex_officio = ref.member_committees('K000389', '119')
    parsed = {c['committee']: c for c in parse_committees_full(text)}
    armed = parsed['Committee on Armed Services']
    assert armed['role'] == ''
    assert {'name': 'Cyber, Information Technologies, and Innovation', 'role': 'Ranking Member'} in armed['subcommittees']
    assert ex_officio is False
    # Quelle: "Chair" auf Ausschussebene (Bryan Steil, Joint Committee on the Library) -> F2 "Chairman"
    steil = {c['committee']: c['role'] for c in parse_committees_full(ref.member_committees('S001213', '119')[0])}
    assert steil['Joint Committee of Congress on the Library'] == 'Chairman'


def test_coverage_comes_from_congresses_table_only(ref, tmp_path):
    for name in ('members', 'committees', 'committee_memberships', 'committee_industries'):
        pd.read_csv(f'data/reference/{name}.csv', dtype=str, keep_default_na=False).to_csv(tmp_path / f'{name}.csv', index=False)
    congresses = pd.read_csv('data/reference/congresses.csv', dtype=str)
    extra = pd.DataFrame([{'congress': '117', 'start_date': '2021-01-03', 'end_date': '2023-01-03', 'data_from_congress': '119'}])
    pd.concat([congresses, extra]).to_csv(tmp_path / 'congresses.csv', index=False)
    trade = _trades([('K000389', '2021-06-01', 'House')])
    assert ref.assign(trade)['Member_Committee_Status'][0] == STATUS_NOT_COVERED
    assert ReferenceData(tmp_path).assign(trade)['Member_Committee_Status'][0] == STATUS_MATCHED


def test_mapper_resolves_every_name_it_generates(ref):
    mapper = ref.mapper('118.0')            # Stage-CSV liefert die Kongressnummer als Float zurück
    assert mapper.congress == '119'
    industries = pd.read_csv('data/reference/committee_industries.csv', dtype=str, keep_default_na=False)
    expected = industries.groupby('committee_id')['industry_name'].apply(set).to_dict()
    committees = pd.read_csv('data/reference/committees.csv', dtype=str, keep_default_na=False)
    names = {r.committee_id: r for r in committees.itertuples()}
    for row in committees.itertuples():
        chambers = ['House', 'Senate'] if row.chamber == 'joint' else [row.chamber.title()]
        top = names[row.parent_committee_id] if row.parent_committee_id else row
        top_name = top.name.removeprefix('House ').removeprefix('Senate ')
        for chamber in chambers:
            got = (mapper.get_sectors_for_subcommittee(chamber, top_name, row.name) if row.parent_committee_id
                   else mapper.get_all_sectors_for_committee(chamber, top_name))
            assert {e['industry'] for e in got} == expected.get(row.committee_id, set()), row.committee_id


def test_committee_tag_reads_reference_mapper(ref):
    text, _ = ref.member_committees('K000389', '119')
    tag = CommitteeTag(mapper=ref.mapper('119'))
    result = tag.calculate_relevance(text, 'House', 'LMT', 'Industrials', 'Aerospace & Defense')
    assert result['insider_sectors']
    assert all(isinstance(e['confidence'], int) for e in result['insider_sectors'])
    assert [e['match_type'] for e in result['relevant_sectors']] == ['full']
