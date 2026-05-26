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

## 3. Import Pipeline (16 Stages)

**Datei:** `scripts/import_trades.py`
**Zweck:** Liest Quiver-Snapshot (`congress-trading-all.xlsx`), matcht Member-Namen (Alias→Exakt→Former→Fuzzy), klassifiziert Tickers offline, normalisiert via OpenFIGI, reichert via yfinance an, wendet Filter an.

**Spalten-Namenskonvention** (seit 2026-05-14): Externe-API-Outputs tragen
Provider-Suffixe — `_figi` (OpenFIGI: `ticker_figi`, `asset_name_figi`,
`security_type_figi`, `market_sector_figi`), `_yf` (yfinance: `sector_yf`,
`industry_yf`, `quote_type_yf`). Die ID-Spalte `figi` selbst behaelt keinen
Suffix. Details siehe `organisation/DATA_STRUCTURE.md`.

```bash
# Standard: vollständige Pipeline (alle 16 Stages sequenziell)
python scripts/import_trades.py

# Delta-Modus: nur neue Trades (via trade_id) anreichern und mergen
python scripts/import_trades.py --delta

# Dry-Run: alle Stages, Output in trades_stage.csv (kein Master-Touch)
python scripts/import_trades.py --dry_run
```

**Output:**
- `data/processed/trades_master_complete.csv` — vollstaendige Spalten
- `data/processed/trades_master.csv` — kuratierte Sicht (settings.CURATED_COLUMNS)
- `data/processed/manual_review_tickers.csv` — Diagnose-Output
- `data/processed/unmatched_names.csv`, `data/processed/unmatched_preferred.csv` (bei Bedarf)

**Migration vor erstem Lauf** (einmalig, vergibt trade_id auf Bestand):
```bash
python scripts/migrate_add_trade_ids.py [--dry-run] [--force]
```

### Einzelne Pipeline-Stages (Debug / Repair)

Nur **ein** `--stage_*` Flag gleichzeitig erlaubt. Kombination mit `--delta` und `--dry_run` möglich.

```bash
python scripts/import_trades.py --stage_rename            # laden + Rename + trade_id
python scripts/import_trades.py --stage_date              # Datum normalisieren
python scripts/import_trades.py --stage_committees        # Committee-Enrichment
python scripts/import_trades.py --stage_tickerclassify    # Offline-Klassifikation (Regex)
python scripts/import_trades.py --stage_tickeropenfigi    # OpenFIGI-Normalisierung
python scripts/import_trades.py --stage_issuer_group      # Cross-Class Issuer Mapping (SEC EDGAR CIK)
python scripts/import_trades.py --stage_tickeryfinance    # yfinance Sector/Industry
python scripts/import_trades.py --stage_trade_volume      # trade_volume_usd aus Amount-Range
python scripts/import_trades.py --stage_asset_volume      # Cross-Class Asset-Tradingvolume rolling-window
python scripts/import_trades.py --stage_gates_tags        # Gates + Tags (Asset-Class, Committee, Vote-Timing)
python scripts/import_trades.py --stage_branch1_signals   # Branch-1 Signale
python scripts/import_trades.py --stage_sequence_signals  # Sequenz-Signale (contrarian, accumulative)
python scripts/import_trades.py --stage_swarm_signals     # Schwarm-Signale (P6)
python scripts/import_trades.py --stage_scoring           # Trade-Score + breakdown + warnings
python scripts/import_trades.py --stage_suspicious_export # Promote Events + Views (inkl. Score-Aggregate)
python scripts/import_trades.py --stage_save              # trades_stage.csv -> Master

# Kombination mit Delta:
python scripts/import_trades.py --delta --stage_rename
```

Stage-Reihenfolge + Indizes: siehe [Pipelines.md](Pipelines.md).

### Bonus-Flags (kein Master-Touch ausser Curated, exklusiv)

```bash
python scripts/import_trades.py --manual_review_only   # NUR review aus complete
python scripts/import_trades.py --regenerate_curated   # NUR curated aus complete
```

#### Hintergrund: "complete" vs. "curated" Master

Die Pipeline schreibt **zwei** Master-Dateien:

