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
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Technology,Software - Application,2,Hoch
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

### `data/raw/yf_sectors_with_industries.csv` (veraltet)
**Status**: Datei existiert nicht mehr. Ersetzt durch JSON-Version.

### `data/mappings/yf_sectors_with_industries.json` ⭐
**Quelle**: Yahoo Finance (validiert via `yf.Sector()` API)
**Format**: JSON (hierarchisch: Sector → Industries)
**Inhalt**: 11 Sektoren, 145 Industries
**Control-Datei**: Alle Industry-Namen im Projekt muessen mit dieser Datei uebereinstimmen.

**Struktur**:
```json
{"sectors": [
  {"sector": "Technology", "industries": ["Semiconductors", "Software - Infrastructure", ...]},
  {"sector": "Financial Services", "industries": ["Banks - Diversified", ...]}
]}
```

### `data/mappings/yf_sectors_with_industries.xlsx`
**Quelle**: Automatisch generiert aus JSON
**Format**: Excel (flache Struktur: Sector, Industry)
**Zeilen**: 145

### `data/mappings/ticker_industry_cache.json`
**Quelle**: Automatisch von `scripts/import_trades.py` (yfinance API)
**Format**: JSON (Ticker → {sector, industry})
**Update**: Bei jedem Import (nur neue Ticker werden abgefragt)

**Struktur**:
```json
{
  "AAPL": {"sector": "Technology", "industry": "Consumer Electronics", "quoteType": "EQUITY"},
  "SPY": {"sector": "", "industry": "", "quoteType": "ETF"},
  "VFIAX": {"sector": "", "industry": "", "quoteType": "MUTUALFUND"}
}
```

**Hinweis**: Enthaelt positive Eintraege — Ticker mit Sector/Industry (inkl. ETFs, Fonds). Null-Eintraege sind in `ticker_0_cache.json` separiert.

**Verwendung**: Cache fuer Ticker-Industry Lookup (Sektor/Industrie) in der Import-Pipeline. Erstlauf ~42 Min (5000+ Ticker), danach nur neue Ticker. Wird NICHT mehr fuer ticker_type verwendet (siehe `data/cache/ticker_type_cache.db`).

### `data/mappings/ticker_0_cache.json`
**Quelle**: Automatisch von `scripts/import_trades.py` (yfinance API + Pre-Filter)
**Format**: JSON (sortierte Liste von Ticker-Strings)
**Update**: Bei jedem Import

**Struktur**:
```json
[
  "07/01/28",
  "AABA",
  "BACWSA",
  "SPY"
]
```

**Hinweis**: Enthaelt Ticker fuer die yfinance kein Sector/Industry liefert (ETFs, delistete Aktien, Fonds) PLUS Ticker die vom Pre-Filter als Non-Standard erkannt wurden (Warrants, CUSIPs, Bonds, Auslandsnotierungen etc.) und gar nicht erst bei yfinance abgefragt werden. Pre-Filter-Patterns konfigurierbar in `config/settings.py` (`TICKER_FILTER_PATTERNS`).

**Verwendung**: Verhindert wiederholte yfinance-API-Calls fuer bekannt unresolvbare Ticker (Sektor/Industrie). Wird NICHT fuer ticker_type verwendet.

### `data/cache/ticker_type_cache.db`
**Quelle**: Automatisch von `scripts/import_trades.py` via `utils/openfigi_client.py` (OpenFIGI API)
**Format**: SQLite-Datenbank
**Update**: Bei jedem Import (nur Cache-Misses werden bei der API abgefragt)

**Schema**:
```sql
CREATE TABLE ticker_types (
    ticker TEXT PRIMARY KEY,        -- Ticker-Symbol (z.B. "SPY", "AAPL")
    security_type TEXT NOT NULL,    -- Interner Typ: "Stock", "ETF", "Mutual Fund" etc.
    openfigi_raw_type TEXT,         -- OpenFIGI securityType (z.B. "ETP", "Common Stock")
    openfigi_raw_type2 TEXT,        -- OpenFIGI securityType2
    openfigi_market_sector TEXT,    -- OpenFIGI marketSector (z.B. "Equity", "Corp")
    source TEXT NOT NULL,           -- Herkunft: "openfigi", "regex", "whitelist", "fallback", "error"
    checked_date TEXT NOT NULL,     -- ISO-Datum des letzten Lookups
    figi TEXT                       -- FIGI-Nummer (Bloomberg-Kennung)
);
```

