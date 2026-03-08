# Modul 4: PDF-Extraktion House Committees

## Input
- `C:\Users\benedikt\OneDrive\bmb_trdprg\Zwischenlager\house_info_all.pdf`
- `data/raw/important/us_house_committees.xlsx`

## Output (alle in `data/raw/important/`)

### 1. `us_house_subcommittees.csv`
```csv
committee,chamber,subcommittee,chairman,chairman_state,vice_chair,ranking_member,ranking_state,all_members,ex_officio_note
```
- **WICHTIG**: Main Committee wird als subcommittee="FULL COMMITTEE" eingetragen
- all_members: Semikolon-getrennte Liste aller Mitglieder

### 2. `us_house_members_and_subcommittee.csv`
```csv
name,state,committee,subcommittee,position
```
Position: Chairman, Vice Chair, Ranking Member, Member, Ex Officio
- FULL COMMITTEE Einträge für Main Committee Members

### 3. `us_house_committees_and_subcommittees.csv`
```csv
committee,subcommittee_count,subcommittees
Appropriations,12,"Defense;Agriculture;Commerce..."
```

## Script
`scripts/extract_house_committees.py`

## Validierung
- Jedes Committee/Subcommittee: genau 1 Chairman
- Jedes Committee/Subcommittee: max 1 Vice Chair
- Jedes Committee/Subcommittee: genau 1 Ranking Member
- Keine Duplikate pro Committee/Subcommittee
- Gültige State Codes (2 Buchstaben)

## Vorgehen
1. **ERST** ein Committee extrahieren → User prüft
2. Dann alle weiteren Committees

## Parsing-Regeln
- Seiten 5-55 (Skip TOC + Alphabetical)
- pdfplumber für Text-Extraktion
- Chairman: Label "Chairman" ODER erster Name nach Header
- Vice Chair: nur wenn explizit "Vice Chair" Label
- Ranking Member: Label ODER erste Person anderer Formatierung (erster Demokrat)
- Ex Officio: "[chairman and ranking...are ex officio]" erkennen

## PDF Struktur
- Republicans: normale Schrift (links nummeriert)
- Democrats: kursiv (rechts nummeriert)
- Delegates: fett
- Ratio zeigt Parteiverhältnis

## Änderungen (User-Feedback)
- Main Committee ("FULL COMMITTEE") mit allen Rollen und Members erfassen
- all_members Spalte für jeden Eintrag
