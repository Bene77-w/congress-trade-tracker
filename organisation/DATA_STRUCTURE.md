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

**Verwendung**: Wird von `config/committee_mapping.py` geladen → Lookup in `tags/committee_tag.py`

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
**Quelle**: Automatisch von `scripts/import_trades.py`. Enthaelt zwei Tabellen parallel:
- `ticker_types` (Legacy, via `utils/openfigi_client.py` — nicht mehr aktiv genutzt)
- `ticker_normalizer` (Rebuild Teil 2/3, via `utils/ticker_normalizer.py` — aktive Pipeline)
**Format**: SQLite-Datenbank
**Update**: Bei jedem Import (nur Cache-Misses werden bei der API abgefragt)

**Schema (aktiv) — `ticker_normalizer`** (seit 2026-05-14: `_figi`-Suffix):
```sql
CREATE TABLE ticker_normalizer (
    cache_key           TEXT PRIMARY KEY,   -- "<ticker_base>:<id_type>:<exch_code|*>"
    classification_type TEXT NOT NULL,      -- "TICKER" | "SEARCH" | "ID_CUSIP"
    figi                TEXT,               -- FIGI (Bloomberg, z.B. "BBG000B9XRY4")
    ticker_figi         TEXT,               -- Canonical Ticker laut OpenFIGI
    asset_name_figi           TEXT,               -- Canonical Name laut OpenFIGI
    security_type_figi  TEXT,               -- OpenFIGI securityType
    raw_security_type   TEXT,               -- OpenFIGI securityType (Roh-Spiegel)
    raw_security_type2  TEXT,               -- OpenFIGI securityType2 (z.B. "ETP" → Type-Korrektur)
    market_sector_figi  TEXT,               -- OpenFIGI marketSector (z.B. "Equity", "Pfd", "Muni")
    source              TEXT NOT NULL,      -- "mapping" | "search" | "search_empty" | "empty"
    checked_date        TEXT NOT NULL       -- ISO-Zeitstempel
);
```

**Schema (Legacy) — `ticker_types`**:
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

**TTL**: Normale Eintraege 90 Tage, "Unknown"/"empty" 7 Tage. Bei Offline: alle Cache-Eintraege gueltig.

**Verwendung**: `ticker_normalizer` ist die aktive Quelle der OpenFIGI-Felder im Master (Spalten `figi`, `ticker_figi`, `asset_name_figi`, `security_type_figi`, `market_sector_figi`). `ticker_types` ist Legacy und wird nicht mehr beschrieben.

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

### `data/processed/trades_master.csv` (kuratierte Sicht)
**Quelle**: Output von `scripts/import_trades.py` (Stage `save`)
**Format**: CSV
**Update**: Bei jedem Import (kumulativ; vollstaendige Spalten in `trades_master_complete.csv`)

**Hinweis (Rebuild Teil 3, 26.04.2026)**: `trades_master.csv` enthaelt jetzt
nur die **kuratierten Spalten** aus `settings.CURATED_COLUMNS`. Die
vollstaendige Spalten-Sicht liegt in `trades_master_complete.csv`. Der
Wechsel passiert beim Speichern automatisch — fuer Stage-Inputs liest
die Pipeline immer aus `trades_master_complete.csv`.

**Kuratierte Spalten** (Default in `settings.CURATED_COLUMNS`, vom
User erweiterbar):
- `trade_id` — 16-Hex Hash-ID (utils/trade_id.compute_trade_ids)
- `name`, `ticker`, `issuer_ticker`, `issuer_group_key`, `ticker_type`, `asset_name`
- `transaction_date`, `transaction_type`, `amount`
- `chamber`, `Member_Party`, `Member_State`
- `security_type_figi`, `sector_yf`, `industry_yf`
- `MatchScore`, `Vote_Timing_Hit`
- **Modul 8** (seit 8.3): `trade_volume_usd`, `asset_tradingvolume_usd`, `trade_score`, `trade_score_breakdown`, `trade_warnings`

`issuer_ticker` (P5.5): Fallback-Kette `share_class_figi → ticker_base
→ ticker`. Garantiert 100% gefuellt solange mindestens eine der drei
Quellen gesetzt ist. Wird als Gruppierungs-Key in Sequenz-Signalen
(Stage 10) und Suspicious-Asset-Aggregation (Stage 12) verwendet
(Share-Class-Level).
Hinweis: shareClassFIGI gruppiert Exchange-Listings derselben
Aktienklasse, jedoch NICHT Cross-Class wie GOOGL+GOOG (separate
Class-A/Class-C-FIGIs).

`issuer_group_key` (P5.6): Issuer-Level (Cross-Class) via SEC EDGAR
CIK. Praefix-Konvention `CIK:<cik>` / `TICKER:<id>` / `SKIP:<type>`.
Wird als SWARM_GROUPING_KEY verwendet. GOOGL und GOOG bekommen
denselben `CIK:1652044`. Vollstaendige Architektur und
Design-Entscheidungen: `organisation/Ticker_Architektur.md`.

**adj_-Prefix-Regel**: Spalten mit `adj_X` lesen den Wert aus der
Quellspalte `X` der complete-Datei und schreiben ihn unter dem
`adj_X`-Namen (signalisiert: Pipeline-modifizierter Wert).

---

