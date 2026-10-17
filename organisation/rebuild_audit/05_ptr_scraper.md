# Audit 05: Trade-Datenbeschaffung (PTR-Scraper, OCR-Extractoren)

Stand: 2026-10-17, Branch `agent/committe_matcher`. Nur Lesezugriff, keine API-/Live-Aufrufe.

## 1. Features

| Feature | Fundort |
|---|---|
| House-Scraper: `{year}FD.zip` laden, XML nach `FilingType=P` + `Prefix=Hon.` filtern, PTR-PDF per pdfplumber parsen, Delta, Retry 3x/5s, Rate-Limit 1s | `scrapers/house_scraper.py` (1019 Z.) |
| Senate-Scraper (Playwright): Agreement-Seite, direkter `POST /search/report/data/` mit `length=-1`, Detailseiten parsen, Paper-PDFs als `PDF_SKIPPED` | `scrapers/senate_scraper.py` (843 Z.) |
| House-OCR v1: ganze Seite an Anthropic Vision | `scrapers/house_pdf_extractor.py` (823 Z.) |
| House-OCR v2: OpenCV-Grid-Erkennung, Pixel-Density für Amount-Checkboxen A-K, Vision-Batches, Fallback | `scrapers/house_pdf_extractor_v2.py` (1189 Z.; `_detect_table_grid` :522, `_detect_amount_for_row` :655, `_extract_with_grid` :721, `_extract_fallback` :925) |
| Senate-Paper-OCR (GIF-Seiten Blumenthal) | `scrapers/senate_pdf_extractor.py` (728 Z.) |
| Quiver-Auto-Download (Primärquelle, Playwright-Login + xlsx-Export) | `scrapers/quiver_scraper.py` (271 Z.), Settings `config/settings.py:455-489` |
| Diagnose Senate-Paginierung/Konsistenz | `scripts/diagnose_scraper.py` (456 Z.) |
| Diagnose Master vs. Quiver (Waisen-trade_ids) | `scripts/diagnose_master_quiver_delta.py` |
| Nebenbei im Baum: `fetch_congress_data.py` (1322 Z.), `generate_committee_structure.py`, Committee-PDF-Parser (`house_pdf_parsing_tool/`, `senate_pdf_parsing_tool/`) | gehören thematisch zum Committee-Bereich, nicht PTR |

## 2. Funktionsprüfung

**Rolle gegenüber Quiver (belegt):** Quiver ist laut `Trade_scraper_status.md` §5 und `config/settings.py:455` die primäre und einzige Quelle. Ein Grep über `scripts/ utils/ config/ app/` findet keinen produktiven Leser von `house_trades.csv`, `senate_trades.csv` oder `house_pdf.csv`. Die Eigen-Scraper werden nur von ihren Tests, von `reset_pipeline.py:126` (löscht ein Done-Log) und von einem Hinweis in `linux_setup.sh:131` referenziert. Sie sind ungenutzt (seit der Quiver-Umstellung, Commit 2221c6f, 2026-04). `data/raw/` enthält in diesem Worktree auch keine solchen CSVs. Letzte Änderung an House-/Senate-Scraper: 2026-04 bzw. 2026-03.

**Tests (belegt, ausgeführt):** `pytest tests/test_house_scraper.py test_senate_scraper.py test_house_pdf_extractor.py`: 210 passed in 1,8 s. Zusätzlich `test_senate_pdf_extractor.py` + `test_quiver_scraper.py`: 44 passed. Einschränkung: Tests laufen offline (Mocks/Fixtures); die Funktion gegen die echten Seiten ist nicht belegt. `house_pdf_extractor_v2.py` (die "empfohlene" Version) hat keinen eigenen Test; getestet wird nur v1 (`tests/test_house_pdf_extractor.py:15`).

**Aktive Extractor-Version:** Dokumentiert ist v2 als empfohlen, v1 als LEGACY. Faktisch ist beides ungenutzt, da keine Pipeline-Stufe die Ausgaben einliest. v1 ist tot (nur noch von seinem eigenen Test referenziert) und zusätzlich fachlich als fehlerhaft dokumentiert (Merge/Truncation, Namenshalluzination).

**Parsing-Robustheit (laut Doku, nicht neu verifiziert):**
- House-Text-PTRs: regelbasiert, durch Regressionstests mit echten PDFs abgesichert (vermutet solide).
- Hand-Delivered-Scans (59 PDFs, 14 Abgeordnete): nur OCR möglich; offene Probleme laut `Trade_scraper_status.md` §3: Amount-NaN bei Typ D (37,8 %, ~2.500 Trades), Vision-Halluzinationen (~50-100), Bond-Maturity als Transaktionsdatum (~100), ~49 % fehlende Ticker.
- Senate-Paper-PTRs: nur 24 PDFs, ein Senator (Blumenthal, nur Fonds), als irrelevant eingestuft.
- Senate-Scraper: der `length=-1`-Direktaufruf löste ein nicht-deterministisches Paginierungsproblem (~16 % Varianz); als gelöst dokumentiert.

## 3. Code-Qualität

