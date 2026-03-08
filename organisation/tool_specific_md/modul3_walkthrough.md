# Walkthrough: Modul 3 – Senate Trade Scraper

## Überblick

Playwright-basierter Scraper (`scrapers/senate_scraper.py`) für Periodic Transaction Reports (PTRs) von https://efdsearch.senate.gov/search/.

### Architektur (finale Version)

```
run()
 └── _single_run() × N
      ├── _accept_agreement(page)          # Cookie/Session setzen
      └── _scrape_results(page, ctx, from, to)
           ├── _fetch_all_report_links()   # POST /search/report/data/ length=-1
           └── _extract_transactions() × M # Jede PTR-Detail-Seite öffnen
```

### Features
| Feature | Details |
|---|---|
| **Direkter API-Call** | `POST /search/report/data/` mit `length=-1` → alle PTRs in einem Request, kein DOM, kein Pagination |
| **Agreement** | Akzeptiert `#agree_statement` automatisch, setzt Session-Cookie |
| **HTML vs PDF** | Extrahiert elektronische PTRs, loggt PDFs als `PDF_SKIPPED` |
| **Delta-Modus** | Liest letztes `disclosure_date` aus CSV (−1 Tag Puffer), lädt nur neue Daten |
| **Deduplication** | Append + `drop_duplicates` auf `(name, ticker, transaction_date, transaction_type, amount, ptr_link, owner)` |
| **Multi-Run** | `--runs N` für optionale Redundanz (Standard: 1 – API ist deterministisch) |
| **Rate Limiting** | 1s Pause zwischen Report-Requests, 5s zwischen Runs |
| **Logging** | Konsole + `logs/senate_scraper.log` (UTF-8, Append) |

### Output-Spalten
`name`, `ticker`, `asset_name`, `asset_type`, `transaction_type`, `amount`, `transaction_date`, `owner`, `comment`, `disclosure_date`, `ptr_link`

---

## CLI-Aufruf

```powershell
cd "C:\Users\benedikt\OneDrive\bmb_trdprg\congress_tracker"

# Standard (seit Session-Start 01/03/2025):
.\venv\Scripts\python.exe scrapers\senate_scraper.py --from-date 01/03/2025

# Delta-Modus (letzte X Tage):
.\venv\Scripts\python.exe scrapers\senate_scraper.py --days 30

# Multi-Run (optional, für maximale Abdeckung):
.\venv\Scripts\python.exe scrapers\senate_scraper.py --from-date 01/03/2025 --runs 2

# Dry-run: scrapen ohne speichern (Testlauf)
.\venv\Scripts\python.exe scrapers\senate_scraper.py --days 7 --dry-run
.\venv\Scripts\python.exe scrapers\senate_scraper.py --from-date 01/15/2025 --dry-run
```

### Python API
```python
from scrapers.senate_scraper import SenateScraper
scraper = SenateScraper(headless=True)
df = scraper.run(delta=True)   # Produktion: nur neue Daten
scraper.save(df)
```

---

## Settings (`config/settings.py`)

```python
SENATE_SCRAPE_RUNS = 1                # Runs pro Aufruf (1 reicht – API deterministisch)
SENATE_SCRAPE_RUN_PAUSE_SECONDS = 5   # Pause zwischen Runs (Sekunden)
```

---

## Entwicklungsgeschichte & Bugfixes

### Phase 1 – Ausgangsproblem: ~16% Varianz durch Race Conditions

**Problem:** Blindes `page.wait_for_timeout(1500)` nach Pagination-Click war zu kurz → DataTables lieferte noch die alte Seite.

**Fixes (interim):**
- Smart Wait: `_navigate_to_next_page()` wartete auf Änderung des DataTables-Info-Texts (`"Showing X to Y of Z entries"`)
- Row-Validierung + Retry: `_get_validated_rows()` prüfte `<td>`-Count vor Verarbeitung
- Click-Retry: erneuter Click wenn DOM unverändert

**Kritischer Playwright-Bug dabei entdeckt:** `page.wait_for_function(expr, arg=old_info, ...)` — `arg=` ist **keyword-only** in der Playwright Python API. Positional übergeben → sofortiger `TypeError`, der sich als Pagination-Fehler tarnte.

