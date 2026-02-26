# Congress Tracker - Datenstruktur-Dokumentation

Vollständige Übersicht aller relevanten Dateien, ihrer Struktur und Verwendung.

---

## 1. Trade-Daten (Quiver)

### `data/raw/congress_trading_data_quiver.xlsx`
**Quelle**: Quiver Quantitative API
**Format**: Excel (.xlsx)
**Größe**: ~9.3 MB
**Update**: Wöchentlich (manuell)

**Spalten**:
- `Name` - Politiker-Name (z.B. "Nancy Pelosi")
- `Ticker` - Stock Symbol (z.B. "AAPL")
- `Chamber` - "House" oder "Senate"
- `Traded` - Transaktionsdatum (YYYY-MM-DD)
- `Transaction` - "Purchase" oder "Sale"
- `Trade_Size_USD` - Handelsgröße in USD
- (weitere Spalten je nach Quiver-Format)

**Verwendung**: Hauptdatenquelle für alle Trades. Wird von `scripts/import_trades.py` importiert.

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

## 5. Processed Data (Output)

### `data/processed/trades_master.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Größe**: Variable (aktuell ~108,982 Trades)
**Update**: Bei jedem Import (kumulativ)

**Spalten** (nach Modul 4):
```csv
# Original Quiver Daten
Name, Ticker, Chamber, Traded, Transaction, Trade_Size_USD, ...

# Anreicherung durch Name Matching (Modul 3)
Member_Party,           # D oder R
Member_State,           # CA, TX, etc.
Member_Committees,      # Abgekürzte Liste
Member_ExOfficio,       # true/false
Member_Chamber,         # House/Senate
MatchScore,             # 0-100 (Fuzzy Match Score)
MatchSource,            # exact, fuzzy, alias, none

# Anreicherung durch Committee Filter (Modul 4) ⭐ NEU
Member_Committees_Full, # Vollständiger Committee-String
Relevant_Sectors,       # JSON: [{sector, industry, rank, confidence, committee, subcommittee}, ...]
Relevance_Score,        # 0-100 (Committee-Sektor Relevanz)
Confidence_Level,       # Hoch/Mittel/Niedrig

# Metadata
ImportDate              # YYYY-MM-DD HH:MM:SS
```

**Beispiel** (Modul 4):
```csv
AAPL,"Tim Scott",Senate,2026-01-15,Purchase,...,D,SC,"Finance, Banking",true,Senate,100,exact,"Committee on Finance|:Subcommittee on Taxation|;Committee on Banking|:Subcommittee on Securities|","[{""sector"":""Technology"",""industry"":""Software—Application"",""rank"":1,""confidence"":""Hoch""}]",100,Hoch,2026-01-20 10:30:00
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
quiver_name, count, chamber, best_match, best_match_score
```

**Beispiel**:
```csv
"J. Powell",15,senate,"Jerome Powell",75
"Nancy P",8,house,"Nancy Pelosi",85
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
quiver_name, correct_name, chamber
```

**Beispiel**:
```csv
"N. Pelosi","Nancy Pelosi",house
"J. Biden Jr","Joseph Biden",senate
```

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
1. IMPORT
   congress_trading_data_quiver.xlsx
   ↓
   scripts/import_trades.py
   ↓

2. NAME MATCHING
   + house_member&(sub)committees.csv
   + us_senate_member&(sub)committees.csv
   + name_aliases.csv
   ↓
   utils/name_matcher.py
   ↓
   [Name, Party, State, Committees, Chamber]

3. COMMITTEE FILTER (MODUL 4)
   + subcommittee_sector_industry_mapping.csv
   ↓
   config/committee_mapping.py → filters/committee_filter.py
   ↓
   [Relevant_Sectors, Relevance_Score, Confidence_Level]

4. OUTPUT
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
| congress_trading_data_quiver.xlsx | Wöchentlich | Quiver API (manuell) |
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

## Zukünftige Erweiterungen

### Modul 5: Ticker → Sector Lookup
**Benötigt**:
- yfinance Integration ODER
- Pre-built ticker→sector mapping CSV
- Erweiterung von `CommitteeFilter.filter_by_ticker_sector()`

**Ziel**: Filter `Relevant_Sectors` auf tatsächlichen Ticker-Sector → Boost bei Exact Match

### Backfill Script
**Zweck**: Bestehende trades_master.csv mit neuen Modul-4-Spalten anreichern
**Script**: `scripts/backfill_committee_relevance.py` (noch zu erstellen)
**Laufzeit**: ~5-10 Min für 108k Trades

---

Erstellt: 2026-02-20
Letzte Aktualisierung: 2026-02-20 (Modul 4 Implementation)
