# Trade-Scraper — Status & Walkthrough

Konsolidierte Referenz zu den Roh-Trade-Scrapern (House/Senate) und den PDF-/OCR-Extractoren: Architektur, bekannte Probleme und Paper-PTR-Formate.

**Verwandte Doku:** CLI-Aufrufe → `CLI_Bedienungsanleitung.md` · Pipeline-Position → `Pipelines.md` · Senate-Website-Selektoren → `Senate_website_structure.md` · House-Paper-PDF-Tiefenanalyse → `house_all_pdf_info.md`.

---

## 1. House Scraper (`scrapers/house_scraper.py`)

Requests/pdfplumber-basiert (kein Browser nötig), Quelle: disclosures-clerk.house.gov. Die jährliche `{year}FD.zip` enthält eine XML mit allen Filings — daraus werden nur PTRs gefiltert.

### Architektur

```
run()
 ├── _get_existing_doc_ids()          # Delta: bekannte DocIDs aus CSV lesen
 └── _download_filing_list(year) × N  # ZIP herunterladen, XML parsen, filtern
      └── _download_pdf(doc_id, year) × M
           └── _parse_ptr_pdf(pdf_bytes)
                └── _extract_transactions_from_text(text)
                     └── _parse_transaction_block(block) × K
save(df)   # Append + Dedup in house_trades.csv
```

### Kernpunkte
- **XML-Filter:** nur `FilingType=P` (PTR) + `Prefix=Hon.` → aktive Members, keine Staff/Officers. Nur **P** enthält Einzeltrades. DocID-Range `200xxxxx` = PTR.
- **PDF-Parsing:** pdfplumber (kein OCR), Null-Bytes (`\x00`) werden bereinigt. Ein neuer Transaktionsblock startet, wenn eine Zeile **gleichzeitig** Tx-Typ (`P`/`S`/`E`/`S (partial/full)`) **und** Datum (`MM/DD/YYYY`) enthält.
- **Skip-Patterns:** `F S:` (Filing Status), `S O:` (Subholding Of), `I P O`, `I V D`, `I CERTIFY`, `Digitally Signed:` (Datum daraus als Notification-Fallback).
- **Feld-Extraktion pro Block:** Owner (`SP`/`JT`/`DC`-Prefix), Ticker (`\([A-Z][A-Z0-9.]*\)`), Asset-Type (`\[(ST|OP|GS|OT|CS|DC|EF|MF)\]`), Amount (`$X - $Y`-Range, Fallback Einzelbeträge), Asset-Name (alles vor dem Tx-Typ-Token, bereinigt).
- **Dedup (zwei Stufen):** `DEDUP_SUBSET` = alle CSV-Spalten außer `disclosure_date` (Amendments mit anderem Meldedatum bleiben erhalten). `run()` → `keep="first"` (In-Session), `save()` → `keep="last"` (Cross-Session). **NaN-Fix:** `df[DEDUP_SUBSET].fillna("")` vor jedem `drop_duplicates()` (sonst `NaN != NaN` → Dauer-Duplikate). Der Scraper-`DEDUP_SUBSET` behält bewusst zusätzlich `owner`/`asset_type`/`ptr_link` (Self/Spouse- bzw. Report-Unterscheidung); ein Test prüft den `owner`-Erhalt. Die kanonische Master-Dedup-Identität liegt separat in `utils/trade_id.py` (`TRADE_ID_SOURCE_COLUMNS` → `trade_id`).
- **Robustheit:** Rate-Limit 1s zwischen Downloads, Retry 3×/5s. Logging → `logs/house_scraper.log`.
- **Tests:** `tests/test_house_scraper.py` (Regex, Block-Parsing, ZIP/XML, Delta, Retry, Real-PDF-Regression).