**TTL**: Normale Eintraege 90 Tage, "Unknown" 7 Tage. Bei Offline: alle Cache-Eintraege gueltig.

**Verwendung**: Primaere Quelle fuer `ticker_type`-Spalte in `trades_master.csv`. Ersetzt die fruehere yfinance-basierte quoteType-Klassifikation.

### `data/mappings/Vorzugsaktien_mapping.csv`
**Quelle**: Manuell gepflegt
**Format**: CSV
**Update**: Bei Bedarf (neue Preferred-Ticker in Daten entdeckt)

**Spalten**:
```csv
stammaktien_ticker, vorzugsaktien_ticker, Company
```

**Beispiel**:
```csv
KMI,EP$C,EL PASO CORPORATION
USB,USB$N,US BANCORP DEL
T,T$A,AT&T INC.
```

**Verwendung**: Mapping von Vorzugsaktien-Tickern (z.B. `EP$C`) auf Stammaktien-Ticker (z.B. `KMI`), damit der yfinance-Lookup den korrekten Sector/Industry findet. Wird in `enrich_with_ticker_industry()` geladen.

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

### `logs/house_pdf_extractor_done.csv`
**Quelle**: Automatisch von `scrapers/house_pdf_extractor.py` erzeugt
**Format**: CSV
**Update**: Nach jedem Extractor-Lauf (auch bei 0 Trades)

**Spalten**:
```csv
doc_id, processed_at
```

**Verwendung**: Delta-Check — bereits verarbeitete DocIDs werden beim naechsten Lauf uebersprungen. `--no-delta` ignoriert diese Datei.

### `data/raw/senate_pdf_extractor_done.csv`
**Quelle**: Automatisch von `scrapers/senate_pdf_extractor.py` erzeugt
**Format**: CSV
**Update**: Nach jedem Extractor-Lauf (auch bei 0 Trades)

**Spalten**:
```csv
ptr_link, processed_at
```

**Verwendung**: Delta-Check — bereits verarbeitete ptr_links werden beim naechsten Lauf uebersprungen. `--no-delta` ignoriert diese Datei.

### `logs/ocr_uncertain.csv`
**Quelle**: `scrapers/house_pdf_extractor_v2.py` (readable=false) + `scripts/import_trades.py` (Datum-Validierung)
**Format**: CSV
**Update**: Nach jedem Extractor-Lauf / Import-Lauf (kumulativ)

**Spalten**:
```csv
name, ticker, asset_name, asset_type, transaction_type, amount, transaction_date,
owner, subholding, comment, disclosure_date, ptr_link, csv_upload_time,
source, rejection_reason
```

**source**: `"extractor"` (Vision API als unleserlich markiert) oder `"import"` (Datum-Validierung fehlgeschlagen)
**rejection_reason**: `"low_confidence"` | `"date_too_old"` | `"date_in_future"` | `"missing_date"`

**Verwendung**: Manuelle Pruefung von Trades die nicht automatisch verarbeitet werden konnten. Trades in dieser Datei sind NICHT in house_trades.csv bzw. trades_master.csv enthalten.

---

### `data/debug/mccaul_verification/` *(Verifikations-Artefakte)*
**Quelle**: Manuell erzeugt für QA-Zwecke
**Inhalt**:
- `page_01_rotated.png` bis `page_09.png` — Seitenbilder (mit Rotation korrigiert, 200 DPI)
- `trades_extracted.csv` — Extrahierte Trades für manuellen Abgleich

**Verwendung**: Manueller Vergleich extrahierter Trades gegen Originalseiten (QA)

---

## 5b. House PDF Extractor v2 (Grid-basiert)

### `scrapers/house_pdf_extractor_v2.py`
**Quelle**: Eigenentwicklung (OpenCV + Anthropic Vision API)
**Zweck**: Verbesserte OCR für gescannte House-PTR-PDFs — löst Merge-, Truncation- und Amount-NaN-Probleme von v1
**Input**: `data/raw/house_scraper_fehler.csv` (reason="empty_text")
**Output**: Ergänzt `data/raw/house_trades.csv`

