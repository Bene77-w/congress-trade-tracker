# Congress Tracker - Datenstruktur-Dokumentation

Vollständige Übersicht aller relevanten Dateien, ihrer Struktur und Verwendung.

---

## 1. Trade-Daten (Scraped)

### `data/raw/house_trades.csv`
**Quelle**: `scrapers/house_scraper.py` (ZIP + PDF-Parsing von disclosures-clerk.house.gov)
**Format**: CSV
**Update**: Bei jedem Scraper-Lauf

**Spalten**:
```csv
name,ticker,asset_name,asset_type,transaction_type,amount,transaction_date,owner,subholding,comment,disclosure_date,ptr_link,csv_upload_time
```

### `data/raw/senate_trades.csv`
**Quelle**: `scrapers/senate_scraper.py` (Playwright + DataTables API von efdsearch.senate.gov)
**Format**: CSV
**Update**: Bei jedem Scraper-Lauf

**Spalten**:
```csv
name,ticker,asset_name,asset_type,transaction_type,amount,transaction_date,owner,comment,disclosure_date,ptr_link,csv_upload_time
```

**Verwendung**: Hauptdatenquellen für alle Trades. Werden von `scripts/import_trades.py` importiert.

---

## 2. Committee-Mitgliedschaften

### `data/raw/house_member&(sub)committees.csv`
**Quelle**: PDF-Extraktion (House Committee Assignments)
**Format**: CSV
**Größe**: 89 KB
**Zeilen**: 433 (432 Mitglieder + Header)
**Update**: Bei neuen Wahlen (alle 2 Jahre)

**Spalten**:
```csv
Name,Party,State,Committees,ExOfficio
```

**Struktur**:
- `Name` - Vorname Nachname (z.B. "John Smith")
- `Party` - 1 Buchstabe: `D` (Democrat), `R` (Republican)
- `State` - 2 Buchstaben: `CA`, `TX`, etc.
- `Committees` - Pipe-delimited String (siehe unten)
- `ExOfficio` - `true` oder `false` (Chairman/Ranking Member von Hauptcommittee)

**Committees-Format** (siehe `member&(sub)committees_structure.md`):
```
Committee|Role:Subcommittee|Role;NextCommittee|...
```

**Separatoren**:
- `;` = Trennt Committees
- `|` = Trennt Name von Role
- `:` = Trennt Committee von Subcommittees

**Beispiel**:
```
"Ways And Means|Chairman:Oversight|;Social Security|;Armed Services|:Seapower|Ranking Member"
```
→ Chairman von Ways And Means (mit Subcommittee Oversight), Member von Social Security, Member von Armed Services/Seapower (Ranking Member)

**Verwendung**: Name-Matching in `utils/name_matcher.py` → Anreicherung in `scripts/import_trades.py`

---

### `data/raw/senate_member&(sub)committees.csv`
**Quelle**: PDF-Extraktion (Senate Committee Assignments)
**Format**: CSV
**Größe**: 78 KB
**Zeilen**: 239 (100 Senatoren + Header + Duplikate durch mehrere Committees)
**Update**: Bei neuen Wahlen (alle 2 Jahre)

**Spalten**: Gleiche Struktur wie House CSV

**Unterschiede zu House**:
- Committee-Namen beginnen oft mit "Committee on ..." (wird normalisiert)
- Ausführlichere Subcommittee-Namen
- Weniger Mitglieder, aber komplexere Committee-Strukturen

**Verwendung**: Gleich wie House CSV

---

### `data/raw/member&(sub)committees_structure.md`
**Dokumentation** der Committee-String-Syntax (siehe oben)

---

## 3. Committee → Sektor Mapping

### `data/raw/subcommittee_sector_industry_mapping_v3.xlsx`
**Quelle**: Manuell erstellt (Mapping-Projekt)
**Format**: Excel (.xlsx) mit 6 Sheets
**Größe**: 76 KB
**Update**: Bei Bedarf (neue Committees, Korrekturen)

**Sheets**:

#### Sheet 1: Overview (180 Zeilen)
Breite Ansicht: Ein Subcommittee pro Zeile, bis zu 6 Sektoren in Spalten

