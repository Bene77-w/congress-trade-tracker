# CLI Bedienungsanleitung – Congress Trade Tracker

Alle Befehle werden aus dem Projektverzeichnis `congress_tracker/` ausgeführt, mit aktiviertem venv:

```bash
cd <Projektverzeichnis>        # z.B. /media/bene/.../congress_tracker  oder  C:\...\congress_tracker
source venv/bin/activate        # Linux/Mac
# oder: venv\Scripts\activate  # Windows
```

---

## 1. Senate Scraper

**Datei:** `scrapers/senate_scraper.py`
**Zweck:** Scrapt neue Senate-Trades von efdsearch.senate.gov via Playwright

```bash
# Standard: letzte 7 Tage (Delta-Modus, speichert)
python scrapers/senate_scraper.py

# Eigene Anzahl Tage zurück
python scrapers/senate_scraper.py --days 30

# Ab einem bestimmten Datum (vollständiger Lauf ab Kongress-Start)
python scrapers/senate_scraper.py --from-date 01/03/2025

# Mehrere Runs (für Konsistenztests)
python scrapers/senate_scraper.py --runs 3

# Dry-run: scrapen ohne speichern
python scrapers/senate_scraper.py --days 7 --dry-run
python scrapers/senate_scraper.py --from-date 01/15/2025 --dry-run
```

**Output:** `data/raw/senate_trades.csv`

### Testläufe Senate Scraper

**Stufe 1 – Kleines Zeitfenster, kein Speichern (schnell)**
```bash
# Letzter Tag, nur anzeigen
python scrapers/senate_scraper.py --days 1 --dry-run

# Letzten 3 Tage, nur anzeigen
python scrapers/senate_scraper.py --days 3 --dry-run
```

**Stufe 2 – Bestimmter Zeitraum, kein Speichern (reproduzierbar)**
```bash
# Festes Datum, nur anzeigen
python scrapers/senate_scraper.py --from-date 01/15/2025 --dry-run
```

**Stufe 3 – Vollständiger Lauf ohne Speichern**
```bash
# Ab Kongress-Start, alles parsen, nichts speichern
python scrapers/senate_scraper.py --from-date 01/03/2025 --dry-run
```

**Stufe 4 – Konsistenztest (mehrere Runs vergleichen)**
```bash
# 3 Runs hintereinander – prüft ob Ergebnisse stabil sind
python scrapers/senate_scraper.py --days 7 --runs 3

# Oder via Diagnose-Script (detailliertere Auswertung)
python scripts/diagnose_scraper.py --test consistency --runs 3 --days 7
```

**Unit-Tests:**
```bash
pytest tests/test_senate_scraper.py -v
```

---

## 2. Senate PDF Extractor

**Datei:** `scrapers/senate_pdf_extractor.py`
**Zweck:** OCR für Paper-PTRs via Claude Vision API (Fallback für PDFs die der Scraper überspringt)
**Voraussetzung:** `ANTHROPIC_API_KEY` als Umgebungsvariable gesetzt

```bash
# Standard: verarbeitet data/raw/senate_pdf_skipped.csv
python scrapers/senate_pdf_extractor.py

# Einzelne URL testen (Testlauf)
python scrapers/senate_pdf_extractor.py --url "https://efdsearch.senate.gov/search/view/paper/UUID/"

# Dry-run: extrahieren ohne speichern
python scrapers/senate_pdf_extractor.py --url "https://..." --dry-run

# Eigenes Modell
python scrapers/senate_pdf_extractor.py --model claude-sonnet-4-6

# Headless-Modus deaktivieren (Browser sichtbar, für Debugging)
python scrapers/senate_pdf_extractor.py --headless False
```

**Output:** `data/raw/senate_trades.csv` (append)

---

## 3. Import Pipeline

**Datei:** `scripts/import_trades.py`
**Zweck:** Liest house_trades.csv + senate_trades.csv, matcht Member-Namen (4-Tier: Alias → Exakt → Former → Fuzzy), reichert mit Committee-Daten an