**Architektur (3-stufig):**
1. **OpenCV Grid-Detection** — Morphologische Linienerkennung (horizontal/vertical Kernels) auf Raw-Graustufenbild → Zeilen- und Spalten-Bounding-Boxes
2. **Pixel-Density Amount-Detection** — Zählt dunkle Pixel pro Zelle in den letzten 11 Spalten (A-K Checkboxen). Zelle mit meisten Pixeln = markierte Checkbox. Kein OCR/LLM nötig.
3. **Vision API pro 5-Zeilen-Batch** — Nur für Text-Felder (Owner, Asset, Tx Type, Dates). Vereinfachter Prompt ohne Amount-Extraktion.

**Fallback**: Wenn Grid nicht erkannt wird (< 3 H-Lines oder < 3 V-Lines) → ganzes Seitenbild an Vision API (wie v1). Wird im Terminal geloggt.

**Unterstützte Formulartypen:**
| Typ | Beschreibung | Beispiel |
|-----|-------------|---------|
| A | Rotiert, handschriftlich, 1 Trade/Seite | Bilirakis, Wagner |
| B | Landscape Tabelle, getippt, ~25 Trades/Seite, manchmal 90° gedreht | McCaul |
| C | Hybrid rotiert getippt, ~7 Trades/Seite | Fleischmann |
| D | Broker Statement, rotiert, ~15-20 Trades/Seite | Khanna |

**Konfiguration** (in `config/settings.py`):
- `PDF_EXTRACTOR_DPI = 300` — Render-Auflösung
- `PDF_EXTRACTOR_AMOUNT_PIXEL_THRESHOLD = 50` — Min. dunkle Pixel für Checkbox-Erkennung
- `PDF_EXTRACTOR_USE_GRID_DETECTION = True` — Grid-Modus an/aus
- `PDF_EXTRACTOR_ROW_BATCH_SIZE = 5` — Zeilen pro Vision-API-Call

**CLI** (gleiche Optionen wie v1):
```bash
python scrapers/house_pdf_extractor_v2.py                              # Alle fehler-PDFs
python scrapers/house_pdf_extractor_v2.py --doc-id 8221228 --year 2025 # Einzelne PDF
python scrapers/house_pdf_extractor_v2.py --dry-run                    # Nicht speichern
python scrapers/house_pdf_extractor_v2.py --model claude-sonnet-4-6    # Anderes Modell
```

**Dependencies**: `opencv-python-headless`, `numpy`, `pdfplumber`, `Pillow`

**Fehlerraten-Vergleich v1 vs v2:**
| Metrik | v1 (Vision API) | v2 (Grid + Pixel) |
|--------|----------------|-------------------|
| Amount NaN (Typ D) | 37.8% | ~0.5% |
| Amount NaN (Typ B) | 12.2% | ~0% |
| Merged Names | 65 | 0 |
| Truncated Names | 23 | 0 |

---

## 6. Processed Data (Output)

### `data/processed/trades_master.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Größe**: Variable (aktuell ~9,591 Trades, nur eigene Scraped-Daten)
**Update**: Bei jedem Import (kumulativ)