**Spalten**:
```
Chamber, Committee, Subcommittee,
Sektor_1, Industries_1, Sektor_2, Industries_2, ..., Sektor_6, Industries_6,
Region_Relevance, Konfidenz, Notiz, Bemerkung
```

#### Sheet 2: Industry_Mapping (861 Zeilen) ⭐ **HAUPTSHEET**
Normalisierte Ansicht: Ein Sektor/Industry pro Zeile

**Spalten**:
```csv
Chamber,Committee,Subcommittee,Sector,Industry,Sector_Rank,Konfidenz
```

**Beispiel**:
```csv
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Financial Services,Capital Markets,1,Hoch
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Financial Services,Financial Data & Stock Exchanges,1,Hoch
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Technology,Software—Application,2,Hoch
```

**Sector_Rank** (wichtig für Scoring):
- `1` = Primärer Sektor (höchste Relevanz) → 100% Gewichtung
- `2` = Sekundärer Sektor → 75% Gewichtung
- `3` = Tertiärer Sektor → 50% Gewichtung
- `4+` = Weitere Sektoren → 25% Gewichtung

**Konfidenz**:
- `Hoch` = High confidence (1.0x Multiplier)
- `Mittel` = Medium confidence (0.7x Multiplier)
- `Niedrig` = Low confidence (0.4x Multiplier)

**Verwendung**: Wird von `config/committee_mapping.py` geladen → Lookup in `filters/committee_filter.py`

#### Sheet 3: Region_Mapping (238 Zeilen)
Geografische Relevanz von Committees

#### Sheet 4: Korrekturen (36 Zeilen)
Audit Trail für Mapping-Änderungen

#### Sheet 5: Statistik (67 Zeilen)
Validierungsstatistiken

#### Sheet 6: Manuelle Prüfung (6 Zeilen)
Foreign Affairs Committees mit schwacher Sektor-Zuordnung

---

### `data/raw/subcommittee_sector_industry_mapping.csv` ⭐
**Quelle**: Automatisch generiert aus xlsx (Industry_Mapping Sheet)
**Format**: CSV
**Größe**: 99 KB
**Zeilen**: 862 (861 Mappings + Header)
**Generierung**: `python scripts/convert_xlsx_to_csv.py`

**Spalten**: Gleich wie Industry_Mapping Sheet (siehe oben)

**Verwendung**: **EMPFOHLEN** für Code (schneller als xlsx)
Wird von `CommitteeMapper` geladen.

---

## 4. Yahoo Finance Sektoren

### `data/raw/yf_secotors_with_industries.xlsx`
**Quelle**: Yahoo Finance Sektor-Klassifikation
**Format**: Excel (.xlsx)
**Größe**: 16 KB
**Zeilen**: 167

**Struktur**: Hierarchisch organisiert

**Spalten**:
```csv
Industry,Market Weight
```

**Format**:
- Sektoren haben `100.00%` Market Weight (z.B. "Technology")
- Industries darunter haben prozentuale Anteile (z.B. "Semiconductors, 36.04%")

**Beispiel**:
```
Technology,100.00%
Semiconductors,36.04%
Software - Infrastructure,21.59%
Consumer Electronics,16.90%
...
Financial Services,100.00%
Banks - Diversified,22.88%
Credit Services,14.38%
...
```

**Inhalt**:
- 11 Haupt-Sektoren
- ~176 Industries

---

### `data/raw/yf_sectors_with_industries.csv` ⭐
**Quelle**: Automatisch generiert aus xlsx
**Format**: CSV (flache Struktur)
**Größe**: 4.6 KB
**Zeilen**: 177 (176 + Header)
**Generierung**: `python scripts/convert_xlsx_to_csv.py`

**Verwendung**: **EMPFOHLEN** für Code (einfacher zu parsen)

---

## 5. House PDF Extractor (Hand-Delivered PTRs)

### `data/raw/house_scraper_fehler.csv`
**Quelle**: Output von `scrapers/house_scraper.py` (automatisch erzeugt)
**Format**: CSV
**Update**: Bei jedem House Scraper Lauf

**Spalten**:
```csv
doc_id, year, disclosure_date, name, ptr_link, reason
```

