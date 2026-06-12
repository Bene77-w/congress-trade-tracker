
import argparse
import os
import pdfplumber
import csv
import re
import sys
from collections import defaultdict

# --- Configuration ---
PDF_PATH = "senate_(sub)committees_all_pdf.pdf"  # Default (committete Datei), via --pdf ueberschreibbar
CSV_OUTPUT_PATH = "us_senate_member&(sub)committees.csv"
TEST_CSV_OUTPUT_PATH = "us_senate_member&(sub)committees_test.csv"
TEST_MODE = False  # Set to True via command line arg or manual toggle

# --- Font & Layout Constants (from senate_pdf_info.md) ---
# Note: pdfplumber font names might differ slightly (e.g., ending in MT or similar).
# We will use strict or partial matching as needed.
FONT_COMMITTEE_NAME = {"size": 10.5, "weight": "bold"}
FONT_SUBCOMMITTEE_NAME = {"size": 11.2, "weight": "bold"}
FONT_ROLE = {"style": "italic", "size": 12.7}
FONT_MEMBER = {"style": "regular", "size": 12.7} 
# Tolerance for float comparison
TOLERANCE = 0.5

# --- Helper Functions ---

def is_font(char, size, style_check):
    """
    Checks if a char matches the font criteria.
    style_check: function that takes char['fontname'] and returns True/False
    """
    # Check size
    if not (size - TOLERANCE <= char['size'] <= size + TOLERANCE):
        return False
    
    # Check style (bold/italic/regular) via fontname
    fontname = char.get('fontname', '').lower()
    return style_check(fontname)

def is_bold(fontname):
    return 'bold' in fontname or 'semib' in fontname

def is_italic(fontname):
    return 'italic' in fontname or 'oblique' in fontname

def is_regular(fontname):
    return not is_bold(fontname) and not is_italic(fontname)

def extract_text_by_style(chars):
    """
    Clusters characters into lines/blocks based on their y-position and style.
    Handles multi-column text by splitting lines with significant gaps.
    Returns specific typed objects: Committee, Subcommittee, MemberLine.
    """
    # efficient sorting
    chars.sort(key=lambda x: (x['top'], x['x0']))
    
    lines = []
    current_line = []
    last_top = -1
    
    for char in chars:
        if last_top != -1 and abs(char['top'] - last_top) > 3: # New line detection
            if current_line:
                lines.append(current_line)
            current_line = []
        
        current_line.append(char)
        last_top = char['top']
    if current_line:
        lines.append(current_line)

    structured_lines = []
    
    for line in lines:
        if not line: continue
        
        # Split line into segments based on x-gaps
        # Sort by x0 just in case
        line.sort(key=lambda c: c['x0'])
        
        segments = []
        current_segment = []
        last_x1 = -1
        
        for char in line:
            x0 = char['x0']
            x1 = char['x1'] # x1 is x0 + width usually, needed to detect gap
            
            # If gap > 15 (arbitrary but safe for columns), split
            # A space char might be ~3-5pt. 20pt is definitely a visual gap.
            if last_x1 != -1 and (x0 - last_x1) > 15:
                if current_segment:
                    segments.append(current_segment)
                current_segment = []
            
            current_segment.append(char)
            last_x1 = x1
            
        if current_segment:
            segments.append(current_segment)
            
        # Process each segment as a potential entity
        for seg in segments:
            text = "".join([c['text'] for c in seg]).strip()
            if not text: continue
            
            # Filter "XML" artifact (usually just "XML", x0 around 486)
            # It might be merged with previous text if gap detection is too loose.
            # Check if text ends with "XML" and the last char is near the button position.
            if text.endswith("XML") and seg[-1]['x0'] > 480:
                 text = text[:-3].strip()
                 if not text: continue
    
            first_char = seg[0]
            x0 = first_char['x0']
            top = first_char['top']
            
            # Detect Type
            
            # Committee Name (10.5 Bold)
            if is_font(first_char, 10.5, is_bold):
                structured_lines.append({
                    "type": "committee_name", 
                    "text": text, 
                    "top": top,
                    "x0": x0
                })
                
            # Subcommittee Name (11.2 Bold)
            elif is_font(first_char, 11.2, is_bold):
                 structured_lines.append({
                    "type": "subcommittee_name", 
                    "text": text,
                    "top": top
                })
                 
            # Member / Role / Label (12.7)
            elif (12.7 - TOLERANCE <= first_char['size'] <= 12.7 + TOLERANCE):
                # Check for Majority/Minority headers (Bold 12.7)
                if is_bold(first_char.get('fontname','').lower()):
                    keywords = ["majority members", "minority members"]
                    if any(k in text.lower() for k in keywords):
                        structured_lines.append({
                            "type": "side_header",
                            "text": text, # e.g. "Majority Members"
                            "x0": x0,
                            "top": top
                        })
                    # Ignoring other bold 12.7 labels usually (Total Members, etc.)
                
                # Regular (Member Name) or Italic (Role) - or mixed
                else:
                     # Check first char style
                     is_it = is_italic(first_char.get('fontname','').lower())
                     
                     structured_lines.append({
                         "type": "content_line",
                         "text": text,
                         "is_italic": is_it,
                         "x0": x0,
                         "top": top,
                         "chars": seg # Keep chars
                     })
                 
    return structured_lines