| Datei | Spalten | Zweck |
|---|---|---|
| `data/processed/trades_master_complete.csv` | **alle** Pipeline-Spalten (~50+, inkl. interner Felder wie `MatchScore`, `MatchSource`, `Member_Committees_Full`, `figi`, `ticker_figi`, `manual_review_flag`, `classification_reason`) | "Quelle der Wahrheit", verlustfrei — daraus wird alles andere abgeleitet |
| `data/processed/trades_master.csv` (**curated**) | nur die ~25 Spalten aus `settings.CURATED_COLUMNS` | **kuratierte Sicht** für Analyse / Dashboard / LibreOffice — schmaler, lesbarer |

"Curated" = englisch *kuratiert / handverlesen* — die für den Endbenutzer relevante Spaltenauswahl.

#### Wozu `--regenerate_curated`?

Aus Performance-Gründen schreibt **nicht jede Stage** den curated Master neu:

- Single-Stage-Runs (`--stage_gates_tags`, `--stage_committees`, …) ohne `--delta`/`--dry_run` schreiben **nur** ins `trades_master_complete.csv` (`_write_stage_output` in `scripts/import_trades.py`).
- Das curated wird sonst nur am Ende der vollständigen Pipeline durch `--stage_save` regeneriert.

→ Wer also nur eine einzelne Stage neu rechnet (z.B. Filter-Logik geändert), aktualisiert das `complete`. LibreOffice öffnet aber das `curated` und sieht weiterhin alte Werte. **`--regenerate_curated`** liest das `complete` und projiziert die kuratierten Spalten neu raus, ohne sonst etwas anzufassen — schneller als ein vollständiger Pipeline-Lauf, weil Scoring/Enrichment nicht erneut berechnet werden.

**Typischer Workflow nach Filter-Änderung:**
```bash
python scripts/import_trades.py --stage_gates_tags     # neu berechnen → complete
python scripts/import_trades.py --regenerate_curated   # Sicht-Refresh → curated
```

#### Wozu `--manual_review_only`?

Schreibt die **Diagnose-CSV** `data/processed/manual_review_tickers.csv` neu — ein Aggregat aller Trades, deren Ticker der Classifier nicht eindeutig zuordnen konnte (`manual_review_flag=True`). Pro Ticker werden Trade-Anzahl, erstes/letztes Trade-Datum und ein Beispiel-Member ausgegeben (`_write_manual_review_subset` in `scripts/import_trades.py`).

**Anwendungsfall:** Du hast `data/mappings/ignored_tickers.csv` erweitert oder den Classifier verbessert und willst die Review-Liste schnell neu erzeugen, ohne die volle Pipeline zu laufen. Der Master selbst wird dabei **nicht** angefasst, nur die Diagnose-CSV wird neu geschrieben (oder gelöscht, wenn keine Reviews mehr offen sind).

#### Merksatz

| Flag | Liest aus | Schreibt | Berührt Master? |
|---|---|---|---|
| `--stage_*` (single) | stage-Input bzw. complete | complete (oder `trades_stage.csv` bei `--delta`/`--dry_run`) | ja (complete) |
| `--regenerate_curated` | complete | curated (`trades_master.csv`) | nein, nur Sicht-Refresh |
| `--manual_review_only` | complete | `manual_review_tickers.csv` | nein, nur Diagnose |

### Komplette Flag-Matrix

| Aufruf | Wirkung |
|---|---|
| `import_trades.py` | Full-Run, schreibt complete + curated + review |
| `--dry_run` | Full-Run, alles in `trades_stage.csv` + review |
| `--delta` | Delta-Detect via trade_id, merge in complete + curated |
| `--delta --dry_run` | Delta in Stage-CSV, kein Master-Touch |
| `--stage_<X>` | Einzel-Stage gegen complete-Master |
| `--stage_<X> --dry_run` | Einzel-Stage in Stage-CSV |
| `--stage_<X> --delta` | Einzel-Stage auf Delta-Subset |
| `--stage_<X> --delta --dry_run` | Stage + Delta, kein Master-Touch |
| `--manual_review_only` | NUR review aus complete |
| `--regenerate_curated` | NUR curated aus complete |

