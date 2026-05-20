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

## Pipeline 5: Import-Pipeline (Hauptpipeline, 16 Stages)

**Script:** `scripts/import_trades.py`
**Input:** `data/raw/congress-trading-all.xlsx` (Quiver-Snapshot)
**Output:**
  - `data/processed/trades_master_complete.csv` (vollstaendige Spalten, Rebuild T3)
  - `data/processed/trades_master.csv` (kuratierte Sicht via `settings.CURATED_COLUMNS`)
  - `data/processed/manual_review_tickers.csv` (Diagnose-Output)
  - `data/processed/unmatched_names.csv`, `data/processed/unmatched_preferred.csv` (bei Bedarf)

**Modi:**
- `python scripts/import_trades.py` — Full Rebuild (alle 16 Stages, schreibt complete + curated + review)
- `python scripts/import_trades.py --delta` — Delta-Import via `trade_id` (nur neue Trades, merge)
- `python scripts/import_trades.py --dry_run` — Alle Stages → trades_stage.csv (Master unberuehrt)

**Migration vor erstem Rebuild-T3-Lauf:**
```bash
python scripts/migrate_add_trade_ids.py [--dry-run]   # vergibt trade_id, splittet complete/curated
```

**Stage-Pipeline (16 Schritte, unabhaengig re-runnable):**
```
--stage_rename            # Quiver laden + Spalten umbenennen + trade_id
--stage_date              # Datum normalisieren
--stage_committees        # Committee-Member-Enrichment
--stage_tickerclassify    # Offline-Klassifikation (Regex, utils/ticker_classifier.py)
--stage_tickeropenfigi    # OpenFIGI-Normalisierung (FIGI, ticker_figi)
--stage_issuer_group      # Cross-Class Issuer Mapping (SEC EDGAR CIK, P5.6)
--stage_tickeryfinance    # yfinance Sector/Industry-Anreicherung
--stage_trade_volume      # Trade-Volume aus Amount-Range (Modul 8, Phase 8.1) → trade_volume_usd
--stage_asset_volume      # Cross-Class Asset-Tradingvolume (Modul 8, Phase 8.2) → asset_tradingvolume_usd
--stage_gates_tags        # Gates + Tags (Asset-Class, Committee, Vote-Timing) + Scoring
--stage_branch1_signals   # Branch-1 Signale (committee, ex_officio, floor_action, extreme_report)
--stage_sequence_signals  # Sequenz-Signale (contrarian, accumulative)
--stage_swarm_signals     # Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6)
--stage_scoring           # Trade-Score + breakdown + warnings (Modul 8, Phase 8.3) → trade_score, trade_score_breakdown, trade_warnings
--stage_suspicious_export # Suspicious-Events → suspicious_*.csv promoten
--stage_save              # trades_stage.csv → complete + curated Master
```

**Bonus-Flags (kein Master-Touch, exklusiv):**
- `--manual_review_only` — regeneriert `manual_review_tickers.csv` aus complete-Master
- `--regenerate_curated` — regeneriert `trades_master.csv` aus complete-Master

**Flag-Matrix:**
| Aufruf | Input | Output | Master-Backup |
|--------|-------|--------|---------------|
| `--stage_X` | trades_master_complete.csv | trades_master_complete.csv | ✅ |
| `--stage_X --dry_run` | trades_master_complete.csv | trades_stage.csv | ❌ |
| `--stage_X --delta` | trades_stage.csv | trades_stage.csv | ❌ |
| `--stage_save` | trades_stage.csv | complete + curated (replace) | ✅ |
| `--delta --stage_save` | trades_stage.csv | complete + curated (merge via trade_id) | ✅ |
| `--dry_run` (kein Stage) | Quiver | trades_stage.csv + review | ❌ |
| `--delta` (kein Stage) | Quiver+complete | complete + curated (merge) | ✅ |
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
    → Checkpoint-Support: alle 5.000 Trades als Pickle
    → 3-Stufen Matching: Alias → Exakt → Fuzzy (85%)
    → Neue Spalten: Member_*
         ↓