# Bekannte Rollen-Phrasen (laengste zuerst -> 'Chairman' vor 'Chair'), um an
# verklebten Spalten-Uebergaengen (z.B. 'ChairmanWhitehouse') die Rolle des
# linken Members vom Namen des rechten Members zu trennen.
ROLE_PHRASES = sorted(
    [
        "Vice Chairwoman", "Vice Chairman", "Vice Chair",
        "Chairwoman", "Chairman", "Chair",
        "Ranking Member",
        "Cochairman", "Co-Chairman", "Cochair",
        "Ex Officio",
    ],
    key=len,
    reverse=True,
)

STATE_RE = re.compile(r'\(([A-Z]{2})\)')


def _normalize_role(role_raw):
    """Normalisiert einen rohen Rollen-Text -> (role, is_ex_officio).

    Committee-Ebene: Chairman/Vice Chairman/Ranking Member/Cochairman/Ex Officio.
    Die ebenen-abhaengige Sub-Remap (Chair/Vice Chair) passiert spaeter in
    format_assignments.
    """
    role = (role_raw or "").strip()
    is_ex_officio = False
    if "ex officio" in role.lower():
        is_ex_officio = True
        role = "Ex Officio"
    elif role:
        rl = role.lower()
        if "chair" in rl:
            if "vice" in rl:
                role = "Vice Chairman"
            elif "co" in rl:
                role = "Cochairman"
            else:
                role = "Chairman"
        elif "ranking" in rl:
            role = "Ranking Member"
    return role, is_ex_officio


def parse_member_line(line_obj, ref_majority_x, ref_minority_x):
    """Parst eine content-Zeile -> Liste von Member-Dicts.

    Eine Zeile kann mehrere Member enthalten, wenn die Spalten-Trennung im PDF
    fehlschlug (Majority+Minority in einer Zeile verklebt, z.B. erste Reihe
    'Chairman' | 'Ranking Member'). Wir splitten entlang der State-Codes und
    trennen am Klebepunkt eine bekannte Rollen-Phrase ab.

    Partei: erster Member nach x-Position (Majority=R in 119th); weitere Member
    einer verklebten Zeile stehen in der Minority-Spalte -> 'D'.
    """
    text = line_obj['text']
    x0 = line_obj['x0']

    dist_maj = abs(x0 - ref_majority_x) if ref_majority_x else 9999
    dist_min = abs(x0 - ref_minority_x) if ref_minority_x else 9999
    first_party = "R" if dist_maj < dist_min else "D"
    # NOTE: Majority is R in 119th Senate. If that changes, this needs config.

    members = []
    remainder = text
    idx = 0
    while True:
        m = STATE_RE.search(remainder)
        if not m:
            break
        name = remainder[:m.start()].strip().strip(',').strip()
        state = m.group(1)
        after = remainder[m.end():].lstrip()
        if after.startswith(','):
            after = after[1:].lstrip()

        # Folgt ein weiterer State-Code? -> verklebte Zeile, Rolle ist eine
        # bekannte Phrase am Anfang von 'after', der Rest ist der naechste Name.
        if STATE_RE.search(after):
            role_raw = ""
            for ph in ROLE_PHRASES:
                if after.startswith(ph):
                    role_raw = ph
                    break
            remainder = after[len(role_raw):]
        else:
            role_raw = after
            remainder = ""

        if name:
            role, is_ex_officio = _normalize_role(role_raw)
            members.append({
                "name": name,
                "party": first_party if idx == 0 else "D",
                "state": state,
                "role": role,
                "is_ex_officio": is_ex_officio,
            })
            idx += 1

        if not remainder.strip():
            break

    return members