### `data/processed/trades_master_complete.csv` (vollstaendige Persistierung)
**Quelle**: Output von `scripts/import_trades.py` (Stage `save`)
**Format**: CSV
**Update**: Bei jedem Import (kumulativ; jeder Stage schreibt hier ein)

**Spalten** (alle, deutlich mehr als `trades_master.csv`):
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
Member_ExOfficio,       # True/False
Member_Chamber,         # House/Senate
MatchScore,             # 0-100 (Fuzzy Match Score)
MatchSource,            # exact, fuzzy, alias, none

# Anreicherung durch Committee Filter (Modul 4)
Member_Committees_Full, # Vollstaendiger Committee-String
Insider_Sectors,        # JSON: ALLE (sub)committee-Sektoren des Members [{sector, industry, rank, confidence, role, committee, subcommittee}, ...]
Relevant_Sectors,       # JSON: Intersection von Insider_Sectors mit sector_yf/industry_yf (Match-Mode konfigurierbar in settings.RELEVANT_SECTORS_MATCH_MODE)
                        # Jeder Eintrag hat Feld match_type: 'full' (Sector+Industry treffen), 'sector' (nur Sector trifft)

# Anreicherung durch Filter 0 (Asset-Klasse)
asset_class_excluded,   # True = Nicht-Aktie (ETF/Option/Bond/Crypto/...) -> wird von Scoring ignoriert

# Anreicherung durch OpenFIGI (Stage tickeropenfigi) — Suffix _figi
figi,                   # FIGI-ID Security-Ebene (z.B. "BBG000B9XRY4")
ticker_figi,            # Canonical Ticker laut OpenFIGI (Bloomberg-Form, z.B. "WRB 7.375 PERP +A")
asset_name_figi,        # Canonical Name laut OpenFIGI (z.B. "W.R. BERKLEY CORPORATION")
security_type_figi,     # OpenFIGI securityType (z.B. "Common Stock", "PUBLIC", "FIXED, OID")
market_sector_figi,     # OpenFIGI marketSector (z.B. "Equity", "Pfd", "Corp", "Muni", "Govt")
share_class_figi,       # FIGI-ID auf Issuer x Aktienklassen-Ebene (z.B. "BBG009S39JY5" GOOGL Class A).
                        # P5: Coverage 82.67% gesamt / 83.1% YFINANCE_DIRECT.
                        # WICHTIG: shareClassFIGI = Issuer x SHARE-CLASS, NICHT reiner Issuer.
                        # GOOGL Class A und GOOG Class C bekommen UNTERSCHIEDLICHE share_class_figi
                        # (BBG009S39JY5 vs BBG009S3NB21). FIGI gruppiert Exchange-Listings derselben
                        # Aktienklasse, NICHT Cross-Class. Cross-Class-Mapping ist offenes Paket.

issuer_ticker,          # Abgeleitete Spalte (P5.5): share_class_figi -> ticker_base -> ticker.
                        # Garantiert 100% nicht-leer. Share-Class-Level. Wird als Gruppierungs-Key
                        # fuer Sequenz-Signale (Contrarian, Accumulative) und Suspicious-Asset-
                        # Aggregation verwendet. Auch in kuratierter trades_master.csv.

issuer_group_key,       # Abgeleitete Spalte (P5.6): Issuer-Level via SEC EDGAR CIK.
                        # Praefix-Konvention: CIK:<cik>, TICKER:<id>, SKIP:<type>.
                        # GOOGL und GOOG bekommen denselben CIK:1652044 (Cross-Class).
                        # ETFs/Foreign-Stocks bypassen den CIK-Lookup -> TICKER:<issuer_ticker>.
                        # Wird als SWARM_GROUPING_KEY verwendet.
                        # Doku: organisation/Ticker_Architektur.md

# Anreicherung durch yfinance (Stage tickeryfinance) — Suffix _yf
sector_yf,              # YF Sector (z.B. "Technology") — leer bei ETFs/Bonds/unbekannten Tickern
industry_yf,            # YF Industry (z.B. "Consumer Electronics") — leer bei ETFs/Bonds/unbekannten Tickern
quote_type_yf,          # YF quoteType (z.B. "EQUITY", "ETF", "MUTUALFUND")

# Modul 8 — Volume-Spalten (Phase 8.1/8.2). Edge-Cases siehe tests/test_amount_parser.py
# und tests/test_asset_volume.py.
trade_volume_usd,       # int64: Mittelpunkt der Disclosure-Amount-Range (utils/amount_parser.midpoint_usd).
asset_tradingvolume_usd,# int64: Cross-Class rolling-window Sum(trade_volume_usd) gruppiert nach
                        # issuer_group_key, Fenster Z = settings.SCORING_VOLUME_WINDOW_DAYS_Z.
                        # SKIP-Buckets / NaT-Datum -> 0. Delta-Mode liest Master-Historie mit.

# Anreicherung durch Branch-1 Signale (Stage branch1_signals)
signal_count,           # int 0-4: Anzahl ausgeloester Branch-1 Signale pro Trade
signals_triggered,      # str ";"-getrennt: Liste der Signal-Typen (z.B. "committee;floor_action")