> **Full Rebuild:** Um alle Trades neu anzureichern (z.B. nach Änderung an Member-CSVs
> oder former_members.csv), `data/processed/trades_master.csv` und
> `data/processed/trades_master_complete.csv` löschen und Pipeline
> erneut ausführen. Die Pipeline erkennt den fehlenden Master und behandelt alle Trades als neu.

> **Dedup-Strategie:** `DEDUP_STRATEGY` in `config/settings.py` steuert das Verhalten bei
> identischer `trade_id`:
> - `'keep_all'` (Default) — alle Zeilen bleiben; `trade_id` ist KEIN eindeutiger Schlüssel.
> - `'last_wins'` — nur die letzte Zeile pro `trade_id` wird behalten.
> Wirkt in `_merge_into_master` (`scripts/import_trades.py`) und in
> `scripts/migrate_add_trade_ids.py`. Delta-Erkennung zwischen Läufen ist davon unberührt.

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

**Drei Modi — wichtig zu unterscheiden:**

| Modus | Aufruf | Zerstört Daten? | Wann |
|-------|--------|-----------------|------|
| **Delta-Update** (empfohlen) | `python scrapers/fetch_congress_data.py --update` | Nein | Wöchentlich. Holt nur Bills die sich seit dem letzten Lauf geändert haben (~5-30 Min). |
| **Erstlauf / Fortsetzung vom Checkpoint** | `python scrapers/fetch_congress_data.py` | Nein | Einmalig oder nach Abbruch. Liest Checkpoint und fuehrt nur die noch nicht abgeschlossenen Schritte aus. Bei komplett abgeschlossenem Erstlauf tut der Befehl praktisch nichts — fuer einen "neuen Fetch" stattdessen `--update` benutzen. |
| **Force-Rebuild** (destruktiv, mit Confirmation) | `python scrapers/fetch_congress_data.py --force` | **Ja** | Verschiebt alle 6 Bill-JSONs nach `archive/fetch_congress_data_backup/rebuild_<ts>/` (reversibel) und fetcht alles komplett neu (~7-8h, ~36.000 Requests). Vor Ausfuehrung wird mit getipptem `JA` abgefragt — alles andere bricht ohne Aenderung ab. |

```bash
# Delta-Update (empfohlen, nicht-destruktiv)
python scrapers/fetch_congress_data.py --update

# Erstlauf bzw. Fortsetzung (nicht-destruktiv, nutzt Checkpoint)
python scrapers/fetch_congress_data.py

# Force-Rebuild (DESTRUKTIV mit Confirmation: alle Bill-JSONs werden archiviert + neu gefetcht)
python scrapers/fetch_congress_data.py --force   # fragt 'JA' zur Bestaetigung ab

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
# Einmalig pro Kongress (Erstlauf, nicht-destruktiv, fortsetzbar):
python scrapers/fetch_congress_data.py          # ~7-8h, Ctrl+C unterbricht sicher

# Wöchentlich (Delta-Update, nicht-destruktiv):
python scrapers/fetch_congress_data.py --update  # fetcht nur geänderte Bills
python scripts/import_trades.py                  # Vote-Timing Backfill anwenden
```

> **Hinweis:** `import_trades.py` ruft seit Mai 2026 zu Beginn der filter-Stage automatisch
> `--update` auf, wenn `bill_actions.json`/`bill_subjects.json` älter als
> `settings.VOTE_DATA_TTL_HOURS` (24h) sind. Der manuelle `--update`-Aufruf bleibt nur
> nötig wenn man die Vote-Daten **vor** dem Import explizit auffrischen will.

> **Checkpoint-System:** Bei Abbruch (Ctrl+C oder Verbindungsfehler) einfach neu starten —
> das Script setzt beim letzten abgeschlossenen Schritt fort.
> `--update` nutzt `fromDateTime` der Congress.gov API — nur wirklich geänderte Bills werden gefetcht.
> API-Limit: 5.000 Req/h (mit Key) → 0.75s Pause → ~4.800 Bills/h.