**Wichtige Werte für `reason`**:
- `empty_text` — PDF hat keinen Textlayer (gescanntes Papierformular) → Input für House PDF Extractor
- `download_error` — HTTP-Fehler beim PDF-Download (wird vom Extractor übersprungen)

**Verwendung**: Input-CSV für `scrapers/house_pdf_extractor.py`

---

### `data/debug/mccaul_verification/` *(Verifikations-Artefakte)*
**Quelle**: Manuell erzeugt für QA-Zwecke
**Inhalt**:
- `page_01_rotated.png` bis `page_09.png` — Seitenbilder (mit Rotation korrigiert, 200 DPI)
- `trades_extracted.csv` — Extrahierte Trades für manuellen Abgleich

**Verwendung**: Manueller Vergleich extrahierter Trades gegen Originalseiten (QA)

---

## 6. Processed Data (Output)

### `data/processed/trades_master.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Größe**: Variable (aktuell ~9,591 Trades, nur eigene Scraped-Daten)
**Update**: Bei jedem Import (kumulativ)

**Spalten** (nach Pipeline-Umbau Maerz 2026):
```csv
# Original Scraped Trade-Daten
name, ticker, asset_name, asset_type, transaction_type, amount,
transaction_date, owner, subholding, comment, disclosure_date, ptr_link, csv_upload_time,
chamber,                # House/Senate (von import_trades.py gesetzt)

# Anreicherung durch Name Matching
Member_Party,           # D oder R
Member_State,           # CA, TX, etc.
Member_Committees,      # Committee-String
Member_ExOfficio,       # true/false
Member_Chamber,         # House/Senate
MatchScore,             # 0-100 (Fuzzy Match Score)
MatchSource,            # exact, fuzzy, alias, none

# Anreicherung durch Committee Filter (Modul 4)
Member_Committees_Full, # Vollstaendiger Committee-String
Relevant_Sectors,       # JSON: [{sector, industry, rank, confidence, committee, subcommittee}, ...]
Relevance_Score,        # 0-100 (Committee-Sektor Relevanz)
Confidence_Level,       # Hoch/Mittel/Niedrig

# Anreicherung durch Vote Timing Filter (Modul 6)
Vote_Timing_Hit,        # True/False — Trade im Zeitfenster einer relevanten Abstimmung
Vote_Date,              # YYYY-MM-DD — Datum der naechsten relevanten Abstimmung
Vote_Bill_Title,        # Titel des Bills (z.B. "National Defense Authorization Act")
Vote_Policy_Area,       # CRS Policy Area (z.B. "Armed Forces and National Security")
Vote_Timing_Score,      # 0 oder VOTE_TIMING_SCORE (25) — Bonus-Score
Days_To_Vote,           # Abstand Trade→Vote in Tagen (negativ = Trade VOR Abstimmung)

# Metadata
ImportDate              # YYYY-MM-DD HH:MM:SS
```

**Verwendung**: Hauptdatenbank für Analyse, Filter und Scoring

---

### `data/processed/trades_new.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Update**: Bei jedem Import (nur neue Trades)

**Spalten**: Gleich wie trades_master.csv

**Verwendung**: Review der letzten Import-Batch

---

### `data/processed/unmatched_names.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Update**: Bei jedem Import

**Spalten**:
```csv
trade_name, count, chamber, best_match, best_match_score
```

**Beispiel**:
```csv
Marjorie Taylor Greene,286,house,Al Green,72
Nancy Pelosi,30,house,Nancy Mace,63
```

**Verwendung**:
- Review von nicht-gematchten Namen
- Kandidaten für `data/config/name_aliases.csv`

---

## 6. Configuration

### `data/config/name_aliases.csv`
**Quelle**: Manuell gepflegt
**Format**: CSV

**Spalten**:
```csv
trade_name, correct_name, chamber
```

**Beispiel**:
```csv
Rob Bresnahan,"Robert P. Bresnahan,Jr.",house
Thomas H Tuberville,"Tuberville, Tommy",senate
```

**Hinweis**: `correct_name` muss exakt dem Namen in der jeweiligen Member-CSV entsprechen (House: "First Last", Senate: "Last, First M.")