# Modul 8 — Score-Spalten (Phase 8.3). Formel + Beispiele: organisation/Filter_Instanz_Parameter.md.
# Edge-Cases: tests/test_trade_score.py (23 Faelle).
trade_score,            # int: gewichteter Trade-Score (kein Cap). Quelle: scoring/trade_score.compute_trade_scores.
trade_score_breakdown,  # str ";"-getrennt: deterministische Komponenten-Aufschluesselung in fester Reihenfolge
                        # (Branch-1 -> Sequenz -> Schwarm -> ex_officio -> volume).
trade_warnings,         # str ";"-getrennt: Warning-Tags (initial nur "paradox_swarm").

# Metadata
ImportDate              # YYYY-MM-DD HH:MM:SS
```

**Hinweis**: `subholding` steht nach `chamber` (nicht nach `owner`), weil Senate-Trades kein `subholding` haben — pandas fuegt die Spalte beim Concat ans Ende an.

**Naming-Konvention (seit 2026-05-14, Spalten-Refactoring)**: Felder, die aus
externen APIs stammen, tragen Suffixe zur eindeutigen Quellzuordnung:
- `_figi` — alle aus OpenFIGI abgeleiteten Felder (Stage `tickeropenfigi`)
- `_yf` — alle aus yfinance abgeleiteten Felder (Stage `tickeryfinance`)
- ohne Suffix — Classifier-Outputs (`ticker_type`, `ticker_base`, …) oder Original-Scrape-Daten.

Die ID-Spalte `figi` selbst behält keinen Suffix (Eigenname/Akronym).

**Stage `tickerclassify` (Rebuild Teil 3) ergaenzt zusaetzliche Spalten**:
- `ticker_base` — normalisierte Form (z.B. BRK-B fuer BRK.B)
- `classification_reason` — welche Regex/Regel hat gefeuert
- `classification_confidence` — high/medium/low
- `api_strategy` — figi_mapping/figi_search/cusip_lookup/yfinance_direct/skip/manual_review
- `exch_code`, `series_letter`, `status_suffix`
- `manual_review_flag` — bool

**Stage `tickeropenfigi` (Rebuild T3 + T5/P5) ergaenzt zusaetzliche Spalten**:
- `figi`, `ticker_figi`, `asset_name_figi`
- `security_type_figi`, `market_sector_figi`
- `share_class_figi` (P5) — FIGI auf Issuer-Ebene (Cross-Share-Class-Anker)

**P5 — Router-Erweiterung (2026-05-18)**: Im Gegensatz zur urspruenglichen
Implementierung gehen jetzt auch `api_strategy=YFINANCE_DIRECT` (ETFs,
NUMERIC_SUFFIX, manche SHARECLASS) und `api_strategy=MANUAL_REVIEW`
(UNKNOWN, LONG_ALPHA_SUSPECT) durch einen optimistischen OpenFIGI-Mapping-
Versuch, damit ihre `share_class_figi`-Spalte gefuellt werden kann. Bei Miss
wird ein Negativ-Cache-Eintrag mit `source='empty'` geschrieben (verhindert
Wiederholungen). `api_strategy=SKIP` bleibt unveraendert ohne API-Call
(OPTION, TREASURY, CRYPTO, WHEN_ISSUED, INVALID).

**Stage `tickeryfinance` ergaenzt**:
- `sector_yf`, `industry_yf`, `quote_type_yf`

**Stage `branch1_signals` (P2) ergaenzt**:
- `signal_count` — int (0-4): Anzahl ausgeloester Branch-1 Signale pro Trade
- `signals_triggered` — str (";"-getrennt): Liste der Signal-Typen pro Trade
- Schreibt parallel die Datei `data/processed/branch1_events_stage.csv`
  (siehe Abschnitt unten) — wird in P4 (`_stage_suspicious_export`) in
  `suspicious_events.csv` promoted.

**Stage `sequence_signals` (P3) ergaenzt**:
- Akkumuliert `signal_count` und `signals_triggered` additiv: Sequenz-Signale
  (`contrarian`, `accumulative`) werden zu den bestehenden Branch-1-Werten
  hinzugefuegt. Pro Trade wird ein Signal-Typ nur einmal gezaehlt
  (Deduplikation), auch wenn er mehrfach getriggert wuerde.
- Schreibt parallel die Datei `data/processed/sequence_events_stage.csv`
  (gleiche 21-Spalten-Form wie `branch1_events_stage.csv` ab P6, inkl. `issuer_group_key`).

**Bekannter Bestand:** Master enthaelt nicht-unique `trade_id`s
(99.706 unique vs. 111.986 Rows, Stand Mai 2026). Ursache liegt im
Hash-Schema `utils.trade_id.compute_trade_ids` und ist ausserhalb des
Signal-Scopes. In `branch1_events_stage.csv` tauchen diese Duplikate als
mehrfache Events mit identischer `(signal_event_id, trade_id)` auf.
Der Duplikat-Schutz in P4 reduziert das beim Promoten auf unique
Eintraege.

**ticker_type Werte** (Rebuild Teil 3 — neue Enum-Werte aus `utils.ticker_classifier.TickerType`,
kleinbuchstabig statt der alten Großbuchstaben-Strings):

| Wert | Quelle | Beschreibung | Beispiel-Ticker |
|------|--------|-------------|-----------------|
| `stock` | Classifier (1-5 Buchstaben) | Normale US-Aktie inkl. ADR/REIT/MLP | AAPL, MSFT, BABA |
| `foreign_stock` | Classifier (Suffix .L, .DE, ...) | Auslandsnotierung | NESN.SW, AIR.PA |
| `shareclass` | Classifier (-A, -B etc., NICHT Preferred) | Aktien-Klasse | BRK-B, GOOGL |
| `etf` | Type-Korrektur OpenFIGI | Exchange-Traded Fund | SPY, QQQ |
| `preferred` | Classifier ($, .PR, -P-X etc.) | Vorzugsaktie | EP$C, ATH.PRC |
| `warrant` | Classifier (-W, -WS, w-Suffix) | Optionsschein | KMB-W, BACWSA |
| `option` | Classifier (OCC-Format) | Option | SPY160219P00180000 |
| `bond` | Classifier (CUSIP-aehnlich) | Anleihe | 37045XEF9 |
| `treasury` | Classifier (T+8-9 alnum) | US-Treasury T-Bill | T912796U49 |
| `crypto` | Classifier (USD/USDT etc.) | Krypto-Pair | BTC-USD |
| `unit` | Classifier (-U) | SPAC-Unit | BLOC-U |
| `when_issued` | Classifier (-W I) | "When-Issued"-Aktie | XYZ-WI |
| `numeric_suffix` | Classifier (AGN1, PYPL1) | Numerisches Suffix (manual_review=True) | AGN1 |
| `long_alpha_suspect` | Classifier (>=6 Großbuchstaben) | Datenmuell-Verdacht | BELGIUM |
| `misc` | Classifier (Sonderzeichen) | Sonstige Sonder-Ticker | |
| `invalid` | Classifier (Datenmuell, leer) | Nicht klassifizierbar | |
| `unknown` | Fallback | Kein Pattern getroffen | |

**Asset-Class-Gate** (User-Sicht „Filter 0", Spalte `asset_class_excluded`): Setzt `True` fuer alle `ticker_type`-Werte **ausser** `stock`, `foreign_stock`, `shareclass` (siehe `config/settings.py::ASSET_CLASS_KEEP_TYPES`). Ausgefilterte Trades bleiben in der Datei, werden aber vom VoteTimingTag und nachgelagerten Scoring-Modulen uebersprungen.

**Verwendung**: Hauptdatenbank fuer Analyse, Filter und Scoring

---

### `data/processed/branch1_events_stage.csv` (Stage-Zwischen-Output, P2)

**Quelle**: `scripts/import_trades.py::_apply_branch1_signals` (Stage `branch1_signals`)
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf der Stage komplett neu geschrieben.

**Zweck**: Snapshot aller Branch-1 Signal-Auslösungen eines Pipeline-Laufs.
Eine spaetere Suspicious-Export-Stage (P4) liest diese Datei ein, schreibt
sie append-only nach `suspicious_events.csv` und entfernt diese Stage-Datei.
Bis P4 fertig ist, dient sie auch der manuellen Inspektion.

**Spalten** (21 ab P6): `signal_event_id`, `trade_id`, `signal_type`,
`triggering_date`, `scope_key`, `member_id`, `member_name`, `chamber`,
`ticker`, `issuer_ticker`, `issuer_group_key`, `asset_name_figi`, `transaction_date`,
`disclosure_date`, `transaction_amount`, `direction`, `is_ex_officio`,
`match_count`, `vote_timing_hit_count`, `committee_subcommittee`,
`relevant_sectors`.

`match_count` = Anzahl Einträge in `Relevant_Sectors` zum Zeitpunkt des Triggers (Snapshot).

`issuer_ticker` wird über `utils/issuer_ticker.resolve_issuer_ticker`
aufgelöst (Fallback-Kette `share_class_figi → ticker_base → ticker`).
`asset_name_figi` fällt auf `asset_name` zurück, falls leer.

Detailbeschreibung pro Spalte: `organisation/tool_specific_md/signal_info.md`.

---

### `data/processed/sequence_events_stage.csv` (Stage-Zwischen-Output, P3)

**Quelle**: `scripts/import_trades.py::_apply_sequence_signals` (Stage `sequence_signals`)
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf der Stage komplett neu geschrieben.

**Zweck**: Snapshot aller Sequenz-Signal-Auslösungen (`contrarian`,
`accumulative`) eines Pipeline-Laufs. Jede Auslösung erzeugt mehrere Event-
Zeilen mit identischer `signal_event_id` (eine pro beteiligtem Trade), um
die Gruppen-Zugehoerigkeit ueber den Hash zu transportieren. P4 promoted
sie zusammen mit `branch1_events_stage.csv` nach `suspicious_events.csv`.

**Spalten:** identisch zu `branch1_events_stage.csv` (21 Spalten ab P6,
inkl. `issuer_group_key`; `SIGNAL_EVENT_COLUMNS` in `scripts/import_trades.py`).

---

### `data/processed/swarm_events_stage.csv` (Stage-Zwischen-Output, P6)

**Quelle**: `scripts/import_trades.py::_apply_swarm_signals` (Stage `swarm_signals`)
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf der Stage komplett neu geschrieben.

**Zweck**: Snapshot aller Schwarm-Signal-Auslösungen (`swarm_buy`,
`swarm_sell`, `swarm_conflict`, `swarm_paradox`). Gruppiert auf
**Issuer-Group-Ebene** (`issuer_group_key` aus P5.6 — CIK-basiert,
Cross-Class). Eine `signal_event_id` pro Cluster bzw. Paradox-Auslösung,
alle beteiligten Trades als Event-Zeilen. P4 promoted die Datei zusammen
mit `branch1_events_stage.csv` und `sequence_events_stage.csv` nach
`suspicious_events.csv`.

**Spalten:** identisch zu den anderen Event-Stage-CSVs (21 Spalten,
inkl. `issuer_group_key`). Vollständige Spalten-Doku siehe
`organisation/tool_specific_md/signal_info.md`.

**Smoke-Run-Beispiel (Mai 2026, 111.986 Trades):**
- 4.950 Issuer-Group-Buckets, davon 3.779 mit ≥ 2 Trades evaluiert
- 24.926 Event-Zeilen: `swarm_buy` 10.267 / `swarm_sell` 10.105 /
  `swarm_paradox` 3.214 / `swarm_conflict` 1.340
- Cross-Class-Beispiel: GOOGL und GOOG teilen sich `signal_event_id`s
  im Alphabet-Bucket (`CIK:1652044`).

---

### `data/processed/suspicious_events.csv` (Master, append-only, P4)

**Quelle**: `scripts/import_trades.py::_stage_suspicious_export` (Stage `suspicious_export`)
**Format**: CSV (UTF-8)
**Update**: Append-only. Dedup-Schlüssel `(signal_event_id, trade_id)` — Re-Runs sind idempotent.

**Zweck**: Master-Liste aller Signal-Auslösungen über die gesamte Historie. Aus dieser Datei werden die beiden Views `suspicious_assets.csv` und `suspicious_members.csv` bei jedem Pipeline-Lauf komplett neu materialisiert.

**Spalten** (21 ab P6): identisch zu `branch1_events_stage.csv` / `sequence_events_stage.csv` / `swarm_events_stage.csv`, inkl. `issuer_group_key`. Vollständige Spalten-Doku: `organisation/tool_specific_md/signal_info.md`.

---

### `data/processed/suspicious_assets.csv` (View, regeneriert, Y-Tage-Verfall, P4 + Modul 8.4)

**Quelle**: `scripts/import_trades.py::_materialize_suspicious_assets`
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf komplett überschrieben. Filter: `last_event_date >= today - settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS` (Default 30 Tage).

**Zweck**: Asset-Aggregat aller aktuell verdächtigen Werte. Ein Asset, dessen jüngste Auslösung älter als 30 Tage ist, fällt aus dieser View, bleibt aber in `suspicious_events.csv` historisch erhalten.

**Spalten** (15): `issuer_ticker`, `asset_name_figi`, `event_count`, `member_count`, `signal_types`, `last_event_date`, `first_event_date`, `directions`, `chambers`, `last_signal_type`, `score_avg`, `score_max`, `score_total`, `total_volume_usd`, `warnings`.

Counter-Spalten (`signal_types`, `directions`, `chambers`): String-Format `"committee:5,accumulative:2"` (Count desc, Tie-Break alphabetisch).

Score-Aggregat-Spalten (Modul 8.4) joinen `trade_score`/`trade_volume_usd`/`trade_warnings` aus `trades_master_complete.csv` über distinct `trade_id`. Aggregations-Logik + Helper-Beschreibung: `organisation/Filter_Instanz_Parameter.md`. Schema + Beispiele: `organisation/tool_specific_md/signal_info.md`.

---

### `data/processed/suspicious_members.csv` (View, regeneriert, dauerhaft, P4 + Modul 8.4)

**Quelle**: `scripts/import_trades.py::_materialize_suspicious_members`
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf komplett überschrieben. Kein Verfallfilter.

**Zweck**: Member-Aggregat aller je in einem Signal aufgetauchten Politiker, mit Rückverweis-Liste auf alle auslösenden Trades.

**Spalten** (13): `member_id`, `member_name`, `chamber`, `event_count`, `asset_count`, `signal_types`, `last_event_date`, `first_event_date`, `is_ex_officio_ever`, `event_trade_pairs`, `score_avg`, `score_max`, `score_total`.

`event_trade_pairs` ist eine JSON-encodierte Liste `[["signal_event_id","trade_id"], ...]`.

Score-Aggregate (Modul 8.4) analog `suspicious_assets.csv`, aber NUR drei Score-Spalten — kein `total_volume_usd` und kein `warnings`. Aggregations-Logik: `organisation/Filter_Instanz_Parameter.md`. Schema-Details: `organisation/tool_specific_md/signal_info.md`.

---

### Problem-Outputs: Stage-vs-Live-Verhalten (Mai 2026)

Die vier Diagnose-Dateien `unmatched_names.csv`, `unmatched_preferred.csv`, `delisted_trades.csv`, `manual_review_tickers.csv` folgen einem einheitlichen Stage→Live-Muster:

| Lauf-Typ | Verhalten | Wirkung |
|----------|-----------|---------|
| **Full-Run** (`python scripts/import_trades.py`) | **Overwrite**: Live-Datei wird mit aktuellem Stand überschrieben. Bei leerem Ergebnis → Datei wird gelöscht. | Auto-Purge: gelöste Probleme verschwinden automatisch. |
| **Delta-Run** (`--delta`) | **Akkumuliert + Re-Validiert**: alte Live-Datei + neue Probleme werden gemerged via natürlichem Schlüssel. Anschließend werden Einträge entfernt, die jetzt gelöst sind (z.B. Name jetzt in `name_aliases.csv`, Ticker in `ignored_tickers.csv` oder im positiven Cache). | Akkumuliert offene Probleme über Runs hinweg. |
| **Dry-Run** (`--dry_run`) | Schreibt nur `*_stage.csv` (Vorschau aktueller Run). Live-Dateien bleiben unangetastet. `manual_review_tickers.csv` zeigt die projizierte Endform (existing-master ∪ delta). | Sichere Vorschau ohne destruktive Änderung. |

**Stage-Dateien** (Zwischen-Output während des Laufs):
- `unmatched_names_stage.csv`
- `unmatched_preferred_stage.csv` (NEU)
- `delisted_trades_stage.csv`

Sie werden nach erfolgreicher Promotion automatisch entfernt (`_promote_problem_outputs(delta)` in `scripts/import_trades.py`).

**Re-Validation-Quellen pro Datei**:
| Datei | Re-Validation gegen |
|-------|---------------------|
| `unmatched_names.csv` | `NameMatcher.match()` mit aktuellen `name_aliases.csv` + Member-CSVs |
| `unmatched_preferred.csv` | `Vorzugsaktien_mapping.csv` + `ignored_tickers.csv` |
| `delisted_trades.csv` | Positiver `ticker_industry_cache.json` (kein API-Call) |
| `manual_review_tickers.csv` | bestehende Filter (`ticker_type!='ignored'`, `manual_review_flag=True`, nicht in `ignored_tickers`) |

`count`-Spalte in `unmatched_names.csv` wird beim Delta-Merge **aufsummiert** (alt + neu), damit Sortierung nach Triage-Priorität bleibt.

---

### `data/processed/manual_review_tickers.csv` (Diagnose-Output, Rebuild Teil 3)
**Quelle**: Stage `save` und Bonus-Flag `--manual_review_only`
**Format**: CSV, sortiert nach `count_of_trades` absteigend
**Update**: Bei jedem Import neu geschrieben (kein Append)
**Daten-Basis**: Full-Run und Delta-Run aggregieren aus dem **vollen gemergten Master**. Im Delta-Dry-Run wird `existing-master ∪ delta_enriched` als Vorschau aggregiert.

**Spalten**:
- `raw_ticker` — wie aus dem Quiver-Feed
- `ticker_base` — normalisierte Form
- `ticker_type` — Classifier-Ergebnis
- `classification_reason` — welche Regel hat gefeuert
- `count_of_trades` — Anzahl Trades mit diesem Ticker
- `first_trade_date`, `last_trade_date`
- `example_member` — ein Politiker als Beispiel

**Verwendung**: Aufmerksame Pruefung von `manual_review=True`-Tickern, fuer die der Classifier keine eindeutige Regel hatte (z.B. NUMERIC_SUFFIX, LONG_ALPHA_SUSPECT). Regeneration ohne Master-Touch via `python scripts/import_trades.py --manual_review_only`.

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
**Update**: Full-Run = Overwrite. Delta-Run = Akkumuliert + Re-Validiert (siehe Übersicht oben).
**Auto-Purge**: Sobald ein Name in `data/mappings/name_aliases.csv` (oder Member-CSV) auftaucht, wird er beim nächsten Run automatisch aus der Datei entfernt.

**Spalten**:
```csv
trade_name, count, chamber, best_match, best_match_score
```
- `count` wird beim Delta-Merge aufsummiert (alt + neu).

**Beispiel**:
```csv
Marjorie Taylor Greene,286,house,Al Green,72
Nancy Pelosi,30,house,Nancy Mace,63
```

**Verwendung**:
- Review von nicht-gematchten Namen
- Kandidaten für `data/mappings/name_aliases.csv`

---

### `data/processed/unmatched_preferred.csv`
**Quelle**: Output von `scripts/import_trades.py` via `utils/ticker_sector.py:_write_unmatched_preferred()`
**Format**: CSV (1 Spalte: `preferred_ticker`)
**Update**: Full-Run = Overwrite. Delta-Run = Akkumuliert + Re-Validiert.
**Auto-Purge**: Sobald ein Ticker in `Vorzugsaktien_mapping.csv` oder `ignored_tickers.csv` auftaucht, wird er beim nächsten Run entfernt.

**Stage-Datei**: `data/processed/unmatched_preferred_stage.csv` (Zwischenpuffer, wird nach Promotion gelöscht).

**Beispiel**:
```csv
preferred_ticker
EFC$D
BAC$I
```

**Verwendung**:
- Review von Preferred-Tickern ohne Mapping in `Vorzugsaktien_mapping.csv`
- Kandidaten für neue Einträge in der Mapping-Datei oder `ignored_tickers.csv`

---

### `data/processed/delisted_trades.csv`
**Quelle**: Output von `scripts/import_trades.py` (Ticker-Sector Stage via `utils/ticker_sector.py`)
**Format**: CSV
**Update**: Full-Run = Overwrite. Delta-Run = Akkumuliert + Re-Validiert (Cache-only).
**Auto-Purge**: Sobald ein Ticker im positiven `data/mappings/ticker_industry_cache.json` auftaucht, wird er beim nächsten Run entfernt (kein API-Call).

**Stage-Datei**: `data/processed/delisted_trades_stage.csv` (Zwischenpuffer, wird nach Promotion gelöscht).

**Spalten**:
```csv
ticker, ticker_type, company_name, classification_reason, transaction_date
```

**Verwendung**:
- Übersicht aller Trades mit delisteten/unbekannten Tickern (kein Sector/Industry bei yfinance)
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

### `data/mappings/ignored_tickers.csv`
**Quelle**: Manuell gepflegt
**Format**: CSV
**Zweck**: Allowlist-Gegenstueck — Trades, deren `raw_ticker` in dieser Liste
steht, werden in der Pipeline mit `ticker_type='ignored'`, `api_strategy='skip'`
und `manual_review_flag=False` markiert. Dadurch entfallen API-Lookups
(OpenFIGI/yfinance) und der Eintrag taucht nicht mehr in
`manual_review_tickers.csv` auf.

**Spalten**:
```csv
raw_ticker, category, reason
```

- `raw_ticker` (str, **exakter Match** auf Spalte `ticker` im Trade): z.B. `"GLAS FUNDS, LP"`
- `category` (str): freie Kategorisierung, z.B. `private_fund`, `non_public`, `obvious_typo`
- `reason` (str): Begruendung fuer den Eintrag (Audit-Trail)

**Beispiel**:
```csv
raw_ticker,category,reason
"GLAS FUNDS, LP",private_fund,Private investment platform - kein oeffentlicher Ticker
```

**Verwendung**: `scripts/import_trades.py` → `_load_ignored_tickers()` /
`_apply_ignore_list()`. Wird nach jeder Klassifikation angewendet (Stage
`tickerclassify` sowie in `run()` und `run_delta()`). Pipeline schreibt
diese Datei NIE — nur lesen.

**Pflege**: Eintraege aus `data/processed/manual_review_tickers.csv` koennen
hierher verschoben werden, wenn klar ist, dass das Asset keinen oeffentlichen
Ticker hat oder anderweitig ausgeschlossen werden soll.

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
   [Member_Party, Member_State, Member_ExOfficio,
    Member_Chamber, Member_Committees_Full]
   (MatchScore/MatchSource kommen in Stage 4)

3. TICKER-TYPE + TICKER-INDUSTRY
   + OpenFIGI API (Stage tickeropenfigi, Suffix _figi)
   + yfinance (Stage tickeryfinance, Suffix _yf)
   ↓
   [ticker_type, figi, ticker_figi, asset_name_figi, security_type_figi,
    market_sector_figi, sector_yf, industry_yf, quote_type_yf]

4. GATES + TAGS (--stage_gates_tags / _apply_gates_and_tags)
   + subcommittee_sector_industry_mapping.csv (CommitteeTag)
   + bill_actions.json + bill_subjects.json (VoteTimingTag)
   ↓
   _apply_match_scores → [MatchScore, MatchSource]
   _apply_committee_relevance → [Insider_Sectors, Relevant_Sectors]
   AssetClassGate → [asset_class_excluded]
   VoteTimingTag → [Vote_Timing_Hit, Vote_Timing_Hits, Vote_Timing_Hit_Count]

5. OUTPUT
   ↓
   trades_master.csv (Backup in archive/trades_master_backup/ vor jedem destruktiven Schreiben)
   trades_stage.csv (Zwischenpuffer bei --dry_run / --delta --stage_*)
   unmatched_names.csv (nicht gematchte Namen)
```