```bash
# Standard: vollständige Pipeline (alle 7 Stages sequenziell)
python scripts/import_trades.py

# Delta-Modus: nur neue Trades seit letztem Lauf anreichern und anhängen
python scripts/import_trades.py --delta
```

**Output:** `data/processed/trades_master.csv`, `data/processed/trades_new.csv`, `data/processed/unmatched_names.csv`

### Einzelne Pipeline-Stages (Debug / Repair)

Nur **ein** `--stage_*` Flag gleichzeitig erlaubt. Kombination mit `--delta` möglich.

```bash
python scripts/import_trades.py --stage_rename        # Stage 1: CSV laden + Spalten umbenennen
python scripts/import_trades.py --stage_date          # Stage 2: Datum normalisieren
python scripts/import_trades.py --stage_committees    # Stage 3: Committee-Enrichment
python scripts/import_trades.py --stage_tickertype    # Stage 4: ticker_type ableiten
python scripts/import_trades.py --stage_tickerindustry  # Stage 5: yfinance Sector/Industry Lookup
python scripts/import_trades.py --stage_votetiming    # Stage 6: Vote-Timing Filter
python scripts/import_trades.py --stage_save          # Stage 7: In trades_master.csv speichern

# Kombination mit Delta:
python scripts/import_trades.py --delta --stage_rename
```

> **Full Rebuild:** Um alle Trades neu anzureichern (z.B. nach Änderung an Member-CSVs
> oder former_members.csv), `data/processed/trades_master.csv` löschen und Pipeline
> erneut ausführen. Die Pipeline erkennt den fehlenden Master und behandelt alle Trades als neu.

---

## 4. XLSX → CSV Konvertierung

**Datei:** `scripts/convert_xlsx_to_csv.py`
**Zweck:** Konvertiert die xlsx-Mapping-Dateien in CSVs (nach manuellen Änderungen an den xlsx-Dateien)
**Keine Argumente** – Script läuft einfach durch

```bash
python scripts/convert_xlsx_to_csv.py
```

**Konvertiert:**
- `data/raw/yf_secotors_with_industries.xlsx` → `data/raw/yf_sectors_with_industries.csv`
- `data/raw/subcommittee_sector_industry_mapping_v3.xlsx` (Sheet: Industry_Mapping) → `data/raw/subcommittee_sector_industry_mapping.csv`
- `data/raw/subcommittee_sector_industry_mapping_v3.xlsx` (Sheet: Region_Mapping) → `data/raw/subcommittee_region_mapping.csv`

---

## 5. Senate Scraper Diagnose

**Datei:** `scripts/diagnose_scraper.py`
**Zweck:** Diagnosetests für den Senate Scraper (Timing, Konsistenz, Zeilenanzahl)

```bash
# Alle Tests
python scripts/diagnose_scraper.py

# Einzelner Test
python scripts/diagnose_scraper.py --test timing
python scripts/diagnose_scraper.py --test consistency
python scripts/diagnose_scraper.py --test rowcount

# Mit Parametern
python scripts/diagnose_scraper.py --test timing --pages 5
python scripts/diagnose_scraper.py --test consistency --runs 3 --days 14
```

**Verfügbare Tests:**

| Test | Beschreibung |
|------|--------------|
| `timing` | Misst Ladezeit pro Seite |
| `consistency` | Mehrere Runs, vergleicht Ergebnisse |
| `rowcount` | Prüft Anzahl gefundener Trades |
| `all` | Alle Tests (default) |

---

## 6. House Committee PDF Parser

**Datei:** `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py`
**Zweck:** Extrahiert Member + Subcommittee-Zugehörigkeit aus dem offiziellen House Committee PDF
**Wann:** Nur bei Neuwahlen (alle 2 Jahre) oder nach Committee-Umstrukturierungen

```bash
# Standard-Aufruf
python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py --pdf "C:\Pfad\zur\house_info_all.pdf"

# Mit eigenem Output-Pfad
python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py \
  --pdf "C:\Pfad\zur\house_info_all.pdf" \
  --out "data/raw/house_member&(sub)committees.csv"
```

