"""Tests für scripts/build_reference_data.py.

Fixtures unter tests/fixtures/reference/ sind unveränderte Ausschnitte der Quelldateien
(unitedstates/congress-legislators, Commits a9ee69e4 und 97261713) und der Mapping-Revision
map-fullrun-20261016. Kein Netz.
"""
import csv
from pathlib import Path

import pytest

from scripts import build_reference_data as ref

FIX = Path(__file__).parent / 'fixtures' / 'reference'
APRIL, NOVEMBER = '2026-04-22', '2026-11-04'


def _csv(name):
    with (FIX / name).open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope='module')
def committees():
    return ref.build_committees(119, ref._yaml(FIX / 'committees-current.yaml'))


@pytest.fixture(scope='module')
def memberships():
    # absichtlich in falscher Reihenfolge übergeben: sortiert wird nach Datum
    snapshots = [(NOVEMBER, ref._yaml(FIX / f'membership-{NOVEMBER}.yaml')),
                 (APRIL, ref._yaml(FIX / f'membership-{APRIL}.yaml'))]
    return {(m['committee_id'], m['bioguide_id']): m for m in ref.build_memberships(119, snapshots)}


def test_congress_dates():
    assert ref.congress_dates(119) == ('2025-01-03', '2027-01-03')
    assert ref.congress_dates(118) == ('2023-01-03', '2025-01-03')
    assert ref.congress_dates(120)[0] == '2027-01-03'


def test_committees_get_ids_parent_and_chamber(committees):
    by_id = {c['committee_id']: c for c in committees}
    assert len(by_id) == len(committees) == 13
    assert by_id['HSWM'] == {'congress': 119, 'committee_id': 'HSWM', 'chamber': 'house',
                             'parent_committee_id': '', 'name': 'House Committee on Ways and Means'}
    assert by_id['HSWM05']['parent_committee_id'] == 'HSWM'
    assert by_id['HSWM05']['name'] == 'Tax'
    assert by_id['SSEV15']['chamber'] == 'senate'
    assert by_id['JSLC']['chamber'] == 'joint'


def test_memberships_union_all_snapshots(memberships):
    left = memberships['SSEV09', 'G000359']        # Lindsey Graham, nur im April-Stand
    assert (left['first_seen'], left['last_seen']) == (APRIL, APRIL)
    joined = memberships['SSEV09', 'G000608']      # Nachfolgerin, nur im November-Stand
    assert (joined['first_seen'], joined['last_seen']) == (NOVEMBER, NOVEMBER)
    stayed = [m for (cid, _), m in memberships.items() if cid == 'SSEV09'
              and (m['first_seen'], m['last_seen']) == (APRIL, NOVEMBER)]
    assert len(stayed) == 10
    assert sum(1 for cid, _ in memberships if cid == 'SSEV09') == 12   # 11 je Stand, vereinigt 12


def test_memberships_keep_roles_as_delivered(memberships):
    titles = {m['title'] for m in memberships.values()}
    assert {'Chairman', 'Ranking Member', 'Ex Officio', 'Vice Chairman', 'Chair', ''} <= titles
    assert {m['side'] for m in memberships.values()} == {'majority', 'minority'}
    joint = [m for (cid, _), m in memberships.items() if cid == 'JSLC']
    assert {m['member_chamber'] for m in joint} == {'house', 'senate'}
    assert all(m['member_chamber'] == '' for (cid, _), m in memberships.items() if cid == 'SSEV09')


def test_members_cover_current_and_recent_historical():
    current = ref._yaml(FIX / 'legislators-current.yaml')
    historical = ref._yaml(FIX / 'legislators-historical.yaml')
    members = {m['bioguide_id']: m for m in ref.build_members(current, historical, '2012-01-01')}
    assert set(members) == {'R000618', 'M001244', 'G000359'}   # B000226 (1789-1793) fällt heraus
    assert members['R000618']['full_name'] == 'Pete Ricketts'
    assert members['R000618']['chamber'] == 'senate'
    assert members['R000618']['in_office'] == 1
    assert members['G000359']['in_office'] == 0
    assert members['G000359']['state'] == 'SC'
    assert 'B000226' in {m['bioguide_id'] for m in ref.build_members(current, historical, '1700-01-01')}


OVERRIDES = [
    {'revision_id': 'map-fullrun-20261016', 'unit_id': 'house--ways-and-means--tax-policy',
     'committee_id': 'HSWM05', 'reason': 'Test'},
]


