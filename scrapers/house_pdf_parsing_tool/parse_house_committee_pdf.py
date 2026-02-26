"""
House PDF Parser - Agriculture Committee Test v3
Uses proximity-based line grouping instead of rounding to avoid edge cases.
"""

import argparse
import pdfplumber
import re
import csv
from collections import OrderedDict

PDF_PATH = "/mnt/user-data/uploads/house_info_all.pdf"  # Default, via --pdf ueberschreibbar
AGRICULTURE_PAGES = [4, 5, 6]
LEFT_COL_THRESHOLD = 330
LINE_MERGE_THRESHOLD = 1.5  # Words within 1.5pt y-distance = same line

VALID_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'AS', 'DC', 'GU', 'MP', 'PR', 'VI'
}

ROLE_PATTERNS = [
    (r',?\s*Ex\s+Officio\s*$', 'Ex Officio'),
    (r',?\s*Vice\s+Chair(?:wo)?man\s*$', 'Vice Chairman'),
    (r',?\s*Vice\s+Chair\s*$', 'Vice Chairman'),
    (r',?\s*Chair(?:wo)?man\s*$', 'Chairman'),
    (r',?\s*Chair\s*$', 'Chairman'),
]


def extract_words(pdf_path, page_indices):
    pdf = pdfplumber.open(pdf_path)
    all_words = []
    for pi in page_indices:
        for w in pdf.pages[pi].extract_words(extra_attrs=['fontname', 'size']):
            w['page'] = pi
            all_words.append(w)
    pdf.close()
    return all_words


def group_words_into_lines(words):
    """
    Group words into lines using proximity-based clustering.
    Words within LINE_MERGE_THRESHOLD y-distance belong to the same line.
    """
    if not words:
        return []
    
    # Sort by page, then y, then x
    sorted_words = sorted(words, key=lambda w: (w['page'], w['top'], w['x0']))
    
    lines = []
    current_line = [sorted_words[0]]
    current_y = sorted_words[0]['top']
    current_page = sorted_words[0]['page']
    
    for w in sorted_words[1:]:
        if w['page'] == current_page and abs(w['top'] - current_y) <= LINE_MERGE_THRESHOLD:
            current_line.append(w)
            # Update y to average for better clustering
            current_y = sum(ww['top'] for ww in current_line) / len(current_line)
        else:
            lines.append(current_line)
            current_line = [w]
            current_y = w['top']
            current_page = w['page']
    
    if current_line:
        lines.append(current_line)
    
    # Sort words within each line by x
    for line in lines:
        line.sort(key=lambda w: w['x0'])
    
    return lines


def split_line_into_columns(line_words):
    """
    Split a line into left/right column entries, or keep as center if it's
    a header/section line that spans both columns.
    """
    text = ' '.join(w['text'] for w in line_words)
    sizes = [w['size'] for w in line_words]
    dom_size = max(set(sizes), key=sizes.count)
    has_bold = any('Bold' in w['fontname'] for w in line_words)
    
    # Detect center/full-width lines that should NOT be split
    is_large_header = dom_size >= 9.5 and has_bold
    is_sub_header = abs(dom_size - 7.5) < 0.3 and text.replace(' ', '').isupper()
    is_section = any(p in text.upper() for p in [
        'SUBCOMMITTEES OF', 'STANDING COMMITTEES', 'ALPHABETICAL',
    ])
    is_bracket = text.strip().startswith('[')  # [The chairman and ranking...]
    is_ratio = text.strip().startswith('Ratio')
    is_meta = 'Republicans in roman' in text or 'Resident Commissioner' in text
    
    if is_large_header or is_sub_header or is_section or is_bracket or is_ratio or is_meta:
        return [{'words': line_words, 'column': 'center'}]
    
    # Split into left and right column
    left = [w for w in line_words if w['x0'] < LEFT_COL_THRESHOLD]
    right = [w for w in line_words if w['x0'] >= LEFT_COL_THRESHOLD]
    
    result = []
    if left:
        result.append({'words': left, 'column': 'left'})
    if right:
        result.append({'words': right, 'column': 'right'})
    return result