### Phase 2 – Restvarianz ~4–50%: Serverseitiger Nicht-Determinismus

**Problem:** Auch nach korrekter Pagination lieferte der EFD-Server bei identischer Suche verschiedene PTR-Sets zurück. Ursache: instabiler `ORDER BY` im Backend — das gleiche Query ergibt auf verschiedenen Seiten verschiedene Subsets.

**Beweis (aus Logs):** Boozman hatte 4 PTRs nur in Run 1, Mullin 3 PTRs nur in Run 1 (netto −43 Trades), obwohl Pagination 193/193 war. Chunking auf 14-Tage-Fenster reduzierte das Problem, löste es aber nicht vollständig.

**Root Cause:** Kein client-seitiger Fix (Smart Wait, Retry, Chunking) kann serverseitigen Nicht-Determinismus bei paginierten Antworten beheben.

### Phase 3 – Finale Lösung: Direkter DataTables-API-Call

**Entdeckung (Browser-Inspektion):**
- EFD nutzt DataTables mit serverSide-Pagination via `POST /search/report/data/`
- DataTables-Parameter `length=-1` weist den Server an, **alle** Einträge auf einmal zurückzugeben
- Test im Browser bestätigt: `length=-1` → `{recordsTotal: 193, recordsFiltered: 193, dataLength: 193}` ✅

**Implementierung: `_fetch_all_report_links()`**

```python
# Ausgeführt via page.evaluate() im Browser-Kontext
params.append('length', '-1')          # ALLE Einträge auf einmal
params.append('report_types', '[11]')  # 11 = Periodic Transaction Report
params.append('submitted_start_date', f"{from_date} 00:00:00")
# ...
```

- `page.evaluate(async_fn)` führt den `fetch()`-Call im Browser aus → Session-Cookies (inkl. CSRF-Token) automatisch mitgeschickt
- `_accept_agreement()` setzt die Session → danach direkt API-Call, kein Suchformular nötig
- Server-Response: `{data: [[first_name, last_name, full_name_html, link_html, disclosure_date], ...]}`
- `href` wird per Regex aus dem HTML-String in `row[3]` extrahiert

**Ergebnis:** Kein Pagination-DOM, keine Race Conditions, vollständig deterministisch. Gleicher Run → immer gleiche Ergebnisse.

**Entfernte Methoden (obsolet):**
- `_search_reports()` – kein DOM-Formular mehr nötig
- `_navigate_to_next_page()` – keine Pagination mehr
- `_get_validated_rows()` – kein DOM mehr
- `_get_datatables_info_text()` / `_get_datatables_total()` – kein DataTables-DOM mehr

**Entfernte Settings (obsolet):**
- `PAGINATION_WAIT_TIMEOUT_MS`, `PAGINATION_POLL_INTERVAL_MS`
- `PAGINATION_CLICK_RETRIES`, `PAGINATION_EMPTY_ROW_RETRIES`
- `SENATE_CHUNK_DAYS`

---

## Tests (`tests/test_senate_scraper.py`)

55 Tests, alle grün. Wichtige Klassen:

| Klasse | Testet |
|---|---|
| `TestScrapeResults` | Routing-Logik: PDF-Skip, URL-Dedup, Rate Limit, leerer href – mockt `_fetch_all_report_links` |
| `TestFetchAllReportLinks` | JSON-Parsing: HTML-Stripping, leere Daten, kurze Rows, mehrere Rows |
| `TestRunDeltaLogic` | Delta-Datum, from_date-Weiterleitung an `_scrape_results.call_args[0][2]` |
| `TestExtractTransactions` | Zellen-Mapping, Ticker-`--`-Handling, Retry-Logik |
| `TestSave` | Append-Modus, Deduplication, Verzeichnis-Erstellung |

---

## Diagnose-Script

`scripts/diagnose_scraper.py` – bei Bedarf zur Verifizierung:

```powershell
.\venv\Scripts\python.exe scripts\diagnose_scraper.py --test consistency  # 3x scrapen, vergleichen
.\venv\Scripts\python.exe scripts\diagnose_scraper.py --test rowcount     # API-Count vs. tatsächlich
```
