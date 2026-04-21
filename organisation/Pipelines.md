# Congress Tracker - Pipelines

Kompakte Uebersicht aller Daten-Pipelines, ihrer Ablaeufe und Abhaengigkeiten.

---

## Ausfuehrungsreihenfolge

```bash
cd congress_tracker && .\venv\Scripts\activate

# 1. Scraper (erzeugen Rohdaten)
python scrapers/house_scraper.py           # → house_trades.csv
python scrapers/senate_scraper.py          # → senate_trades.csv

# 2. Optional: OCR fuer gescannte PDFs
python scrapers/house_pdf_extractor_v2.py  # → ergaenzt house_trades.csv (empfohlen)
python scrapers/senate_pdf_extractor.py    # → ergaenzt senate_trades.csv (irrelevant)

# 3. Import-Pipeline (Anreicherung + Dedup)
python scripts/import_trades.py            # → trades_master.csv + trades_new.csv

# 4. Optional: Referenz-CSVs aktualisieren (nur bei xlsx-Aenderungen)
python scripts/convert_xlsx_to_csv.py      # → mapping-CSVs neu generieren
```

---

## Pipeline 1: House Scraper

**Script:** `scrapers/house_scraper.py`
**Quelle:** disclosures-clerk.house.gov (ZIP-Archiv + PDFs)
**Output:** `data/raw/house_trades.csv`, `data/raw/house_scraper_fehler.csv`

**Ablauf:**
1. ZIP-Download pro Jahr (z.B. `2025FD.zip`)
2. XML-Parsing → filtert PTR-Filings (FilingType="P", Prefix="Hon.")
3. Delta-Check: DocIDs aus bestehender CSV ueberspringen
4. PDF-Download pro Filing → pdfplumber-Textextraktion (Null-Bytes bereinigt)
5. Regex-basiertes Parsing: Owner, Ticker, Asset, Transaction, Amount, Dates
6. Subholding-Extraktion aus "S O:"-Zeilen
7. Fehler-PDFs (kein Text = Scan) → `house_scraper_fehler.csv`

**CLI:**
```
--year 2025          Einzelnes Jahr
--no-delta           Alle Filings neu verarbeiten
--doc-id ID --year Y Einzelne PDF testen
--dry-run            Nicht speichern
--list-only          Nur Filing-Liste anzeigen
```

**Spalten:** `name, ticker, asset_name, asset_type, transaction_type, amount, transaction_date, owner, subholding, comment, disclosure_date, ptr_link, csv_upload_time`

---

## Pipeline 2: Senate Scraper

**Script:** `scrapers/senate_scraper.py`
**Quelle:** efdsearch.senate.gov (Playwright + DataTables API)
**Output:** `data/raw/senate_trades.csv`, `data/raw/senate_pdf_skipped.csv`

**Ablauf:**
1. Playwright oeffnet Senate-Suchseite, akzeptiert Agreement
2. POST an DataTables API (`/search/report/data/`) mit CSRF-Token
3. JSON-Response → Filtert PTR-Links (elektronisch vs. Papier)
4. Paper-PTRs → `senate_pdf_skipped.csv` (fuer PDF-Extractor)
5. Pro elektronischem PTR: Neue Seite oeffnen, Transaktions-Tabelle parsen
6. Bond-Metadaten (Rate/Coupon/Maturity) aus `div.text-muted` → comment-Spalte
7. Delta-Check: Letztes disclosure_date aus CSV minus 1 Tag Sicherheitspuffer

**CLI:**
```
--days 7             Letzte N Tage (default: 7)
--from-date 01/03/25 Explizites Startdatum
--runs 3             Mehrere Durchlaeufe
--dry-run            Nicht speichern
```

**Spalten:** `name, ticker, asset_name, asset_type, transaction_type, amount, transaction_date, owner, comment, disclosure_date, ptr_link, csv_upload_time`

---

## Pipeline 3: House PDF Extractor (OCR)

**Script:** `scrapers/house_pdf_extractor.py`
**Input:** `data/raw/house_scraper_fehler.csv` (reason="empty_text")
**Output:** Ergaenzt `data/raw/house_trades.csv`

