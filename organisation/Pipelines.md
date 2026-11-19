# Congress Tracker - Pipelines

Kompakte Uebersicht aller Daten-Pipelines, ihrer Ablaeufe und Abhaengigkeiten.

---

## Ausfuehrungsreihenfolge

```bash
cd congress_tracker && .\venv\Scripts\activate

# 0. Quiver Auto-Download (optional, empfohlen täglich via systemd-User-Timer)
python scrapers/quiver_scraper.py          # → congress-trading-all.xlsx (atomar)

# 1. Scraper (erzeugen Rohdaten)
python scrapers/house_scraper.py           # → house_trades.csv
python scrapers/senate_scraper.py          # → senate_trades.csv

# 2. Optional: OCR fuer gescannte PDFs
python scrapers/house_pdf_extractor_v2.py  # → ergaenzt house_trades.csv (empfohlen)
python scrapers/senate_pdf_extractor.py    # → ergaenzt senate_trades.csv (irrelevant)

# 3. Import-Pipeline (Anreicherung + Dedup)
python scripts/import_trades.py            # → trades_master.csv + trades_new.csv

```

---

## Pipeline 0: Quiver Auto-Download-Scraper

**Script:** `scrapers/quiver_scraper.py`
**Quelle:** Quiver Quantitative (headless Playwright Login)
**Output:** `data/raw/congress-trading-all.xlsx`
**Status:** Produktiv, täglich 03:00 via systemd-User-Timer (`quiver-import.timer`, `Persistent=true`)

**Zweck:** Automatisierter Headless-Download der Congress-Trading-XLSX von Quiver.
Ohne manuellen Eingriff; verkettet direkt mit der Import-Pipeline (Scraper-Exit ≠ 0 ⇒ kein Import).

**CLI-Flags:**
```
--no-headless       Browser sichtbar (Debug/Selektoren-Abgleich), headless=False
--dry-run          Download in Temp-Datei, Zieldatei NICHT ersetzen
```

**Exit-Codes:**
```
0   Erfolg: congress-trading-all.xlsx aktualisiert
1   Generischer Fehler (Netzwerk, FileIO, Fehler im Script)
2   Login fehlgeschlagen (falsche Credentials / 2FA-Challenge / geblockter Account)
3   Eingeloggt aber Download fehlgeschlagen (abgelaufenes Abo, keine Datei vorhanden)
```

**Fehlerklassen (getrennt):**
- `QuiverLoginError` (Exit 2): Verifizierung eines „Post-Login-Elements" misslingt
- `QuiverDownloadError` (Exit 3): Login erfolgreich, aber Download/Plausibilität-Check fehlgeschlagen

**Ablauf:**
1. Playwright headless starten (PLAYWRIGHT_BROWSERS_PATH konfigurierbar)
2. Login-Seite öffnen → Email/Passwort eintragen → Login-Button klicken
3. Warten auf Post-Login-Element (Selektor aus `config/settings.py`) — prüft 2FA-Challenge
4. Congress-Trading-Seite laden, download-Event abfangen via `expect_download()`
5. Plausibilität: Dateigröße > 0
6. Atomar speichern: Download nach `.congress-trading-all.xlsx.tmp`, dann `os.replace()` auf `congress-trading-all.xlsx`
7. Logging: `logs/quiver_scraper.log` (Format `%(asctime)s [%(levelname)s] %(name)s: %(message)s`)

**Wartet auf Netzwerk/UI:**
- `wait_for_selector()` + `networkidle` (kein fixes sleep)
- `expect_download()` (erstmalige Nutzung von Playwright Download im Projekt)