**Verwendung**: Name-Matching in `utils/name_matcher.py`
Überschreibt Fuzzy-Matching für bekannte Varianten.

---

## 7. Weitere Mappings

### `data/raw/subcommittee_region_mapping.csv`
**Quelle**: Automatisch generiert aus xlsx (Region_Mapping Sheet)
**Format**: CSV
**Größe**: 20 KB
**Zeilen**: 239

**Spalten**:
```csv
Chamber, Committee, Subcommittee, Region, Konfidenz
```

**Regionen**: USA, Global, Europe, Asia, Latin_America, Africa, Middle_East

**Verwendung**: Zukünftige Erweiterung (geografische Filter)

---

## Data Flow Übersicht

```
1. SCRAPING
   house_scraper.py → house_trades.csv
   senate_scraper.py → senate_trades.csv
   ↓

2. IMPORT + NAME MATCHING
   scripts/import_trades.py
   + house_member&(sub)committees.csv
   + senate_member&(sub)committees.csv
   + name_aliases.csv
   ↓
   utils/name_matcher.py (Senate-Normalisierung + Fuzzy 85%)
   ↓
   [Name, Party, State, Committees, Chamber]

3. COMMITTEE FILTER (MODUL 4)
   + subcommittee_sector_industry_mapping.csv
   ↓
   config/committee_mapping.py → filters/committee_filter.py
   ↓
   [Relevant_Sectors, Relevance_Score, Confidence_Level]

4. VOTE TIMING FILTER (MODUL 6)
   + congress_bills_cache.csv (via fetch_congress_votes.py)
   + congress_votes.csv (gecachte Floor-Votes)
   ↓
   config/vote_sector_mapping.py → filters/vote_timing_filter.py
   ↓
   [Vote_Timing_Hit, Vote_Date, Vote_Bill_Title, Vote_Policy_Area,
    Vote_Timing_Score, Days_To_Vote]

5. OUTPUT
   ↓
   trades_master.csv (kumulativ)
   trades_new.csv (neue Trades)
   unmatched_names.csv (nicht gematchte Namen)
```

---

## Scoring-Formel (Modul 4)

```python
# Für jeden Sector Match:
base_score = RANK_WEIGHTS[rank] * 100
  # Rank 1 = 100, Rank 2 = 75, Rank 3 = 50, Rank 4 = 25

confidence_mult = CONFIDENCE_WEIGHTS[confidence]
  # Hoch = 1.0, Mittel = 0.7, Niedrig = 0.4

role_mult = ROLE_WEIGHTS[role]
  # Chairman/Ranking Member/Vice Chairman/EXOfficio = 1.5
  # Regular Member = 1.0

sector_score = base_score × confidence_mult × role_mult

# Final Score = Maximum über alle Matches (0-100, capped)
```

**Beispiel**:
- Politician: Chairman von Agriculture/Commodity Markets
- Trade: Financial Services Aktie
- Mapping: Rank 1, Hoch Confidence
- Score: 100 × 1.0 × 1.5 = **150 → capped at 100**

---

## Datei-Generierung

### CSV aus xlsx erzeugen:
```bash
cd congress_tracker
.\venv\Scripts\activate
python scripts/convert_xlsx_to_csv.py
```

Erzeugt:
- `yf_sectors_with_industries.csv`
- `subcommittee_sector_industry_mapping.csv`
- `subcommittee_region_mapping.csv`

---

## Update-Frequenzen

| Datei | Update | Quelle |
|-------|--------|--------|
| house_trades.csv / senate_trades.csv | Bei Scraper-Lauf | house_scraper.py / senate_scraper.py |
| house/senate committees.csv | Alle 2 Jahre | PDF-Extraktion (Wahlen) |
| subcommittee_sector_mapping.xlsx | Bei Bedarf | Manuell (neue Committees) |
| trades_master.csv | Bei jedem Import | Automatisch |
| name_aliases.csv | Bei Bedarf | Manuell (neue Varianten) |

---

## Wichtige Hinweise

### 1. Committee String Parsing
- **Komplex**: Verschachtelte Struktur mit 3 Separatoren (`;`, `|`, `:`)
- **Chamber-spezifisch**: Senate hat "Committee on ..." Prefix
- **Normalisierung**: Lowercase, Strip, Prefix-Handling

