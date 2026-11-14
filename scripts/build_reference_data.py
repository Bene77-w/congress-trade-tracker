"""Referenzdaten je Kongress: Abgeordnete, Ausschüsse, Mitgliedschaften, Ausschuss -> Industries.

Aufruf (Kongresswechsel = derselbe Aufruf mit anderen Werten):
    python scripts/build_reference_data.py [--congress 119] [--source-commit <sha>]
        [--trades-xlsx <pfad>] [--official-snapshot]

Quelle ist das gemeinfreie Repo unitedstates/congress-legislators an einem gepinnten
Commit. Mitgliedschaften werden über alle Stände der Mitgliedschaftsdatei innerhalb des
Kongresses vereinigt, weil jeder einzelne Stand nur enthält, wer gerade im Amt ist.
Ausschuss-IDs bezeichnen Plätze, keine feste Bedeutung: Schlüssel ist immer
(congress, committee_id), nie die ID allein.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import settings  # noqa: E402

REF = ROOT / settings.REFERENCE_DATA_PATH
CACHE = ROOT / settings.REFERENCE_CACHE_PATH
REPO = settings.REFERENCE_SOURCE_REPO
MEMBERSHIP, COMMITTEES, CURRENT = (
    'committee-membership-current.yaml', 'committees-current.yaml', 'legislators-current.yaml')
HISTORICAL = 'legislators-historical.yaml'

TABLES = {  # Name -> (Spalten, Anzahl führender Schlüsselspalten)
    'congresses': (['congress', 'start_date', 'end_date', 'data_from_congress'], 1),
    'members': (['bioguide_id', 'full_name', 'first_name', 'last_name', 'chamber', 'state',
                 'district', 'party', 'last_term_start', 'last_term_end', 'in_office'], 1),
    'committees': (['congress', 'committee_id', 'chamber', 'parent_committee_id', 'name'], 2),
    'committee_memberships': (['congress', 'committee_id', 'bioguide_id', 'side', 'rank', 'title',
                               'member_chamber', 'first_seen', 'last_seen'], 3),
    'unit_committee_map': (['congress', 'revision_id', 'unit_id', 'unit_kind', 'committee_id',
                            'match', 'unresolved_reason'], 3),
    'committee_industries': (['congress', 'revision_id', 'unit_id', 'industry_id', 'committee_id',
                              'unit_kind', 'industry_name', 'sector_id', 'sector_name',
                              'confidence'], 4),
}


def congress_dates(congress: int) -> tuple[str, str]:
    year = 1787 + 2 * congress
    return f'{year}-01-03', f'{year + 2}-01-03'


# --- Abruf -----------------------------------------------------------------

def _http(url: str) -> bytes:
    """GET ohne Projekt- oder Nutzerdaten; drei Versuche."""
    request = urllib.request.Request(
        url, headers={'User-Agent': 'reference-data-build', 'Accept-Encoding': 'gzip'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
                gzipped = response.headers.get('Content-Encoding') == 'gzip'
                return gzip.decompress(body) if gzipped else body
        except OSError:
            if attempt == 2:
                raise
            time.sleep(3)
    raise AssertionError('unreachable')


def _raw_url(commit: str, name: str) -> str:
    return f'https://raw.githubusercontent.com/{REPO}/{commit}/{name}'


def _fetch(commit: str, name: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_http(_raw_url(commit, name)))
    return dest


def _digest(path: Path) -> dict:
    data = path.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def list_snapshots(congress: int, commit: str) -> list[dict]:
    """Alle Commits der Mitgliedschaftsdatei im Kongress, vom gepinnten Commit rückwärts."""
    start, end = congress_dates(congress)
    found, page = [], 1
    while True:
        batch = json.loads(_http(
            f'https://api.github.com/repos/{REPO}/commits?sha={commit}&path={MEMBERSHIP}'
            f'&since={start}T00:00:00Z&until={end}T00:00:00Z&per_page=100&page={page}'))
        found += [{'commit': c['sha'], 'date': c['commit']['committer']['date'][:10]} for c in batch]
        if len(batch) < 100:
            return sorted(found, key=lambda s: (s['date'], s['commit']))
        page += 1


def snapshot_official(raw_dir: Path) -> dict:
    """Amtliche Quellen als unabhängige Rohsicherung ablegen (kein Parsing)."""
    sys.path.insert(0, str(ROOT / 'scrapers' / 'senate_committee_xml'))
    import fetch_senate_xml as senate
    out = raw_dir / 'official'
    info = {'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'senate_xml_source': senate.DISCOVERY_URL, 'senate_xml_error': None,
            'house_pdf_url': settings.REFERENCE_HOUSE_PDF_URL}
    senate.MANIFEST = out / 'senate_xml' / '_manifest.json'  # nicht das Manifest des Scrapers
    try:
        senate.fetch_all(out / 'senate_xml')
    except RuntimeError as exc:  # senate.gov sperrt gelegentlich; im Manifest sichtbar lassen
        info['senate_xml_error'] = str(exc)
    response = senate.requests.get(settings.REFERENCE_HOUSE_PDF_URL, impersonate='chrome', timeout=60)
    if response.status_code != 200 or not response.content.startswith(b'%PDF'):
        raise RuntimeError(f'House-PDF nicht abrufbar: HTTP {response.status_code}')
    (out / 'house_scsoal.pdf').write_bytes(response.content)
    return info


def fetch_sources(congress: int, commit: str, official: bool = False) -> dict:
    """Legt den jüngsten Stand roh ab, lädt ältere Stände in den Cache, schreibt das Manifest."""
    raw_dir = REF / 'raw' / str(congress)
    manifest_path = raw_dir / 'manifest.json'
    old = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else {}
    source = old.get('source', {})
    if source.get('commit') != commit:
        for name in (MEMBERSHIP, COMMITTEES, CURRENT):
            (raw_dir / name).unlink(missing_ok=True)
        source = {'repo': REPO, 'commit': commit, 'url': _raw_url(commit, '<datei>'),
                  'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        snapshots = list_snapshots(congress, commit)
    else:
        snapshots = [{'commit': s['commit'], 'date': s['date']} for s in old['membership_snapshots']]
    for name in (MEMBERSHIP, COMMITTEES, CURRENT):
        _fetch(commit, name, raw_dir / name)
    historical = _fetch(commit, HISTORICAL, CACHE / commit / HISTORICAL)
    for snap in snapshots:
        snap['path'] = _fetch(snap['commit'], MEMBERSHIP, CACHE / snap['commit'] / MEMBERSHIP)
    official_info = snapshot_official(raw_dir) if official else old.get('official_snapshot')

    cfg = settings.REFERENCE_CONGRESSES.get(congress, {})
    related = {}
    if cfg.get('jurisdiction_manifest'):
        related['jurisdiction_inputs'] = {
            'manifest': cfg['jurisdiction_manifest'],
            **_digest(ROOT / cfg['jurisdiction_manifest'])}
    if cfg.get('mapping_revision'):
        rel = f"{settings.REFERENCE_REVISIONS_PATH}/{cfg['mapping_revision']}/reviewed_assignments.csv"
        related['mapping_revision'] = {
            'revision_id': cfg['mapping_revision'], 'reviewed_assignments': rel, **_digest(ROOT / rel)}
    manifest = {
        'congress': congress,
        'source': source,
        'raw_files': [{'path': p.relative_to(raw_dir).as_posix(), **_digest(p)}
                      for p in sorted(raw_dir.rglob('*')) if p.is_file() and p != manifest_path],
        'not_stored': [{'file': HISTORICAL, 'commit': commit, **_digest(historical)}],
        'membership_snapshots': [
            {'commit': s['commit'], 'date': s['date'], 'sha256': _digest(s['path'])['sha256']}
            for s in snapshots],
        'belongs_to_congress': related,
        'official_snapshot': official_info,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', 'utf-8')
    return {'raw_dir': raw_dir, 'historical': historical, 'snapshots': snapshots}


# --- Tabellen ---------------------------------------------------------------

def _yaml(path: Path):
    return yaml.load(Path(path).read_text('utf-8'), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))


def build_committees(congress: int, committees: list[dict]) -> list[dict]:
    rows = []
    for committee in committees:
        top = {'congress': congress, 'committee_id': committee['thomas_id'], 'chamber': committee['type'],
               'parent_committee_id': '', 'name': committee['name']}
        rows.append(top)
        for sub in committee.get('subcommittees', []):
            rows.append({**top, 'committee_id': committee['thomas_id'] + sub['thomas_id'],
                         'parent_committee_id': committee['thomas_id'], 'name': sub['name']})
    return rows


def build_memberships(congress: int, snapshots: list[tuple[str, dict]]) -> list[dict]:
    """Vereinigt die Stände (aufsteigend nach Datum); Rollen stammen aus dem letzten Vorkommen."""
    merged: dict[tuple, dict] = {}
    for date, data in sorted(snapshots, key=lambda s: s[0]):
        for committee_id, people in (data or {}).items():
            for person in people:
                key = (committee_id, person['bioguide'])
                first_seen = merged[key]['first_seen'] if key in merged else date
                merged[key] = {
                    'congress': congress, 'committee_id': committee_id, 'bioguide_id': person['bioguide'],
                    'side': person.get('party', ''), 'rank': person.get('rank', ''),
                    'title': person.get('title', ''), 'member_chamber': person.get('chamber', ''),
                    'first_seen': first_seen, 'last_seen': date}
    return list(merged.values())


def build_members(current: list[dict], historical: list[dict], since: str) -> list[dict]:
    rows = []
    for in_office, people in ((1, current), (0, historical)):
        for person in people:
            term = person['terms'][-1]
            if not in_office and str(term['end']) < since:
                continue
            name = person['name']
            rows.append({
                'bioguide_id': person['id']['bioguide'],
                'full_name': name.get('official_full') or f"{name['first']} {name['last']}",
                'first_name': name['first'], 'last_name': name['last'],
                'chamber': {'rep': 'house', 'sen': 'senate'}[term['type']],
                'state': term['state'], 'district': term.get('district', ''),
                'party': term.get('party', ''), 'last_term_start': term['start'],
                'last_term_end': term['end'], 'in_office': in_office})
    return rows


def _norm(name: str) -> str:
    name = re.sub(r'^(house|senate) ', '', name.lower().replace("'", '').replace('’', ''))
    return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'^subcommittee on ', '', name)).strip()


def map_units(congress: int, units: list[dict], committees: list[dict], overrides: list[dict]) -> list[dict]:
    """unit_id -> Ausschuss-ID über exakten Namensvergleich (normalisiert) oder Override-Datei."""
    index: dict[tuple, list[str]] = {}
    for c in committees:
        index.setdefault((c['chamber'], c['parent_committee_id'], _norm(c['name'])), []).append(c['committee_id'])
    manual = {(o['revision_id'], o['unit_id']): o['committee_id'] for o in overrides}
    rows = []
    for unit in units:
        row = {'congress': congress, 'revision_id': unit['review_id'], 'unit_id': unit['unit_id'],
               'unit_kind': unit['unit_kind'], 'committee_id': '', 'match': '', 'unresolved_reason': ''}
        parents = index.get((unit['chamber'], '', _norm(unit['committee_name'])), [])
        hits = parents
        if unit['unit_kind'] == 'subcommittee' and len(parents) == 1:
            hits = index.get((unit['chamber'], parents[0], _norm(unit['unit_name'])), [])
        if (row['revision_id'], row['unit_id']) in manual:
            row.update(committee_id=manual[row['revision_id'], row['unit_id']], match='override')
        elif len(hits) == 1:
            row.update(committee_id=hits[0], match='name')
        elif len(parents) != 1:
            row['unresolved_reason'] = f'{len(parents)} Ausschüsse mit dem Namen "{unit["committee_name"]}"'
        else:
            row['unresolved_reason'] = f'{len(hits)} Unterausschüsse von {parents[0]} mit dem Namen "{unit["unit_name"]}"'
        rows.append(row)
    return rows


def build_committee_industries(congress: int, assignments: list[dict], unit_map: list[dict]) -> list[dict]:
    """Alle Zuordnungen der Revision; committee_id bleibt leer, wenn die Unit nicht auflösbar ist."""
    resolved = {(u['revision_id'], u['unit_id']): u['committee_id'] for u in unit_map}
    return [{'congress': congress, 'revision_id': a['review_id'], 'unit_id': a['unit_id'],
             'industry_id': a['industry_id'], 'committee_id': resolved[a['review_id'], a['unit_id']],
             'unit_kind': a['unit_kind'], 'industry_name': a['industry_name'], 'sector_id': a['sector_id'],
             'sector_name': a['sector_name'], 'confidence': a['confidence']} for a in assignments]


def build_congresses(with_data: set[int]) -> list[dict]:
    """Kongresse mit eigener Erhebung plus solche, die deren Daten laut Settings übernehmen."""
    source = {c: c for c in with_data}
    source.update({c: s for c, s in settings.REFERENCE_CONGRESS_DATA_FROM.items() if s in with_data})
    return [{'congress': c, 'start_date': congress_dates(c)[0], 'end_date': congress_dates(c)[1],
             'data_from_congress': s} for c, s in sorted(source.items())]


# --- Schreiben und Prüfen -----------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def write_table(name: str, rows: list[dict], congress: int | None = None, out_dir: Path = REF) -> list[dict]:
    """Schreibt sortiert; mit congress bleiben die Zeilen anderer Kongresse erhalten."""
    fields, key_len = TABLES[name]
    path = out_dir / f'{name}.csv'
    rows = [{f: str(r[f]) for f in fields} for r in rows]
    if congress is not None:
        rows += [r for r in _read_csv(path) if r['congress'] != str(congress)]
    rows.sort(key=lambda r: [r[f] for f in fields])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    return rows


def check(tables: dict[str, list[dict]]) -> list[str]:
    """Gate: Schlüssel eindeutig, Fremdschlüssel lösen auf. Liefert Fehlermeldungen."""
    errors = []
    for name, rows in tables.items():
        key_fields = TABLES[name][0][:TABLES[name][1]]
        keys = [tuple(str(r[f]) for f in key_fields) for r in rows]
        if len(keys) != len(set(keys)):
            errors.append(f'{name}: Schlüssel {key_fields} nicht eindeutig')
    committee_keys = {(str(c['congress']), c['committee_id']) for c in tables['committees']}
    member_ids = {m['bioguide_id'] for m in tables['members']}
    for name, field in (('committees', 'parent_committee_id'), ('committee_memberships', 'committee_id'),
                        ('unit_committee_map', 'committee_id'), ('committee_industries', 'committee_id')):
        missing = sorted({r[field] for r in tables.get(name, [])
                          if r[field] and (str(r['congress']), r[field]) not in committee_keys})
        if missing:
            errors.append(f'{name}.{field}: nicht in committees: {missing}')
    missing = sorted({r['bioguide_id'] for r in tables['committee_memberships']} - member_ids)
    if missing:
        errors.append(f'committee_memberships.bioguide_id: nicht in members: {missing}')
    # Die Pipeline löst Ausschüsse über ihren Anzeigenamen auf (utils/reference_data.py):
    # je Kongress und Elternausschuss bzw. Kammer muss er eindeutig sein.
    names = [(str(c['congress']), c['parent_committee_id'] or c['chamber'],
              c['name'] if c['parent_committee_id'] else re.sub(r'^(House|Senate) ', '', c['name']))
             for c in tables['committees']]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        errors.append(f'committees: Name je Kongress und Elternausschuss nicht eindeutig: {duplicates}')
    return errors


def report(congress: int, tables: dict[str, list[dict]], trades_xlsx: Path) -> None:
    """Kopfzahlen und Gate-Zahlen auf stdout; bewusst keine Berichtsdatei."""
    members = tables['members']
    chamber_of = {c['committee_id']: c['chamber'] for c in tables['committees']}
    memberships = tables['committee_memberships']
    latest = max((m['last_seen'] for m in memberships), default='')
    for chamber, roster in (('house', settings.HOUSE_COMMITTEES_FILE), ('senate', settings.SENATE_COMMITTEES_FILE)):
        seated = sum(1 for m in members if m['in_office'] == '1' and m['chamber'] == chamber)
        def people(rows):
            return len({m['bioguide_id'] for m in rows
                        if (m['member_chamber'] or chamber_of[m['committee_id']]) == chamber})
        print(f'{chamber}: {seated} im Amt | mit Ausschuss: {people(memberships)} im Kongress, '
              f'{people([m for m in memberships if m["last_seen"] == latest])} im jüngsten Stand | '
              f'Roster-CSV: {len(_read_csv(ROOT / settings.DATA_MAPPINGS_PATH / roster))} Zeilen')
    print(f'members: {len(members)} | committees: {len(tables["committees"])} | '
          f'committee_memberships: {len(memberships)} (jüngster Stand {latest})')
    if 'unit_committee_map' in tables:
        open_units = [u for u in tables['unit_committee_map'] if not u['committee_id']]
        lost = sum(1 for a in tables['committee_industries'] if not a['committee_id'])
        print(f'units: {len(tables["unit_committee_map"])}, nicht aufgelöst {len(open_units)} | '
              f'Zuordnungen: {len(tables["committee_industries"])}, ohne Ausschuss {lost}')
        for unit in open_units:
            print(f'  offen: {unit["unit_id"]}: {unit["unresolved_reason"]}')
    if trades_xlsx.exists():
        import pandas as pd
        trades = pd.read_excel(trades_xlsx, usecols=['BioGuideID', 'Name']).dropna(subset=['BioGuideID'])
        names = trades.drop_duplicates('BioGuideID').set_index('BioGuideID')['Name']
        missing = sorted(set(names.index) - {m['bioguide_id'] for m in members})
        print(f'Trade-Quelle: {len(names) - len(missing)}/{len(names)} BioGuideIDs in members')
        for bioguide in missing:
            print(f'  fehlt: {bioguide} {names[bioguide]}')
    else:
        print(f'Trade-Quelle nicht gefunden, Abdeckung nicht geprüft: {trades_xlsx}')


def build(congress: int, sources: dict, out_dir: Path = REF) -> dict[str, list[dict]]:
    raw_dir = sources['raw_dir']
    committees = build_committees(congress, _yaml(raw_dir / COMMITTEES))
    memberships = build_memberships(congress, [(s['date'], _yaml(s['path'])) for s in sources['snapshots']])
    members = build_members(_yaml(raw_dir / CURRENT), _yaml(sources['historical']), settings.REFERENCE_MEMBERS_SINCE)
    new = {'members': write_table('members', members, out_dir=out_dir),
           'committees': write_table('committees', committees, congress, out_dir),
           'committee_memberships': write_table('committee_memberships', memberships, congress, out_dir)}
    revision = settings.REFERENCE_CONGRESSES.get(congress, {}).get('mapping_revision')
    if revision:
        review_dir = ROOT / settings.REFERENCE_REVISIONS_PATH / revision
        unit_map = map_units(congress, _read_csv(review_dir / 'reviewed_unit_coverage.csv'), committees,
                             _read_csv(REF / 'unit_overrides.csv'))
        industries = build_committee_industries(congress, _read_csv(review_dir / 'reviewed_assignments.csv'), unit_map)
        new['unit_committee_map'] = write_table('unit_committee_map', unit_map, congress, out_dir)
        new['committee_industries'] = write_table('committee_industries', industries, congress, out_dir)
    write_table('congresses', build_congresses({int(c['congress']) for c in new['committees']}), out_dir=out_dir)
    return {name: rows if name == 'members' else [r for r in rows if r['congress'] == str(congress)]
            for name, rows in new.items()}


def main() -> int:
    default_congress = max(settings.REFERENCE_CONGRESSES)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--congress', type=int, default=default_congress)
    parser.add_argument('--source-commit', help='Default: Eintrag in settings.REFERENCE_CONGRESSES')
    parser.add_argument('--trades-xlsx', type=Path, default=ROOT / settings.REFERENCE_TRADES_XLSX)
    parser.add_argument('--official-snapshot', action='store_true',
                        help='Senate-XML und House-Clerk-PDF als Rohsicherung neu ablegen')
    args = parser.parse_args()
    commit = args.source_commit or settings.REFERENCE_CONGRESSES[args.congress]['source_commit']
    sources = fetch_sources(args.congress, commit, args.official_snapshot)
    tables = build(args.congress, sources)
    report(args.congress, tables, args.trades_xlsx)
    errors = check(tables)
    if not tables.get('committee_industries'):
        errors.append(f'committee_industries: keine Zuordnung für Kongress {args.congress} (mapping_revision in '
                      'settings.REFERENCE_CONGRESSES fehlt); seine Trades blieben ohne Sektoren')
    for error in errors:
        print(f'FEHLER: {error}')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