**Output:** CSV mit Spalten `Name, Party, State, Committees, ExOfficio`

> **Hinweis:** Das PDF `house_info_all.pdf` muss manuell von der offiziellen
> House-Website heruntergeladen werden. Ausgabe-CSV danach nach `data/raw/` kopieren.

---

## 7. Senate Committee PDF Parser

**Datei:** `scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py`
**Zweck:** Extrahiert Member + Subcommittee-Zugehörigkeit aus dem offiziellen Senate Committee PDF
**Wann:** Nur bei Neuwahlen (alle 2 Jahre) oder nach Committee-Umstrukturierungen

```bash
# Standard-Aufruf
python scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py \
  --pdf "C:\Pfad\zur\senate_committees.pdf"

# Test-Modus: nur erste 3 Seiten (schnelle Vorschau)
python scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py \
  --pdf "C:\Pfad\zur\senate_committees.pdf" --test

# Mit eigenem Output-Pfad
python scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py \
  --pdf "C:\Pfad\zur\senate_committees.pdf" \
  --out "data/raw/senate_member&(sub)committees.csv"
```

**Output:** CSV mit Spalten `Name, Party, State, Committees, ExOfficio`

> **Hinweis:** Das PDF muss manuell von congress.gov heruntergeladen werden.
> Ausgabe-CSV danach nach `data/raw/` kopieren und umbenennen in
> `senate_member&(sub)committees.csv`.

---

## 8. House Scraper

**Datei:** `scrapers/house_scraper.py`
**Zweck:** Scrapt House PTR-Trades via ZIP-Download + PDF-Parsing

```bash
# Standard: aktuelles + vorheriges Jahr, Delta-Modus
python scrapers/house_scraper.py

# Nur ein bestimmtes Jahr
python scrapers/house_scraper.py --year 2025

# Alles neu verarbeiten (kein Delta)
python scrapers/house_scraper.py --year 2025 --no-delta
```

**Output:** `data/raw/house_trades.csv`

### Testläufe House Scraper

**Stufe 1 – Filing-Liste abrufen (kein PDF-Download)**
Lädt nur die XML-Datei, zeigt welche PTR-Filings vorhanden sind. Schnell, kein Risiko.
```bash
python scrapers/house_scraper.py --list-only --year 2025
```

**Stufe 2 – Einzelne PDF testen**
DocID aus der Filing-Liste (Stufe 1) entnehmen und gezielt eine PDF parsen.
```bash
# Parsen + anzeigen, NICHT speichern
python scrapers/house_scraper.py --year 2025 --doc-id 20025832 --dry-run

# Parsen + speichern nach house_trades.csv
python scrapers/house_scraper.py --year 2025 --doc-id 20025832
```

**Stufe 3 – Vollständiger Lauf**
```bash
# Nur neue Filings des Jahres (Delta-Modus)
python scrapers/house_scraper.py --year 2025

# Dry-run: alles parsen, nichts speichern
python scrapers/house_scraper.py --year 2025 --dry-run

# Alles neu verarbeiten, nichts speichern
python scrapers/house_scraper.py --year 2025 --no-delta --dry-run
```

**Empfohlene Reihenfolge beim ersten Mal:**
1. `--list-only --year 2025` → DocID aus der Liste notieren
2. `--doc-id XXXXX --year 2025 --dry-run` → Parsing prüfen
3. `--year 2025 --dry-run` → Vollständiger Lauf ohne Speichern

**Unit-Tests:**
```bash
pytest tests/test_house_scraper.py -v
```

---

## 9. House PDF Extractor

**Datei:** `scrapers/house_pdf_extractor.py`
**Zweck:** OCR für Hand-Delivered House PTR PDFs via Claude Vision API (gescannte Papierformulare ohne Textlayer)
**Voraussetzung:** `ANTHROPIC_API_KEY` als Umgebungsvariable gesetzt