### 2. Sector_Rank Bedeutung
- **NICHT** die Qualität des Mappings (das ist `Konfidenz`)
- **SONDERN** die Priorität des Sektors für dieses Subcommittee
- Beispiel: Agriculture/Livestock → Consumer Defensive (Rank 1), Healthcare (Rank 3)

### 3. Ex Officio Status
- **Definition**: Chairman oder Ranking Member eines **Hauptcommittees** (nicht Subcommittee)
- **Automatisch berechnet** beim PDF-Import
- **Gewichtung**: Alle Ex Officio Rollen bekommen 1.5x Multiplier

### 4. JSON in CSV
- `Relevant_Sectors` ist JSON-String in CSV
- **Beim Lesen**: `json.loads(row['Relevant_Sectors'])`
- **Beim Schreiben**: `json.dumps(sectors_list)`

---

## 8. Dokumentation

### `organisation/FILTER_PARAMETER.md`
**Quelle**: Zusammenstellung aus config/settings.py, Aufgaben_Uebersicht, CLAUDE.md
**Format**: Markdown
**Erstellt**: 2026-02-20

**Zweck**: Zentrale Übersicht aller Filter-Parameter, Multiplikatoren und Punktesysteme

**Inhalt**:
- Filter 1-7: Alle Parameter mit Implementierungsstatus
- Committee Scoring: Rank Weights, Confidence, Role Multiplier
- Gesamt-Scoring: Gewichtungen (Summe = 100%)
- Score-Schwellen: 0-29, 30-49, 50-74, 75-100
- Email-Alert Threshold: 50

**Status-Übersicht**:
- ✅ Filter 3 (Committee): Code vollständig implementiert
- ✅ Filter 1, 2, 6, 7: Parameter definiert, Code ausstehend
- ⏸️ Filter 4, 5: Vorerst übersprungen

**Verwendung**: Referenz für Filter-Implementierung und Scoring-System

---

---

## 8. Vote Timing Daten (Modul 6)

### `data/raw/congress_bills_cache.csv` ⭐
**Quelle**: `scripts/fetch_congress_votes.py` (Congress.gov API v3)
**Format**: CSV
**Update**: Einmalig (~4h erster Lauf), danach Delta (nur neue Bills)

**Spalten**:
```csv
congress, bill_type, bill_number, bill_title, policy_area,
mapped_sectors, origin_chamber, introduced_date, update_date, fetched_at
```

**Wichtige Felder**:
- `congress` — Kongress-Nummer (119 = 2025-2027)
- `bill_type` — hr, s, hjres, sjres, hconres, sconres, hres, sres
- `policy_area` — CRS Policy Area (z.B. "Armed Forces and National Security")
- `mapped_sectors` — kommaseparierte YF-Sektoren (leer wenn unmapped)
- `fetched_at` — Zeitstempel des API-Calls

**Zweck**: Lokaler Cache aller Bills eines Kongresses. Vermeidet wiederholte API-Calls.
Enthält Policy Area fuer jedes Bill (nur abrufbar via Bill-Detail-Endpoint, nicht in der Bill-Liste).

**Generierung**:
```bash
python scripts/fetch_congress_votes.py          # Delta (neue Bills)
python scripts/fetch_congress_votes.py --full   # Alles neu
```

---

### `data/raw/congress_votes.csv`
**Quelle**: `scripts/fetch_congress_votes.py` (Phase 2, aus Bill-Actions)
**Format**: CSV
**Update**: Bei jedem Fetcher-Lauf (Delta)

**Spalten**:
```csv
congress, bill_type, bill_number, bill_title, policy_area,
mapped_sectors, vote_date, action_text, chamber, bill_url
```

**Wichtige Felder**:
- `mapped_sectors` — kommaseparierte YF-Sektoren (aus `config/vote_sector_mapping.py`)
- `vote_date` — Abstimmungsdatum (YYYY-MM-DD)
- `action_text` — Beschreibung der Action (z.B. "On passage Agreed to by the Yeas...")
- `chamber` — "House" (vorerst nur House; Senate-XML-Parser kommt spaeter)