[4] Ticker-Klassifikation (offline, Rebuild T3)
    → utils/ticker_classifier.classify(ticker, asset_name)
    → Neue Spalten: ticker_type, ticker_base, classification_*, api_strategy,
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
    → Doku: organisation/Ticker_Architektur.md
         ↓
[7] yfinance Sector/Industry (Rebuild T3)
    → utils/ticker_sector.TickerSectorEnricher.enrich_batch()
    → Neue Spalten (Suffix _yf): sector_yf, industry_yf, quote_type_yf
    → Vorzugsaktien-Mapping (EP$C → KMI), Hyphen-Fallback (BRK-B → BRK/B)
         ↓
[8] Trade-Volume (Modul 8, Phase 8.1 — neue Stage)
    → utils/amount_parser.midpoint_usd(amount) — pro Trade individuelle
      Mittelpunkt-Berechnung der Disclosure-Amount-Range.
    → Neue Spalte: trade_volume_usd (int64)
    → Beispiele:
        "$1,001 - $15,000"  → 8000
        "$15,001 - $50,000" → 32500
        "Over $50,000,000"  → 50_000_000 (konservativer Punkt-Trade)
        "$94.91"            → 94 (Single-Wert)
        ""/NaN/unparsebar   → 0
    → Eingabe fuer Stage [9] (Asset-Tradingvolume-Aggregation) und
      Phase 8.3 (Volume-Score: Schwelle U + lineare Steigung SLOPE).
         ↓