**Ablauf:**
1. Liest Fehler-CSV, filtert `empty_text`-Eintraege
2. PDF-Download → Seiten als Bilder (pdfplumber)
3. Rotationserkennung (Portrait = 90° gedreht)
4. Anthropic Vision API (Claude) → strukturiertes JSON pro Seite
5. Parsing: Owner, Ticker, Asset, Transaction Type, Amount (Code A-K), Dates
6. Dedup gegen bestehende house_trades.csv → nur neue Trades anhaengen

**Unterstuetzte Formulartypen:**
- Typ A: Rotierte handschriftliche Formulare (1 Trade/Seite)
- Typ B: Querformat getippte Tabellen (mehrere Trades/Seite)
- Typ C: Hybrid rotierte getippte Formulare

**Delta-Check:** Bereits verarbeitete DocIDs werden in `logs/house_pdf_extractor_done.csv` getrackt und beim naechsten Lauf uebersprungen. Gilt auch fuer PDFs mit 0 Trades.

**CLI:**
```
--doc-id ID --year Y Einzelne PDF (kein Delta-Check)
--dry-run            Nicht speichern
--no-delta           Alle PDFs verarbeiten (Done-CSV ignorieren)
--model claude-sonnet-4-6  Anderes Modell
```

---

## Pipeline 3b: House PDF Extractor v2 (Grid-basiert)

**Script:** `scrapers/house_pdf_extractor_v2.py`
**Input:** `data/raw/house_scraper_fehler.csv` (reason="empty_text")
**Output:** Ergaenzt `data/raw/house_trades.csv`
**Status:** Empfohlen (loest Merge-, Truncation- und Amount-NaN-Probleme von v1)

**Ablauf:**
1. Liest Fehler-CSV, filtert `empty_text`-Eintraege
2. PDF-Download → Seiten als Bilder (pdfplumber, 300 DPI)
3. Rotationserkennung (Portrait = 90° gedreht)
4. **OpenCV Grid-Detection** auf Raw-Graustufenbild (morphologische Linienerkennung)
5. **Pixel-Density Amount-Detection**: Zaehlt dunkle Pixel in A-K Checkbox-Zellen
6. **Vision API pro 5-Zeilen-Batch** fuer Text-Felder (Owner, Asset, Tx Type, Dates) + `readable`-Flag pro Zeile
7. Fallback: Wenn Grid nicht erkannt → ganzes Seitenbild an Vision API (wie v1), Terminal-Ausgabe
8. **Quality Gate**: Zeilen mit `readable: false` → `logs/ocr_uncertain.csv` (nicht in house_trades.csv)
9. Dedup gegen bestehende house_trades.csv → nur neue Trades anhaengen

**Unterstützte Formulartypen:**
- Typ A: Rotierte handschriftliche Formulare (1 Trade/Seite) — Bilirakis, Wagner
- Typ B: Querformat getippte Tabellen (~25 Trades/Seite, manchmal 90° gedreht) — McCaul
- Typ C: Hybrid rotierte getippte Formulare (~7 Trades/Seite) — Fleischmann
- Typ D: Broker Statements, rotiert (~15-20 Trades/Seite) — Khanna

**Delta-Check:** Wie v1 — `logs/house_pdf_extractor_done.csv`

**CLI:**
```
--doc-id ID --year Y Einzelne PDF (kein Delta-Check)
--dry-run            Nicht speichern
--no-delta           Alle PDFs verarbeiten (Done-CSV ignorieren)
--model claude-sonnet-4-6  Anderes Modell
```

**Konfiguration** (config/settings.py):
```
PDF_EXTRACTOR_DPI = 300
PDF_EXTRACTOR_AMOUNT_PIXEL_THRESHOLD = 50
PDF_EXTRACTOR_USE_GRID_DETECTION = True
PDF_EXTRACTOR_ROW_BATCH_SIZE = 5
```

---

## Pipeline 4: Senate PDF Extractor (OCR)