**Spalten** (26 Spalten, Stand Maerz 2026):
```csv
# Original Scraped Trade-Daten (Reihenfolge wie in CSV)
name,                   # Politiker-Name
ticker,                 # Stock Symbol (leer bei Bonds etc.)
ticker_type,            # Asset-Typ (abgeleitet aus Ticker, NICHT aus Quiver-Daten)
asset_name,             # Name des Assets
asset_type,             # ST/OP/GS/OT/CS/DC/EF/MF/Corporate Bond etc.
transaction_type,       # Purchase/Sale (Full)/Sale (Partial)/Exchange
amount,                 # Dollar-Range ("$1,001 - $15,000" etc.)
transaction_date,       # Transaktionsdatum
owner,                  # Self/Spouse/Joint/Child
comment,                # Freitext (Bond-Metadaten etc.)
disclosure_date,        # Meldedatum (bis 45 Tage spaeter)
ptr_link,               # URL zum Original-PTR
csv_upload_time,        # Zeitstempel des Scraper-Laufs
chamber,                # House/Senate (von import_trades.py gesetzt)
subholding,             # Depot/Trust-Name (nur House, aus "S O:" Zeilen)

# Anreicherung durch Name Matching
Member_Party,           # D oder R
Member_State,           # CA, TX, etc.
Member_Committees,      # Committee-String
Member_ExOfficio,       # True/False
Member_Chamber,         # House/Senate
MatchScore,             # 0-100 (Fuzzy Match Score)
MatchSource,            # exact, fuzzy, alias, none

# Anreicherung durch Committee Filter (Modul 4)
Member_Committees_Full, # Vollstaendiger Committee-String
Relevant_Sectors,       # JSON: [{sector, industry, rank, confidence, committee, subcommittee}, ...]
Relevance_Score,        # 0-100 (Committee-Sektor Relevanz)
Confidence_Level,       # Hoch/Mittel/Niedrig

# Anreicherung durch Filter 0 (Asset-Klasse)
asset_class_excluded,   # True = Nicht-Aktie (ETF/Option/Bond/Crypto/...) -> wird von Scoring ignoriert

# Anreicherung durch Ticker-Industry Lookup (yfinance)
Ticker_Sector,          # YF Sector (z.B. "Technology") — leer bei ETFs/Bonds/unbekannten Tickern
Ticker_Industry,        # YF Industry (z.B. "Consumer Electronics") — leer bei ETFs/Bonds/unbekannten Tickern

# Metadata
ImportDate              # YYYY-MM-DD HH:MM:SS
```

**Hinweis**: `subholding` steht nach `chamber` (nicht nach `owner`), weil Senate-Trades kein `subholding` haben — pandas fuegt die Spalte beim Concat ans Ende an.

**ticker_type Werte** (abgeleitet via OpenFIGI API + Regex Pre-Filter):

| Wert | Quelle | Beschreibung | Beispiel-Ticker |
|------|--------|-------------|-----------------|
| Stock | OpenFIGI (Common Stock, REIT, ADR) | Normale Aktie | AAPL, MSFT, BRK-B |
| ETF | OpenFIGI (ETP) | Exchange-Traded Fund/Product | SPY, QQQ, GLD |
| Mutual Fund | OpenFIGI (Open-End Fund) oder Regex | Investmentfonds | VFIAX, SYMBOL: AEPGX |
| Closed-End Fund | OpenFIGI (Closed-End Fund) | Geschlossener Fonds | UTF, EOS |
| Preferred Stock | OpenFIGI (Preferred Stock) | Vorzugsaktie | O-PA, ATH-PE |
| Warrant | Regex oder OpenFIGI | Optionsschein | KMB-W, GE-W, BACWSA |
| Rights | OpenFIGI (Right) | Bezugsrecht | TTM-R |
| SPAC Unit | OpenFIGI (Unit) | SPAC-Unit | BLOC-U |
| Bond/CUSIP | Regex | Anleihe/CUSIP-Nummer | 912796U49, 37045XEF9 |
| Bond | OpenFIGI (marketSector: Corp/Govt/Muni) | Anleihe (API-erkannt) | |
| Bond (Maturity) | Regex | Anleihe mit Faelligkeitsdatum | DUE 12/01/27 |
| T-Bill | Regex | Geldmarkt-Instrument | 8.WEEK, MATURE |
| Crypto | Regex | Kryptowaehrung | CRYPTO:BTC, BITCOIN |
| Foreign Stock | Regex | Auslandsnotierung | 1AMT.MI, NYSE: COV |
| Index | Regex | Index-Ticker | ^MWE, ^RGP |
| Option | Regex | Option | SPY160219P00180000 |
| SEDOL | Regex | UK-Wertpapierkennung | B1WSP14 |
| Other | Regex | Sonstiges (Junk, Test, Freitext) | 44, BELGIUM |
| Unknown | Fallback/Error | Kein Ergebnis von OpenFIGI | |
| Stock (Delisted) | Regression-Schutz (refresh_ticker_types) | Legacy-Label fuer akquirierte/delisted Aktien, die OpenFIGI heute nicht mehr kennt | CELG, ALXN, TWTR, ATVI |
| Depositary Receipt | OpenFIGI | ADR fuer auslaendische Aktien (expliziter als "Stock") | BABA, TSM, ASML |
| Partnership Shares | OpenFIGI | MLP-Anteile | EPD, MMP, ET |