[9] Asset-Tradingvolume (Modul 8, Phase 8.2 — neue Stage)
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
[10] Filter-Logik (_apply_filter_logic)
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
    → MatchScore/MatchSource (NameMatcher)
    → Insider_Sectors / Relevant_Sectors / Relevance_Score (CommitteeTag — User-Sicht „Filter 3")
      Insider_Sectors  = JSON-Liste ALLER (sub)committee-Sektoren des Members
      Relevant_Sectors = Intersection Insider × sector_yf/industry_yf
                         (Match-Mode via settings.RELEVANT_SECTORS_MATCH_MODE,
                          Default: sector_or_industry)
      Score-Formel: sector_match (33) + industry_match (33), * 1.5 wenn ex officio
      Vergleich Mapping.Sector/Industry == sector_yf/industry_yf → 0/33/50/66/99
    → asset_class_excluded (AssetClassGate — User-Sicht „Filter 0": prueft ASSET_CLASS_KEEP_TYPES = {stock, foreign_stock, shareclass})
    → Vote_Timing_Hit / Vote_Timing_Hits / Vote_Timing_Hit_Count (VoteTimingTag — User-Sicht „Filter 4")
      Quelle der Trade-Sektoren ist Relevant_Sectors (Intersection!), damit
      Sektor-Treffer am Ticker haengen, nicht nur am Committee. Alle Treffer
      im Fenster werden als JSON-Liste gespeichert.
         ↓
[11] Branch-1 Signale (_apply_branch1_signals)
    → Vier Per-Trade-Signale: committee, committee_ex_officio,
      floor_action, extreme_report (signals/*.py)
    → Schwellwerte aus settings.py: COMMITTEE_THRESHOLD,
      VOTE_TIMING_HIT_THRESHOLD, EXTREME_REPORT_DAYS
    → Neue Trade-Spalten: signal_count (int 0-4), signals_triggered (str, ";"-getrennt)
    → Auslöse-Events werden in data/processed/branch1_events_stage.csv
      geschrieben (Snapshot pro Auslösung, 21 Spalten inkl. issuer_ticker,
      issuer_group_key und asset_name_figi; siehe
      organisation/tool_specific_md/signal_info.md).
      Diese Datei wird in einer spaeteren Suspicious-Export-Stage (P4) in
      suspicious_events.csv promoted.
         ↓
[12] Sequenz-Signale (_apply_sequence_signals)
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
[13] Schwarm-Signale (_apply_swarm_signals)  -- P6, Branch 2
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
         ↓
[14] Trade-Score (Modul 8, Phase 8.3 — _stage_scoring)
    → scoring/trade_score.compute_trade_scores(df, settings)
    → Drei neue Spalten:
        trade_score             — int, gewichteter Score nach Formel
                                   round(swarm × PARADOX_DAMPER + other + volume + ex_officio_bonus)
        trade_score_breakdown   — ;-getrennte Komponenten in fester Reihenfolge
                                   (Branch-1 → Sequenz → Schwarm → ex_officio → volume)
        trade_warnings          — Warning-Tags (aktuell: paradox_swarm)
    → Eingaben: signals_triggered (aus [11]/[12]/[13]), Member_ExOfficio,
      asset_tradingvolume_usd (aus [9]).
    → Parameter aus settings.py: SCORING_WEIGHTS, SCORING_PARADOX_DAMPER,
      SCORING_EX_OFFICIO_BONUS, SCORING_EX_OFFICIO_TRIGGERS,
      SCORING_VOLUME_ENABLED, SCORING_VOLUME_THRESHOLD_U, SCORING_VOLUME_SLOPE.
    → Score = 0 bei trade ohne Signal-Hit; kein Cap.
         ↓
[15] Suspicious-Export (_stage_suspicious_export, + Modul 8.4)
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
      werden bereits in Stage [11]/[12] gesetzt)
         ↓
[16] Speichern
    → Backup: archive/trades_master_backup/trades_master[_complete]_{ts}.csv
    → trades_master_complete.csv (alle Spalten, neu geschrieben — inkl.
      trade_volume_usd, asset_tradingvolume_usd, trade_score,
      trade_score_breakdown, trade_warnings aus Modul 8)
    → trades_master.csv (kuratiert via settings.CURATED_COLUMNS — inkl.
      signal_count + signals_triggered + alle 5 Modul-8-Spalten)
    → manual_review_tickers.csv (falls Tickers mit manual_review_flag)
    → _promote_problem_outputs(delta) — siehe Abschnitt unten
        - unmatched_names.csv
        - unmatched_preferred.csv
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
- `unmatched_names.csv` ↔ `NameMatcher.match()` (lädt aktuelle `name_aliases.csv` + Member-CSVs)
- `unmatched_preferred.csv` ↔ `Vorzugsaktien_mapping.csv` + `ignored_tickers.csv`
- `delisted_trades.csv` ↔ positiver `ticker_industry_cache.json` (cache-only, kein API-Call)

**`manual_review_tickers.csv`** wird im Live-Pfad immer aus dem vollen gemergten Master aggregiert (Re-Validation läuft implizit über Filter `ticker_type!='ignored'` + `manual_review_flag=True`). Im Delta-Dry-Run zeigt die Datei eine Vorschau des Endzustands (`existing-master ∪ delta_enriched`).

Stage-Dateien (`unmatched_names_stage.csv`, `unmatched_preferred_stage.csv`, `delisted_trades_stage.csv`) werden nach Promotion automatisch entfernt.

**Hinweis:** Die Pipeline ist ein reiner Enrichment-Durchlauf — es werden keine Zeilen entfernt.

**Dedup-Strategie (`DEDUP_STRATEGY` in settings.py):**
- `'keep_all'` (Default): Bei mehreren Zeilen mit gleicher `trade_id` innerhalb eines Imports
  bleiben alle erhalten (z.B. Splits, identische Ticker am gleichen Tag). `trade_id` ist dann
  KEIN eindeutiger Schlüssel im Master.
- `'last_wins'`: Nur die letzte Zeile pro `trade_id` wird behalten.
- Wirkt in `_merge_into_master` (`scripts/import_trades.py`) und `scripts/migrate_add_trade_ids.py`.
- Delta-Erkennung zwischen Läufen (`detect_new_trades`) ist davon nicht betroffen.

**Delta-Modus** (`--delta`): Lädt aktuelle Quiver-CSV, vergleicht per Key-Dedup gegen bestehende
trades_master.csv, reichert nur neue Trades an und mergt sie (mit Dedup) in Master.

**Einzelstage re-runnen (Beispiele):**
```bash
# Klassifikation nach Regex-Update neu berechnen (mit Backup):
python scripts/import_trades.py --stage_tickerclassify

# OpenFIGI-Cache nutzen (refresh_ticker_types.py wurde in T3 entfernt):
python scripts/import_trades.py --stage_tickeropenfigi

# Filter-Stage nach Mapping-Änderung neu berechnen:
python scripts/import_trades.py --stage_filter

# Dry-Run: komplette Pipeline ohne Master-Touch:
python scripts/import_trades.py --dry_run

# Dry-Run committen:
python scripts/import_trades.py --stage_save

# Delta stage-weise:
python scripts/import_trades.py --delta --stage_rename    # neue Trades → stage.csv
python scripts/import_trades.py --delta --stage_filter    # filter auf stage.csv
python scripts/import_trades.py --delta --stage_save      # merge in complete-Master

# Bonus-Flags (kein Master-Touch ausser Curated):
python scripts/import_trades.py --manual_review_only      # review aus complete
python scripts/import_trades.py --regenerate_curated      # curated aus complete
```

**Abhaengigkeiten:**
- `utils/name_matcher.py` — 3-Tier Matching (Alias/Exakt/Fuzzy)
- `tags/committee_tag.py` — CommitteeTag (User-Sicht „Filter 3"): Committee-Relevanz-Scoring
- `gates/asset_class_gate.py` — AssetClassGate (User-Sicht „Filter 0"): Nicht-Aktien flaggen (asset_class_excluded)
- `tags/vote_timing_tag.py` — VoteTimingTag (User-Sicht „Filter 4"): Vote-Timing (respektiert asset_class_excluded)
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
| `signals/committee_signal.py` | Branch-1 Per-Trade-Signal: triggert bei `Relevance_Score >= COMMITTEE_THRESHOLD` | 8 (in `test_branch1_signals.py`) |
| `signals/committee_ex_officio_signal.py` | Branch-1 Per-Trade-Signal: triggert wenn CommitteeSignal + `Member_ExOfficio` | 4 |
| `signals/floor_action_signal.py` | Branch-1 Per-Trade-Signal: triggert bei `Vote_Timing_Hit_Count >= VOTE_TIMING_HIT_THRESHOLD` | 4 |
| `signals/extreme_report_signal.py` | Branch-1 Per-Trade-Signal: triggert bei `disclosure_date - transaction_date > EXTREME_REPORT_DAYS` | 5 |
| `signals/contrarian_signal.py` | Branch-1 Sequenz-Signal: triggert bei Buy↔Sell-Wende eines Members auf demselben issuer_ticker innerhalb `CONTRARIAN_WINDOW_DAYS` | 8 (in `test_sequence_signals.py`) |
| `signals/accumulative_signal.py` | Branch-1 Sequenz-Signal: triggert bei `>= ACCUMULATIVE_MIN_COUNT` gleichgerichteten Trades eines Members innerhalb `ACCUMULATIVE_WINDOW_DAYS` | 8 |

### Datenfluss (geplant)

```
DataFrame (raw_ticker, company_name, ...)
        ↓
[1] ticker_classifier.classify(ticker, company_name)
        → TickerClassification (frozen):
          ticker_type ∈ {STOCK, ETF, PREFERRED, WARRANT, OPTION, BOND, ...}
          api_strategy ∈ {SKIP, FIGI_MAPPING, FIGI_SEARCH, CUSIP_LOOKUP,
                          YFINANCE_DIRECT, MANUAL_REVIEW}
          confidence ∈ {HIGH, MEDIUM, LOW}
        ↓
[2] TickerNormalizer.normalize_batch(classifications)
        → NormalizedTicker (frozen):
          figi, ticker_figi, asset_name_figi, security_type_figi,
          market_sector_figi, share_class_figi
        Routing nach api_strategy:
          SKIP                 → kein Call (OPTION, TREASURY, CRYPTO, WHEN_ISSUED, INVALID)
          FIGI_MAPPING         → /v3/mapping (Batches à 100)
          FIGI_SEARCH          → /v3/search (einzeln, Name+Ticker)
          CUSIP_LOOKUP         → /v3/mapping mit idType=ID_CUSIP
          YFINANCE_DIRECT      → /v3/mapping optimistisch (P5), danach
                                 yfinance Sector/Industry in [3]
          MANUAL_REVIEW        → /v3/mapping optimistisch (P5),
                                 Negativ-Cache bei Miss
        Type-Korrektur: STOCK + securityType2='ETP' → ETF
        ↓
[3] TickerSectorEnricher.enrich_batch(normalized)
        → SectorInfo (frozen): sector_yf, industry_yf, quote_type_yf
        Routing nach ticker_type:
          STOCK/SHARECLASS/FOREIGN_STOCK/ETF/NUMERIC_SUFFIX → yfinance
          PREFERRED                                          → via Mapping (Vorzugsaktien_mapping.csv)
          WARRANT/OPTION/BOND/CRYPTO/...                     → kein Call
        Hyphen-Fallback: yfinance(BRK-B) leer → yfinance(BRK/B)
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
| 11.8 | sector | Vorzugsaktien-Mapping aus `Vorzugsaktien_mapping.csv` + Terminal-Report `Preferred-Mapping: matched=X, unmatched=Y` nach Batch-Ende |
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
[i] BRK-B ohne Sektor → Retry mit Slash-Variante BRK/B
[i] Cache-Checkpoint: 100 Lookups gespeichert
[!] Preferred-Ticker XYZ$A ohne Mapping
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
- `data/processed/suspicious_assets.csv` — Assets-View mit Score-Aggregaten
- `data/processed/suspicious_members.csv` — Members-View mit Score-Aggregaten

**Outputs:** keine — reine Lese-UI, kein Schreiben auf Disk

**Datenfluss pro Render:**
```
Load (cached)
    → Pre-Filter (Datum + Member-Suche + Ticker-Suche)
        → Score-Recompute auf Subset (nur wenn Signale ausgeschlossen)
            → Post-Filter (Min-Score)
                → Sort (nach trade_score, asc/desc)
                    → Render (Tab: Trades / Assets / Members)
```

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

**Voll-Recompute-Mechanismus:**
Werden Signale via Multiselect ausgeschlossen, ruft die App `app/score_recompute.py` auf:
- `build_settings_proxy(excluded)` — kopiert `config.settings.SCORING_*`-Konstanten in einen `types.SimpleNamespace`; setzt Gewichte ausgeschlossener Signale auf 0; bei `swarm_paradox` in excluded wird `SCORING_PARADOX_DAMPER=1.0` (Dämpfung aus); `SCORING_EX_OFFICIO_TRIGGERS` wird um ausgeschlossene Trigger bereinigt.
- `recompute_subset(df, excluded)` — ruft `scoring.trade_score.compute_trade_scores(df, proxy)` auf und überschreibt die Spalten `trade_score`, `trade_score_breakdown`, `trade_warnings` im gefilterten Subset.
Zweck: Paradox-Dämpfung und Ex-Officio-Bonus bleiben bei Signal-Ausschluss konsistent — einfaches String-Filtern würde diese Interaktionen brechen.

**Drei Tabs:**
- **Trades** (Standard): KPIs (Anzahl, Mean, Max, # ausgeschlossene Signale) + Datentabelle mit 16 Spalten
- **Assets**: rohe Ansicht von `suspicious_assets.csv` sortiert nach `score_max` desc (Datum/Score-Filter noch nicht aktiv — geplante Iteration)
- **Members**: rohe Ansicht von `suspicious_members.csv` sortiert nach `score_max` desc (gleiche MVP-Beschränkung)

**Neue Dateien:**
| Datei | Zweck |
|-------|-------|
| `app/__init__.py` | Package-Marker (leer) |
| `app/score_recompute.py` | Helper: `available_signals()`, `build_settings_proxy()`, `recompute_subset()` |
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
--stage-files       *_stage.csv, pipeline_state.json, duplicate_inspection_sample.csv
--ticker-caches     ticker_type_cache.db, ticker_industry_cache.json, ticker_0_cache.json
--processed         trades_master[_complete].csv, manual_review_tickers.csv,
                    unmatched_*.csv, delisted_trades.csv, ticker_liste_index.csv
--all               alle vier Gruppen kombiniert
--dry-run           keine Aktion, nur Vorschau
--purge             echtes rm statt Move (irreversibel, doppelte Confirmation)
```

**Confirmation-Stufen:**
| Flag | Prompt |
|------|--------|
| `--logs`, `--stage-files`, `--ticker-caches` | einfach `[j/N]` |
| `--processed`, `--all` | getipptes `JA` |
| `--purge` (zusätzlich) | getipptes `PURGE` |

**Geschützt (nie verschoben/gelöscht):** `name_aliases.csv`, `Vorzugsaktien_mapping.csv`,
`house_member&(sub)committees.csv`, `senate_member&(sub)committees.csv`,
`subcommittee_sector_industry_mapping*.csv`, `yf_sectors_with_industries.csv`,
`vote_sector_mapping.py`, `logs/house_pdf_extractor_done.csv` (State).

**Terminal-Output:** Pro Datei `[OK]`/`[X]`/`[SKIP]` mit Größe und Dauer,
Zwischensummen pro Gruppe, finale Zusammenfassung.

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
         │  CommitteeTag    ← Mapping-CSV      │
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
                                │
                                ↓
              ┌─────────────────────────────────────────┐
              │  Streamlit Dashboard (Pipeline 10)      │
              │  app/streamlit_app.py                   │
              │  Lese-UI: Trades / Assets / Members     │
              └─────────────────────────────────────────┘
              [Modul 10-11: TODO]
              Email → Automatisierung
```

---

## Statische Referenzdaten

Diese Dateien werden nicht durch Pipelines erzeugt, sondern manuell gepflegt:

| Datei | Aktualisierung | Zweck |
|-------|----------------|-------|
| `data/mappings/house_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen) | 437 House-Mitglieder + Committees (inkl. 5 Leadership ex officio) |
| `data/mappings/senate_member&(sub)committees.csv` | Alle 2 Jahre (Wahlen) | 100 Senatoren + Committees |
| `data/mappings/name_aliases.csv` | Bei Bedarf (neue Varianten) | Manuelle Namenskorrekturen (11 Eintraege) |
| `data/mappings/ignored_tickers.csv` | Bei Bedarf | Manuelle Ignore-Liste fuer raw_ticker (z.B. private Funds ohne oeffentlichen Ticker). Wird in Stage `tickerclassify` ausgewertet → setzt `ticker_type=ignored`, `api_strategy=skip`, kein Manual-Review-Eintrag |
| `data/mappings/subcommittee_sector_industry_mapping.csv` | Bei Bedarf | Subcommittee→Sektor/Industry (861 Zeilen) |
| `data/mappings/vote_sector_mapping.py` | Bei Bedarf | Policy Area→YF-Sektoren (Python, 12 Mappings) |
| `data/raw/former_members.csv` | Bei Bedarf (Congresswechsel) | Ehemalige Politiker mit Trades (6 Eintraege) |
| `config/settings.py` | Bei Bedarf | Alle Schwellenwerte, Pfade, Gewichtungen |

---

Erstellt: 2026-03-07
Letzte Aktualisierung: 2026-05-20 (Pipeline 10: Streamlit Dashboard MVP)

### Aenderungs-Historie

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