### Companion: OCR-Extractoren für gescannte Paper-PTRs
Für PDFs mit `reason="empty_text"` in `house_scraper_fehler.csv` (Bild-Scans ohne Textlayer) — OCR via Anthropic Vision API. Name kommt aus XML-Metadata (nicht Vision) → 100 % Match-Rate.
- **`house_pdf_extractor_v2.py`** (empfohlen): Grid-Erkennung (OpenCV) + Pixel-Density + Vision-API-Batches. Output → `data/raw/house_pdf.csv`.
- **`house_pdf_extractor.py`** (v1, LEGACY-Fallback): schickt ganze Seiten an die Vision-API → Merge/Truncation-Fehler, Name-Halluzinationen. Durch v2 ersetzt.
- **Amount-Checkboxen A–K** statt Beträgen (Papierformulare): A=$1.001–15k, B=15k–50k, C=50k–100k, D=100k–250k, E=250k–500k, F=500k–1M, G=1M–5M, H=5M–25M, I=25M–50M, J=>50M, K=Spouse/DC >1M.
- Detail-Layout der Formulartypen → `house_all_pdf_info.md`; Committee-PDF-Parsing → `house_all_pdf_info.md`.

---

## 2. Senate Scraper (`scrapers/senate_scraper.py`)

Playwright-basiert, Quelle: efdsearch.senate.gov.

### Architektur

```
run()
 └── _single_run() × N
      ├── _accept_agreement(page)          # #agree_statement, Session-Cookie setzen
      └── _scrape_results(page, ctx, from, to)
           ├── _fetch_all_report_links()   # POST /search/report/data/ mit length=-1
           └── _extract_transactions() × M # jede PTR-Detail-Seite öffnen
```

### Kernpunkte
- **Direkter DataTables-API-Call:** `POST /search/report/data/` mit `length=-1` liefert **alle** PTRs in einem Request — kein Pagination-DOM, keine Race Conditions, voll deterministisch. `report_types=[11]` = PTR. Ausgeführt via `page.evaluate()` im Browser-Kontext, damit Session-/CSRF-Cookies automatisch mitgehen.
- **HTML vs. PDF:** elektronische PTRs werden extrahiert, Paper-PDFs als `PDF_SKIPPED` geloggt (s. §4).
- **Delta-Modus:** liest letztes `disclosure_date` aus CSV (−1 Tag Puffer).
- **Dedup:** Append + `drop_duplicates` auf `(name, ticker, transaction_date, transaction_type, amount, ptr_link, owner)`.
- **Tests:** `tests/test_senate_scraper.py`. Diagnose: `scripts/diagnose_scraper.py --test consistency|rowcount`.

### Warum direkter API-Call (Bugfix-Historie, gelöst)
Ältere DOM-Pagination hatte ~16 % Varianz durch Race Conditions (zu kurze Waits) **und** serverseitigen Nicht-Determinismus (instabiles `ORDER BY` → gleiche Query liefert auf verschiedenen Seiten verschiedene Subsets). Kein client-seitiger Fix (Smart-Wait, Retry, Chunking) konnte das lösen. Der `length=-1`-Direktaufruf umgeht Pagination komplett. Dabei entfernt: `_search_reports`, `_navigate_to_next_page`, `_get_validated_rows`, alle `PAGINATION_*`- und `SENATE_CHUNK_DAYS`-Settings.

---

## 3. Bekannte Probleme & Status

Stand: 2026-03-21. Priorisierte Offene-Punkte-Liste:

| Prio | Problem | Betroffene Trades | Komponente |
|------|---------|-------------------|------------|
| HOCH | Amount-NaN bei Typ D (Broker Statements, 37,8 %) | ~2.500 | house_pdf_extractor_v2 |
| HOCH | Vision-API-Halluzinationen (erfundene Asset-Namen, Jahr 1925 statt 2025) | ~50–100 | house_pdf_extractor_v2 |
| MITTEL | Bond-Maturity als transaction_date (nur Fälligkeitsdatum auf Formular) | ~100 | house_pdf_extractor_v2 |
| MITTEL | Fehlende Ticker (~49 %): House 51 % Paper-Formulare, Senate 37 % e-Filing | ~8.000 | alle Scraper (Design) |
| NIEDRIG | Ticker-Reuse/Delisting → falsche Kurshistorie im Preis-Cache | einzelne | post_trade (display-only) |
| NIEDRIG | Grid-Detection-Fallback bei stark rotierten Seiten | Edge Cases | house_pdf_extractor_v2 |
| NIEDRIG | Late-Filer >180 Tage (reale Meldeverzögerung, kein Bug) | ~850 | Datenrealität |
| IRRELEVANT | Senate Paper-PTRs (s. §4) | 24 PDFs | senate_pdf_extractor |

**Gegenmaßnahmen (implementiert):** Vision-Prompt-Regel „2-stellige Jahre immer 20XX"; `readable`-Flag pro Zeile → unsichere Trades nach `logs/ocr_uncertain.csv`; Datum-Validierung in `import_trades.py` (`transaction_date` nach `disclosure_date` → rejected bzw. mit `disclosure_date` ersetzt, Fenster via `TRADE_DATE_WINDOW_BEFORE/AFTER_DAYS`); fehlende `transaction_date` ← `disclosure_date`-Fallback.

**Ticker-Reuse-Detail (OFFEN, niedrig):** Delistete/wiederverwendete Symbole (`NYC`, `PW`) bekommen in `data/cache/price_history.parquet` die Historie eines *neuen* Unternehmens desselben Tickers. Erkennungsmerkmal: extrem niedrige Baseline + absurd hoher Post-Trade-Return. **Nicht im Scoring** (`post_return_*` sind display-only), nur App-Anzeige betroffen.

---

## 4. Senate Paper-PTRs (Blumenthal) — irrelevant, archiviert

Der einzige Senator mit Paper-PTRs (Richard Blumenthal, via Elias Law Group) handelt ausschließlich Fonds → für Trading-Signale irrelevant. `senate_pdf_extractor.py` ist funktional, wird aber nicht produktiv genutzt. Format-Kurzfassung, falls je benötigt:

- Seiten werden **nicht als PDF**, sondern als **GIF-Bilder** ausgeliefert (`efd-media-public.senate.gov/media/{YYYY}/{M}/000/000/{seq_id}.gif`) → **OCR zwingend**. Print-URL `efdsearch.senate.gov/search/print/paper/{uuid}/` rendert alle Seiten als `<img>`.
- Formular „PERIODIC DISCLOSURE OF FINANCIAL TRANSACTIONS": Spalten Asset · Transaction-Type (Purchase/Sale/Exchange) · Date · Amount A–J (gleiche Ranges wie House A–J).
- **Owner-Codes** vor dem Asset: `(S)` Self, `(SP)` Spouse, `(DC)` Dependent Child, `(J)` Joint.
- Aktien: `[owner] [Name] (STOCK) (TICKER)` — Ticker immer in letzter Klammer. Fonds/LLCs: kein Ticker.
- **Parent/Child:** Zeilen mit `:` am Ende sind Gruppenköpfe ohne Transaktion → nur eingerückte Child-Zeilen extrahieren.

---

## 5. Import-/Daten-Historie

Aktuelle primäre Datenquelle ist **Quiver** (`congress-trading-all.csv`, vollständiger Snapshot ab 2012), eingelesen von `scripts/import_trades.py`. Der Importer baut die Master bei Full-Rebuild komplett neu (kein Dedup gegen alte Master) und bietet **Checkpoint-Support** (`CHECKPOINT_INTERVAL`, Pickle) sowie einen **`--delta`-Modus** für inkrementelle Läufe.

Die detaillierte chronologische Änderungshistorie (Quiver-Migration, OCR-QS, Dedup-Umbau) steht im `CHANGELOG.md` bzw. `git log -- scripts/import_trades.py`. Spalten-Semantik → `../DATA_STRUCTURE.md`, Stage-Ablauf → `../Pipelines.md`.