> **Trennung von `trades_master`:** `fetch_congress_data.py` schreibt **ausschließlich**
> die JSON-Dateien unter `data/congress_api/` — `trades_master.csv` /
> `trades_master_complete.csv` werden nie angefasst. Die beiden Datenflüsse sind
> getrennt: ein Rebuild der Bill-Daten (`--force`) lässt deine Trades-Master-CSVs
> unberührt; erst der nächste `python scripts/import_trades.py`-Lauf zieht die
> aktualisierten Vote-Daten in die Filter-Stage.

> **Wann `--force` nötig ist:** Wenn der Erstlauf bereits abgeschlossen ist
> (alle 6 Schritte `completed` im `fetch_checkpoint.json`), gibt der Aufruf
> ohne Args nur `[SKIP]`-Meldungen aus und macht 0 Requests. Für einen
> vollständigen Re-Fetch aller Bill-Daten ist dann **nur `--force`** der
> richtige Befehl — `--update` holt nur seit dem letzten Lauf geänderte Bills.

> **Lock-Datei (seit Mai 2026):** Jeder Lauf legt
> `data/congress_api/.force_in_progress` mit PID + Timestamp + Modus an
> und entfernt sie am Ende. Verhindert paralleles Schreiben durch zwei
> Fetcher-Instanzen (z.B. manueller `--force` während ein anderer Tab
> `--update` ausführt, oder `import_trades.py` den Auto-Refresh ansteuert).
> Bei vorhandenem Lock bricht der zweite Aufruf ab. Stale Lock (PID nicht
> mehr aktiv) wird automatisch erkannt und entfernt. `_ensure_fresh_vote_data`
> in `import_trades.py` skippt den Auto-Refresh wenn Lock vorhanden und
> nutzt die bestehenden JSONs.

> **Force-Stage-Eintritts-Schutz (seit Mai 2026):** `--force` aktiviert nicht
> nur das einmalige Archivieren am Anfang, sondern bleibt **die ganze
> Laufzeit aktiv**: vor jedem Schritt (2-6) prüft der Fetcher, ob die
> zugehörige JSON inzwischen wieder aufgetaucht ist (paralleler Prozess,
> Cron, abgebrochener Resume-Lauf), und verschiebt sie ggf. ebenfalls
> in `archive/fetch_congress_data_backup/rebuild_<ts>/`. Der Rebuild
> bleibt dadurch garantiert sauber, auch wenn parallel Schreiboperationen
> stattfinden.

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

## 13. Pipeline Reset

**Datei:** `scripts/reset_pipeline.py`
**Zweck:** Granularer Reset von Logs, Caches, Stage-Files und Processed-Outputs mit
Defense-in-Depth. Default ist ein reversibler **Move** nach
`archive/reset_pipeline/reset_<ts>/` (inkl. `_manifest.txt`); kuratierte Mappings
(12 PROTECTED-Files) bleiben in jedem Fall unangetastet.

```bash
# Logs (logs/*.log + logs/debug_tesseract/)
python scripts/reset_pipeline.py --logs

# Stage-Files (*_stage.csv, pipeline_state.json, duplicate_inspection_sample.csv)
python scripts/reset_pipeline.py --stage-files

# Ticker-Caches (ticker_type_cache.db, ticker_industry_cache.json, ticker_0_cache.json)
python scripts/reset_pipeline.py --ticker-caches

# Processed-Outputs (trades_master[_complete].csv, manual_review_tickers.csv,
# unmatched_*.csv, delisted_trades.csv, ticker_liste_index.csv)
python scripts/reset_pipeline.py --processed

# Congress-API JSONs (bills_list, bill_details, bill_actions, bill_committees,
# bill_summaries, bill_subjects, fetch_checkpoint, fetch_log, votes_*)
# Hinweis: nach Reset ist ein vollstaendiger ~7-8h Re-Fetch via fetch_congress_data.py noetig
python scripts/reset_pipeline.py --congress-api

# Alle fünf Gruppen
python scripts/reset_pipeline.py --all

# Vorschau ohne Aktion
python scripts/reset_pipeline.py --all --dry-run

# Echtes rm statt Move (irreversibel, doppelte Confirmation)
python scripts/reset_pipeline.py --processed --purge
```

**Confirmation-Stufen:**

