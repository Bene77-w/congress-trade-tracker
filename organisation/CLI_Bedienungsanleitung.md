# CLI Bedienungsanleitung – Congress Trade Tracker

Alle Befehle werden aus dem Projektverzeichnis `congress_tracker/` ausgeführt, mit aktiviertem venv:

```bash
cd "C:\Users\benedikt\OneDrive\bmb_trdprg\congress_tracker"
venv\Scripts\activate
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
# Standard: verarbeitet nur neue Trades (Duplikat-Check gegen trades_master.csv)
python scripts/import_trades.py
```

**Output:** `data/processed/trades_master.csv`, `data/processed/trades_new.csv`, `data/processed/unmatched_names.csv`

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

## 10. Congress Vote Fetcher

**Datei:** `scripts/fetch_congress_votes.py`
**Zweck:** Lädt Bill-Daten und Floor-Votes aus der Congress.gov API (zwei Phasen)
**Voraussetzung:** `CONGRESS_API_KEY` in `.env` gesetzt (api.data.gov)

```bash
# Standard: Delta-Modus (Phase 1 + Phase 2, nur neue Bills)
python scripts/fetch_congress_votes.py

# Vollständiger Lauf: Cache komplett neu aufbauen (~4h, 14.000 Bills)
python scripts/fetch_congress_votes.py --full

# Nur Phase 2: Votes aus bestehendem Cache extrahieren (Cache muss existieren)
python scripts/fetch_congress_votes.py --phase2-only

# Statistiken anzeigen (Bills im Cache, gemappte Sektoren, Votes)
python scripts/fetch_congress_votes.py --stats

# Dry-run: nur zählen, nichts speichern
python scripts/fetch_congress_votes.py --dry-run
```

**Output:**
- `data/raw/congress_bills_cache.csv` – Bill-Metadaten mit policyArea (Phase 1)
- `data/raw/congress_votes.csv` – House Floor-Votes mit Sektor-Mapping (Phase 2)

**Empfohlene Reihenfolge beim ersten Mal:**
1. `--stats` → prüfen ob bereits ein Cache vorhanden ist
2. `--full` → einmalig alle Bills fetchen (~4h, Ctrl+C unterbricht sicher, Delta setzt fort)
3. `--stats` → Ergebnisse prüfen
4. Danach reicht `python scripts/fetch_congress_votes.py` (Delta, nur neue Bills)

> **Hinweis:** Wenn `--stats` meldet "Kein Bill-Cache vorhanden", muss `--full` ausgeführt werden.
> Verbindungsabbrüche (DNS/Connection Reset) sind unkritisch — Delta-Modus setzt beim nächsten Lauf fort.

> **Hinweis:** Nur House-Votes (vorerst). Senate-Votes benötigen separaten XML-Parser.
> API-Limit: 1 Request/Sekunde. Bei 429-Fehler automatischer Retry nach 60s.

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
| Congress Vote Fetcher | `python scripts/fetch_congress_votes.py` | Bills + Floor-Votes aus Congress.gov API |
| Tests | `pytest tests/ -v` | Alle Unit-Tests |