**Script:** `scrapers/senate_pdf_extractor.py`
**Input:** `data/raw/senate_pdf_skipped.csv`
**Output:** Ergaenzt `data/raw/senate_trades.csv`
**Status:** Funktional, aber irrelevant (einziger Senator mit Paper-PTRs handelt nur Fonds)

**Ablauf:**
1. Liest Skipped-CSV (Paper-PTR-Links)
2. Playwright oeffnet Print-URL mit Session-Cookies
3. GIF-Bilder aus DOM extrahieren (efd-media-public.senate.gov)
4. Vision API → JSON-Extraktion (2s Pause zwischen Seiten)
5. Anhaengen an senate_trades.csv

**Delta-Check:** Bereits verarbeitete ptr_links werden in `data/raw/senate_pdf_extractor_done.csv` getrackt und beim naechsten Lauf uebersprungen. Gilt auch fuer PDFs mit 0 Trades.

**CLI:**
```
--url URL            Einzelne PTR-URL (kein Delta-Check)
--dry-run            Nicht speichern
--no-delta           Alle PDFs verarbeiten (Done-CSV ignorieren)
--model claude-sonnet-4-6  Anderes Modell
```

---

## Pipeline 5: Import-Pipeline (Hauptpipeline)

**Script:** `scripts/import_trades.py`
**Input:** `data/raw/congress-trading-all.csv` (Quiver-Snapshot, vollständiger Datensatz)
**Output:** `data/processed/trades_master.csv`, `data/processed/unmatched_names.csv`, `data/processed/unmatched_preferred.csv` (bei Bedarf)

**Modi:**
- `python scripts/import_trades.py` — Full Rebuild (trades_master.csv komplett neu schreiben, mit Backup)
- `python scripts/import_trades.py --delta` — Delta-Import (nur neue Trades anreichern + merge in Master)
- `python scripts/import_trades.py --dry_run` — Alle Stages → trades_stage.csv (Master unberührt)

**Stage-Pipeline (einzelne Schritte, unabhängig re-runnable):**
```
--stage_rename       # Quiver laden, Spalten umbenennen → Master/Stage
--stage_date         # Datum normalisieren
--stage_committees   # Committee-Member-Enrichment (nur Member-Spalten, kein Scoring)
--stage_tickertype   # ticker_type via OpenFIGI ableiten
--stage_tickerindustry  # yfinance Sector/Industry Lookup
--stage_filter       # Scoring + Filter 0 (Asset-Klasse) + Filter 4 (Vote-Timing)
--stage_save         # trades_stage.csv → trades_master.csv (replace/delta-merge)
```

**Flag-Matrix: Was wird gelesen/geschrieben?**
| Aufruf | Input | Output | Master-Backup |
|--------|-------|--------|---------------|
| `--stage_X` | trades_master.csv | trades_master.csv | ✅ |
| `--stage_X --dry_run` | trades_master.csv | trades_stage.csv | ❌ |
| `--stage_X --delta` | trades_stage.csv | trades_stage.csv | ❌ |
| `--stage_save` | trades_stage.csv | trades_master.csv (replace) | ✅ |
| `--delta --stage_save` | trades_stage.csv | trades_master.csv (merge) | ✅ |
| `--dry_run` (kein Stage) | Quiver | trades_stage.csv | ❌ |
| `--delta` (kein Stage) | Quiver+Master | trades_master.csv (merge) | ✅ |