| Flag-Kombination | Prompt |
|------------------|--------|
| `--logs`, `--stage-files`, `--ticker-caches` | einfach `[j/N]` |
| `--processed`, `--congress-api`, `--all` | getipptes `JA` |
| zusätzlich `--purge` | getipptes `PURGE` |

**Output:** Pro Datei `[OK]` / `[X]` / `[SKIP]` mit Größe und Dauer, Zwischensummen
pro Gruppe, finale Zusammenfassung. Bei Default-Move zusätzlich `_manifest.txt`
im Archiv-Ordner für Wiederherstellung.

**Geschützte Mappings (nie betroffen):** `name_aliases.csv`, `Vorzugsaktien_mapping.csv`,
House/Senate Member-CSVs, Subcommittee-Mappings, `yf_sectors_with_industries.csv`,
`vote_sector_mapping.py`, `logs/house_pdf_extractor_done.csv` (State).

---

## 14. Streamlit Dashboard

**Datei:** `app/streamlit_app.py`
**Zweck:** Interaktive Web-UI zur Exploration von Trades, Assets und Members. Lädt `trades_master.csv` (gecached); Asset- und Member-Tab werden live aus dem gefilterten Trade-Subset aggregiert (reaktiv auf alle Filter).

```bash
# Linux/Mac
venv/bin/streamlit run app/streamlit_app.py

# Windows
venv\Scripts\streamlit run app/streamlit_app.py

# Alternativer Port
venv/bin/streamlit run app/streamlit_app.py --server.port 8502

# Headless (kein Browser-Fenster)
venv/bin/streamlit run app/streamlit_app.py --server.headless true
```

Default-Port: **8501** → im Browser öffnen: `http://localhost:8501`

**Sidebar-Filter (Kurzübersicht):**
- **Datumsbasis:** `disclosure_date` / `transaction_date` (Default: `disclosure_date`)
- **Zeitfenster:** 7 / 30 / 365 Tage / Alles (Default: 30 Tage)
- **Sortierung:** absteigend / aufsteigend nach `trade_score` (Default: absteigend)
- **Signal-Ausschluss:** Multiselect über alle 9 Signale — löst Voll-Recompute via `app/score_recompute.py` aus
- **Member-Suche:** Substring-Suche auf `name` (case-insensitive)
- **Ticker-Suche:** Substring auf `ticker` ODER `issuer_ticker` (uppercase)
- **Min-Score:** Slider 0 .. max(50, max(trade_score))

**Tabs:** Trades (Standard) | Assets | Members

**Trade-Tab:** enthält abgeleitete Spalte `relevant_sectors` (aus `Relevant_Sectors`-JSON, eindeutige Sektor-Namen kommagetrennt).

**Asset- und Member-Tab:** werden live aus dem gefilterten Trade-Subset aggregiert — alle Sidebar-Filter wirken. Min-Score greift auf `score_max` des Aggregats.

> **Hinweis:** Signal-Ausschluss löst einen Voll-Recompute auf dem gefilterten Subset aus
> (via `scoring/trade_score.py`), damit Paradox-Dämpfung und Ex-Officio-Bonus konsistent
> bleiben. Einfaches String-Filtern würde diese Interaktionen brechen.

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
| Congress Data Fetcher (Delta) | `python scrapers/fetch_congress_data.py --update` | **Empfohlen**: nur geänderte Bills (~5-30 Min, nicht-destruktiv) |
| Congress Data Fetcher (Erstlauf/Resume) | `python scrapers/fetch_congress_data.py` | Erstlauf bzw. Checkpoint-Fortsetzung (~7-8h, nicht-destruktiv) |
| Congress Data Fetcher (Force-Rebuild) | `python scrapers/fetch_congress_data.py --force` | **DESTRUKTIV** (mit `JA`-Confirmation): JSONs archivieren + von 0 neu fetchen (~7-8h) |
| Tests | `pytest tests/ -v` | Alle Unit-Tests |
| Pipeline Reset | `python scripts/reset_pipeline.py --all --dry-run` | Logs/Caches/Stage/Processed reversibel zurücksetzen |
| Streamlit Dashboard | `venv/bin/streamlit run app/streamlit_app.py` | Web-UI (Trades / Assets / Members) auf Port 8501 |