def test_units_resolve_by_name_or_override_and_never_by_guess(committees):
    rows = ref.map_units(119, _csv('reviewed_unit_coverage.csv'), committees, OVERRIDES)
    by_unit = {r['unit_id']: r for r in rows}
    assert len(rows) == 6
    assert (by_unit['house--ways-and-means']['committee_id'], by_unit['house--ways-and-means']['match']) == ('HSWM', 'name')
    assert by_unit['house--ways-and-means--health']['committee_id'] == 'HSWM02'
    assert by_unit['joint--library']['committee_id'] == 'JSLC'
    assert by_unit['senate--environment-and-public-works']['committee_id'] == 'SSEV'
    tax = by_unit['house--ways-and-means--tax-policy']
    assert (tax['committee_id'], tax['match']) == ('HSWM05', 'override')
    # "Fisheries, Wildlife, and Water" heißt in der Quelle "Fisheries, Water, and Wildlife":
    # ohne Override bleibt die Unit offen und trägt einen Grund.
    fisheries = by_unit['senate--environment-and-public-works--fisheries-wildlife-and-water']
    assert fisheries['committee_id'] == '' and fisheries['match'] == ''
    assert 'SSEV' in fisheries['unresolved_reason']
    assert all(r['congress'] == 119 and r['revision_id'] == 'map-fullrun-20261016' for r in rows)


def test_all_assignments_arrive_with_provenance(committees):
    unit_map = ref.map_units(119, _csv('reviewed_unit_coverage.csv'), committees, OVERRIDES)
    assignments = _csv('reviewed_assignments.csv')
    rows = ref.build_committee_industries(119, assignments, unit_map)
    assert len(rows) == len(assignments) == 8
    assert {(r['unit_id'], r['industry_id'], r['confidence']) for r in rows} == {
        (a['unit_id'], a['industry_id'], a['confidence']) for a in assignments}
    assert {r['committee_id'] for r in rows} == {'HSWM', 'HSWM02', 'HSWM05', ''}   # '' = Fisheries, offen
    assert all(r['revision_id'] == 'map-fullrun-20261016' and r['congress'] == 119 for r in rows)


def test_congresses_mark_inherited_data(monkeypatch):
    monkeypatch.setattr(ref.settings, 'REFERENCE_CONGRESS_DATA_FROM', {118: 119, 117: 116})
    assert ref.build_congresses({119}) == [
        {'congress': 118, 'start_date': '2023-01-03', 'end_date': '2025-01-03', 'data_from_congress': 119},
        {'congress': 119, 'start_date': '2025-01-03', 'end_date': '2027-01-03', 'data_from_congress': 119},
    ]


def test_write_table_replaces_only_its_congress(tmp_path, committees):
    ref.write_table('committees', committees, 119, tmp_path)
    renamed = [{**c, 'congress': 120, 'name': c['name'] + ' (120)'} for c in committees[:2]]
    ref.write_table('committees', renamed, 120, tmp_path)
    first = (tmp_path / 'committees.csv').read_bytes()
    rows = ref.write_table('committees', renamed, 120, tmp_path)       # Neulauf: identisch
    assert (tmp_path / 'committees.csv').read_bytes() == first
    assert [r['congress'] for r in rows].count('119') == 13
    assert [r['congress'] for r in rows].count('120') == 2
    # dieselbe ID trägt je Kongress einen eigenen Namen: Schlüssel ist (congress, committee_id)
    names = {(r['congress'], r['committee_id']): r['name'] for r in rows}
    assert names['119', 'HSWM'] != names['120', 'HSWM']


def _tables(committees, memberships):
    return {'committees': committees, 'committee_memberships': list(memberships.values()),
            'members': [{'bioguide_id': b} for _, b in memberships]}


def test_check_passes_on_consistent_tables(committees, memberships):
    assert ref.check(_tables(committees, memberships)) == []


def test_check_reports_duplicate_keys_and_dangling_references(committees, memberships):
    tables = _tables(committees, memberships)
    tables['committees'] = committees + [committees[0]]
    tables['members'] = tables['members'][1:]
    tables['committee_memberships'] = tables['committee_memberships'] + [
        {**tables['committee_memberships'][0], 'committee_id': 'XXXX'}]
    errors = '\n'.join(ref.check(tables))
    assert 'committees: Schlüssel' in errors
    assert "committee_memberships.committee_id: nicht in committees: ['XXXX']" in errors
    assert 'committee_memberships.bioguide_id: nicht in members' in errors


def test_check_does_not_resolve_ids_across_congresses(committees, memberships):
    tables = _tables(committees, memberships)
    tables['committee_memberships'] = [{**m, 'congress': 120} for m in tables['committee_memberships']]
    assert any('committee_memberships.committee_id' in e for e in ref.check(tables))


def test_build_writes_all_tables_from_local_sources(tmp_path):
    sources = {'raw_dir': FIX, 'historical': FIX / 'legislators-historical.yaml',
               'snapshots': [{'date': d, 'path': FIX / f'membership-{d}.yaml'} for d in (APRIL, NOVEMBER)]}
    tables = ref.build(119, sources, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(f'{name}.csv' for name in ref.TABLES)
    assert len(tables['committees']) == 13
    assert len(tables['unit_committee_map']) == 225          # echte Revision gegen Fixture-Ausschüsse
    assert len(tables['committee_industries']) == 522        # nichts geht verloren, auch wenn Units offen sind
    with (tmp_path / 'congresses.csv').open(encoding='utf-8') as handle:
        assert [r['data_from_congress'] for r in csv.DictReader(handle)] == ['119', '119']