- **Toter Code:** gesamte Eigen-Scraper-Linie (~4.600 Z. Python + ~3.900 Z. Tests) hängt an nichts; v1 (823 Z.) ist doppelt tot.
- **Duplikate:** v1/v2 und `senate_pdf_extractor.py` teilen `AMOUNT_RANGES`, `OWNER_LONG`, `_normalize_date`, `_init_anthropic`, `_image_to_base64`, Done-Log-Logik, `save()` (Copy-Paste statt gemeinsamer Basis). v1 und v2 importieren `CSV_COLUMNS/DEDUP_SUBSET` aus `house_scraper`.
- **Diagnose-Skripte:** `diagnose_scraper.py` testet `test_pagination_timing`, also einen Weg, der laut Doku entfernt wurde (veraltet). `diagnose_master_quiver_delta.py` enthält eine dokumentierte "LOGIK-KOPIE" von `normalize_dates` (Drift-Risiko), ist aber ein sinnvolles Einmal-Werkzeug.
- **Müll im Baum:** `scrapers/Recherche-Aufgabe in Datei - Claude_files/` (1,2 MB, 20 Dateien, gespeicherte Webseite mit CSS/JS/`.Herunterladen`) ist git-getrackt und ohne Bezug zum Code. `saved_resource*.html` ebenfalls. Zusätzlich ~3,3 MB PDFs unter `*_pdf_parsing_tool/` (Committee-Bereich). `__pycache__` ist nicht getrackt.
- Das Modell für die OCR ist hartkodiert in Settings (`PDF_EXTRACTOR_MODEL`, `config/settings.py:307`), Default-Argumente in v1 nennen noch `claude-sonnet-4-6`.

## 4. Wiederverwendbares Wissen

- **House:** Jahres-ZIP `{year}FD.zip`, XML-Filter `FilingType=P` und `Prefix=Hon.`; DocID `200xxxxx` = PTR; PDF-URL-Template `PDF_URL_TEMPLATE` (`scrapers/house_scraper.py`). Block-Start = Zeile mit Tx-Typ UND Datum; Skip-Patterns `F S:`, `S O:`, `I P O`, `I V D`, `I CERTIFY`, `Digitally Signed:`; Regexe für Owner `SP/JT/DC`, Ticker `\([A-Z][A-Z0-9.]*\)`, Asset-Type `[ST|OP|GS|OT|CS|DC|EF|MF]`; Null-Byte-Bereinigung; NaN-Fix vor `drop_duplicates` (`Trade_scraper_status.md` §1).
- **House-Papierformulare:** Formulartypen (rotiert/handschriftlich etc.), Layouts und A-K-Amount-Mapping: `organisation/tool_specific_md/HAND_DELIVERED_PDF_ANALYSE.md`, Mapping in `house_pdf_extractor_v2.py:48`. Namen aus XML-Metadaten statt OCR (100 % Match).
- **Senate:** Selektoren (`#agree_statement`, `table#filedReports`, `/search/view/ptr/` vs `/search/view/paper/`, Detailtabelle `table.table-striped` mit 9 Spalten, Asset-Name-Zellformate): `organisation/tool_specific_md/Senate_website_structure.md`. DataTables-POST `/search/report/data/` mit `report_types=[11]`, `length=-1`, im Browser-Kontext per `page.evaluate` (CSRF/Session). Instabiles serverseitiges ORDER BY: Paginierung nie verwenden.
- **Senate-Paper:** GIF-Seiten unter `efd-media-public.senate.gov/media/{YYYY}/{M}/000/000/{seq}.gif`, Owner-Codes `(S)(SP)(DC)(J)`, Parent-Zeilen mit `:` überspringen (`Trade_scraper_status.md` §4).
- **OCR-Lehren:** Prompt-Regel "2-stellige Jahre immer 20XX", `readable`-Flag pro Zeile, Datum-Validierung gegen `disclosure_date`.
- **Quiver:** Login-/Export-Selektoren in `config/settings.py:474-486`; Delta-Diagnose (Master hält Waisen nach `last_wins`).

## 5. Rebuild-Empfehlung

- **Nicht übernehmen als Code:** `house_pdf_extractor.py` (v1), `senate_pdf_extractor.py`, den Senate-Scraper und `diagnose_scraper.py`. Sie sind ungenutzt und kosten Wartung. Das Senate-Paper-Thema (24 PDFs, nur Fonds) ist ignorierbar.
- **Auch den House-Scraper streichen**, solange Quiver die Primärquelle bleibt. Nur falls Quiver als Abhängigkeit (Kosten/ToS) wegfallen soll, wäre er die Basis; dann ist der Code kompakt neu schreibbar (ZIP+XML+Regex, grob 300 Z.).
- **House-OCR v2 nur bei Bedarf:** ~2.500 Trades mit Amount-NaN und ~49 % fehlenden Tickern sind bekannte Lücken; Papier-PTRs sind ein kleiner Anteil. Die Grid/Pixel-Density-Idee ist wertvoll, aber ohne Test und ohne Anbindung. Im Rebuild als optionales, getestetes Modul mit gemeinsamer Basisklasse, nicht als Kopie.
- **Behalten (als Wissen, nicht als Code):** die drei Markdown-Dateien (Trade_scraper_status, HAND_DELIVERED_PDF_ANALYSE, Senate_website_structure) und `quiver_scraper.py` samt Settings.
- **Aufräumen sofort:** `scrapers/Recherche-Aufgabe in Datei - Claude_files/` und `saved_resource*.html` aus git entfernen.