---

## Committee-Relevanz (Modul 4)

`_apply_committee_relevance` schreibt `Insider_Sectors` und `Relevant_Sectors` in den Master.
`Relevance_Score` wurde entfernt (Refactor 2026-05-21).

**Score-Beitrag** läuft über `SCORING_WEIGHTS` in `config/settings.py`:
- `committee = 10` — für jeden Trade mit `len(Relevant_Sectors) > 0`
- `committee_full = 5` — zusätzlich, wenn mind. ein Eintrag `match_type='full'`
- Voll-Treffer ergibt 10 + 5 = 15 (entspricht dem Wert vor dem Refactor)

`COMMITTEE_EX_OFFICIO_ROLES` (Default: Chairman, Ranking Member, Vice Chairman, Cochairman, EXOfficio) bleibt konfigurierbar.
`RELEVANT_SECTORS_MATCH_MODE` steuert welche Einträge in `Relevant_Sectors` landen (Default `'sector_and_industry'`).

### Relevant_Sectors (Intersection) vs. Insider_Sectors

- **`Insider_Sectors`** = JSON-Liste aller (sub)committee-Sektoren des Members
  (frueher: `Relevant_Sectors`). Inhalt haengt nur am Member, nicht am Ticker.
- **`Relevant_Sectors`** (neu) = Teilmenge von `Insider_Sectors`, deren Eintraege
  zum gehandelten Ticker passen (Match gegen `sector_yf` / `industry_yf`).
  Match-Modus konfigurierbar in `config/settings.py`:
  - `RELEVANT_SECTORS_MATCH_MODE = 'sector_or_industry'` (Default): Eintrag matcht,
    wenn Sektor ODER Industry passt.
  - `'sector_and_industry'`: nur Eintraege mit Sektor UND Industry-Match.
  - `'hybrid'`: bevorzuge Sektor+Industry-Matches, sonst Fallback auf
    sector_or_industry.