```bash
# Alle fehler-PDFs aus house_scraper_fehler.csv (reason="empty_text") verarbeiten:
python scrapers/house_pdf_extractor.py

# Einzelne DocID testen:
python scrapers/house_pdf_extractor.py --doc-id 8221228 --year 2025

# Trockenlauf (extrahieren ohne speichern):
python scrapers/house_pdf_extractor.py --doc-id 8221228 --year 2025 --dry-run

# Anderes Modell (Standard: claude-haiku-4-5):
python scrapers/house_pdf_extractor.py --model claude-sonnet-4-6

# Eigene Input-CSV (statt house_scraper_fehler.csv):
python scrapers/house_pdf_extractor.py --input-csv data/raw/meine_fehler.csv
```

**Output:** `data/raw/house_trades.csv` (Append + Dedup, identisches Schema)

**Unterstützte Formulartypen:**

| Typ | Beschreibung | Beispiel |
|-----|-------------|---------|
| A | Rotated Handwritten (90° gedreht, handschriftlich, 1 Trade/Seite) | Adrian Smith, Gus Bilirakis |
| B | Landscape Table (maschinengetippt, viele Trades/Seite) | Michael McCaul, Harold Rogers |
| C | Hybrid (90° gedreht + maschinengetippt, mehrere Trades/Seite) | Chuck Fleischmann |

**Testläufe (alle mit --dry-run):**
```bash
# Typ A – Rotated Handwritten:
python scrapers/house_pdf_extractor.py --doc-id 8220764 --year 2025 --dry-run

# Typ B – Landscape "Nothing to report":
python scrapers/house_pdf_extractor.py --doc-id 8221310 --year 2025 --dry-run

# Typ B – Landscape viele Trades (McCaul, 9 Seiten, ~200 Trades):
python scrapers/house_pdf_extractor.py --doc-id 8221228 --year 2025 --dry-run

# Typ C – Hybrid:
python scrapers/house_pdf_extractor.py --doc-id 9115670 --year 2025 --dry-run
```

---

## 10. Congress Data Fetcher

**Datei:** `scrapers/fetch_congress_data.py`
**Zweck:** Lädt alle Bill-Daten aus der Congress.gov API (6 Datentypen pro Bill)
**Voraussetzung:** `CONGRESS_API_KEY` in `.env` gesetzt (api.data.gov)

```bash
# Erstlauf: alle 6 Schritte (~7-8h, einmalig pro Kongress)
python scrapers/fetch_congress_data.py

# Delta-Update: nur neue/geänderte Bills seit letztem Lauf (~5-30 Min, wöchentlich)
python scrapers/fetch_congress_data.py --update

# Erstlauf wiederholen (ignoriert Checkpoint, startet von vorne)
python scrapers/fetch_congress_data.py --force

# Anderen Kongress fetchen
python scrapers/fetch_congress_data.py --congress 118
```

**Output** (`data/congress_api/`):
| Datei | Inhalt | Schritt |
|-------|--------|---------|
| `bills_list.json` | Alle Bills (IDs, Typen) | 1 (~30 Min) |
| `bill_details.json` | policyArea, title, sponsors | 2 (~1.5-2h) |
| `bill_actions.json` | Actions inkl. Floor Votes + Roll Calls | 3 (~1-2h) |
| `bill_committees.json` | Committee-Zuweisungen | 4 (~1h) |
| `bill_summaries.json` | CRS Summaries (Texte) | 5 (~1.5h) |
| `bill_subjects.json` | Legislative Subjects (Rider-Erkennung) | 6 (~2.5h) |
| `fetch_checkpoint.json` | Resume-State + Delta-Timestamps | — |

**Empfohlener Workflow:**
```bash
# Einmalig (Erstlauf):
python scrapers/fetch_congress_data.py          # ~7-8h, Ctrl+C unterbricht sicher

# Wöchentlich (Delta):
python scrapers/fetch_congress_data.py --update  # fetcht nur geänderte Bills
python scripts/import_trades.py                  # Vote-Timing Backfill anwenden
```

