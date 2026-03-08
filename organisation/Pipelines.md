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
python scrapers/house_pdf_extractor.py     # → ergaenzt house_trades.csv
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

**CLI:**
```
--doc-id ID --year Y Einzelne PDF
--dry-run            Nicht speichern
--model claude-sonnet-4-6  Anderes Modell
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
4. Vision API → JSON-Extraktion
5. Anhaengen an senate_trades.csv

---

## Pipeline 5: Import-Pipeline (Hauptpipeline)

**Script:** `scripts/import_trades.py`
**Input:** `house_trades.csv` + `senate_trades.csv`
**Output:** `trades_master.csv`, `trades_new.csv`, `unmatched_names.csv`

**Ablauf:**
```
[1] Lade Scraped Trades
    → house_trades.csv + senate_trades.csv einlesen
    → chamber-Spalte hinzufuegen ("House"/"Senate")
    → Datum-Spalten konvertieren
         ↓
[2] Lade bestehende Master-Daten
    → trades_master.csv (kumulativ)
    → Leerer DataFrame bei Erstimport
         ↓
[3] Duplikat-Erkennung
    → Composite Key aus 11 Spalten (settings.DUPLICATE_CHECK_COLUMNS)
    → Vergleich gegen Master → nur neue Trades weiter
         ↓
[4] Anreicherung (NameMatcher + CommitteeFilter)
    → 3-Stufen Matching: Alias → Exakt → Fuzzy (85%)
    → Committee-Relevanz berechnen (Rank × Confidence × Role)
    → Neue Spalten: Member_Party/State/Committees/ExOfficio/Chamber,
      MatchScore/Source, Relevant_Sectors, Relevance_Score, Confidence_Level
         ↓
[5] Speichern
    → trades_new.csv (aktuelle Batch)
    → trades_master.csv (Master + neue Trades)
    → unmatched_names.csv (fehlgeschlagene Matches mit Best-Guess)
```

**Full Rebuild:** Die Pipeline verarbeitet nur neue Trades (Duplikat-Check). Um alle Trades neu anzureichern (z.B. nach Member-CSV-Aenderung), `trades_master.csv` loeschen und Pipeline erneut ausfuehren.

**Abhaengigkeiten:**
- `utils/name_matcher.py` — 3-Tier Matching (Alias/Exakt/Fuzzy)
- `filters/committee_filter.py` — Committee-Relevanz-Scoring
- `config/committee_mapping.py` — O(1) Subcommittee→Sektor Lookup
- `config/settings.py` — Alle Schwellenwerte und Pfade

**NameMatcher-Details:**
| Stufe | Methode | Datenquelle |
|-------|---------|-------------|
| 1. Alias | Exakter Lookup | `data/config/name_aliases.csv` |
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
| `yf_secotors_with_industries.xlsx` | — | `yf_sectors_with_industries.csv` |
| `subcommittee_sector_industry_mapping_v3.xlsx` | Industry_Mapping | `subcommittee_sector_industry_mapping.csv` |
| `subcommittee_sector_industry_mapping_v3.xlsx` | Region_Mapping | `subcommittee_region_mapping.csv` |

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
| `data/raw/house_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen) | 437 House-Mitglieder + Committees (inkl. 5 Leadership ex officio) |
| `data/raw/senate_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen) | 100 Senatoren + Committees |
| `data/raw/former_members.csv` | Bei Bedarf (Congresswechsel) | Ehemalige Politiker mit Trades (6 Eintraege) |
| `data/config/name_aliases.csv` | Bei Bedarf (neue Varianten) | Manuelle Namenskorrekturen (11 Eintraege) |
| `config/settings.py` | Bei Bedarf | Alle Schwellenwerte, Pfade, Gewichtungen |

---

Erstellt: 2026-03-07