def classify_entry(words, column):
    """Classify an entry (after column split)."""
    if not words:
        return 'skip'
    
    text = ' '.join(w['text'] for w in words).strip()
    sizes = [w['size'] for w in words]
    dom_size = max(set(sizes), key=sizes.count)
    
    # Skip patterns
    if re.match(r'^Ratio\s+\d+/\d+', text):
        return 'skip'
    if re.match(r'^\d{1,2}$', text):
        return 'skip'
    if text.startswith('(') and re.match(r'^\(\d+\)', text):
        return 'skip'
    if 'Republicans in roman' in text or 'Resident Commissioner' in text:
        return 'skip'
    if re.match(r'^\d+\.\s*$', text):  # Just a number like "29."
        return 'skip'
    
    # Committee header
    if dom_size >= 9.5 and any('Bold' in w['fontname'] for w in words):
        if text.isupper() and len(text) > 3:
            return 'committee_header'
    
    # Subcommittees of...
    if 'SUBCOMMITTEES OF THE COMMITTEE ON' in text.upper():
        return 'subcommittees_of'
    
    # Ex officio note (full line in center column)
    if '[The chairman' in text or '[the chairman' in text:
        return 'ex_officio_note'
    if 'ex officio members of all' in text.lower():
        return 'ex_officio_note'
    
    # Subcommittee header
    if abs(dom_size - 7.5) < 0.3 and text.isupper() and len(text) > 5:
        return 'subcommittee_header'
    
    # Member line
    if abs(dom_size - 8.0) < 0.5:
        return 'member'
    
    return 'skip'


def fix_name(raw):
    """Fix concatenated names and normalize."""
    n = raw
    # Insert space before uppercase following lowercase
    n = re.sub(r'([a-z])([A-Z])', r'\1 \2', n)
    # Fix period+letter: A.Rick -> A. Rick  
    n = re.sub(r'\.([A-Z])', r'. \1', n)
    # Fix quote+letter: "Rick"Crawford -> "Rick" Crawford
    n = re.sub(r'([""\u201d])([A-Z])', r'\1 \2', n)
    # Normalize unicode quotes
    n = n.replace('\u201c', '"').replace('\u201d', '"')
    n = n.replace('\u2018', "'").replace('\u2019', "'")
    # Fix Mc/Des prefix splits
    n = re.sub(r'\bMc ([A-Z])', r'Mc\1', n)
    n = re.sub(r'\bDes ([A-Z])', r'Des\1', n)
    # Clean spaces
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def parse_member(words, column):
    """Parse member words into name, state, party, role."""
    if not words:
        return None
    
    party = 'D' if column == 'right' else 'R'
    
    # Check for Bold font (Delegates)
    name_words = [w for w in words if not re.match(r'^\d+\.$', w['text'])]
    if name_words and all('Bold' in w['fontname'] for w in name_words):
        pass  # Delegate - keep column-based party
    
    text = ' '.join(w['text'] for w in words)
    text = re.sub(r'^\d+\.\s*', '', text)  # Remove leading number
    if not text.strip():
        return None
    
    # Extract role
    role = ''
    clean = text.strip()
    for pat, rname in ROLE_PATTERNS:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            role = rname
            clean = clean[:m.start()].strip()
            break
    
    # Extract state
    state = ''
    name = clean
    
    # Pattern 1: ",STATE" at end
    sm = re.search(r',\s*([A-Z]{2})\s*$', clean)
    if sm and sm.group(1) in VALID_STATES:
        state = sm.group(1)
        name = clean[:sm.start()].strip()
    else:
        # Pattern 2: space+STATE at end (state as separate word)
        parts = clean.rsplit(None, 1)
        if len(parts) == 2 and parts[1] in VALID_STATES:
            state = parts[1]
            name = parts[0].rstrip(',').strip()
    
    if not state:
        return None
    
    name = name.rstrip(',').strip()
    name = fix_name(name)
    
    if not name or len(name) < 3:
        return None
    
    return {'name': name, 'party': party, 'state': state, 'role': role}