**Filter 0 (Asset-Klasse, `asset_class_excluded`)**: Setzt `True` fuer alle `ticker_type`-Werte **ausser** `Stock`, `Stock (Delisted)`, `Depositary Receipt`, `Partnership Shares` (siehe `config/settings.py::FILTER_0_KEEP_TYPES`). Ausgefilterte Trades bleiben in der Datei, werden aber vom VoteTimingFilter und nachgelagerten Scoring-Modulen uebersprungen.

**Verwendung**: Hauptdatenbank fuer Analyse, Filter und Scoring

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

### `data/processed/unmatched_preferred.csv`
**Quelle**: Output von `scripts/import_trades.py` (Ticker-Industry Lookup)
**Format**: CSV
**Update**: Bei jedem Import (nur wenn unmatched Preferred-Ticker existieren)

**Spalten**:
```csv
ticker, count, names, asset_names
```

**Beispiel**:
```csv
XYZ$A,3,"John Doe, Jane Smith","COMPANY INC."
```

**Verwendung**:
- Review von Preferred-Tickern ohne Mapping in `Vorzugsaktien_mapping.csv`
- Kandidaten fuer neue Eintraege in der Mapping-Datei

---

### `data/processed/delisted_trades.csv`
**Quelle**: Output von `scripts/import_trades.py` (Ticker-Industry Lookup)
**Format**: CSV
**Update**: Bei jedem Import

**Spalten**:
```csv
ticker, name, asset_name, transaction_date, chamber
```

**Verwendung**:
- Uebersicht aller Trades mit delisteten/unbekannten Tickern (kein Sector/Industry bei yfinance)
- Terminal-Report zeigt Verteilung nach Jahr

---

### `archive/trades_master_backup/trades_master_{YYYYMMDD-HHMMSS}.csv`
**Quelle**: Automatisch erstellt von `scripts/import_trades.py` (`_backup_master()`)
**Format**: CSV (identisch zu trades_master.csv)
**Update**: Vor jedem destruktiven Schreiben auf trades_master.csv (normal stage mode ohne --dry_run)

**Verwendung**:
- Wiederherstellung des letzten Zustands bei fehlgeschlagener Stage
- Kein automatisches Löschen — manuell aufräumen bei Bedarf
- Pfad konfigurierbar via `settings.ARCHIVE_BACKUP_PATH`

### `data/processed/trades_stage.csv`
**Quelle**: Zwischenpuffer von `scripts/import_trades.py`
**Format**: CSV (selbe Struktur wie trades_master.csv)
**Update**: Bei `--dry_run`, `--delta --stage_*`, oder `--stage_X --dry_run`

**Verwendung**:
- Output-Ziel für alle nicht-destruktiven Stage-Läufe
- Input für `--stage_save` (committet Stage → Master)
- Wird nach erfolgreichem `--stage_save` automatisch gelöscht

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

2. IMPORT + NAME MATCHING (--stage_committees / enrich_member_data)
   scripts/import_trades.py
   + house_member&(sub)committees.csv
   + senate_member&(sub)committees.csv
   + name_aliases.csv
   ↓
   utils/name_matcher.py (Senate-Normalisierung + Fuzzy 85%)
   ↓
   [Member_Party, Member_State, Member_Committees, Member_ExOfficio,
    Member_Chamber, Member_Committees_Full]
   (MatchScore/MatchSource kommen in Stage 4)

3. TICKER-TYPE + TICKER-INDUSTRY
   + OpenFIGI API (ticker_type)
   + yfinance (Ticker_Sector, Ticker_Industry)
   ↓
   [ticker_type, Ticker_Sector, Ticker_Industry]