**Selektor-Konfiguration** (in `config/settings.py`, Sektion „QUIVER-AUTO-DOWNLOAD-SCRAPER"):
```python
QUIVER_EMAIL_SELECTOR           # z.B. "input#email"  — MUSS vom User via `playwright codegen` befüllt werden
QUIVER_PASSWORD_SELECTOR        # z.B. "input#password"
QUIVER_SUBMIT_SELECTOR          # z.B. "button[type='submit']"
QUIVER_POST_LOGIN_SELECTOR      # z.B. "a[href='/congress-trading']"  — testet erfolgreiches Login
QUIVER_TWOFA_SELECTOR           # z.B. ".mfa-input"  — 2FA-Feld (falls vorhanden → QuiverLoginError)
QUIVER_DOWNLOAD_SELECTOR        # z.B. "button:has-text('Download')"
```

**Leere Selektoren führen zu Abbruch:** Wenn Selektoren nicht gesetzt sind (oder `None`/empty-String),
bricht der Scraper mit `QuiverLoginError` ab, statt zu hängen.

**Umgebungsvariablen / Credentials:**
- `QUIVER_EMAIL` — aus `.env` (gitignored)
- `QUIVER_PASSWORD` — aus `.env` (gitignored)
- `.env.example` enthält Vorlagen mit leeren Werten

**Scheduler (systemd-User-Timer):** `quiver-import.timer` triggert täglich 03:00
`quiver-import.service`. Die Service-Unit führt Scraper + Import als zwei
`ExecStart`-Schritte aus; schlägt der Scraper fehl (Exit ≠ 0), wird `import_trades.py`
**nicht** ausgeführt (entspricht `&&`). `Persistent=true` holt verpasste Läufe nach.
Einrichtung/Bedienung (Single-Source): `deploy/systemd/README.md`.

**Abhängigkeiten:**
- `playwright` (Chromium headless)
- `config/settings.py` — Selektor-Konstanten + Login-URL/Congress-URL
- `.env` — QUIVER_EMAIL, QUIVER_PASSWORD (Vorlage: `.env.example`)

**Tests:** `tests/test_quiver_scraper.py` (8 Unit-Tests, keine echte Browser-Instanz)

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

## Pipeline 3c: Committee-Roster-Producer (F2-Format)

**Erzeugen die `data/mappings/{house,senate}_member&(sub)committees.csv`-
Roster; die Stage `--stage_committees` (Pipeline 5) liest sie nicht mehr
(sie nutzt `data/reference/`, gebaut mit `build_reference_data.py`).**

**Senate** (`scrapers/senate_committee_xml/`):
```bash
python scrapers/senate_committee_xml/fetch_senate_xml.py   # → raw/*.xml + _manifest.json (committet)
python scrapers/senate_committee_xml/parse_senate_xml.py [out_pfad]  # → senate_member&(sub)committees.csv (F2, 100 Senatoren)
```
Dynamische Committee-Discovery aus dem senate.gov-Dropdown (kein
Hardcoding), Download via curl_cffi (Chrome-Impersonation gegen
Akamai-403). **Ersetzt** den alten
`scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py`
(LEGACY/retiriert, samt PDF).

**House** (`scrapers/house_pdf_parsing_tool/`):
```bash
python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py --pdf <pfad/scsoal.pdf> [--out <pfad>]
```
Generalisierter Parser für alle 20 Standing + 2 Select (Intelligence mit 6
Subs, China) + 4 Joint Committees (House-Hälfte; Senatoren aus dem
Senate-XML) → F2-CSV, 436 Member, 26 Committees. Quelle `scsoal.pdf`
(clerk.house.gov, committet).

**Struktur-Validierung** (beide Kammern):
```bash
python scrapers/generate_committee_structure.py   # → data/processed/committees_structure.csv (gitignored)
```
Gegen Struktur-Orakel (`data/mappings/us_{house,senate}_all_committees.csv`,
untracked) validierbar via `scrapers/house_pdf_parsing_tool/validate_house.py`.

**Format-Spec:** F2-Format (`;`/`:`/`~`/`|`), Single-Source:
`scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md`.

> **WICHTIG — nach Änderung der Referenzdaten (`data/reference/`):** Pipeline
> vollständig neu laufen lassen (`python scripts/import_trades.py`, s. Pipeline 5).

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

## Pipeline 5: Import-Pipeline (Hauptpipeline, 18 Stages)

**Script:** `scripts/import_trades.py`
**Input:** `data/raw/congress-trading-all.xlsx` (Quiver-Snapshot)
**Output:**
  - `data/processed/trades_master_complete.csv` (vollstaendige Spalten, Rebuild T3)
  - `data/processed/trades_master.csv` (kuratierte Sicht via `settings.CURATED_COLUMNS`)
  - `data/processed/manual_review_tickers.csv` (Diagnose-Output)
  - `data/processed/unmatched_names.csv` (bei Bedarf)

**Modi (schliessen sich gegenseitig aus):**
- `python scripts/import_trades.py` — Vollauf (schreibt complete + curated + review + suspicious_*.csv)
- `python scripts/import_trades.py --dry_run` — Vollauf → trades_stage.csv + Event-Stage-Dateien (Master unberuehrt)
- `python scripts/import_trades.py --save` — uebernimmt den Dry-Run: Event-Stage → suspicious_*.csv, dann trades_stage.csv → complete + curated

**Bonus-Flags (kein Master-Touch, exklusiv):**
- `--manual_review_only` — regeneriert `manual_review_tickers.csv` aus complete-Master
- `--regenerate_curated` — regeneriert `trades_master.csv` aus complete-Master

**Flag-Matrix:**
| Aufruf | Input | Output | Master-Backup |
|--------|-------|--------|---------------|
| (kein Flag) | Quiver | complete + curated + review + suspicious_*.csv | ✅ |
| `--dry_run` | Quiver | trades_stage.csv + Event-Stage-Dateien + review | ❌ |
| `--save` | trades_stage.csv + Event-Stage-Dateien | complete + curated + suspicious_*.csv | ✅ |
| `--manual_review_only` | complete | review (only) | ❌ |
| `--regenerate_curated` | complete | curated (only) | ✅ |

**Ablauf Full Rebuild:**
```
[1] Lade Quiver-Trades
    → Spalten-Rename via QUIVER_COLUMN_MAP
         ↓
[2] Datum normalisieren + trade_id vergeben
    → Datum-Fallback: fehlende/future transaction_date ← disclosure_date
    → trade_id = sha256(content_columns)[:16] (utils/trade_id.compute_trade_ids)
         ↓
[3] Committee-Member-Enrichment (enrich_member_data)
    → Verknüpfung über bio_guide_id + Kongress des Transaktionsdatums (utils/reference_data.py, data/reference/)
    → Neue Spalten: Member_*, Member_Congress, Member_Committee_Status
         ↓
[4] Ticker-Klassifikation (offline, Rebuild T3)
    → utils/ticker_classifier.classify(ticker, asset_name)
    → Neue Spalten: ticker_type, ticker_base, classification_*,
      exch_code, series_letter, status_suffix, manual_review_flag
    → Schreibt manual_review_tickers.csv (aggregiert, sortiert nach Häufigkeit)
         ↓
[5] OpenFIGI-Normalisierung (Rebuild T3 + T5/P5 + P5.5)
    → utils/ticker_normalizer.TickerNormalizer.normalize_batch()
    → Neue Spalten (Suffix _figi): figi, ticker_figi, asset_name_figi,
      security_type_figi, market_sector_figi, share_class_figi
    → P5.5: zusaetzliche abgeleitete df-Spalte `issuer_ticker` (vektorisiert) =
      share_class_figi → ticker_base → ticker. Garantiert 100% nicht-leer
      solange mindestens eine der drei Quellen gesetzt ist. Wird in Stage [10]
      Sequenz-Grouping und Stage [12] Asset-Aggregation als Gruppen-Key
      verwendet (Share-Class-Level). Liegt sowohl in trades_master_complete.csv
      als auch (direkt nach `ticker`) in trades_master.csv (kuratiert).
    → Type-Korrektur: STOCK + securityType2='ETP' → ETF
    → P5-Router-Erweiterung: YFINANCE_DIRECT und MANUAL_REVIEW gehen jetzt durch
      einen optimistischen /v3/mapping-Versuch (vorher: kein OpenFIGI-Call).
      Negativ-Cache via source='empty' verhindert Wiederholungen.
      SKIP (OPTION, TREASURY, CRYPTO, WHEN_ISSUED, INVALID) bleibt unveraendert.
    → P5-Cache-Migration: alte Cache-Hits mit figi NOT NULL aber NULL
      share_class_figi werden als Cache-Miss behandelt → einmaliger Re-Fetch
      fuellt das fehlende Feld nach.
    → Dedup-Schicht in normalize_batch: identische TickerClassifications werden
      einmal verarbeitet und das Resultat auf alle Vorkommen expandiert; FIGI_MAPPING-
      Misses schreiben einen source='empty' Negativ-Cache (TTL TICKER_TYPE_UNKNOWN_TTL_DAYS).
      Verhindert wiederholte /v3/mapping- und /v3/search-Calls bei haeufig
      vorkommenden Tickern (z.B. AAM-PA, SEAL-PB) innerhalb eines Laufs und
      ueber Laeufe hinweg.
         ↓
[6] Cross-Class Issuer Mapping (P5.6, neue Stage)
    → utils/issuer_group_mapper.IssuerGroupMapper.resolve_issuer_group_key()
    → Neue Spalte: issuer_group_key (Issuer-Level, Cross-Class via SEC EDGAR CIK)
    → Entscheidungsbaum:
        SKIP-Klassen (option/bond/...) → "SKIP:<type>"
        ETF/Foreign-Stock              → "TICKER:<issuer_ticker>"  (Bypass)
        EDGAR-Treffer                  → "CIK:<cik>"               (Cross-Class)
        Fallback                       → "TICKER:<ticker_base>"
    → GOOGL und GOOG bekommen denselben CIK:1652044 (Alphabet).
    → Cache: data/cache/edgar_cik_map.json, TTL 7 Tage.
    → Bei API-Failure: strikter Abbruch (kein Fallback auf veralteten Cache).
    → Doku: organisation/tool_specific_md/Ticker_Architektur.md
         ↓
[7] yfinance Sector/Industry (Rebuild T3)
    → utils/ticker_sector.TickerSectorEnricher.enrich_batch()
    → Neue Spalten (Suffix _yf): sector_yf, industry_yf, quote_type_yf
    → Preferred-Auto-Resolution via parent_ticker (EP$C → parent_ticker='EP' → yfinance(EP))
    → Netz-Robustheit (2026-06-14): leeres yfinance-`.info` ohne Identitaet wird via
      utils/network_canary.py (NetworkCanary-Verdikt) disambiguiert — ROT ⇒ transient
      (Retry), GRUEN ⇒ echtes Delisting. Ersetzt die zirkulaere `_network_healthy`-
      Heuristik (jetzt LEGACY). Vor dem Schreiben von delisted_trades_stage.csv wird
      eine frische force_reprobe() erzwungen (ROT ⇒ Datei wird nicht geschrieben).
      Details: organisation/tool_specific_md/Briefing_stage_post_trade_performance.md
         ↓
[8] Post-Trade Performance (Stage post_trade_performance, **2026-06-04: Exit-Entkopplung; 2026-06-06: Paper Returns**)
    
    **Sub-Phase A: Post-Trade Returns (historische Performance geschlossener Positionen)**
    → scoring/post_trade_returns.compute_post_trade_returns(df, horizons_days, exit_window_days, cache_path)
    → Spalten (19 = 16 bisher + 3 neu: post_price_180d, post_return_180d, first_close_offset_days):
      post_trade_baseline_price, post_price_30d/60d/90d/120d/180d/270d/365d,
      post_return_30d/60d/90d/120d/180d/270d/365d, first_close_offset_days, exit_status, exit_date, exit_price,
      exit_amount_max_usd, effective_return_pct, effective_return_horizon_days,
      opposing_trade_ids
    → **Exit-Fenster-Entkopplung (2026-06-04):**
        - Settings.EXIT_DETECTION_WINDOW_DAYS (Default None=unbegrenzt) entkoppelt vom Return-Horizont
        - Exit-Suche nutzt exit_window_days (kann >120 Tage sein oder unbegrenzt)
        - Return-Snapshots bleiben an POST_TRADE_HORIZONS_DAYS gebunden
        - Horizonte 270d + 365d hinzugefuegt (2026-07-04, dynamisch mit POST_TRADE_HORIZONS_DAYS;
          Refetch-Fenster POST_TRADE_REFRESH_WINDOW_DAYS 199→384 = 365+19 Puffer)
        - Neue Spalte first_close_offset_days: Tage bis ERSTEM Gegen-Trade (Hilfe für Sichtbarkeitsfilter)
    → **Semantik held→NaN (2026-06-04):**
        - effective_return_pct: Nur bei closed_* gesetzt; held/delisted/unknown/not_applicable → NaN
        - effective_return_horizon_days: Nur bei closed_* gesetzt; sonst NaN
    → Exit-Detection: gruppiert nach (name, issuer_group_key), chronologisch
    → Preisdaten von yfinance, gecached in data/cache/price_history.parquet
      (MultiIndex [ticker, date], Spalte close, TTL 7 Tage)
    → Kanonische Richtungserkennung via _direction_from_transaction_type (handling Sale/Sale (Full)/Sale (Partial)/SALE)
    → **Netz-Robustheit-Haertung (2026-06-14):** Variante-B-Fenster-State-Machine — das
      ganze Preis-Fenster ist vorlaeufig, nichts wird vor einem GRUEN-Verdikt
      (utils/network_canary.py) in den Cache geflusht. ROT/Throttle (inkl. HTTP 429)
      → gesamtes Fenster verworfen + ab Fenster-Beginn im selben Lauf neu verarbeitet
      (rueckwirkende Invalidierung). Fail-closed nach CANARY_MAX_RED_ROUNDS (30) ROT-
      Runden → RuntimeError, keine degradierten Daten geschrieben.
      Delisted-Marker-Schema geaendert: (ticker, last_checked_date=heute, close=NaN)
      statt (ticker, 1900-01-01, NaN); Erkennung ueber NaN-close. last_checked-TTL
      POST_TRADE_DELISTED_RECHECK_DAYS (30 Tage) steuert Re-Check-Frequenz delisteter
      Ticker. exit_status='delisted' wird jetzt aus dem Preis-Lookup abgeleitet (None
      ⇒ delisted). Details: organisation/tool_specific_md/Briefing_stage_post_trade_performance.md
    
    **Sub-Phase B: Paper Returns (Mark-to-Market für offene/frische Positionen, DELTA-UNABHÄNGIG)**
    → scoring/post_trade_returns.compute_paper_returns(df, cache_path, recent_window_days, max_price_age_bdays)
    → Spalten (2 neu): paper_return_pct, price_as_of
    → Fenster: RECENT_MONITORING_WINDOW_DAYS (730d seit 2026-08-04, vorher 180d; konfig. in settings.py)
      Anhebung, damit der Recent-Tab auch 365/730-Tage-Fenster mit Papier-Return füllen kann.
      Kosten: mehr Ticker im Frische-Refetch (814 → 1.685), davon ~95 % bereits im Preis-Cache.
      Nur Trades mit transaction_date >= now - recent_window_days befüllt
    → Richtungsbewusst: Vorzeichen-Inversion für Verkäufe (sell_sign = -1)
    → Cache-Frische: RECENT_PRICE_MAX_AGE_BUSINESS_DAYS (Default 1 BDay; relevant für 03:00-Daily-Lauf)
      Refresht yfinance nur für Recent-Ticker mit Business-Day-Cutoff (z.B. Nachts-Lauf sieht Vortagsclose)
    → VOLL-PASS über alle Trades (Delta-unabhängig) bei jedem Lauf, da Aktuell-Preis täglich ändert
      Auch long-importierte offene Positionen bekommen neuen Mark-to-Market
    → Tests: test_post_trade_returns.py (compute_paper_returns, _sell_sign_series, _latest_close_map, _apply_paper_returns)
    → Reihenfolge-Begruendung: nach tickeryfinance (Sektor-Daten bereits vorhanden),
      vor trade_volume (Volume unabhaengig von Preis-Daten)
         ↓
[8b] Abnormal Return (Stage `abnormal_returns`, deskriptiv — KEIN Scoring-Input)
    → scoring/abnormal_returns.compute_abnormal_returns(df, cache_path, AR_*-Settings)
    → Neue Master-Spalten (Konstrukt A): signed_ar_{30,60,90,120,180}d (Bruchteil, 0.03 = 3 %)
        signed_ar = sell_sign × ((P_ticker(t0+H)/P_ticker(t0)−1) − (P_SPY(t0+H)/P_SPY(t0)−1))
    → Teilt den Preis-Cache mit [8]; pro Trade deterministisch (delta-sicher).
    → NUR signed_ar_{H}d in den Master; Outlier-Flag/Perzentil-Rang werden in der App bei der
      Anzeige abgeleitet, Konstrukt B (signed_ar_disc_*) nur im Standalone-Report (Pipeline 7).
    → Im Full-Run als Schritt [9b], im Delta-Run als [10b] (auf den Delta-Trades).
    → Spalten + App-Anzeige: DATA_STRUCTURE.md Abschnitt 7e.
         ↓
[9] Trade-Volume (Modul 8 — Stage `trade_volume`)
    → utils/amount_parser.midpoint_usd(amount) und max_usd(amount) — pro
      Trade zwei Spalten aus der Disclosure-Amount-Range.
    → Neue Spalten:
        - trade_volume_usd (int64): Mittelpunkt, Eingabe für [10].
        - trade_amount_max_usd (int64): Obergrenze, Score-Basis für das
          trade_volume-Signal (Stage [11]).
    → Beispiele (Mittelpunkt / Obergrenze):
        "$1,001 - $15,000"  → 8000 / 15000
        "$15,001 - $50,000" → 32500 / 50000
        "Over $50,000,000"  → 50_000_000 / 50_000_000 (konservativ)
        "$94.91"            → 94 / 94 (Single-Wert)
        ""/NaN/unparsebar   → 0 / 0
         ↓
[10] Asset-Tradingvolume (Modul 8, Phase 8.2 — neue Stage)
    → scoring/asset_volume.compute_asset_tradingvolume(df, window_days_z)
    → Neue Spalte: asset_tradingvolume_usd (int64)
    → Cross-Class rolling-window Sum(trade_volume_usd), gruppiert nach
      issuer_group_key, im Fenster [T.transaction_date - Z, T.transaction_date]
      mit Z = settings.SCORING_VOLUME_WINDOW_DAYS_Z (Default 90).
    → Tages-Aggregation: alle Trades am gleichen Tag in derselben Gruppe
      sehen denselben asset_vol-Wert (Schwarm-Konsistenz). rolling closed='both'
      schliesst beide Fenster-Enden ein.
    → SKIP-Buckets (SKIP:OPTION, SKIP:BOND, ...): asset_vol = 0.
    → Edge-Cases (NaT-Datum, leerer issuer_group_key): asset_vol = 0.
    → Delta-Mode: Master-Historie aus trades_master_complete.csv wird
      mitgeladen, damit das 90d-Window korrekt zurueckblickt; nur die
      Delta-Zeilen werden zurueckgeschrieben (Option A — siehe
      _stage_asset_volume).
    → GOOG+GOOGL aggregieren ueber CIK:1652044 (Cross-Class verifiziert).
         ↓
[11] Filter-Logik (_apply_filter_logic)
    → Pre-Hook _ensure_fresh_vote_data() prueft Alter von bill_actions.json/
      bill_subjects.json. Liegt eine Datei aelter als settings.VOTE_DATA_TTL_HOURS
      (Default 24h), wird scrapers/fetch_congress_data.CongressFetcher.run_update()
      programmatisch ausgeloest. Fehler (kein API-Key, Netzwerk, fehlender Erstlauf)
      werden geloggt, die Pipeline laeuft mit dem letzten bekannten Stand weiter.
      Lock-Schutz: Liegt data/congress_api/.force_in_progress vor (anderer Fetcher-
      Prozess laeuft, z.B. manueller --force in einem zweiten Terminal), wird der
      Auto-Refresh komplett uebersprungen — Filter 4 nutzt die vorhandene JSON.
      Schema-Migrate: Beim Resume-Laden der bill_summaries.json/bill_subjects.json
      werden Alt-Eintraege ohne 'has_summary'/'subject_count' (Felder nachtraeglich
      eingefuehrt) automatisch nachgeruestet — verhindert KeyError im Progress-Print.
    → Insider_Sectors / Relevant_Sectors (CommitteeTag — User-Sicht „Filter 3")
      Insider_Sectors  = JSON-Liste ALLER (sub)committee-Sektoren des Members
      Relevant_Sectors = Intersection Insider × sector_yf/industry_yf, jeder Eintrag
                         mit Feld match_type ('full' oder 'sector')
                         (Match-Mode via settings.RELEVANT_SECTORS_MATCH_MODE,
                          Default: sector_and_industry)
      Relevance_Score entfernt (Refactor 2026-05-21)
    → asset_class_excluded (AssetClassGate — User-Sicht „Filter 0": prueft ASSET_CLASS_KEEP_TYPES = {stock, foreign_stock, shareclass})
    → Vote_Timing_Hit / Vote_Timing_Hits / Vote_Timing_Hit_Count (VoteTimingTag — User-Sicht „Filter 4")
      Quelle der Trade-Sektoren ist Relevant_Sectors (Intersection!), damit
      Sektor-Treffer am Ticker haengen, nicht nur am Committee. Alle Treffer
      im Fenster werden als JSON-Liste gespeichert.
         ↓
[12] Branch-1 Signale (_apply_branch1_signals)
    → Fünf Per-Trade-Signale: committee, committee_full, committee_ex_officio,
      floor_action, extreme_report (signals/*.py)
      committee triggert bei len(Relevant_Sectors) > 0
      committee_full triggert zusätzlich wenn mind. 1 Eintrag match_type='full'
    → Schwellwerte aus settings.py: VOTE_TIMING_HIT_THRESHOLD, EXTREME_REPORT_DAYS,
      EXTREME_REPORT_FAST_DAYS
    → Neue Trade-Spalten: signal_count (int 0-4), signals_triggered (str, ";"-getrennt)
    → Auslöse-Events werden in data/processed/branch1_events_stage.csv
      geschrieben (Snapshot pro Auslösung, 21 Spalten inkl. issuer_ticker,
      issuer_group_key und asset_name_figi; siehe
      organisation/tool_specific_md/signal_info.md).
      Diese Datei wird in einer spaeteren Suspicious-Export-Stage (P4) in
      suspicious_events.csv promoted.
         ↓
[13] Sequenz-Signale (_apply_sequence_signals)
    → Zwei Gruppen-Signale: contrarian, accumulative (signals/*.py)
    → Gruppierung: (bio_guide_id, issuer_ticker) via
      utils/issuer_ticker.resolve_issuer_ticker — Fallback-Kette
      share_class_figi → ticker_base → ticker (vektorisiert in Stage 5
      bereits als df-Spalte materialisiert)
    → Parameter aus settings.py: CONTRARIAN_WINDOW_DAYS,
      ACCUMULATIVE_WINDOW_DAYS, ACCUMULATIVE_MIN_COUNT
    → Aggregiert additiv in signal_count + signals_triggered pro Trade
      (dedupliziert pro Trade — ein Typ wird nur einmal gezaehlt, auch wenn
      er in mehreren Fenstern desselben Trades getriggert wuerde)
    → Events nach data/processed/sequence_events_stage.csv (gleiche
      21-Spalten-Form wie branch1_events_stage.csv, inkl. issuer_group_key)
         ↓
[14] Schwarm-Signale (_apply_swarm_signals)  -- P6, Branch 2
    → Vier Issuer-Group-Signale: swarm_buy, swarm_sell, swarm_conflict,
      swarm_paradox (signals/swarm_*.py)
    → Gruppierung: settings.SWARM_GROUPING_KEY (= 'issuer_group_key',
      Issuer-Level via SEC EDGAR CIK, Cross-Class — GOOGL+GOOG sind
      ein Schwarm). SKIP-Buckets (Options/Bonds/Cash) werden gebypassed.
    → Parameter aus settings.py: SWARM_WINDOW_DAYS, SWARM_MIN_MEMBERS,
      SWARM_CONFLICT_WINDOW_DAYS_BEFORE/_AFTER, SWARM_PARADOX_MIN_CONFLICTS
    → swarm_buy/_sell: Cluster aus >=N distinct Members im Rolling-Window;
      eine signal_event_id pro Cluster, alle beteiligten Trades als Zeilen
    → swarm_conflict: GENAU 1 Gegen-Trade im erweiterten Fenster
      [first_swarm - BEFORE, last_swarm + AFTER] -> 1 Event-Zeile
    → swarm_paradox: >= PARADOX_MIN_CONFLICTS Gegen-Trades
      (mutually exclusive zu conflict) -> 1 signal_event_id mit N Zeilen
    → Aggregiert additiv in signal_count + signals_triggered (dedupliziert)
    → Events nach data/processed/swarm_events_stage.csv (21 Spalten)
    → **Delta-Kontext-Fix (2026-07-02):** Im Delta-Modus (`--delta`) laden beide Stages
      ([13] Sequenz-Signale und [14] Schwarm-Signale) jetzt zusaetzlich die Master-Historie als
      Gruppierungs-Kontext (Union, analog zu Stage [10] Asset-Volume) — historische Trades
      liefern nur Kontext (keine Events/Aggregate), Events/Aggregate entstehen weiterhin nur
      fuer Delta-Zeilen. Vorher sah eine neue Delta-Charge Gruppen isoliert (< 2 Mitglieder) und
      contrarian/accumulative/swarm_buy/swarm_sell/conflict/paradox loesten im Produktivbetrieb
      (taeglicher Delta-Lauf) systematisch nicht aus (`swarm_buy` ist Primaersignal!). Gilt fuer
      `run_delta()` und Einzelstage-Delta (`--delta --stage_sequence_signals` /
      `--delta --stage_swarm_signals`), Helper `_load_signal_history` in `scripts/import_trades.py`.
         ↓
[15] Trade-Score (Modul 8 — _stage_scoring)
    → scoring/trade_score.compute_trade_scores(df, settings)
    → Drei neue Spalten:
        trade_score             — int, gewichteter Score nach Formel
                                   round(swarm × PARADOX_DAMPER + other +
                                         volume_tier + ex_officio_bonus)
        trade_score_breakdown   — ;-getrennte Komponenten in fester Reihenfolge
                                   (Branch-1 → Sequenz → Schwarm → trade_volume → ex_officio).
                                   trade_volume erscheint nur wenn das Signal getriggert hat.
        trade_warnings          — Warning-Tags (aktuell: paradox_swarm)
    → Eingaben: signals_triggered (aus [12]/[13]/[14]), Member_ExOfficio,
      trade_amount_max_usd (aus [9]).
    → Parameter aus settings.py: SCORING_WEIGHTS, SCORING_PARADOX_DAMPER,
      SCORING_EX_OFFICIO_ROLE_BONUSES, SCORING_EX_OFFICIO_COMMITTEE_GLOBAL,
      SCORING_EX_OFFICIO_TRIGGERS, SCORING_TRADE_VOLUME_ENABLED,
      SCORING_TRADE_VOLUME_TIERS, SCORING_TRADE_VOLUME_TRIGGER_FLOOR.
    → ex_officio-Bonus = MAX über die Boni der in ex_officio_role_keys
      vorhandenen Keys (nur wenn Trigger-Signal getriggert hat).
    → Score = 0 bei trade ohne Signal-Hit; kein Cap.
         ↓
[16] Suspicious-Export (_stage_suspicious_export, + Modul 8.4)
    → Liest branch1_events_stage.csv + sequence_events_stage.csv +
      swarm_events_stage.csv
    → Append nach data/processed/suspicious_events.csv mit
      (signal_event_id, trade_id)-Dedup (Idempotenz)
    → Materialisiert suspicious_assets.csv (Y-Tage-Verfall via
      settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS, Default 30) — inkl.
      Score-Aggregat-Spalten score_avg/score_max/score_total/total_volume_usd/
      warnings (Helper _load_trade_scores + _join_distinct_warnings).
      Aggregation auf distinct trade_id pro issuer_ticker.
    → Materialisiert suspicious_members.csv (kein Filter) — inkl. drei
      Score-Aggregat-Spalten score_avg/score_max/score_total (analog,
      kein total_volume_usd / warnings).
    → dry_run: schreibt nichts, gibt nur Vorschau-Counts aus
    → Mutiert trades_stage.csv NICHT (signal_count/signals_triggered
      werden bereits in Stage [12]/[13] gesetzt)
         ↓
[17] Speichern
    → Backup: archive/trades_master_backup/trades_master[_complete]_{ts}.csv
    → trades_master_complete.csv (alle Spalten, neu geschrieben — inkl.
      trade_volume_usd, trade_amount_max_usd, asset_tradingvolume_usd,
      trade_score, trade_score_breakdown, trade_warnings aus Modul 8)
    → trades_master.csv (kuratiert via settings.CURATED_COLUMNS — inkl.
      signal_count + signals_triggered + alle 6 Modul-8-Spalten)
    → CURATED_COALESCE (Bugfix 2026-07-02): `Member_Committees` existiert im Master nur als
      `Member_Committees_Full` — der kuratierte Export befuellt sie jetzt per Coalesce
      (erstes nicht-leeres Element der Quellspalten-Liste). Vorher war die Spalte immer leer.
    → manual_review_tickers.csv (falls Tickers mit manual_review_flag)
    → _promote_problem_outputs(delta) — siehe Abschnitt unten
        - unmatched_names.csv
        - delisted_trades.csv
```

### Problem-Output-Promotion (Mai 2026)

`_promote_problem_outputs(delta)` (in `scripts/import_trades.py`) kümmert sich um drei Diagnose-Dateien einheitlich:

| Lauf-Typ | Verhalten |
|----------|-----------|
| **Full-Run** (kein `--delta`) | Stage → Live overwrite. Stage fehlt/leer → Live wird gelöscht (Auto-Purge). |
| **Delta-Run** (`--delta`) | Alte Live-Datei + neue Stage-Datei werden gemerged via natürlichem Schlüssel; anschließend re-validiert (gefixte Einträge fallen raus). |
| **Dry-Run** | Schreibt nur `*_stage.csv`. Live-Dateien bleiben unangetastet. |

**Re-Validation-Quellen** (Delta-Modus):
- `unmatched_names.csv` ↔ `data/reference/committee_memberships.csv` (über `bio_guide_id` + Kongress)
- `delisted_trades.csv` ↔ positiver `ticker_industry_cache.json` (cache-only, kein API-Call)

**`manual_review_tickers.csv`** wird im Live-Pfad immer aus dem vollen gemergten Master aggregiert (Re-Validation läuft implizit über Filter `ticker_type!='ignored'` + `manual_review_flag=True`). Im Delta-Dry-Run zeigt die Datei eine Vorschau des Endzustands (`existing-master ∪ delta_enriched`).

Stage-Dateien (`unmatched_names_stage.csv`, `delisted_trades_stage.csv`) werden nach Promotion automatisch entfernt.

**Hinweis:** Die Pipeline ist ein reiner Enrichment-Durchlauf — es werden keine Zeilen entfernt.

**Dedup-Strategie (`DEDUP_STRATEGY` in settings.py):**
- `'keep_all'` (Default): Bei mehreren Zeilen mit gleicher `trade_id` innerhalb eines Imports
  bleiben alle erhalten (z.B. Splits, identische Ticker am gleichen Tag). `trade_id` ist dann
  KEIN eindeutiger Schlüssel im Master.
- `'last_wins'`: Nur die letzte Zeile pro `trade_id` wird behalten.
- Wirkt in `_merge_into_master` (`scripts/import_trades.py`) und `scripts/migrate_add_trade_ids.py`.
- Delta-Erkennung zwischen Läufen (`detect_new_trades`) respektiert dieselbe Strategie
  (Multiset-Differenz, s.u.).

**Delta-Erkennung (`detect_new_trades` in `utils/trade_id.py`):** Berechnet die Multiset-Differenz
Input − Master via `groupby('trade_id').cumcount()`. Hat eine `trade_id` im Master m Kopien und im
Input i Kopien, gelten die (i − m) überzähligen Input-Vorkommen als neu. Damit bleibt
`keep_all`-treues Verhalten erhalten: legitime Mehrfach-Vorkommen derselben `trade_id`
(z.B. gleichzeitige Trades in identischem Ticker) gehen im Delta-Modus nicht verloren.
Normalfall (rein neue oder rein unveränderte `trade_id`s) ist verhaltensidentisch zur früheren
Set-Membership-Logik.

**Master-Sortierung:** Jeder Master-Schreibvorgang (staged via `_stage_save` / `_write_stage_output`
und monolithisch via `run()` / `run_delta`) sortiert den Master absteigend nach `transaction_date`
(Helper `_sort_master_by_date` in `scripts/import_trades.py`). Tiebreak innerhalb desselben Datums =
Eingabe-/Quell-Reihenfolge (Quiver liefert bereits absteigend). Zeilen ohne parsebares
`transaction_date` (NaT) wandern ans Ende. Die CSV-Serialisierung des Datums bleibt unverändert.
`trades_master.csv` (Curated) erbt die Reihenfolge, da er aus dem bereits sortierten Complete
abgeleitet wird.

**Bedienung (Beispiele):**
```bash
# Dry-Run: komplette Pipeline ohne Master-Touch, dann uebernehmen:
python scripts/import_trades.py --dry_run
python scripts/import_trades.py --save

# Bonus-Flags (kein Master-Touch ausser Curated):
python scripts/import_trades.py --manual_review_only      # review aus complete
python scripts/import_trades.py --regenerate_curated      # curated aus complete
```

**Abhaengigkeiten:**
- `utils/reference_data.py` — Member-Anreicherung über `bio_guide_id` + Kongress, `ReferenceMapper` (Ausschuss → Industries je Kongress); Quelle `data/reference/`
- `tags/committee_tag.py` — CommitteeTag (User-Sicht „Filter 3"): Committee-Relevanz-Scoring
- `gates/asset_class_gate.py` — AssetClassGate (User-Sicht „Filter 0"): Nicht-Aktien flaggen (asset_class_excluded)
- `tags/vote_timing_tag.py` — VoteTimingTag (User-Sicht „Filter 4"): Vote-Timing (respektiert asset_class_excluded)
- `config/committee_mapping.py` — O(1) Subcommittee→Sektor Lookup (nur noch von der App genutzt, nicht von der Pipeline)
- `config/settings.py` — Alle Schwellenwerte und Pfade
- `data/mappings/ticker_industry_cache.json` — yfinance-Cache (Ticker→Sector/Industry)
- `yfinance` (Python-Paket) — Sector/Industry Lookup via Yahoo Finance API

**Member-Anreicherung (`Member_Committee_Status`):**
| Status | Bedeutung |
|--------|-----------|
| `matched` | Mitgliedschaft im Kongress des Transaktionsdatums gefunden |
| `no_membership` | Kongress abgedeckt, aber keine Mitgliedschaft (→ `unmatched_names.csv`) |
| `congress_not_covered` | Trade vor dem 118. Kongress (vor 2023-01-03), keine Ausschüsse |

Von der Pipeline nicht mehr gelesen: `utils/name_matcher.py`, `name_aliases.csv`, Roster-CSVs `house_/senate_member&(sub)committees.csv`.

---

## Pipeline 6: XLSX→CSV Konvertierung (entfernt, Debug-Intervall 4)

`scripts/convert_xlsx_to_csv.py` wurde entfernt: die Input-xlsx existieren nicht
mehr, der Mapping-Master `subcommittee_sector_industry_mapping.csv` wird heute
vom Committee-Mapping-Agent erzeugt (s. Pipeline 11). Heading bleibt fuer die
stabile Pipeline-Nummerierung stehen.

---

## Pipeline 8: Foundation- & Network-Module (Rebuild Teil 1+2, in Vorbereitung)

**Status:** Module fertig + getestet (25.04.2026), aber **noch nicht** in
`import_trades.py` integriert. Integration erfolgt in Rebuild Teil 3.

**Spezifikation:** `rebuild_pitch.md` (Sektionen 4–7, 11, 14, 16).

### Übersicht

| Modul | Rolle | Tests |
|-------|-------|-------|
| `utils/trade_id.py` | Hash-basierte stabile Trade-ID, ersetzt `_create_dup_key` | 13 |
| `utils/ticker_classifier.py` | Offline-Regex-Klassifikator, liefert `TickerClassification` | 67 |
| `utils/ticker_normalizer.py` | OpenFIGI-Wrapper, liefert `NormalizedTicker` | 6 |
| `utils/ticker_sector.py` | yfinance-Wrapper, liefert `SectorInfo` | 6 |
| `utils/signal_event_id.py` | Hash-basierte Signal-Event-ID (SHA256[:16]) für Signal-Auslösungen (gruppiert Trades bei Schwärmen) — Konvention siehe `organisation/tool_specific_md/signal_info.md` | 14 |
| `utils/issuer_ticker.py` | `resolve_issuer_ticker(row)` — Cross-Share-Class-Aufloesung mit Fallback-Kette `share_class_figi → ticker_base → ticker`. share_class_figi (OpenFIGI shareClassFIGI) ist der Erstkandidat, faellt bei Miss zurueck auf ticker_base, dann ticker. Aufrufer (alle Signal-Module + sequence-stage Grouping) erhalten garantiert nicht-leeren Identifier solange mindestens eine der drei Quellen gesetzt ist. | 17 |
| `utils/network_canary.py` (NEU 2026-06-14) | Out-of-Band-Verdikt-Orakel fuer Netzwerk-Gesundheit: probt garantiert-live Ticker (CANARY_TICKERS) unabhaengig vom eigentlich abgefragten Ticker. `verdict()` (TTL-gecacht 60s), `force_reprobe()`, `wait_then_reprobe()`. Konsumiert von `scoring/post_trade_returns.py` + `utils/ticker_sector.py`. Details: `organisation/tool_specific_md/Briefing_stage_post_trade_performance.md` | (in `test_network_canary.py`) |
| `utils/network_retry.py` (NEU 2026-06-14) | Zentraler `retry_call`-Helfer (R1-R6): typ-basierte transient-Klassifikation (`is_transient_exception`), Backoff via `NETWORK_RETRY_BACKOFFS`, `NetworkRetryExhausted`, optionaler `is_empty`-Callback + `on_attempt_failed`-Hook. Genutzt von `utils/ticker_normalizer.py` + `utils/issuer_group_mapper.py` (ersetzt eigene Pattern-Listen). | (in `test_network_retry.py`) |
| `signals/committee_signal.py` | Branch-1 Per-Trade-Signal: triggert bei `len(Relevant_Sectors) > 0` | 8 (in `test_branch1_signals.py`) |
| `signals/committee_full_signal.py` | Branch-1 Per-Trade-Signal: triggert wenn mind. 1 Eintrag in `Relevant_Sectors` hat `match_type='full'` | (in `test_branch1_signals.py`) |
| `signals/committee_ex_officio_signal.py` | Branch-1 Per-Trade-Signal: triggert wenn CommitteeSignal + `Member_ExOfficio` | 4 |
| `signals/floor_action_signal.py` | Branch-1 Per-Trade-Signal: triggert bei `Vote_Timing_Hit_Count >= VOTE_TIMING_HIT_THRESHOLD` | 4 |
| `signals/extreme_report_signal.py` | Branch-1 Per-Trade-Signal: triggert bei `delay > EXTREME_REPORT_DAYS` ODER `delay <= EXTREME_REPORT_FAST_DAYS` (`delay = disclosure_date - transaction_date`; sehr späte ODER sehr schnelle Meldung) | 8 (in `test_branch1_signals.py`) |
| `signals/contrarian_signal.py` | Branch-1 Sequenz-Signal: triggert bei Buy↔Sell-Wende eines Members auf demselben issuer_ticker innerhalb `CONTRARIAN_WINDOW_DAYS` | 8 (in `test_sequence_signals.py`) |
| `signals/accumulative_signal.py` | Branch-1 Sequenz-Signal: triggert bei `>= ACCUMULATIVE_MIN_COUNT` gleichgerichteten Trades eines Members innerhalb `ACCUMULATIVE_WINDOW_DAYS` | 8 |

### Datenfluss (geplant)

```
DataFrame (raw_ticker, company_name, ...)
        ↓
[1] ticker_classifier.classify(ticker, company_name)
        → TickerClassification (frozen):
          ticker_type ∈ {STOCK, ETF, PREFERRED, WARRANT, OPTION, BOND, ...}
          parent_ticker — für PREFERRED: automatisch auf Common-Stamm gesetzt (z.B. EP$C → parent_ticker='EP')
          confidence ∈ {HIGH, MEDIUM, LOW}
        ↓
[2] TickerNormalizer.normalize_batch(classifications)
        → NormalizedTicker (frozen):
          figi, ticker_figi, asset_name_figi, security_type_figi,
          market_sector_figi, share_class_figi
        Routing: Klassifikation → OpenFIGI-Normalisierung. api_strategy
        bleibt intern; wird nicht persistiert.
        Type-Korrektur: STOCK + securityType2='ETP' → ETF
        ↓
[3] TickerSectorEnricher.enrich_batch(normalized)
        → SectorInfo (frozen): sector_yf, industry_yf, quote_type_yf
        Routing nach ticker_type (Set zentral in `utils/ticker_routing.py::SECTOR_LOOKUP_TYPES`):
          STOCK/SHARECLASS/FOREIGN_STOCK/ETF/NUMERIC_SUFFIX → yfinance
          PREFERRED                                          → Fallback via parent_ticker (z.B. EP$C → yfinance(EP))
          WARRANT/OPTION/BOND/CRYPTO/...                     → kein Call
```

### Caches

| Cache | Pfad | TTL | Hinweis |
|-------|------|-----|---------|
| OpenFIGI SQLite | `data/cache/ticker_type_cache.db` (Tabelle `ticker_normalizer`) | 90 Tage / 7 Tage Unknown | Neue Tabelle parallel zur alten `ticker_types` |
| yfinance positiv | `data/mappings/ticker_industry_cache.json` | `TICKER_SECTOR_CACHE_TTL_DAYS=30` | Bestand ohne `cached_at` = ewig gültig |
| yfinance null | `data/mappings/ticker_0_cache.json` | ∞ (delisted bleibt delisted) | Sortierte Liste |

### Defensiv-Mechanismen (Pitch §11)

Jeder Mechanismus ist Resultat eines echten Bugs — keine Vereinfachung.

| § | Implementiert in | Verhalten |
|---|------------------|-----------|
| 11.1 | beide | 3 Net-Errors → 30s Pause; 5 → RuntimeError; Retry-Queue |
| 11.2 | beide | Cache-Checkpoint alle 100 Lookups |
| 11.3 | sector | JSON-Split positiv/null in 2 Dateien |
| 11.5 | sector | Confirm-Prompt bei ≥100 neuen Tickern (callback-injizierbar) |
| 11.6 | sector | yfinance-Logger CRITICAL während Loop |
| 11.7 | sector | Delisted → `data/processed/delisted_trades_stage.csv`; Terminal-Report nach Jahr aggregiert (`enrich_batch(normalized, transaction_dates=...)`) |
| 11.8 | sector | Parent-Ticker-Fallback für PREFERRED automatisch; Terminal-Report nach Batch-Ende |
| 11.9 | normalizer | OpenFIGI 429 Backoff 2^n max 90s, 6 → RuntimeError; getrennte Pausen pro Endpoint (Mapping/Search) |
| 11.10 | beide | Lazy Init (DB/Caches erst bei Bedarf) |

### Terminal-Ausgabe bei Fehlern

Pro Defensiv-Mechanismus ein klarer `print(...)`:

```
[!] Netzwerk-Fehler 1/5: Could not resolve host …
[!] 3 Netzwerk-Fehler in Folge → 30s Pause...
[!!] 5 Netzwerk-Fehler in Folge → STAGE-ABBRUCH (12 Ticker in Retry-Queue)
[!] OpenFIGI 429 (Versuch 1/3) → Backoff 2.0s
[i] Kein Mapping-Hit für OBSCURE → Fallback /v3/search
[i] SPY: Type-Korrektur STOCK → ETF (securityType2=ETP)
[i] Cache-Checkpoint: 100 Lookups gespeichert
[i] Delisted/Unbekannte Ticker: 3 Eintraege → …/delisted_trades_stage.csv
```

### Tests

```
pytest tests/test_trade_id.py            13 passed
pytest tests/test_ticker_classifier.py   67 passed
pytest tests/test_ticker_normalizer.py    6 passed   alle gemockt
pytest tests/test_ticker_sector.py        6 passed   alle gemockt
```

### Abhängigkeiten

- `requests` (OpenFIGI-Calls)
- `yfinance` (Sektor-Lookups)
- `config/settings.py`: `OPENFIGI_API_KEY`, `TICKER_LOOKUP_*`,
  `TICKER_TYPE_CACHE_TTL_DAYS`, `TICKER_TYPE_UNKNOWN_TTL_DAYS`,
  `TICKER_SECTOR_CACHE_TTL_DAYS` (NEU), `OPENFIGI_BATCH_ABORT_THRESHOLD` (6),
  `OPENFIGI_RATE_LIMIT_BACKOFF_BASE/MAX` (umbenannt von `OPENFIGI_BACKOFF_*`),
  `OPENFIGI_MAPPING_PAUSE_WITH_KEY`/`_WITHOUT_KEY` (0.3 / 3.5),
  `OPENFIGI_SEARCH_PAUSE_WITH_KEY`/`_WITHOUT_KEY` (0.4 / 13.0),
  Legacy-Aliase `OPENFIGI_REQUEST_PAUSE_*` weiter gültig,
  `PREFERRED_TICKER_MAPPING_FILE`, `TICKER_INDUSTRY_CACHE_FILE`,
  `TICKER_0_CACHE_FILE`, `DEDUP_STRATEGY` (`'keep_all'` | `'last_wins'`).

**OpenFIGI Rate-Limit-Hintergrund:** `/v3/mapping` erlaubt 250 Req/6s, `/v3/search`
nur 20 Req/6s. Da der Mapping→Search-Fallback in `_process_mapping` direkt nach einem
Mapping-Burst auf das engere Search-Limit trifft, war 429 dort Standardfehler.
`TickerNormalizer` hält jetzt zwei separate Pausen (`mapping_pause`, `search_pause`)
und schiebt vor dem Fallback eine `search_pause` ein. Backoff: 2^n max 90s,
6 aufeinanderfolgende 429 → RuntimeError (vorher 3 → 60s).

### Nicht in Rebuild T1+T2

- Touch an `scripts/import_trades.py` (kommt in T3)
- Touch an `OpenFigiClient` (kommt in T4)
- Stage-Code, Member-Enrichment-Pickle-Checkpoint (§11.4)

---

## Pipeline 10: Streamlit Dashboard (App)

**Script:** `app/streamlit_app.py`
**Start-Befehl:**
```bash
# Linux/Mac
venv/bin/streamlit run app/streamlit_app.py

# Windows
venv\Scripts\streamlit run app/streamlit_app.py

# Alternativer Port / Headless
venv/bin/streamlit run app/streamlit_app.py --server.port 8502
venv/bin/streamlit run app/streamlit_app.py --server.headless true
```
**Inputs (read-only):**
- `data/processed/trades_master.csv` — Haupt-Trades-Tabelle (gecached via `@st.cache_data`)
- Im Deployment: `data/deploy/trades_snapshot.parquet`, wenn vorhanden (s. u.)

**Outputs:** grundsätzlich Lese-UI; als manueller Schreibpfad steht der optionale
Recent-Trades-CSV-Export zur Verfügung (im öffentlichen Modus ausgeblendet, s. u.).

**Deployment-Modus:** Im öffentlichen Betrieb läuft die App nicht gegen den
Master-CSV, sondern gegen einen eingefrorenen Snapshot in `data/deploy/`
(`trades_snapshot.parquet` + `price_history.parquet` + `snapshot_manifest.json`).
Der Loader nimmt den Snapshot automatisch, wenn die Datei existiert — lokal
ändert sich also nichts, solange kein Snapshot gebaut wurde. Der Snapshot
entsteht **außerhalb** dieser Pipeline, manuell nach einem
Import-Pipeline-Durchlauf:
```bash
python scripts/build_deploy_snapshot.py
```
Details zum Skript: `organisation/CLI_Bedienungsanleitung.md` Abschnitt 14;
Datenformat: `organisation/DATA_STRUCTURE.md` Abschnitt 7g; Server-Setup:
`organisation/DEPLOYMENT.md` Abschnitt 9.

**Optionaler Recent-Trades-CSV-Export:** Der Recent-Trades-Tab kann entweder den
kompletten gefilterten `recent_pool` oder die per `st.dataframe` markierten Zeilen
exportieren. `app/staging_export.py:export_selection` schreibt das feste
22-Spalten-Schema nach
`data/agent_exports/trade_selection_<timestamp>.csv` (Details:
`organisation/DATA_STRUCTURE.md`, Abschnitt 7f). Zielverzeichnis, Präfix und
Zeitstempelformat sind über `config/settings.py` konfigurierbar. Der Producer
normalisiert `direction` lokal und besitzt keine Imports, Pfade oder
Laufzeitabhängigkeiten zu externen Workflows; externe Programme können die CSV
optional übernehmen.

**Datenfluss pro Render:**
```
Load (cached)
    → Pre-Filter (Datum + Member-Suche + Ticker-Suche)
        → Score-Recompute auf Subset (nur wenn Signale ausgeschlossen)
            → Post-Filter (Min-Score auf trade_score; bei Assets/Members auf score_max)
                → Sort (nach trade_score / score_max, asc/desc)
                    → Render (Tab: Trades / Assets / Members)
```

**Abgeleitete Spalte `relevant_sectors` (Trade-Tab):** Wird on-the-fly aus der
Quellspalte `Relevant_Sectors` (JSON-Liste) per Helper `_parse_relevant_sectors()`
erzeugt — eindeutige `sector`-Namen, `', '`-getrennt. Erscheint im Trade-DataFrame
zwischen `trade_score` und `signals_triggered`. Quellspalte bleibt auf Disk unverändert.

**Live-Aggregation (Asset- und Member-Tab):** Beide Tabs aggregieren on-the-fly aus
dem gefilterten Trade-Subset (`prefilt`) — `suspicious_assets.csv` und
`suspicious_members.csv` werden nicht mehr geladen. Min-Score wirkt auf `score_max`
des Aggregats.

Asset-Aggregat (`aggregate_assets`): gruppiert nach `issuer_ticker`:
- `issuer_ticker`, `asset_name`, `trade_count`, `member_count`
- `score_max`, `score_avg`, `score_total`, `total_volume_usd`
- `last_date`, `first_date`, `chambers`, `sectors`, `signals`

Member-Aggregat (`aggregate_members`): gruppiert nach `(name, chamber)`:
- `name`, `chamber`, `Member_Party`, `trade_count`, `asset_count`
- `score_max`, `score_avg`, `score_total`, `total_volume_usd`
- `last_date`, `first_date`, `signals`

**KPI-Zeile** (Anzahl, Max-Score, Σ Volumen) erscheint jetzt in allen drei Tabs.

**Sidebar-Widgets:**
| Widget | Optionen | Default |
|--------|----------|---------|
| Datumsbasis | `disclosure_date` / `transaction_date` | `disclosure_date` |
| Zeitfenster | 7 / 30 / 365 Tage / Alles | 30 Tage |
| Sortierung | absteigend / aufsteigend (nach `trade_score`) | absteigend |
| Signal-Ausschluss | Multiselect über alle 9 Signale | (keines) |
| Member-Suche | Textfeld, Substring auf `name` | — |
| Ticker-Suche | Textfeld, Substring auf `ticker` ODER `issuer_ticker` | — |
| Min-Score-Slider | 0 .. max(50, max(trade_score)) | 0 |
| Score-Gewichte-Form | Number-Input pro `SCORING_WEIGHTS`-Key + Paradox-Damper-Slider + Volume-Enabled/Threshold/Slope + Ex-Officio-Bonus | Defaults aus `config/settings.py` |
| Preset-Selectbox | `Default` + alle in `data/config/scoring_presets.json` gespeicherten Konstellationen | `Default` |

**Voll-Recompute-Mechanismus:**
Werden Signale ausgeschlossen ODER Gewichte/Parameter über die Sidebar-Form überschrieben, ruft die App `app/score_recompute.py` auf:
- `build_settings_proxy(excluded, weights_override=None, params_override=None)` — kopiert `config.settings.SCORING_*`-Konstanten in einen `types.SimpleNamespace`; wendet `weights_override` und `params_override` an; anschließend nullt der Exclude-Schritt Gewichte ausgeschlossener Signale, setzt `SCORING_PARADOX_DAMPER=1.0` bei `swarm_paradox` in excluded und bereinigt `SCORING_EX_OFFICIO_TRIGGERS`. **Reihenfolge:** Defaults → Override → Exclude (0 gewinnt).
- `recompute_subset(df, excluded, weights_override=None, params_override=None)` — ruft `scoring.trade_score.compute_trade_scores(df, proxy)` auf und überschreibt die Spalten `trade_score`, `trade_score_breakdown`, `trade_warnings` im gefilterten Subset.
- `params_override`-Keys (explizit erlaubt, siehe `PARAMS_OVERRIDE_KEYS`): `paradox_damper`, `volume_enabled`, `volume_threshold_u`, `volume_slope`, `ex_officio_bonus`.
Zweck: Paradox-Dämpfung und Ex-Officio-Bonus bleiben bei Signal-Ausschluss konsistent — einfaches String-Filtern würde diese Interaktionen brechen.

**Preset-Persistenz (`app/scoring_presets.py`):**
- `load_presets()` liest `data/config/scoring_presets.json`; ein synthetischer `Default`-Eintrag (Snapshot aus `config.settings`) ist immer enthalten.
- `save_preset(name, payload)` speichert; `Default` ist reserviert. Datei + Ordner werden bei Bedarf angelegt.
- `delete_preset(name)` entfernt; `Default` ist nicht löschbar.
- Payload-Schema: `{weights: {...}, paradox_damper, volume_enabled, volume_threshold_u, volume_slope, ex_officio_bonus}`.

**Drei Tabs:**
- **Trades** (Standard): KPIs (Anzahl, Max-Score, Σ Volumen) + Datentabelle inkl. abgeleiteter Spalte `relevant_sectors`
- **Assets**: Live-Aggregat aus gefiltertem Trade-Subset, sortiert nach `score_max` desc — reaktiv auf alle Sidebar-Filter
- **Members**: Live-Aggregat aus gefiltertem Trade-Subset, sortiert nach `score_max` desc — reaktiv auf alle Sidebar-Filter

**Neue Dateien:**
| Datei | Zweck |
|-------|-------|
| `app/__init__.py` | Package-Marker (leer) |
| `app/score_recompute.py` | Helper: `available_signals()`, `build_settings_proxy(excluded, weights_override, params_override)`, `recompute_subset(...)` |
| `app/scoring_presets.py` | Persistenz für Scoring-Konstellationen: `load_presets()`, `save_preset()`, `delete_preset()`, `default_payload()`; JSON in `data/config/scoring_presets.json` |
| `app/streamlit_app.py` | Haupt-UI, Streamlit 1.57.0, Pandas 3.0.0 |

**Abhängigkeiten:**
- `scoring/trade_score.py` — Voll-Recompute
- `config/settings.py` — `SCORING_*`-Konstanten
- `streamlit` >= 1.57.0, `pandas` >= 3.0.0

---

## Pipeline 9: Wartung & Reset

**Script:** `scripts/reset_pipeline.py`
**Zweck:** Granularer Reset von Logs, Caches, Stage-Files und Processed-Outputs.
Default-Verhalten: reversibler **Move** nach `archive/reset_pipeline/reset_<ts>/` mit
`_manifest.txt`. Kuratierte Mapping-Dateien (12 PROTECTED-Files) werden niemals angefasst.

**CLI:**
```
--logs              logs/*.log + logs/debug_tesseract/
--stage-files       *_stage.csv, pipeline_state.json, duplicate_inspection_sample.csv,
                    .enrichment_checkpoint.pkl (nur Altbestand)
--cache_reset       gesamtes data/cache/* (OpenFIGI-DB, EDGAR-CIK-Map,
                    Post-Trade-Preishistorie + jeder kuenftige Cache) +
                    ticker_industry_cache.json, ticker_0_cache.json (data/mappings/)
                    Naechster Lauf netzabhaengig (EDGAR + yfinance Refetch).
--processed         trades_master[_complete].csv, manual_review_tickers.csv,
                    unmatched_*.csv, delisted_trades.csv, ticker_liste_index.csv,
                    suspicious_events.csv, suspicious_assets.csv, suspicious_members.csv
--congress-api      data/congress_api/*.json (Bill-Daten + Checkpoint)
--rebuild           cache-unabhaengiger import_trades-Rebuild:
                    stage-files + cache_reset + processed (OHNE logs/congress-api)
--all               alle fuenf Gruppen kombiniert (inkl. logs + congress-api)
--dry-run           keine Aktion, nur Vorschau
--purge             echtes rm statt Move (irreversibel, doppelte Confirmation)
```

**Confirmation-Stufen:**
| Flag | Prompt |
|------|--------|
| `--logs`, `--stage-files`, `--cache_reset` | einfach `[j/N]` |
| `--processed`, `--congress-api`, `--rebuild`, `--all` | getipptes `JA` |
| `--purge` (zusätzlich) | getipptes `PURGE` |

**Geschützt (nie verschoben/gelöscht):** `name_aliases.csv`,
`house_member&(sub)committees.csv`, `senate_member&(sub)committees.csv`,
`subcommittee_sector_industry_mapping*.csv`, `yf_sectors_with_industries.csv`,
`logs/house_pdf_extractor_done.csv` (State).

**Terminal-Output:** Pro Datei `[OK]`/`[X]`/`[SKIP]` mit Größe und Dauer,
Zwischensummen pro Gruppe, finale Zusammenfassung.

---

## Pipeline 7: Diagnose & Analyse (optional)

| Script | Zweck | CLI |
|--------|-------|-----|
| `scripts/diagnose_scraper.py` | Senate-Scraper QA (Timing, Konsistenz, Rowcount) | `--test [timing\|consistency\|rowcount\|all] --runs N --days N` |
| `scripts/analyse_event_study.py` | Event-Study / Abnormal Return (Discovery): validiert `trade_score` gegen signierten, marktbereinigten BHAR (2 Konstrukte A/B, member-geclusterter Bootstrap). **Dieses Standalone-Skript schreibt keinen Master** (Konstrukt B + Bootstrap nur in-memory); die produktive Stage `abnormal_returns` (Pipeline 5, Schritt [8b]) schreibt dagegen die `signed_ar_*` (Konstrukt A) in den Master. Liest `trades_master_complete.csv`, schreibt Report `organisation/analyse_event_study.md` + `data/processed/ar_outliers.csv`. Settings-Änderung = Skript neu starten. Konfiguration: `AR_*`-Block in `config/settings.py`. Spalten + Metrik: `DATA_STRUCTURE.md` Abschnitt 7e. | keine Flags |

---

## Pipeline 11: Committee Matcher V2 (auf Befehl, kein Scheduler)

Der frühere Committee-Mapping-Agent (`scripts/committee_mapping_agent/`) ist entfernt.
Sein Nachfolger ist das Paket `committee_matcher/` (`venv/bin/python -m committee_matcher <kommando>`).
Ablauf: Input/Roster prüfen → Taxonomie-Snapshot → Mapping → Review in Excel →
`data/committee_matcher_v2/reviews/<id>/reviewed_assignments.csv`. Die aktive Revision
wählt `COMMITTEE_MAPPING_REVIEW_ID` in `config/settings.py`; `import_trades` liest sie
über `config/committee_mapping.py`.
**CLI-Referenz:** `CLI_Bedienungsanleitung.md` (Committee Matcher V2) · **Datenverträge:** `DATA_STRUCTURE.md` Abschnitt 3a.

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
    │House Scraper │ │Senate      │ │(Pipeline 6 entf.)  
    │(Pipeline 1)  │ │Scraper     │ │                   │
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
         │  Member-Anreicherung ← data/reference│
         │  CommitteeTag ← committee_industries│
         └──────────────┬──────────────────────┘
                        │
                        ↓
              ┌─────────────────────────────────────────┐
              │ trades_master.csv                       │
              │ trades_master_complete.csv              │
              │ suspicious_assets.csv                   │
              │ suspicious_members.csv                  │
              │ unmatched_names.csv                     │
              └─────────────────┬───────────────────────┘
                                │ (nur trades_master.csv)
                                ↓
              ┌─────────────────────────────────────────┐
              │  Streamlit Dashboard (Pipeline 10)      │
              │  app/streamlit_app.py                   │
              │  Lese-UI: Trades / Assets / Members     │
              │  (Assets + Members live aggregiert)     │
              └─────────────────────────────────────────┘
              [Modul 10-11: TODO]
              Email → Automatisierung
```

---

## Statische Referenzdaten

Diese Dateien werden nicht durch Pipelines erzeugt, sondern manuell gepflegt:

| Datei | Aktualisierung | Zweck |
|-------|----------------|-------|
| `data/mappings/house_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen), via `parse_house_committee_pdf.py` (Pipeline 3c) | 437 Zeilen, 436 House-Mitglieder + Committees, F2-Format; von der Pipeline nicht mehr gelesen |
| `data/mappings/senate_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen), via `fetch_senate_xml.py` + `parse_senate_xml.py` (Pipeline 3c) | 101 Zeilen, 100 Senatoren + Committees, F2-Format; von der Pipeline nicht mehr gelesen |
| `scrapers/senate_committee_xml/raw/*.xml` + `_manifest.json` | Bei jedem `fetch_senate_xml.py`-Lauf | 26 Senate-Committee-XML-Snapshots (committet, Determinismus) |
| `data/processed/committees_structure.csv` | Nach jeder Roster-Neuerzeugung, via `generate_committee_structure.py` | Chamber/Committee/Subcommittee-Strukturliste (gitignored) |
| `data/mappings/us_{house,senate}_all_committees.csv` | Bei Bedarf (manuell/extern) | Struktur-Orakel für Validierung (untracked) |
| `data/mappings/name_aliases.csv` | – (von der Pipeline nicht mehr gelesen) | Frühere Namenskorrekturen (11 Einträge) |
| `data/mappings/ignored_tickers.csv` | Bei Bedarf | Manuelle Ignore-Liste fuer raw_ticker (z.B. private Funds ohne oeffentlichen Ticker). Wird in Stage `tickerclassify` ausgewertet → setzt `ticker_type=ignored`, kein Manual-Review-Eintrag |
| `data/mappings/subcommittee_sector_industry_mapping.csv` | Bei Bedarf | Subcommittee→Sektor/Industry (861 Zeilen) |
| `data/raw/former_members.csv` | Bei Bedarf (Congresswechsel) | Ehemalige Politiker mit Trades (6 Eintraege) |
| `config/settings.py` | Bei Bedarf | Alle Schwellenwerte, Pfade, Gewichtungen |

---

Erstellt: 2026-03-07
Letzte Aktualisierung: 2026-05-20 (Pipeline 10: Streamlit Dashboard MVP)

### Aenderungs-Historie

- **2026-05-21** — Pipeline 10 Iteration: Asset- und Member-Tab live aus gefiltertem
  Trade-Subset aggregiert (`suspicious_assets.csv`/`suspicious_members.csv` kein Input
  mehr). Neue abgeleitete Spalte `relevant_sectors` im Trade-Tab. KPI-Zeile in allen
  drei Tabs.
- **2026-05-20** — Pipeline 10 (Streamlit Dashboard MVP) hinzugefuegt.
  Drei neue Dateien: `app/__init__.py`, `app/score_recompute.py`,
  `app/streamlit_app.py`. Abhaengigkeitsgraph um Streamlit-App-Knoten ergaenzt.
- **2026-05-14** — Spalten-Refactoring in Stage 5 (OpenFIGI) und Stage 6
  (yfinance). Output-Spalten der externen-API-Stages bekommen Provider-Suffixe:
  `_figi` fuer OpenFIGI-Felder (`ticker_figi`, `asset_name_figi`,
  `security_type_figi`, `market_sector_figi`), `_yf` fuer yfinance-Felder
  (`sector_yf`, `industry_yf`, `quote_type_yf`). Die ID-Spalte `figi` selbst
  bleibt unveraendert. Filter-Stage liest jetzt `sector_yf`/`industry_yf` statt
  `Ticker_Sector`/`Ticker_Industry`. SQLite-Cache-Tabelle `ticker_normalizer`
  wurde mit DROP+CREATE auf neues Schema migriert (Backup in `data/cache/`).