def parse_agriculture(pdf_path):
    words = extract_words(pdf_path, AGRICULTURE_PAGES)
    raw_lines = group_words_into_lines(words)
    
    # State
    current_comm = None
    current_sub = None
    in_sub_section = False
    has_exoff_rule = False
    comm_chair = None
    comm_ranking = None
    all_subs = []
    first_dem = {}
    sub_temp = []
    members = OrderedDict()
    
    for raw_line in raw_lines:
        entries = split_line_into_columns(raw_line)
        
        for entry in entries:
            ws = entry['words']
            col = entry['column']
            lt = classify_entry(ws, col)
            text = ' '.join(w['text'] for w in ws).strip()
            
            if lt == 'skip':
                continue
            
            if lt == 'committee_header':
                if text == 'ALPHABETICAL LIST OF STANDING COMMITTEES':
                    continue
                if 'AGRICULTURE' in text and current_comm is None:
                    current_comm = 'Agriculture'
                    print(f"[COMMITTEE] {current_comm}")
                continue
            
            if current_comm is None:
                continue
            
            if lt == 'subcommittees_of':
                in_sub_section = True
                current_sub = None
                continue
            
            if lt == 'ex_officio_note':
                has_exoff_rule = True
                print(f"  [EX OFFICIO RULE ACTIVE]")
                continue
            
            if lt == 'subcommittee_header':
                _assign_ranking(sub_temp, members, current_comm, current_sub)
                sub_temp = []
                current_sub = text.title()
                all_subs.append(current_sub)
                print(f"  [SUB] {current_sub}")
                continue
            
            if lt == 'member':
                m = parse_member(ws, col)
                if not m:
                    continue
                
                name, party, state, role = m['name'], m['party'], m['state'], m['role']
                
                if name not in members:
                    members[name] = {
                        'party': party, 'state': state,
                        'committees': OrderedDict(), 'ex_officio': False
                    }
                
                if current_comm not in members[name]['committees']:
                    members[name]['committees'][current_comm] = {
                        'role': '', 'subcommittees': OrderedDict()
                    }
                
                cd = members[name]['committees'][current_comm]
                
                # Normalize role
                if role in ('Chairman', 'Chair'):
                    role = 'Chairman'
                elif role in ('Vice Chairman', 'Vice Chair'):
                    role = 'Vice Chairman'
                
                if not in_sub_section:
                    # Main committee
                    if role == 'Chairman':
                        comm_chair = name
                    
                    k = (current_comm, None)
                    if party == 'D' and k not in first_dem:
                        first_dem[k] = True
                        if not role:
                            role = 'Ranking Member'
                        comm_ranking = name
                    
                    cd['role'] = role
                    print(f"    [{party}] {name}, {state} -> {role or 'Member'}")
                else:
                    # Subcommittee
                    if current_sub:
                        if 'Ex Officio' in (role or ''):
                            members[name]['ex_officio'] = True
                        cd['subcommittees'][current_sub] = role
                        sub_temp.append((name, party, role))
                        print(f"      [{party}] {name}, {state} -> {role or 'Member'}")
    
    # Final ranking
    _assign_ranking(sub_temp, members, current_comm, current_sub)
    
    # Apply ex-officio rule
    if has_exoff_rule and current_comm:
        print(f"\n  [EX-OFFICIO] Chair={comm_chair}, Ranking={comm_ranking}")
        for sub in all_subs:
            for ex in [comm_chair, comm_ranking]:
                if ex and ex in members:
                    cd = members[ex]['committees'].get(current_comm)
                    if cd and sub not in cd['subcommittees']:
                        cd['subcommittees'][sub] = 'Ex Officio'
                        members[ex]['ex_officio'] = True
                        print(f"    -> {ex} added to '{sub}'")
    
    return members


def _assign_ranking(sub_temp, members, comm, sub):
    if not sub_temp or not sub:
        return
    for name, party, role in sub_temp:
        if party == 'D' and not role:
            if name in members and comm in members[name]['committees']:
                members[name]['committees'][comm]['subcommittees'][sub] = 'Ranking Member'
                print(f"      [RANKING] {name} -> {sub}")
            break


def build_comm_string(comms):
    parts = []
    for cname, d in comms.items():
        s = f"{cname}|{d['role']}"
        subs = d.get('subcommittees', {})
        if subs:
            s += ':' + ';'.join(f"{sn}|{sr}" for sn, sr in subs.items())
        parts.append(s)
    return ';'.join(parts)


def write_csv(members, path):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Name', 'Party', 'State', 'Committees', 'ExOfficio'])
        for name in sorted(members.keys()):
            d = members[name]
            w.writerow([name, d['party'], d['state'],
                        build_comm_string(d['committees']),
                        str(d['ex_officio']).lower()])


def main():
    parser = argparse.ArgumentParser(description="House Committee PDF Parser")
    parser.add_argument(
        "--pdf",
        type=str,
        default=PDF_PATH,
        help=f"Pfad zur House Committee PDF (default: {PDF_PATH})",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Ausgabe-CSV-Pfad (default: <pdf_stem>_members.csv im selben Verzeichnis)",
    )
    args = parser.parse_args()

    import os
    pdf_path = args.pdf
    if args.out:
        out = args.out
    else:
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        out = os.path.join(os.path.dirname(pdf_path), f"{stem}_members.csv")

    print("=" * 70)
    print("PARSING AGRICULTURE (TEST v3)")
    print("=" * 70)
    print(f"PDF:    {pdf_path}")
    print(f"Output: {out}")

    members = parse_agriculture(pdf_path)
    write_csv(members, out)

    r = sum(1 for m in members.values() if m['party'] == 'R')
    d = sum(1 for m in members.values() if m['party'] == 'D')
    exoff = sum(1 for m in members.values() if m['ex_officio'])

    print(f"\n{'=' * 70}")
    print(f"TOTAL: {len(members)} | R: {r} | D: {d} | ExOfficio: {exoff}")
    print(f"Expected: 53 (28R + 25D, seat 29R empty)")
    print(f"{'=' * 70}")

    print("\n--- CSV ---")
    with open(out, 'r') as f:
        print(f.read())


if __name__ == '__main__':
    main()