def process_pdf(pdf_path, limit_pages=None):
    members_db = {} # Key: "Name" (assuming unique enough, or Name+State)
    
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        if limit_pages:
            pages = pages[:limit_pages]
            
        print(f"Processing {len(pages)} pages...")
            
        current_committee = None
        current_subcommittee = None
        
        # We need to track context across lines
        # But we also need to respect that a new committee resets everything
        
        for p_idx, page in enumerate(pages):
            print(f"  Page {p_idx + 1}")
            # Crop margins? Standard page seems fine.
            
            # 1. Filter Chars
            chars = page.chars
            
            # 2. Structure Lines
            structured = extract_text_by_style(chars)
            
            # 3. Context Tracking for this page
            # Note: Headers (Majority/Minority) appear multiple times (main comm, each subcomm)
            # We need to associate members with the *closest previous* header or context.
            
            # Better approach: Iterate linearly.
            
            ref_maj_x = 80 # default
            ref_min_x = 280 # default
            
            # State machine
            # We are inside a Committee (Global for page unless changed)
            # We are inside a Subcommittee (Resets when new sub or new comm found)
            
            # Logic: 
            # - If Committee Name found -> Set Current Comm, Clear Subcomm
            # - If Subcommittee Name found -> Set Current Subcomm
            # - If "Majority Members" / "Minority" found -> Update ref X coords
            # - If Member found -> Add to DB with Current Comm/Subcomm
            
            # Problem: Multi-line headers.
            # "Committee on Environment and Public" (line 1)
            # "Works" (line 2)
            # We need to merge them.
            
            idx = 0
            while idx < len(structured):
                item = structured[idx]
                
                # Check for multi-line Committee Name
                if item['type'] == 'committee_name':
                    full_name = item['text']
                    # Look ahead
                    j = idx + 1
                    while j < len(structured) and structured[j]['type'] == 'committee_name':
                        # Check top distance to see if it's the same block
                        if structured[j]['top'] - structured[j-1]['top'] < 20: 
                             full_name += " " + structured[j]['text']
                             j += 1
                        else:
                            break
                    idx = j - 1 # Update index
                    
                    current_committee = full_name
                    current_subcommittee = None # Reset sub
                    # print(f"    New Committee: {current_committee}")
                
                # Check for multi-line Subcommittee Name
                elif item['type'] == 'subcommittee_name':
                    full_name = item['text']
                    j = idx + 1
                    while j < len(structured) and structured[j]['type'] == 'subcommittee_name':
                        if structured[j]['top'] - structured[j-1]['top'] < 20:
                             full_name += " " + structured[j]['text']
                             j += 1
                        else:
                            break
                    idx = j - 1
                    
                    current_subcommittee = full_name
                    # print(f"    New Subcommittee: {current_subcommittee}")
                    
                elif item['type'] == 'side_header':
                    if "majority" in item['text'].lower():
                        ref_maj_x = item['x0']
                    elif "minority" in item['text'].lower():
                        ref_min_x = item['x0']
                        
                elif item['type'] == 'content_line':
                    if not item['is_italic']: # Actual member list usually regular
                        # Try parsing -> Liste (verklebte Zeilen = mehrere Member)
                        for m_data in parse_member_line(item, ref_maj_x, ref_min_x):
                            # Add to DB
                            key = m_data['name']
                            if key not in members_db:
                                members_db[key] = {
                                    'Name': m_data['name'],
                                    'Party': m_data['party'],
                                    'State': m_data['state'],
                                    'ExOfficio_Global': False,
                                    'Assignments': []
                                }

                            # Add Assignment
                            # Special Rule: Cochairman at Committee level is Ex Officio
                            is_cochair_comm = (current_subcommittee is None and "Cochairman" in m_data['role'])

                            final_ex_officio = m_data['is_ex_officio'] or is_cochair_comm

                            assign = {
                                'Committee': current_committee,
                                'Subcommittee': current_subcommittee,
                                'Role': m_data['role'],
                                'ExOfficio': final_ex_officio
                            }
                            members_db[key]['Assignments'].append(assign)

                            if final_ex_officio:
                                members_db[key]['ExOfficio_Global'] = True

                idx += 1
                
    return members_db