**Ablauf Full Rebuild:**
```
[1] Lade Quiver-Trades + Datum normalisieren
    → Quiver-CSV einlesen (Spalten-Rename via QUIVER_COLUMN_MAP)
    → Datum-Fallback: fehlende/future transaction_date ← disclosure_date
         ↓
[2] Committee-Member-Enrichment (enrich_member_data)
    → Checkpoint-Support: alle 5.000 Trades als Pickle (Abbruch-sicher)
    → 3-Stufen Matching: Alias → Exakt → Fuzzy (85%)
    → Neue Spalten: Member_Party, Member_State, Member_Committees,
      Member_ExOfficio, Member_Chamber, Member_Committees_Full
    (KEIN Scoring in dieser Stage → kommt in [4])
         ↓
[3] Ticker-Type Klassifikation (OpenFIGI API + Regex)
    → ticker_type Spalte setzen
         ↓
[4] Ticker-Industry Lookup (yfinance)
    → Ticker_Sector, Ticker_Industry Spalten setzen
         ↓
[5] Filter-Logik (_apply_filter_logic)
    → MatchScore, MatchSource (via NameMatcher erneut, schnell)
    → Relevant_Sectors, Relevance_Score, Confidence_Level (via CommitteeFilter)
    → asset_class_excluded (Filter 0: Asset-Klasse)
    → Vote_Timing_Hit, Vote_Date, Vote_Bill_Title, Vote_Policy_Area,
      Vote_Timing_Score, Days_To_Vote (Filter 4: Vote-Timing)
    → TODO: Filter 1/2/6/7 (Volumen, Schwarm, Long/Short, Position)
         ↓
[6] Speichern
    → Backup: archive/trades_master_backup/trades_master_{YYYYMMDD-HHMMSS}.csv
    → trades_master.csv (komplett neu geschrieben)
    → unmatched_names.csv
```

**Hinweis:** Die Pipeline ist ein reiner Enrichment-Durchlauf — es werden keine Zeilen entfernt.

**Delta-Modus** (`--delta`): Lädt aktuelle Quiver-CSV, vergleicht per Key-Dedup gegen bestehende
trades_master.csv, reichert nur neue Trades an und mergt sie (mit Dedup) in Master.

**Einzelstage re-runnen (Beispiele):**
```bash
# Filter-Stage nach Mapping-Änderung neu berechnen (mit Backup):
python scripts/import_trades.py --stage_filter

# Dry-Run: komplette Pipeline ohne Master-Touch:
python scripts/import_trades.py --dry_run

# Dry-Run committen:
python scripts/import_trades.py --stage_save

# Delta stage-weise:
python scripts/import_trades.py --delta --stage_rename    # neue Trades → stage.csv
python scripts/import_trades.py --delta --stage_filter    # filter auf stage.csv
python scripts/import_trades.py --delta --stage_save      # merge in Master
```

**Abhaengigkeiten:**
- `utils/name_matcher.py` — 3-Tier Matching (Alias/Exakt/Fuzzy)
- `filters/committee_filter.py` — Committee-Relevanz-Scoring
- `filters/asset_class_filter.py` — Filter 0: Nicht-Aktien flaggen (asset_class_excluded)
- `filters/vote_timing_filter.py` — Filter 4: Vote-Timing (respektiert asset_class_excluded)
- `config/committee_mapping.py` — O(1) Subcommittee→Sektor Lookup
- `config/settings.py` — Alle Schwellenwerte und Pfade
- `data/mappings/Vorzugsaktien_mapping.csv` — Preferred→Stammaktien-Ticker (16 Mappings)
- `data/mappings/ticker_industry_cache.json` — yfinance-Cache (Ticker→Sector/Industry)
- `yfinance` (Python-Paket) — Sector/Industry Lookup via Yahoo Finance API

**NameMatcher-Details:**
| Stufe | Methode | Datenquelle |
|-------|---------|-------------|
| 1. Alias | Exakter Lookup | `data/mappings/name_aliases.csv` |
| 2. Exakt | Case-insensitive Key | House/Senate Member-CSVs + Former Members |
| 3. Fuzzy | rapidfuzz WRatio ≥85% | Alle Member-Namen (inkl. Former) |

Senate-Besonderheit: Zwei Lookup-Keys pro Senator ("Last, First" + "First Last")

---

## Pipeline 6: XLSX→CSV Konvertierung

**Script:** `scripts/convert_xlsx_to_csv.py`
**Zweck:** Referenz-Excel-Dateien in CSV konvertieren (schneller ladbar)