**Zweck**: Gecachte Floor-Votes fuer Filter 4 (Vote Timing).
Nur Bills mit `mapped_sectors` werden in Phase 2 verarbeitet (~38% aller Bills mit Policy Area).

**Beispiel**:
```csv
119,hr,144,"Tennessee Valley Authority Salary Transparency Act",Government Operations and Politics,,2025-01-15,"On motion to suspend the rules and pass the bill Agreed to",House,...
119,hr,187,"Public Lands...","Public Lands and Natural Resources","Energy,Basic Materials",2025-01-15,"On passage...",House,...
```

---

### `config/vote_sector_mapping.py`
**Quelle**: Manuell erstellt (basierend auf `organisation/modul6_mapping.md`)
**Format**: Python-Modul

**Inhalt**:
- `POLICY_AREA_TO_SECTORS` — 12 sichere Mappings: Policy Area → [YF-Sektoren]
- `AGENT_REQUIRED_POLICY_AREAS` — 3 kontextabhaengige Areas (fuer spaetere AI-Agent-Integration)
- `SYSTEMIC_POLICY_AREAS` — 6 systemische Areas (Steuer, Aussenpolitik, etc.)
- `IRRELEVANT_POLICY_AREAS` — 10 wirtschaftlich irrelevante Areas

**Hilfsfunktionen**:
- `get_relevant_sectors(policy_area)` → Liste von Sektoren
- `is_sector_relevant(policy_area, trade_sector)` → bool
- `get_unmapped_policy_areas()` → Liste fuer AI-Agent
- `get_mapping_coverage()` → Statistiken (aktuell ~38.7% der bekannten Areas)

**Abdeckung**: 12/31 bekannte Policy Areas = 38.7% fest gemappt.
Erweiterung durch AI-Agent geplant (analysiert bill_title + summary fuer unmapped Areas).

---

### `filters/vote_timing_filter.py`
**Klasse**: `VoteTimingFilter`

**Methoden**:
- `load_votes()` → laedt congress_votes.csv
- `apply(trades_df, votes_df)` → reichert DataFrame mit Vote_Timing_* Spalten an

**Matching-Logik**:
1. Trade-Sektoren aus `Relevant_Sectors` (JSON aus Modul 4) extrahieren
2. Votes mit uebereinstimmendem `mapped_sectors` finden
3. Zeitfenster: `trade_date ZWISCHEN (vote_date - AFTER) UND (vote_date + BEFORE)`
4. Bei mehreren Treffern: naechstgelegenen Vote waehlen (abs(days_diff) minimal)

**Parameter** (aus settings.py):
- `VOTE_TIMING_WINDOW_BEFORE = 30` — max Tage VOR der Abstimmung
- `VOTE_TIMING_WINDOW_AFTER = 5` — max Tage NACH der Abstimmung (Toleranz)
- `VOTE_TIMING_SCORE = 25` — Bonus-Score bei Treffer

---

## Zukünftige Erweiterungen

### AI-Agent fuer Vote-Sektor-Mapping
**Zweck**: `AGENT_REQUIRED_POLICY_AREAS` klassifizieren (Commerce, Crime, Gov. Operations)
**Input**: bill_title + bill_summary + trade_sector
**Output**: {relevant: bool, confidence: 0-1, sectors: [...], reason: str}
**Vorbereitung**: `get_unmapped_policy_areas()` in vote_sector_mapping.py bereit

### Senate-Vote-Parsing
**Zweck**: Senate-Votes aus XML-Roll-Call-Dateien parsen
**Quelle**: clerk.senate.gov XML-Dateien (separater Endpoint)
**Vorbereitung**: `chamber='Senate'` in congress_votes.csv-Schema vorgesehen

### Modul 5: Ticker → Sector Lookup
**Benoetigt**:
- yfinance Integration ODER
- Pre-built ticker→sector mapping CSV
- Erweiterung von `CommitteeFilter.filter_by_ticker_sector()`

**Ziel**: Filter `Relevant_Sectors` auf tatsaechlichen Ticker-Sector → Boost bei Exact Match

---

Erstellt: 2026-02-20
Letzte Aktualisierung: 2026-03-08 (Modul 6: Vote Timing hinzugefuegt)
