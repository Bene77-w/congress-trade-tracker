# CLI Bedienungsanleitung – Congress Trade Tracker

Alle Befehle werden aus dem Projektverzeichnis `congress_tracker/` ausgeführt, mit aktiviertem venv:

```bash
cd "C:\Users\benedikt\OneDrive\BMB Tradingprogramm\congress_tracker"
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

## 3. Import Pipeline (Quiver-Daten)

**Datei:** `scripts/import_trades.py`
**Zweck:** Importiert Quiver-Trades, matcht Member-Namen, reichert mit Committee-Daten an
**Voraussetzung:** Quiver-Datei muss vorher abgelegt werden

```bash
# Schritt 1: Neue Quiver-CSV ablegen (eine von beiden):
#   data/uploads/quiver_latest.csv      ← bevorzugt
#   data/raw/congress_trading_data_quiver.xlsx

# Schritt 2: Import ausführen
python scripts/import_trades.py
```

**Output:** `data/processed/trades_master.csv`, `data/processed/unmatched_names.csv`

> **Hinweis:** Das Script hat kein `--help`. Es liest automatisch die Quiver-Datei
> aus den oben genannten Pfaden und gibt eine Fehlermeldung wenn keine gefunden wird.

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

## 9. Tests ausführen

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
| Import Pipeline | `python scripts/import_trades.py` | Quiver-Daten importieren |
| XLSX → CSV | `python scripts/convert_xlsx_to_csv.py` | Mapping-CSVs aktualisieren |
| Senate Diagnose | `python scripts/diagnose_scraper.py` | Scraper-Tests |
| House Committee Parser | `python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py --pdf ...` | House Committee PDF |
| Senate Committee Parser | `python scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py --pdf ...` | Senate Committee PDF |
| House Scraper | `python scrapers/house_scraper.py` | House PTR-Trades scrapen |
| Tests | `pytest tests/ -v` | Alle Unit-Tests |