`Confidence_Level` wurde entfernt — die Info liegt pro Eintrag in `Insider_Sectors`
unter dem `confidence`-Key.

**Beispiele**:
- Member im Energy-Committee, Trade in NEE (Utilities/Utilities-Regulated Electric): kein Sektor-Match → **0**
- Rick Larsen (Transportation) + WAB (Industrials/Railroads): Sektor matcht über Subcommittee → **33**
- Jennifer McClellan (Energy & Commerce) + VZ (Communication Services/Telecom Services): Sektor + Industry matchen → **66**
- Chairman des Energy-Committees + ENPH (Technology/Solar): Sektor + Industry matchen, ex officio → **(33+33) × 1.5 = 99**

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
- `Insider_Sectors`, `Relevant_Sectors` und `Vote_Timing_Hits` sind JSON-Strings in CSV
- **Beim Lesen**: `json.loads(row['Insider_Sectors'])` / `json.loads(row['Vote_Timing_Hits'])`
- **Beim Schreiben**: `json.dumps(list_or_dict)`
- `Vote_Timing_Hits` enthaelt **alle** Treffer im Fenster (frueher nur der naechstgelegene
  Vote). Anzahl liegt in `Vote_Timing_Hit_Count`.

---

## 8. Dokumentation

### `organisation/Filter_Instanz_Parameter.md`
**Quelle**: Zusammenstellung aus config/settings.py
**Format**: Markdown
**Erstellt**: 2026-02-20 (umbenannt 2026-05-17)