> **Checkpoint-System:** Bei Abbruch (Ctrl+C oder Verbindungsfehler) einfach neu starten —
> das Script setzt beim letzten abgeschlossenen Schritt fort.
> `--update` nutzt `fromDateTime` der Congress.gov API — nur wirklich geänderte Bills werden gefetcht.
> API-Limit: 5.000 Req/h (mit Key) → 0.75s Pause → ~4.800 Bills/h.

---

## 11. Tests ausführen

**Framework:** pytest
**Testdateien:** `tests/`

```bash
# Alle Tests
pytest tests/ -v

# Einzelne Testdatei
pytest tests/test_house_scraper.py -v
pytest tests/test_senate_scraper.py -v
pytest tests/test_senate_pdf_extractor.py -v

# Einzelne Testklasse
pytest tests/test_house_scraper.py::TestParseTransactionBlock -v

# Einzelner Test
pytest tests/test_house_scraper.py::TestParseTransactionBlock::test_purchase -v

# Nur fehlgeschlagene Tests erneut ausführen
pytest tests/ --lf -v

# Kurzausgabe (ohne -v)
pytest tests/
```

**Aktuelle Testanzahl:** 122 Tests (House Scraper)

---

## 12. Git – Projektstand committen

**Voraussetzung:** Git ist bereits auf dem Rechner eingerichtet und das Repository ist unter `congress_tracker/` vorhanden.

### Aktuellen Stand sichern (add + commit)

```bash
# 1. Überblick: Was hat sich geändert?
git status

# 2. Alle geänderten Dateien zum nächsten Commit vormerken
git add .

# Oder gezielt einzelne Dateien/Ordner auswählen:
git add scripts/import_trades.py
git add data/processed/trades_master.csv
git add organisation/

# 3. Commit erstellen mit aussagekräftiger Nachricht
git commit -m "Kurze Beschreibung was geändert wurde"

# Beispiele für gute Commit-Nachrichten:
git commit -m "Filter 3 Committee-Filter implementiert"
git commit -m "Senate Scraper: Delta-Modus hinzugefügt"
git commit -m "trades_master.csv aktualisiert (9.591 Trades, 100% Match)"
```

### Commit-History anzeigen

```bash
# Letzte Commits anzeigen
git log --oneline -10
```

### Änderungen vor dem Commit prüfen

```bash
# Was wurde geändert (zeilenweise Diff)?
git diff

# Was ist bereits vorgemerkt (staged)?
git diff --staged
```

> **Hinweis:** `git add .` nimmt **alle** geänderten Dateien auf. Große Datendateien
> (z.B. `trades_master.csv`) können Commits langsam machen — bei Bedarf in `.gitignore`
> eintragen.

---

## Schnellreferenz

| Script | Befehl | Zweck |
|--------|--------|-------|
| Senate Scraper | `python scrapers/senate_scraper.py` | Senate-Trades scrapen |
| Senate PDF Extractor | `python scrapers/senate_pdf_extractor.py` | Paper-PTRs via OCR |
| Import Pipeline | `python scripts/import_trades.py` | Trades anreichern (Name-Match + Committees) |
| XLSX → CSV | `python scripts/convert_xlsx_to_csv.py` | Mapping-CSVs aktualisieren |
| Senate Diagnose | `python scripts/diagnose_scraper.py` | Scraper-Tests |
| House Committee Parser | `python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py --pdf ...` | House Committee PDF |
| Senate Committee Parser | `python scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py --pdf ...` | Senate Committee PDF |
| House Scraper | `python scrapers/house_scraper.py` | House PTR-Trades scrapen |
| House PDF Extractor | `python scrapers/house_pdf_extractor.py` | Hand-Delivered PDFs via OCR |
| Congress Data Fetcher | `python scrapers/fetch_congress_data.py` | Erstlauf: alle 6 Datentypen (~7-8h) |
| Congress Data Fetcher (Update) | `python scrapers/fetch_congress_data.py --update` | Delta: nur geänderte Bills (~5-30 Min) |
| Tests | `pytest tests/ -v` | Alle Unit-Tests |