4. FILTER-LOGIK (--stage_filter / _apply_filter_logic)
   + subcommittee_sector_industry_mapping.csv (CommitteeFilter)
   + bill_actions.json + bill_subjects.json (VoteTimingFilter)
   ↓
   _apply_match_scores → [MatchScore, MatchSource]
   _apply_committee_relevance → [Relevant_Sectors, Relevance_Score, Confidence_Level]
   AssetClassFilter → [asset_class_excluded]
   VoteTimingFilter → [Vote_Timing_Hit, Vote_Date, Vote_Bill_Title,
                        Vote_Policy_Area, Vote_Timing_Score, Days_To_Vote]

5. OUTPUT
   ↓
   trades_master.csv (Backup in archive/trades_master_backup/ vor jedem destruktiven Schreiben)
   trades_stage.csv (Zwischenpuffer bei --dry_run / --delta --stage_*)
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

## 8. Congress API Daten (Modul 6)

**Quelle**: `scrapers/fetch_congress_data.py` (Congress.gov API v3)
**Pfad**: `data/congress_api/`
**Update**: Erstlauf (~7-8h), danach wöchentlich `--update` (~5-30 Min)

```bash
python scrapers/fetch_congress_data.py           # Erstlauf (alle 6 Schritte)
python scrapers/fetch_congress_data.py --update  # Delta (nur geänderte Bills)
python scrapers/fetch_congress_data.py --force   # Erstlauf erzwingen
```

---

### `data/congress_api/bills_list.json`
**Schritt 1** (~30 Min) — Übersicht aller Bills des Kongresses
**Felder**: `congress, number, type, bill_type, title, originChamber, updateDate, latestAction`

---

### `data/congress_api/bill_details.json`
**Schritt 2** (~1.5-2h) — Details pro Bill inkl. policyArea
**Felder**: `bill_id, bill_type, number, title, short_title, policy_area, introduced_date, latest_action, sponsors, cosponsors_count`

**Hinweis**: `policy_area` ist nur über den Bill-Detail-Endpoint verfügbar, nicht in der paginierten Bill-Liste.

---

### `data/congress_api/bill_actions.json`
**Schritt 3** (~1-2h) — Alle Actions pro Bill inkl. Floor Votes
**Felder pro Action**: `date, text, type, action_code, source_system, committee, roll_call`

**Wichtig**: `roll_call` enthält `[{chamber, congress, date, rollNumber, url}]` für namentliche Abstimmungen (Vote-Timing-Filter nutzt `type="Floor"` + vorhandenes `roll_call`).

---

### `data/congress_api/bill_committees.json`
**Schritt 4** (~1h) — Committee-Zuweisungen pro Bill
**Felder**: `bill_id, policy_area, committees: [{name, chamber, system_code, subcommittees}]`

---

### `data/congress_api/bill_summaries.json`
**Schritt 5** (~1.5h) — CRS Summaries pro Bill (für AI-Agent geplant)
**Felder**: `bill_id, policy_area, summaries: [{action_date, text, version_code}], has_summary`

---

### `data/congress_api/bill_subjects.json` ⭐
**Schritt 6** (~2.5h) — Legislative Subjects pro Bill (**löst Rider-Problem**)
**Felder**: `bill_id, policy_area, legislative_subjects: [str], subject_count`

**Zweck**: Omnibus-Bills (z.B. HR 1) haben eine einzelne `policy_area` aber decken viele Themen ab.
Legislative Subjects listen alle Themen explizit auf und erfassen auch Rider.

**Beispiel HR 1**:
```json
{
  "bill_id": "hr-119-1",
  "policy_area": "Economics and Public Finance",
  "legislative_subjects": ["Energy policy", "Oil and gas", "Medicaid",
                            "Immigration enforcement", "Defense spending", ...],
  "subject_count": 238
}
```

---

### `data/congress_api/fetch_checkpoint.json`
**Felder**:
- `completed_steps` — abgeschlossene Schritte (für Resume bei Abbruch)
- `last_full_run` — Timestamp des letzten vollständigen Erstlaufs
- `last_update_run` — Timestamp des letzten `--update` Laufs
- `update_count` — Anzahl Delta-Updates seit Erstlauf

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
Letzte Aktualisierung: 2026-03-13 (House PDF Extractor v2 hinzugefuegt)