def format_assignments(assignments):
    # Sort: Global Committee first, then Subcommittees?
    # Structure: Committee|Role:Sub|Role;Sub|Role
    
    # 1. Group by Committee
    grouped = defaultdict(list)
    for a in assignments:
        if not a['Committee']: continue # Should not happen
        grouped[a['Committee']].append(a)
        
    # 2. Build String
    # "Agriculture|Chairman:Commodity Markets...|Ex Officio;Conservation...|Ex Officio"
    
    comm_strings = []
    
    for comm_name, items in grouped.items():
        # Find main committee role text
        main_role = ""
        # The entry with Subcommittee=None is the main committee entry
        main_entries = [i for i in items if i['Subcommittee'] is None]
        if main_entries:
            main_role = main_entries[0]['Role']
        
        # Build Subcommittees part
        sub_strings = []
        sub_entries = [i for i in items if i['Subcommittee'] is not None]
        for sub in sub_entries:
            # "Subname|Role"
            r = sub['Role']
            if sub['ExOfficio']:
                r = "Ex Officio" # Ensure consistent casing
            else:
                # F2: Subcommittee-Ebene nutzt Chair/Vice Chair (interne Bezeichnung,
                # um Committee- von Subcommittee-Fuehrung zu unterscheiden).
                if r == "Chairman":
                    r = "Chair"
                elif r == "Vice Chairman":
                    r = "Vice Chair"

            s_str = f"{sub['Subcommittee']}|{r}"
            sub_strings.append(s_str)

        # Combine (F2): Comm|Role:Sub1|R~Sub2|R ; naechstes Committee mit ';'
        base = f"{comm_name}|{main_role}"
        if sub_strings:
            base += ":" + "~".join(sub_strings)

        comm_strings.append(base)

    return ";".join(comm_strings)

def main():
    parser = argparse.ArgumentParser(description="Senate Committee PDF Parser")
    parser.add_argument(
        "--pdf",
        type=str,
        default=PDF_PATH,
        help=f"Pfad zur Senate Committee PDF (default: {PDF_PATH})",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Ausgabe-CSV-Pfad (default: senate_member&(sub)committees.csv neben der PDF)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test-Modus: nur erste 3 Seiten verarbeiten",
    )
    args = parser.parse_args()

    pdf_path = args.pdf

    if args.out:
        output_path = args.out
    elif args.test:
        output_path = os.path.join(os.path.dirname(pdf_path), TEST_CSV_OUTPUT_PATH)
    else:
        output_path = os.path.join(os.path.dirname(pdf_path), CSV_OUTPUT_PATH)

    limit = 3 if args.test else None

    if args.test:
        print("Running in TEST MODE (First 3 pages)...")

    print(f"PDF:    {pdf_path}")
    print(f"Output: {output_path}")

    data = process_pdf(pdf_path, limit_pages=limit)

    # Write CSV
    print(f"Writing {len(data)} members to {output_path}...")
    headers = ["Name", "Party", "State", "Committees", "ExOfficio"]

    rows = []
    for member in data.values():
        committees_str = format_assignments(member['Assignments'])
        rows.append({
            "Name": member['Name'],
            "Party": member['Party'],
            "State": member['State'],
            "Committees": committees_str,
            "ExOfficio": "true" if member['ExOfficio_Global'] else "false"
        })

    # Sort by Name
    rows.sort(key=lambda x: x['Name'])

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    print("Done.")

if __name__ == "__main__":
    main()
