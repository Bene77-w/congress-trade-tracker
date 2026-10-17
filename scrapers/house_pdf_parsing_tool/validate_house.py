"""Phase-2-Validierung: House-PDF-Parser Voll-Output gegen Orakel/Baseline/PDF.
Ungetrackt (laeuft nur im Worktree). Aufruf: venv/bin/python /tmp/validate_house.py
"""
import csv
import os
import re
import sys
import unicodedata
from collections import OrderedDict, defaultdict

ROOT = '/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker-agent-member_detailed_info_modul'
sys.path.insert(0, ROOT)
import scrapers.house_pdf_parsing_tool.parse_house_committee_pdf as P

PDF = os.path.join(ROOT, 'scrapers/house_pdf_parsing_tool/scsoal.pdf')
ORACLE = os.path.join(ROOT, 'data/mappings/us_house_all_committees.csv')
BASELINE = os.path.join(ROOT, 'data/mappings/house_member&(sub)committees.csv')

# Die 6 Intelligence-Subs wurden nach manueller PDF-Gegenprüfung (User) ins
# Orakel us_house_all_committees.csv aufgenommen -> Whitelist nicht mehr noetig.
INTEL_SUB_WHITELIST = set()

# Erwartete House-Kopfzahlen, direkt aus scsoal.pdf gezaehlt (Select S56-57, Joint S58).
EXPECTED_ROSTER = {
    'Permanent Select Committee On Intelligence': 27,  # Crawford(Chair)+13 R-rows... +12 D = 15R+12D
    'Select Committee On The Strategic Competition Between The United States And The Chinese Communist Party': 23,
    'Joint Economic Committee': 10,
    'Joint Committee On The Library': 5,
    'Joint Committee On Printing': 5,
    'Joint Committee On Taxation': 5,
}

OK = '\033[92m'; BAD = '\033[91m'; END = '\033[0m'
errors = []
def check(cond, msg):
    print((OK + '  ✓ ' if cond else BAD + '  ✗ ') + msg + END)
    if not cond:
        errors.append(msg)


def build_full():
    m, det = P.parse_all_committees(PDF, range(4, 58), cross_ref=False)
    P.parse_joint(PDF, 58, m)
    P._cross_reference_alphabetical(PDF, m)
    return m, det


def load_oracle():
    pairs = set()
    with open(ORACLE) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            comm = row[0].strip()
            sub = row[1].strip() if len(row) > 1 else ''
            pairs.add((comm, sub))
    return pairs


def parser_pairs(members):
    pairs = set()
    for md in members.values():
        for cname, cd in md['committees'].items():
            subs = cd.get('subcommittees', {})
            if subs:
                for sn in subs:
                    pairs.add((cname, sn))
            else:
                pairs.add((cname, ''))
    return pairs


# --- Regression-Normalisierung: (member, committee, sub) Tripel ---
def norm_name(name):
    n = unicodedata.normalize('NFKD', name)
    n = ''.join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r',?\s*(Jr\.?|Sr\.?|III|IV|II)\s*$', '', n)
    n = re.sub(r'([a-z])([A-Z])', r'\1 \2', n)
    return re.sub(r'\s+', ' ', n).strip().lower()


def triples_from_f2_old(name, f2):
    """Baseline (altes Format: ; trennt Committees UND Subs). Heuristik:
    jeder ;-Teil mit ':' eroeffnet ein Committee; folgende ;-Teile sind dessen
    Subs bis zum naechsten ':'. Wir bilden nur (member, committee, sub)-Mengen
    fuer den Vergleich (Sub-Remap/Trenner neutralisiert)."""
    triples = set()
    if not f2:
        return triples
    cur_comm = None
    for part in f2.split(';'):
        if not part:
            continue
        if ':' in part:
            head, subpart = part.split(':', 1)
            cur_comm = head.split('|')[0].strip()
            triples.add((norm_name(name), cur_comm, ''))
            sub = subpart.split('|')[0].strip()
            if sub:
                triples.add((norm_name(name), cur_comm, sub))
        else:
            seg = part.split('|')[0].strip()
            # Hat dieser Teil ein eigenes Committee oder ist es ein Sub des aktuellen?
            # Im alten Format ist das mehrdeutig (genau der Bug). Heuristik wie
            # CommitteeParser: ohne ':' = Sub des laufenden Committees, FALLS eines
            # offen ist; sonst eigenes Committee.
            if cur_comm is not None:
                triples.add((norm_name(name), cur_comm, seg))
            else:
                cur_comm = seg
                triples.add((norm_name(name), cur_comm, ''))
    return triples


def triples_from_f2_new(name, f2):
    """Neues F2: ; = nur Committees, : = Header->Sublist, ~ = Subs."""
    triples = set()
    if not f2:
        return triples
    for comm_part in f2.split(';'):
        if not comm_part:
            continue
        if ':' in comm_part:
            head, subpart = comm_part.split(':', 1)
            cname = head.split('|')[0].strip()
            triples.add((norm_name(name), cname, ''))
            for sub in subpart.split('~'):
                sn = sub.split('|')[0].strip()
                if sn:
                    triples.add((norm_name(name), cname, sn))
        else:
            cname = comm_part.split('|')[0].strip()
            triples.add((norm_name(name), cname, ''))
    return triples