**Zweck**: Dokumentation der konkreten Filter-Instanzen (Gates / Tags / Signals) mit Parametern, Schwellen und Implementierungsstatus

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

### `tags/vote_timing_tag.py`
**Klasse**: `VoteTimingTag` (User-Sicht „Filter 4")

**Methoden**:
- `load_votes()` → laedt congress_votes.csv
- `apply(trades_df, votes_df)` → reichert DataFrame mit Vote_Timing_* Spalten an

**Matching-Logik**:
1. Trade-Sektoren aus `Relevant_Sectors` (Intersection Insider × Ticker aus Modul 4) extrahieren
2. Votes mit uebereinstimmendem `mapped_sectors` finden
3. Zeitfenster: `trade_date ZWISCHEN (vote_date - AFTER) UND (vote_date + BEFORE)`
4. **Alle** Treffer im Fenster werden in `Vote_Timing_Hits` (JSON-Liste) gesammelt,
   `Vote_Timing_Hit_Count` enthaelt die Anzahl. Pro Treffer:
   `{date, bill_title, policy_area, days_to_vote, konfidenz}`.

**Parameter** (aus settings.py):
- `VOTE_TIMING_WINDOW_BEFORE = 30` — max Tage VOR der Abstimmung
- `VOTE_TIMING_WINDOW_AFTER = 5` — max Tage NACH der Abstimmung (Toleranz)
- `VOTE_DATA_TTL_HOURS = 24` — Auto-Refresh-Schwelle fuer bill_actions/bill_subjects

Hinweis: `Vote_Timing_Score`/`Vote_Date`/`Vote_Bill_Title`/`Vote_Policy_Area`/`Days_To_Vote`
existieren nicht mehr — alle Treffer liegen jetzt in `Vote_Timing_Hits`.

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
- Erweiterung von `CommitteeTag.filter_by_ticker_sector()`

**Ziel**: Filter `Relevant_Sectors` auf tatsaechlichen Ticker-Sector → Boost bei Exact Match

---

Erstellt: 2026-02-20
Letzte Aktualisierung: 2026-05-14 (Spalten-Refactoring: API-Provider-Suffixe)

### Aenderungs-Historie

- **2026-05-14** — Provider-Suffixe an externe-API-Spalten:
  - OpenFIGI-Spalten bekommen Suffix `_figi`:
    `canonical_ticker → ticker_figi`,
    `canonical_name → asset_name_figi`,
    `security_type → security_type_figi`,
    `market_sector → market_sector_figi`.
    Die ID-Spalte `figi` selbst bleibt unveraendert.
  - yfinance-Spalten bekommen Suffix `_yf`:
    `Ticker_Sector → sector_yf`,
    `Ticker_Industry → industry_yf`,
    `quote_type → quote_type_yf`.
  - Konsistent auf drei Ebenen: CSV-Output, Python-Dataclasses
    (`NormalizedTicker`, `SectorInfo`), SQLite-Tabelle `ticker_normalizer`
    (DROP+CREATE-Migration; Backup liegt in `data/cache/`).
  - Curated-Schema (`settings.CURATED_COLUMNS` + `CURATED_COALESCE`)
    referenziert die neuen Namen.
  - Geaendert: `utils/ticker_normalizer.py`, `utils/ticker_sector.py`,
    `scripts/import_trades.py`, `config/settings.py`,
    `tests/test_ticker_*.py` (98 Tests gruen).
- **2026-03-13** — House PDF Extractor v2 hinzugefuegt.