**Konvertierungen:**
| Input (xlsx) | Sheet | Output (csv) |
|---|---|---|
| `data/mappings/yf_secotors_with_industries.xlsx` | — | `data/mappings/yf_sectors_with_industries.csv` |
| `data/mappings/subcommittee_sector_industry_mapping_v3.xlsx` | Industry_Mapping | `data/mappings/subcommittee_sector_industry_mapping.csv` |
| `data/mappings/subcommittee_sector_industry_mapping_v3.xlsx` | Region_Mapping | `data/mappings/subcommittee_region_mapping.csv` |

Nur ausfuehren wenn xlsx-Dateien geaendert wurden.

---

## Pipeline 7: Diagnose & Analyse (optional)

| Script | Zweck | CLI |
|--------|-------|-----|
| `scripts/diagnose_scraper.py` | Senate-Scraper QA (Timing, Konsistenz, Rowcount) | `--test [timing\|consistency\|rowcount\|all] --runs N --days N` |
| `scripts/analyze_fehler_pdfs.py` | House-Fehler-PDFs analysieren (Textlayer, Rotation, Bilder) | — |
| `scripts/inspect_excel.py` | Excel-Spaltenstruktur inspizieren | — |

---

## Abhaengigkeitsgraph

```
                    ┌─────────────┐
                    │  Externe    │
                    │  Quellen    │
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           ↓               ↓               ↓
    ┌──────────────┐ ┌────────────┐ ┌──────────────────┐
    │House Scraper │ │Senate      │ │convert_xlsx_to_csv│
    │(Pipeline 1)  │ │Scraper     │ │(Pipeline 6)       │
    └──────┬───────┘ │(Pipeline 2)│ └────────┬─────────┘
           │         └─────┬──────┘          │
           │               │           Referenz-CSVs
     fehler.csv      skipped.csv     (Mapping, Sektoren)
           │               │                │
           ↓               ↓                │
    ┌──────────────┐ ┌────────────┐         │
    │House PDF     │ │Senate PDF  │         │
    │Extractor     │ │Extractor   │         │
    │(Pipeline 3)  │ │(Pipeline 4)│         │
    └──────┬───────┘ └─────┬──────┘         │
           │               │                │
           ↓               ↓                │
    ┌──────────────────────────────┐        │
    │  house_trades.csv            │        │
    │  senate_trades.csv           │        │
    └──────────────┬───────────────┘        │
                   │                        │
                   ↓                        ↓
         ┌─────────────────────────────────────┐
         │        Import-Pipeline              │
         │        (Pipeline 5)                 │
         │                                     │
         │  NameMatcher ← Member-CSVs + Aliases│
         │  CommitteeFilter ← Mapping-CSV      │
         └──────────────┬──────────────────────┘
                        │
                        ↓
              ┌─────────────────────┐
              │ trades_master.csv   │
              │ trades_new.csv      │
              │ unmatched_names.csv │
              └─────────────────────┘
                        │
                        ↓
              [Modul 7-11: TODO]
              Filter → Scoring → Dashboard → Email
```

---

## Statische Referenzdaten

Diese Dateien werden nicht durch Pipelines erzeugt, sondern manuell gepflegt:

| Datei | Aktualisierung | Zweck |
|-------|----------------|-------|
| `data/mappings/house_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen) | 437 House-Mitglieder + Committees (inkl. 5 Leadership ex officio) |
| `data/mappings/senate_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen) | 100 Senatoren + Committees |
| `data/mappings/name_aliases.csv` | Bei Bedarf (neue Varianten) | Manuelle Namenskorrekturen (11 Eintraege) |
| `data/mappings/subcommittee_sector_industry_mapping.csv` | Bei Bedarf | Subcommittee→Sektor/Industry (861 Zeilen) |
| `data/mappings/vote_sector_mapping.py` | Bei Bedarf | Policy Area→YF-Sektoren (Python, 12 Mappings) |
| `data/raw/former_members.csv` | Bei Bedarf (Congresswechsel) | Ehemalige Politiker mit Trades (6 Eintraege) |
| `config/settings.py` | Bei Bedarf | Alle Schwellenwerte, Pfade, Gewichtungen |

---

Erstellt: 2026-03-07