def main():
    print('=' * 72)
    print('PHASE-2-VALIDIERUNG  House-PDF-Parser')
    print('=' * 72)
    members, det = build_full()

    # --- 1. Boundary: 26 Committees, keine Dupes/2-zeilige-Header-Reste ---
    print('\n[1] Committee-Boundary')
    all_comms = set()
    for md in members.values():
        all_comms.update(md['committees'].keys())
    check(len(all_comms) == 26, f'26 Committees erkannt (ist: {len(all_comms)})')
    oracle = load_oracle()
    oracle_comms = {c for c, s in oracle}
    missing_c = oracle_comms - all_comms
    extra_c = all_comms - oracle_comms
    check(not missing_c, f'keine fehlenden Committees ggü. Orakel ({missing_c})')
    check(not extra_c, f'keine Extra-Committees ggü. Orakel ({extra_c})')

    # --- 2. Struktur-Orakel: Paare ---
    print('\n[2] Struktur-Orakel (Paare)')
    pp = parser_pairs(members)
    op = oracle
    # Orakel listet Committees ohne Subs als (C, '') -> Parser ebenso.
    unknown = {(c, s) for c, s in pp if s and (c, s) not in op} - INTEL_SUB_WHITELIST
    check(not unknown, f'keine unbekannten (Committee,Sub)-Paare (ist: {sorted(unknown)})')
    # Volle Orakel-Abdeckung: jedes Orakel-Paar mit Sub muss geparst sein
    oracle_subpairs = {(c, s) for c, s in op if s}
    parsed_subpairs = {(c, s) for c, s in pp if s}
    uncovered = oracle_subpairs - parsed_subpairs
    check(not uncovered, f'volle Orakel-Sub-Abdeckung (fehlt: {sorted(uncovered)})')
    print(f'      Intelligence-Subs (Whitelist, Orakel-Luecke): {len(INTEL_SUB_WHITELIST)} Paare')

    # --- 3. Roster-Headcount Select/Joint direkt gegen erwartete PDF-Zahl ---
    print('\n[3] Roster-Headcount Select/Joint (direkt vs PDF)')
    for comm, exp in EXPECTED_ROSTER.items():
        cnt = sum(1 for md in members.values() if comm in md['committees'])
        check(cnt == exp, f'{comm[:45]:45} House={cnt} (erwartet {exp})')

    # --- 4. Quell-intern: 1 Chairman + 1 Ranking je Standing/Select, States, Dupes ---
    print('\n[4] Quell-interne Checks')
    JOINTS = {'Joint Economic Committee', 'Joint Committee On The Library',
              'Joint Committee On Printing', 'Joint Committee On Taxation'}
    chair_count = defaultdict(int)
    rank_count = defaultdict(int)
    for md in members.values():
        for cname, cd in md['committees'].items():
            if cd['role'] == 'Chairman':
                chair_count[cname] += 1
            elif cd['role'] == 'Ranking Member':
                rank_count[cname] += 1
    bad_chair = {c: chair_count.get(c, 0) for c in all_comms
                 if c not in JOINTS and chair_count.get(c, 0) != 1}
    bad_rank = {c: rank_count.get(c, 0) for c in all_comms
                if c not in JOINTS and rank_count.get(c, 0) != 1}
    check(not bad_chair, f'genau 1 Chairman je Nicht-Joint-Committee (Abw: {bad_chair})')
    check(not bad_rank, f'genau 1 Ranking je Nicht-Joint-Committee (Abw: {bad_rank})')
    # Joint: kein Ranking
    joint_rank = {c: rank_count.get(c, 0) for c in JOINTS if rank_count.get(c, 0)}
    check(not joint_rank, f'kein Ranking in Joint-Committees (Abw: {joint_rank})')
    # States
    bad_states = {n: md['state'] for n, md in members.items()
                  if md['state'] and md['state'] not in P.VALID_STATES}
    check(not bad_states, f'alle State-Codes gueltig (Abw: {bad_states})')
    # Duplikate (normalisierte Namens-Keys)
    seen = defaultdict(list)
    for n in members:
        seen[norm_name(n)].append(n)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    check(not dupes, f'keine Member-Duplikate (Abw: {dupes})')

    # --- 5. Member/Rollen-Regression vs Baseline (Tripel-Mengen) ---
    print('\n[5] Member/Rollen-Regression vs Baseline')
    base_triples = set()
    base_names = set()
    with open(BASELINE) as f:
        r = csv.DictReader(f)
        for row in r:
            base_names.add(norm_name(row['Name']))
            base_triples |= triples_from_f2_old(row['Name'], row['Committees'])
    new_triples = set()
    for n, md in members.items():
        f2 = P.build_comm_string(md['committees'])
        new_triples |= triples_from_f2_new(n, f2)
    new_names = {norm_name(n) for n in members}

    only_base = base_triples - new_triples
    only_new = new_triples - base_triples
    print(f'      Baseline-Tripel: {len(base_triples)}  Neu-Tripel: {len(new_triples)}')
    print(f'      Nur Baseline (entfallen/geaendert): {len(only_base)}')
    print(f'      Nur Neu (Zugaenge: Select+Joint+Korrekturen): {len(only_new)}')
    # Erwartete Neuzugaenge: Select (Intelligence-Subs, China), Joint
    new_member_only = new_names - base_names
    lost_member = base_names - new_names
    print(f'      Neue Member (nicht in Baseline): {len(new_member_only)} -> {sorted(new_member_only)[:8]}')
    print(f'      Verlorene Member (in Baseline, jetzt fehlend): {len(lost_member)} -> {sorted(lost_member)}')

    print('\n' + '=' * 72)
    if errors:
        print(f'{BAD}FEHLGESCHLAGEN: {len(errors)} Check(s){END}')
        for e in errors:
            print('   -', e)
    else:
        print(f'{OK}ALLE HARTEN CHECKS GRUEN{END}')
    print('=' * 72)


if __name__ == '__main__':
    main()
