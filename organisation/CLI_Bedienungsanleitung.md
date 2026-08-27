# CLI Bedienungsanleitung – Congress Trade Tracker

Alle Befehle werden aus dem Projektverzeichnis `congress_tracker/` ausgeführt, mit aktiviertem venv:

```bash
cd <Projektverzeichnis>        # z.B. /media/bene/.../congress_tracker  oder  C:\...\congress_tracker
source venv/bin/activate        # Linux/Mac
# oder: venv\Scripts\activate  # Windows
```

---

## 0. Quiver Auto-Download-Scraper

**Datei:** `scrapers/quiver_scraper.py`
**Zweck:** Automatisierter täglicher Download der Congress-Trading-XLSX von Quiver Quantitative

**Voraussetzung:**
- `.env` mit `QUIVER_EMAIL` und `QUIVER_PASSWORD` gesetzt
- Selektoren in `config/settings.py` konfiguriert (siehe unten)
- `playwright install chromium` ausgeführt

```bash
# Standard: Download + atome Speicherung
python scrapers/quiver_scraper.py

# Browser sichtbar (Debug/Selektor-Abgleich)
python scrapers/quiver_scraper.py --no-headless

# Trockenrun: Download in Temp-Datei, Zieldatei NICHT ersetzen
python scrapers/quiver_scraper.py --dry-run
```

**Output:** `data/raw/congress-trading-all.xlsx`

**Exit-Codes:**
```
0   Erfolg
1   Generischer Fehler
2   Login fehlgeschlagen (Credentials / 2FA / Account blockiert)
3   Eingeloggt aber Download fehlgeschlagen (abgelaufenes Abo, File-API-Error)
```

### Selektor-Setup

Die Selektoren sind in `config/settings.py` bereits **konfiguriert und end-to-end
verifiziert** (Stand 2026-06-04). Ablauf des Scrapers:
Login → Cookie-Banner ablehnen → `/export/` direkt ansteuern → Congress-XLSX per
href-Selektor herunterladen (kein Menü-Klicken nötig).

Aktive Werte:
```python
QUIVER_LOGIN_URL         = 'https://www.quiverquant.com/login/'
QUIVER_EXPORT_URL        = 'https://www.quiverquant.com/export/'
QUIVER_COOKIE_REJECT_SELECTOR = 'role=button[name="Reject All"]'   # best-effort
QUIVER_EMAIL_SELECTOR    = 'role=textbox[name="Username"]'         # E-Mail ins Username-Feld
QUIVER_PASSWORD_SELECTOR = 'role=textbox[name="Password"]'
QUIVER_SUBMIT_SELECTOR   = 'role=button[name="Sign In"]'
QUIVER_POST_LOGIN_SELECTOR = '#hamburger-icon'                     # abo-unabhängig (s. WICHTIG)
QUIVER_DOWNLOAD_SELECTOR = 'a[href*="congress-trading-all.xlsx"]' # href-basiert, reihenfolge-unabhängig
```

Müssen die Selektoren nach einem Quiver-Redesign neu ermittelt werden:
`playwright codegen https://www.quiverquant.com/login/` (Python-Playwright aus dem venv),
Interaktionen aufnehmen, Selektoren extrahieren und oben ersetzen.

> **WICHTIG — `QUIVER_POST_LOGIN_SELECTOR` muss ABO-UNABHÄNGIG sein.**
> Element, das bei *jedem* eingeloggten User existiert (hier: `#hamburger-icon`),
> **niemals** paywalled Inhalt. Der Login gelingt auch ohne aktives Abo. Nur ein
> abo-unabhängiges Post-Login-Element hält die Trennung Login-Fehler (Exit 2) vs.
> abgelaufenes Abo / Download-Fehler (Exit 3) korrekt — die Premium-Gating fällt
> erst beim Export-Download auf, nicht beim Login.

### .env Datei

Vorlage `.env.example` bereits vorhanden:
```
QUIVER_EMAIL=
QUIVER_PASSWORD=
```

Echte `.env` Werte nicht in Git committen — `.env` ist gitignored.

### Scheduler: systemd-User-Timer (täglich 03:00)

Scraper + Import laufen verkettet über einen **systemd-User-Timer** (statt cron),
weil `Persistent=true` einen verpassten 03:00-Lauf nachholt, sobald der Laptop
wieder läuft — ein reiner cron-Job würde bei nachts ausgeschaltetem Rechner nie
auslösen. Die Service-Unit hat zwei `ExecStart`-Schritte: schlägt der Scraper fehl
(Exit ≠ 0), wird `import_trades.py` nicht ausgeführt (entspricht der `&&`-Semantik).

Einrichtung + Bedienung (Single-Source): **`deploy/systemd/README.md`**.

Kurz-Diagnose:
```bash
systemctl --user list-timers quiver-import.timer   # nächster/letzter Lauf
systemctl --user start quiver-import.service        # sofort manuell auslösen
journalctl --user -u quiver-import.service -e       # Logs (zusätzl. logs/quiver_cron.log)
```

**Voraussetzungen:**
- Host-`venv` unter `<projekt>/venv/bin/python` (nicht `/opt/venv`, das ist nur im Docker-Image)
- Playwright-Chromium installiert (`venv/bin/playwright install chromium`)
- `.env` mit `QUIVER_EMAIL`/`QUIVER_PASSWORD` im Projekt-Root (wird via `WorkingDirectory` gefunden)

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

## 3. Import Pipeline (18 Stages)

**Datei:** `scripts/import_trades.py`
**Zweck:** Liest Quiver-Snapshot (`congress-trading-all.xlsx`), matcht Member-Namen (Alias→Exakt→Former→Fuzzy), klassifiziert Tickers offline, normalisiert via OpenFIGI, reichert via yfinance an, wendet Filter an.

**Spalten-Namenskonvention** (seit 2026-05-14): Externe-API-Outputs tragen
Provider-Suffixe — `_figi` (OpenFIGI: `ticker_figi`, `asset_name_figi`,
`security_type_figi`, `market_sector_figi`), `_yf` (yfinance: `sector_yf`,
`industry_yf`, `quote_type_yf`). Die ID-Spalte `figi` selbst behaelt keinen
Suffix. Details siehe `organisation/DATA_STRUCTURE.md`.

```bash
# Standard: vollständige Pipeline (alle 17 Stages sequenziell)
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
- `data/processed/unmatched_names.csv` (bei Bedarf)

### Dry-Run-Ergebnisse in `trades_master_complete.csv` uebernehmen

`--dry_run` schreibt absichtlich **nur** nach `data/processed/trades_stage.csv` —
Sicherheits-Sandbox, damit der Master unangetastet bleibt. Wenn der Dry-Run
geprueft ist und das Ergebnis in den `complete`-Master soll, gibt es zwei Wege:

**Weg 1 — Promotion via `--stage_save` (empfohlen, wenn der Dry-Run bereits gelaufen ist):**

```bash
# 1. Dry-Run anschauen / sanity-checken
python scripts/import_trades.py --dry_run            # -> trades_stage.csv
head -1 data/processed/trades_stage.csv              # Spalten pruefen
wc -l data/processed/trades_stage.csv                # Zeilen zaehlen

# 2. Promoten: Stage-CSV -> complete + curated Master
python scripts/import_trades.py --stage_save
```

`_stage_save` legt vorher automatisch ein Backup unter
`archive/trades_master_backup/` an, schreibt `trades_master_complete.csv`
**komplett neu** aus `trades_stage.csv`, regeneriert `trades_master.csv`
(kuratiert) und raeumt `trades_stage.csv` danach auf.

> **Achtung — `--stage_save` ohne `--delta` ersetzt den Master komplett.**
> Das ist nur sicher, wenn der vorhergehende Dry-Run eine **volle Pipeline**
> war (`python scripts/import_trades.py --dry_run`). Bei einem Single-Stage-Dry-Run
> (`--stage_post_trade_performance --dry_run`) enthaelt `trades_stage.csv` nur das
> Snapshot dieser einen Stage — ein anschliessendes `--stage_save` wuerde
> dann den Master auf diesen Snapshot reduzieren. In dem Fall stattdessen
> Weg 2 nehmen.

**Weg 2 — Direkt in den Master schreiben (Single-Stage, kein `--dry_run`):**

Wenn nur eine einzelne Stage ausgefuehrt wird, schreibt `_write_stage_output`
ohne `--dry_run` und ohne `--delta` automatisch direkt in
`trades_master_complete.csv` (mit Backup vorher). Alle anderen Spalten bleiben
erhalten — die Stage fuegt nur ihre eigenen Output-Spalten hinzu bzw. ueberschreibt
diese.

```bash
# Direkter Stage-Lauf -> trades_master_complete.csv (mit auto-Backup)
python scripts/import_trades.py --stage_post_trade_performance

# Optional: kuratierte Sicht aktualisieren (falls Stage curated-relevante Spalten anfasst)
python scripts/import_trades.py --regenerate_curated
```

**Wann welcher Weg?**

| Situation | Weg | Befehl |
|---|---|---|
| Volle Pipeline-Verifikation, dann Promotion | Weg 1 | `--dry_run` → Pruefung → `--stage_save` |
| Einzelne Stage soll direkt in Master | Weg 2 | nur `--stage_<X>` (ohne `--dry_run`) |
| Einzelne Stage erst pruefen, dann promoten | Weg 1 (vorsichtig) | `--stage_<X> --dry_run` → Pruefung → `--stage_save` **erst wenn klar ist, dass der Stage-Snapshot vollstaendig ist** |

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
python scripts/import_trades.py --stage_trade_volume      # trade_volume_usd aus Amount-Range (MUSS VOR post_trade_returns)
python scripts/import_trades.py --stage_post_trade_performance  # Post-Trade-Preise + Exit-Detection (Modul 9.x, braucht trade_amount_max_usd)
python scripts/import_trades.py --stage_abnormal_returns  # Abnormal Return (marktbereinigt vs. SPY) → signed_ar_{H}d (deskriptiv, kein Scoring-Input)
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

Stage-Reihenfolge und Indizes werden von `scripts/import_trades.py` definiert.

### Welche Stages bei Scoring-Anpassungen neu laufen lassen?

Hängt davon ab, welcher Parameter in `config/settings.py` geändert wurde. Drei Fälle:

**Fall A — Nur Score-Gewichte / Formel-Parameter** (z. B. `SCORING_WEIGHTS`, `SCORING_PARADOX_*`, Ex-Officio-Multiplikator — alles was ausschliesslich in `scoring/trade_score.py` gelesen wird):

```bash
python scripts/import_trades.py --stage_scoring
python scripts/import_trades.py --stage_suspicious_export
python scripts/import_trades.py --stage_save
python scripts/import_trades.py --regenerate_curated   # optional, falls curated benötigt
```

`_stage_scoring` rechnet `trade_score`, `trade_score_breakdown`, `trade_warnings` aus dem unveränderten `signals_triggered` neu. `_stage_suspicious_export` materialisiert anschliessend `suspicious_assets.csv` / `suspicious_members.csv` mit den neuen Scores.

**Fall B — Signal-Trigger-Schwellen geändert** (z. B. `SWARM_*`, `SEQUENCE_*`, `EXTREME_REPORT_*`, Vote-Timing-Trigger — Parameter, die bestimmen, *ob* ein Signal feuert, nicht nur dessen Gewicht):

Ab der betroffenen Signal-Stage neu laufen lassen, dann Scoring + Export + Save:

```bash
--stage_branch1_signals    # bei committee / ex_officio / floor_action / extreme_report
--stage_sequence_signals   # bei contrarian / accumulative
--stage_swarm_signals      # bei swarm_buy / swarm_sell / swarm_conflict / swarm_paradox
--stage_scoring
--stage_suspicious_export
--stage_save
```

**Fall C — Gate/Tag-Parameter geändert** (z. B. `COMMITTEE_*`, `VOTE_TIMING_*`, Asset-Class-Gate — Parameter, die Master-Spalten wie `Insider_Sectors`, `Relevant_Sectors`, `Vote_Timing_Hit_Count` erzeugen):

Ab `--stage_gates_tags` neu, danach alle Signal-Stages + Scoring + Export + Save (= praktisch P2..P5 der Pipeline). Bei sehr breiten Tag-Änderungen ist oft ein voller `python scripts/import_trades.py` einfacher.

**Merksatz:** `STAGE_DEPS` in `scripts/import_trades.py` zeigt pro Stage, welche Spalten produziert oder konsumiert werden — daraus lässt sich der minimale Re-Run ableiten.

> **Re-Run-Sequenz nach Roster-/Committee-Daten-Änderung** (Abschnitt 6/7
> neu ausgeführt → `data/mappings/{house,senate}_member&(sub)committees.csv`
> geändert): vollständig
> ```
> --stage_committees → --stage_gates_tags → --stage_branch1_signals →
> --stage_sequence_signals → --stage_swarm_signals → --stage_scoring
> (+ --regenerate_curated)
> ```
> durchlaufen lassen. Grund: `--stage_branch1_signals` baut
> `signals_triggered` von Grund auf neu — ohne die anschließenden
> sequence-/swarm-Stages verschwinden Sequenz-/Schwarm-Signale aus dem
> Master. Die Datenfelder sind in `organisation/DATA_STRUCTURE.md` dokumentiert.

### Bonus-Flags (kein Master-Touch ausser Curated, exklusiv)

```bash
python scripts/import_trades.py --manual_review_only   # NUR review aus complete
python scripts/import_trades.py --regenerate_curated   # NUR curated aus complete
```

#### Hintergrund: "complete" vs. "curated" Master

Die Pipeline schreibt **zwei** Master-Dateien:

| Datei | Spalten | Zweck |
|---|---|---|
| `data/processed/trades_master_complete.csv` | **alle** Pipeline-Spalten (~50+, inkl. interner Felder wie `Member_Committees_Full`, `figi`, `ticker_figi`, `manual_review_flag`, `classification_reason`) | "Quelle der Wahrheit", verlustfrei — daraus wird alles andere abgeleitet |
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

## 4. XLSX → CSV Konvertierung (entfernt)

`scripts/convert_xlsx_to_csv.py` wurde im Debug-Intervall 4 entfernt (Input-xlsx
existieren nicht mehr; der Mapping-Master `subcommittee_sector_industry_mapping.csv`
bleibt als geschützter Produktionsmaster bestehen; eine Neugenerierung ist erst
mit der geplanten V2-Integration vorgesehen, s. Abschnitt 15).

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

## 5b. Event-Study / Abnormal-Return-Analyse (Discovery)

**Datei:** `scripts/analyse_event_study.py`
**Zweck:** Validiert den `trade_score` gegen den signierten, marktbereinigten Abnormal Return (signed BHAR). Keine Flags — alle Parameter ueber den `AR_*`-Block in `config/settings.py`.

```bash
python scripts/analyse_event_study.py
```

**Outputs:**
- `organisation/analyse_event_study.md` — Befund-Report (Single-Source der Ergebnisse)
- `data/processed/ar_outliers.csv` — Ausreißer-Trades (gitignored)

**Hinweise:** Dieses Standalone-Skript schreibt keinen Master (Konstrukt B + Bootstrap nur in-memory). Die **produktive Stage `--stage_abnormal_returns`** (Abschnitt 3) schreibt dagegen die `signed_ar_*` (Konstrukt A) in den Master und speist die App-Detail-Popups. Settings-Aenderung = Skript bzw. Stage neu laufen lassen. Spalten + Metrik: `DATA_STRUCTURE.md` Abschnitt 7e.

---

## 6. House Committee PDF Parser

**Datei:** `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py`
**Zweck:** Extrahiert Member + (Sub)Committee-Zugehörigkeit (F2-Format) aus
dem offiziellen House Committee PDF (`scsoal.pdf`, clerk.house.gov, committet)
**Wann:** Nur bei Neuwahlen (alle 2 Jahre) oder nach Committee-Umstrukturierungen
**Status (2026-06-12):** generalisiert auf alle 20 Standing + 2 Select
(Intelligence mit 6 Subs, China) + 4 Joint Committees (House-Hälfte;
Senatoren kommen aus dem Senate-XML, s. Abschnitt 7) → 436 Member, 26
Committees, 437 Zeilen.

```bash
# Standard-Aufruf
python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py --pdf <pfad/zur/scsoal.pdf>

# Mit eigenem Output-Pfad
python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py \
  --pdf <pfad/zur/scsoal.pdf> \
  --out "data/mappings/house_member&(sub)committees.csv"
```

**Output:** CSV mit Spalten `Name, Party, State, Committees, ExOfficio` —
`Committees` im F2-Format (Spec: `scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md`).

**Struktur-Validierung:** `scrapers/house_pdf_parsing_tool/validate_house.py`
prüft den Output gegen das Struktur-Orakel
`data/mappings/us_house_all_committees.csv` (Stand 2026-06-12: 0 Abweichungen
bei 26 Committees).

---

## 7. Senate Committee XML-Pipeline

**Dateien:** `scrapers/senate_committee_xml/fetch_senate_xml.py` +
`scrapers/senate_committee_xml/parse_senate_xml.py`
**Zweck:** Extrahiert Member + (Sub)Committee-Zugehörigkeit (F2-Format) aus
den offiziellen Senate-Committee-XML-Daten
**Wann:** Nur bei Neuwahlen (alle 2 Jahre) oder nach Committee-Umstrukturierungen
**Status (2026-06-12):** ersetzt den alten
`scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py`
(LEGACY/retiriert, samt PDF) — 100 Senatoren, 101 Zeilen.

```bash
# 1. XML-Snapshots aktualisieren (dynamische Committee-Discovery aus dem
#    senate.gov-Dropdown, kein Hardcoding; curl_cffi Chrome-Impersonation
#    gegen Akamai-403)
python scrapers/senate_committee_xml/fetch_senate_xml.py
# → scrapers/senate_committee_xml/raw/*.xml (26 Dateien, committet) +
#   _manifest.json (Change-Report +NEU/−ENTFALLEN)

# 2. XML → F2-CSV (Produktiv-Ziel explizit angeben — Default ist
#    /tmp/senate_f2_xml_output.csv)
python scrapers/senate_committee_xml/parse_senate_xml.py "data/mappings/senate_member&(sub)committees.csv"
```

**Output:** CSV mit Spalten `Name, Party, State, Committees, ExOfficio` —
`Committees` im F2-Format (Spec: `scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md`),
Name-Format „Last, First".

**Struktur-Validierung:** gegen Struktur-Orakel
`data/mappings/us_senate_all_committees.csv` (Stand 2026-06-12: 70/70 Sub-Paare).

---

## 7b. Committee-Struktur-Liste (beide Kammern)

**Datei:** `scrapers/generate_committee_structure.py`
**Zweck:** Erzeugt `data/processed/committees_structure.csv`
(`Chamber, Committee, Subcommittee`, gitignored) aus den Roster-CSVs beider
Kammern — für Validierung/Vollständigkeitsprüfung.
**Wann:** Nach jeder Roster-Neuerzeugung (Abschnitt 6/7)

```bash
python scrapers/generate_committee_structure.py
```

---

## 7c. Committee Matcher V2 – Inputbaseline (P0)

**Dateien:** `scripts/committee_matcher_migrate_inputs.py`,
`scripts/committee_matcher_baseline.py`
**Zweck:** Migriert die 46 kanonischen Committee-Markdowns auf den Inputvertrag
(`unit_input_basis`) und erzeugt/prueft Corpusmanifest sowie Schutzbaseline.
**Wann:** Einmalig zur Baselinebildung; danach als Regressionscheck, bevor eine
spaetere Matcherphase auf den Inputs aufsetzt.
**Netzwerk:** keiner der Befehle ruft externe Dienste auf.

```bash
# 1. Migration trocken pruefen (schreibt nichts)
python scripts/committee_matcher_migrate_inputs.py --check

# 2. Migration anwenden (atomar je Datei, anschliessend Vollvalidierung)
python scripts/committee_matcher_migrate_inputs.py --apply

# 3. Klassifikation je Datei ausgeben (Units, name_only, Delta)
python scripts/committee_matcher_migrate_inputs.py --report

# 4. Corpusmanifest erzeugen bzw. gegen die Platte pruefen
python scripts/committee_matcher_baseline.py manifest --write
python scripts/committee_matcher_baseline.py manifest --verify

# 5. Schutzbaseline der Produktionspfade erzeugen bzw. pruefen
python scripts/committee_matcher_baseline.py protect --write
python scripts/committee_matcher_baseline.py protect --verify

# 6. Unit-Sollmenge ableiten (ohne kanonische IDs; unit-id-v1 folgt in P3)
python scripts/committee_matcher_baseline.py units
python scripts/committee_matcher_baseline.py units --list
```

**Flags**

| Flag | Skript | Wirkung |
|---|---|---|
| `--check` | migrate | Sollzustand berechnen, nichts schreiben |
| `--apply` | migrate | Sollzustand atomar schreiben + validieren |
| `--report` | migrate | Tabelle je Datei (Kammer, Subcommittees, name_only, Delta) |
| `--input-root PFAD` | migrate, baseline | Abweichender Inputroot; eingefrorene Sollwerte gelten dann nicht (nur fuer Tests) |
| `manifest --write/--verify` | baseline | Corpusmanifest erzeugen bzw. Rollen + SHA-256 pruefen |
| `protect --write/--verify` | baseline | Schutzbaseline erzeugen bzw. Unveraendertheit pruefen |
| `units [--list]` | baseline | Unit-Sollmenge zaehlen bzw. einzeln ausgeben |

**Exit-Codes** (Teilmenge der verbindlichen V2-Matrix): `0` erfolgreich ·
`4` Input-/Config-Preflight blockiert (Strukturfehler oder ausstehende
Migration) · `10` unerwarteter interner Fehler.

Beide Skripte geben immer einen nach Schweregrad und Fehlercode gruppierten
Abschlussbericht aus — auch bei null Befunden. `--apply` ist idempotent: ein
zweiter Lauf schreibt nichts mehr, weil ein vorhandenes `unit_input_basis` die
massgebliche Quelle der Basiswerte ist.

`--apply` arbeitet corpusweit **all-or-nothing**: Zuerst berechnet ein Preflight
den Sollzustand aller Dateien samt Struktur-, Corpus- und Kohaerenzpruefung;
erst bei null Blockern wird geschrieben. Ein Schreibfehler rollt den gesamten
Lauf zurueck. Jeder Mischzustand aus migrierten und unmigrierten Dateien
(`COR_STATE_MIXED`, `COR_STATE_LEGACY_RESIDUE`, `COR_STATE_LEGACY_INCOHERENT`)
blockiert, statt einen fehlenden `name_only`-Wert zu raten.

`manifest --verify` und `protect --verify` vergleichen die gespeicherte
Baselinedatei feldweise mit dem neu berechneten Sollpayload — inklusive Rolle,
Bytezahl, Hash, Kopffeldern, doppelten Eintraegen (`*_DUPLICATE`), verwaisten
Eintraegen (`*_STALE_ENTRY`) und nicht manifestierten Dateien
(`MAN_UNMANIFESTED`).

Artefakte + Spalten: `DATA_STRUCTURE.md` Abschnitt 3a.
Tests: `tests/test_committee_matcher_input_baseline.py` (90 Tests, netzwerkfrei).

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

# Stage-Files (*_stage.csv, pipeline_state.json, duplicate_inspection_sample.csv,
#              .enrichment_checkpoint.pkl)
python scripts/reset_pipeline.py --stage-files

# Cache-Reset: gesamtes data/cache/* (ticker_type_cache.db / OpenFIGI,
#   edgar_cik_map.json / SEC EDGAR, price_history.parquet / Post-Trade-Preise
#   + jeder künftige Cache) PLUS yfinance-Sektor-Caches unter data/mappings/
#   (ticker_industry_cache.json, ticker_0_cache.json).
# Hinweis: naechster import_trades-Lauf ist netzabhaengig (EDGAR- und yfinance-Refetch).
python scripts/reset_pipeline.py --cache_reset

# Processed-Outputs (trades_master[_complete].csv, manual_review_tickers.csv,
#   unmatched_*.csv, delisted_trades.csv, ticker_liste_index.csv,
#   suspicious_events.csv, suspicious_assets.csv, suspicious_members.csv)
python scripts/reset_pipeline.py --processed

# Congress-API JSONs (bills_list, bill_details, bill_actions, bill_committees,
# bill_summaries, bill_subjects, fetch_checkpoint, fetch_log)
# Hinweis: nach Reset ist ein vollstaendiger ~7-8h Re-Fetch via fetch_congress_data.py noetig
python scripts/reset_pipeline.py --congress-api

# Cache-unabhaengiger import_trades-Rebuild in einem Befehl:
#   stage-files + cache_reset + processed (OHNE logs/congress-api)
# Naechster Lauf ist netzabhaengig (EDGAR + yfinance). Erfordert getipptes "JA".
python scripts/reset_pipeline.py --rebuild

# Alle fuenf Gruppen (inkl. logs + congress-api)
python scripts/reset_pipeline.py --all

# Vorschau ohne Aktion
python scripts/reset_pipeline.py --rebuild --dry-run

# Echtes rm statt Move (irreversibel, doppelte Confirmation)
python scripts/reset_pipeline.py --processed --purge
```

**Confirmation-Stufen:**

| Flag-Kombination | Prompt |
|------------------|--------|
| `--logs`, `--stage-files`, `--cache_reset` | einfach `[j/N]` |
| `--processed`, `--congress-api`, `--rebuild`, `--all` | getipptes `JA` |
| zusätzlich `--purge` | getipptes `PURGE` |

**Output:** Pro Datei `[OK]` / `[X]` / `[SKIP]` mit Größe und Dauer, Zwischensummen
pro Gruppe, finale Zusammenfassung. Bei Default-Move zusätzlich `_manifest.txt`
im Archiv-Ordner für Wiederherstellung.

**Geschützte Mappings (nie betroffen):** `name_aliases.csv`,
House/Senate Member-CSVs, Subcommittee-Mappings, `yf_sectors_with_industries.csv`,
`logs/house_pdf_extractor_done.csv` (State).

---

## 14. Streamlit Dashboard

**Datei:** `app/streamlit_app.py`
**Zweck:** Interaktive Web-UI zur Exploration von Trades, Assets, Members und Post-Trade-Performance. Lädt `trades_master.csv` (gecached); Asset- und Member-Tab werden live aus dem gefilterten Trade-Subset aggregiert (reaktiv auf alle Filter).

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

**Tabs:** Trades (Standard) | Assets | Members | Post Trade Performance

**Trade-Tab:** enthält abgeleitete Spalte `relevant_sectors` (aus `Relevant_Sectors`-JSON, eindeutige Sektor-Namen kommagetrennt) + Spalte `trade_id` (Hash-ID).

**Asset- und Member-Tab:** werden live aus dem gefilterten Trade-Subset aggregiert — alle Sidebar-Filter wirken. Min-Score greift auf `score_max` des Aggregats.

**Post-Trade-Performance-Tab:** Top-25 nach Kapital (`trade_amount_max_usd`) sortiert + Ausklapp-Buttons (+100 / Alle anzeigen) + Freitext-Suche (Name / Ticker / Hash-ID, case-insensitive) + Performance-Charts (Preisverlauf, Exit-Status-Visualisierung).

> **Hinweis:** Signal-Ausschluss löst einen Voll-Recompute auf dem gefilterten Subset aus
> (via `scoring/trade_score.py`), damit Paradox-Dämpfung und Ex-Officio-Bonus konsistent
> bleiben. Einfaches String-Filtern würde diese Interaktionen brechen.

---

## 15. Committee Matcher V2 – CLI und Settings (P2-Gerüst, P3-Kern, P4-Snapshot)

**Paket:** `committee_matcher/` · **Einstieg:** `python -m committee_matcher`
**Zweck:** Providerfreier Kern des neu implementierten Matchers plus der
Routing-Preflight; kennt alle CLI-Familien, prüft die Settings und blockiert
jeden Providerpfad mit unvollständigen Providerwerten.
**Netzwerk:** der Paketimport zieht weder `openrouter` noch `yfinance` oder
eine externe HTTP-Bibliothek nach — `provider.py` nutzt das stdlib-`urllib`,
yfinance lädt erst der erste Abruf. Netzwerk berühren
`snapshot build`, `snapshot resume` (yfinance) und `provider check` (öffentlicher
OpenRouter-Katalog, kein Key, keine Kosten). Ein Modell ruft bis einschließlich
P4 kein Kommando auf.

Der frühere Aufruf `python -m scripts.committee_mapping_agent.run ...` ist nach
dem V1-Rückbau nicht mehr verfügbar; der produktive Mapping-Master bleibt
unverändert.

### Kompaktablauf — Entwicklungsrun bis reviewed CSV

Ein Run-ID bleibt vom Snapshot bis zum Review unverändert; Beispiel:
`RUN_ID=dev-agriculture-20260827`.

```text
Input + Roster prüfen
  -> Taxonomie abrufen + prüfen + freigeben
  -> Providerroute prüfen
  -> kleinen Entwicklungsrun planen
  -> Kosten prüfen
  -> kostenpflichtig ausführen
  -> bei Exit 8: abgleichen, dann fortsetzen
  -> Excel-Workbook exportieren
  -> in Excel reviewen
  -> validiert importieren
  -> reviewed_assignments.csv an import_trades übergeben
```

```bash
# 1. Lokale Vorprüfungen; keine Modellkosten
RUN_ID=dev-agriculture-20260827
venv/bin/python -m committee_matcher input validate --run-id "$RUN_ID"
venv/bin/python -m committee_matcher roster validate --run-id "$RUN_ID"

# 2. Aktuelle Industry-Taxonomie; Netzwerk, keine Modellkosten
venv/bin/python -m committee_matcher snapshot build --run-id "$RUN_ID"
# Bei Unterbrechung: snapshot resume statt build
venv/bin/python -m committee_matcher snapshot approve --run-id "$RUN_ID"

# 3. Routing kostenlos prüfen; danach kleinen Run ohne Modellcall planen
venv/bin/python -m committee_matcher provider check --run-id "$RUN_ID"
venv/bin/python -m committee_matcher run plan --run-id "$RUN_ID" \
  --committee "Committee on Agriculture"

# 4. Plan + Worst-Case-Kosten prüfen; erst dann Modellcall starten
venv/bin/python -m committee_matcher run execute --run-id "$RUN_ID"

# Nur bei Exit 8 (outcome_unknown): Providerausgang klären, danach execute erneut
venv/bin/python -m committee_matcher run reconcile --run-id "$RUN_ID"
venv/bin/python -m committee_matcher run execute --run-id "$RUN_ID"

# 5. Review; Werte im Blatt 'assignments' direkt ändern
venv/bin/python -m committee_matcher review export --run-id "$RUN_ID"
venv/bin/python -m committee_matcher review import --run-id "$RUN_ID"
```

Ergebnis nach erfolgreichem Import:
`data/committee_matcher_v2/reviews/review-<RUN_ID>/reviewed_assignments.csv`.
Diese Datei ist die vorgesehene Übergabe an `import_trades`; der genaue
Aufruf für diese Übernahme bleibt offen.

Offen: P8-Live-Roundtrip; `evaluate goldset`, `qualify holdout`, `full-run`,
`report` + ihr Bedienablauf. Diese Kommandos bis zur Implementierung nicht
ausführen; sie enden derzeit mit Exit 4.

```bash
python -m committee_matcher --help
python -m committee_matcher input validate      # Corpus + Units + Sollmenge
python -m committee_matcher roster validate     # Roster-Coverage gegen die Units
python -m committee_matcher provider check      # Endpointverfügbarkeit, kostenlos
python -m committee_matcher run plan --run-id run-1 --committee "Committee on Agriculture"
python -m committee_matcher run execute --run-id run-1   # kostenpflichtig
python -m committee_matcher review export --run-id run-1 # Workbook für den Review
```

`input validate` und `roster validate` sind seit P3 implementiert, die
`snapshot`-Familie seit P4, `provider check` und `provider smoke` seit dem
Gate-B-Beschluss, `run plan|execute|reconcile` seit P7c und
`review export|import` seit P7d; `evaluate`, `qualify`, `full-run` und `report`
melden weiterhin `CLI_STAGE_NOT_IMPLEMENTED` mit ihrer Phase.

**Familien** (vollständig, Plan 2.7 — es gibt kein `promote`, `finalize`,
`research` oder LLM-`review`):

| Kommando | Phase | Provider | Zweck |
|---|---|---|---|
| `input validate` | P3 | nein | Corpusmanifest, Parser, Unitbildung, 225-ID-Sollmenge |
| `roster validate` | P3 | nein | Roster-Coverage inkl. Joint-Cross-Chamber-Regel |
| `snapshot build\|approve\|resume` | P4 | nein | yfinance-Snapshot erzeugen, freigeben, fortsetzen |
| `provider check` | P5 | ja | Endpointverfügbarkeit prüfen (kostenlos, kein Modellaufruf) |
| `provider smoke` | P5 | ja | Contract-Smoke: ein kostenpflichtiger Minimalcall |
| `run plan\|execute\|reconcile` | P7 | ja | Entwicklungsrun planen, ausführen, `outcome_unknown` klären |
| `review export\|import` | P7 | nein | Review-XLSX exportieren und validiert zurückimportieren |
| `evaluate goldset` | P9 | ja | Entwicklungs-Gold-Set auswerten |
| `qualify holdout` | P10 | ja | unabhängiges Holdout qualifizieren |
| `full-run` | P11 | ja | qualifizierter, filterloser Full-Run |
| `report` | P7 | nein | Fehler-, Kosten- und Coverage-Bericht einer Revision |

Die Zielangaben `--chamber`, `--committee` und `--unit-id` existieren nur bei
`run` und `evaluate`; `full-run` lehnt sie schon beim Parsen ab (Exit 4).

**Exit-Codes** (verbindliche Matrix, Plan 2.7): `0` erfolgreich oder erwartete
Human-Übergabe · `2` erfolgreich mit Warnungen · `3` finale fehlgeschlagene
Mapping-Units · `4` Input-/Prompt-/Config-Preflight blockiert ·
`5` `awaiting_taxonomy_approval` · `6` Kostenlimit oder `cost_unknown` ·
`7` Review-/Gold-Set-/Holdout-Import abgelehnt · `8` `outcome_unknown` ·
`9` nicht-retrybarer Providerfehler · `10` interner Fehler · `130` Abbruch.
`1` bleibt unbelegt; ein Bedienfehler von argparse liefert bewusst `4` statt `2`.

Jeder Lauf endet mit einem nach Schweregrad und Stage gruppierten Bericht mit
Fehlercode, Objekt, Handlungshinweis, Kosten und Artefaktpfaden — auch bei null
Befunden, bei Benutzerabbruch und bei einem Bedienfehler des Parsers. Noch nicht
implementierte Stages melden `CLI_STAGE_NOT_IMPLEMENTED` mit der Phase, in der
sie entstehen.

Zusätzlich persistiert jeder Lauf die Befunde strukturiert nach
`data/committee_matcher_v2/reports/<kommando>/errors.json` (Bedienfehler unter
`reports/usage/`). Ein laufgebundenes Kommando schreibt stattdessen nach
`runs/<run_id>/reports/<kommando>/errors.json`; sonst überschriebe der nächste
Aufruf desselben Kommandos genau den Bericht, der den vorigen Lauf erklärt. Der
Schreibweg läuft über die Pfadwache und ist atomar. Diese volatilen Berichte
sind gitignoriert, Run-Revisionen bleiben versioniert.

Parallel zum Abschlussbericht meldet jeder Vorgang seinen Stand live:
`[START|OK|INFO|WARN|ERROR|WAIT] <stage> | feld=wert`, Status auf `stdout`,
Warnungen und Fehler auf `stderr`. `no_mapping` ist eine gültige Entscheidung
und erscheint als `INFO`; ein Human-Gate erscheint als `WAIT`.

**Settings** (`config/settings.py`, Block „COMMITTEE MATCHER V2"; Verbraucher
ist ausschließlich `committee_matcher/settings_view.py`):

| Setting | Typ / Bereich | Default | Verbraucher |
|---|---|---|---|
| `COMMITTEE_MATCHER_MAX_FILE_BYTES` | int 1.000–10.000.000 | 512000 | Inputparser (P3) |
| `COMMITTEE_MATCHER_MAX_FRONTMATTER_BYTES` | int 200–1.000.000 | 32000 | Inputparser (P3) |
| `COMMITTEE_MATCHER_MIN_EVIDENCE_CHARS` | int 0–5.000 | 60 | Inputparser (P3) |
| `COMMITTEE_MATCHER_YFINANCE_REQUEST_PAUSE_S` | float 0–60 | 1.5 | Snapshot (P4) |
| `COMMITTEE_MATCHER_YFINANCE_MAX_RETRIES` | int 0–10 | 3 | Snapshot (P4) |
| `COMMITTEE_MATCHER_YFINANCE_CHECKPOINT_EVERY` | int 1–500 | 10 | Snapshot (P4) |
| `COMMITTEE_MATCHER_BATCH_SIZE` | 1 oder 3 | 3 | Mappingcalls (P6) |
| `COMMITTEE_MATCHER_WORKFLOW_RETRIES` | int 0–5 | 2 | Workflow (P7) |
| `COMMITTEE_MATCHER_MAX_REPAIR_ATTEMPTS` | int 0–3 | 1 | Repair (P6) |
| `COMMITTEE_MATCHER_CHARS_PER_TOKEN` | float 1–4 | 3.0 | Context-Gate (P6) |
| `COMMITTEE_MATCHER_RECONCILE_MIN_AGE_S` | float 0–86.400 | 300.0 | `run reconcile` (P7) |
| `COMMITTEE_MATCHER_COST_GUARD` | bool | True | Kostenwache (P7) |
| `COMMITTEE_MATCHER_MODEL` | str oder None | `deepseek/deepseek-v4-flash-0731` | Provider (P5) |
| `COMMITTEE_MATCHER_QUANTIZATIONS` | Tupel aus `int4`,`int8`,`fp4`,`mxfp4`,`nvfp4`,`fp6`,`fp8`,`mxfp8`,`fp16`,`bf16`,`fp32`,`unknown` oder None | `("fp8","bf16","fp16")` | Routingfilter (P5) |
| `COMMITTEE_MATCHER_DATA_COLLECTION` | `deny`/`allow`/None | `deny` | Provider (P5) |
| `COMMITTEE_MATCHER_ZDR` | bool oder None | True | Provider (P5) |
| `COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK` | dict mit `prompt`+`completion` > 0 | `{prompt: 0.30, completion: 0.90}` | Provider (P5) |
| `COMMITTEE_MATCHER_ENDPOINT_CONTEXT_TOKENS` | int 1.000–10.000.000 | 200000 | Context-Gate (P6) |
| `COMMITTEE_MATCHER_MAX_OUTPUT_TOKENS` | int 256–1.000.000 | 8000 | Context-Gate (P6) |
| `COMMITTEE_MATCHER_REASONING_RESERVE_TOKENS` | int 0–1.000.000 | 24000 | Context-Gate (P6) |
| `COMMITTEE_MATCHER_REQUEST_TIMEOUT_S` | float 1–3600 | 300.0 | Provider (P5) |
| `COMMITTEE_MATCHER_MAX_RUN_COST_USD` | float 0 < x ≤ 10.000 | 25.0 | Kostenstop (P7) |
| `COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD` | float 0 < x ≤ 10.000 | 40.0 | Kostenstop (P7) |
| `COMMITTEE_MATCHER_MAX_QUALIFICATION_COST_USD` | float 0 < x ≤ 10.000 | 15.0 | Kostenstop (P7) |

Ungültige Offline-Settings blockieren jeden Lauf mit Exit 4. Ein fehlender
Provider-Wert blockiert weiterhin jeden Providerpfad und wird im Bericht
einzeln benannt; ein fehlendes Budget blockiert mit Exit 6.

Seit Gate B gilt: Den Endpoint wählt OpenRouter. `only` und `order` werden
nicht gesetzt, `allow_fallbacks=true` erlaubt den Wechsel zwischen Providern
desselben Modells, und gefiltert wird ausschließlich über Fähigkeit
(`require_parameters=true`), Quantisierung, Datenschutz und Preis. Der
tatsächlich geroutete Provider wird je Call protokolliert, aber nicht
gebunden. Codekonstanten und bewusst nicht konfigurierbar bleiben
`require_parameters=true`, `allow_fallbacks=true`, keine SDK-Retries und genau
ein gleichzeitiger Call.

`COMMITTEE_MATCHER_QUANTIZATIONS` begrenzt die Rechenpräzision der zulässigen
Endpoints. Ohne Filter mischt ein Lauf 4-Bit- und 16-Bit-Endpoints, womit
Evaluationsergebnisse nicht mehr auf den Produktionslauf übertragbar sind;
`None` hebt die Einschränkung auf.

### 15a. `input validate` — Corpus, Parser, Unit-Sollmenge (P3)

Liest den eingefrorenen Corpus unter `data/committee_matcher_inputs/`, verifiziert
ihn gegen `corpus_manifest.yaml`, parst jedes Mapping-Markdown strikt nach dem
Inputvertrag und bildet daraus die Units. Der Lauf schreibt nichts in den Corpus.

Geprüfte Regeln (jede Abweichung nennt Datei und Zeile, es wird nichts geraten):

- Manifestdeckung: jede reguläre Datei genau einmal, Hash **und** Bytezahl müssen
  stimmen; unbekannte, fehlende oder driftende Dateien blockieren
  (`INP_UNKNOWN_FILE`, `INP_MANIFEST_DRIFT`, `INP_MANIFEST_SIZE`,
  `INP_MANIFEST_STALE`). Nicht UTF-8-dekodierbare Dateien melden `INP_ENCODING`
  statt als interner Fehler zu enden.
- Größengrenzen für Datei und Frontmatter (`INP_FILE_TOO_LARGE`,
  `INP_FRONTMATTER_TOO_LARGE`).
- Frontmatter-Pflichtfelder, Kammer-Dateiname-Konsistenz und `unit_input_basis`
  passend zu den vorhandenen H2-Abschnitten (`INP_FIELD_MISSING`,
  `INP_CHAMBER_MISMATCH`, `INP_BASIS_KEY_MISMATCH`, `INP_BASIS_VALUE`).
- Provenienz: `source_metadata_status` ist `complete` oder `pending`; `complete`
  verlangt `source_url` **und** `retrieved_at`, `pending` erlaubt beides `null`.
  Gesetzte Werte müssen http(s)-URL beziehungsweise ISO-8601 sein
  (`INP_SOURCE_STATUS`, `INP_SOURCE_URL`, `INP_RETRIEVED_AT`,
  `INP_SOURCE_METADATA_INCOMPLETE`). Es wird nichts ergänzt oder geraten.
- `source_files` trägt den Präfix `data/committee_matcher_inputs/` und zeigt nur
  auf `raw_source` oder `source_only` (`INP_SOURCE_OUTSIDE_ROOT`,
  `INP_SOURCE_UNKNOWN`, `INP_SOURCE_ROLE`); Traversal blockiert über die Pfadwache.
  Umgekehrt muss jede referenzierbare Quelle mindestens einmal referenziert sein
  (`INP_SOURCE_ORPHAN`).
- Bodyvertrag: genau eine H1 `# Committee jurisdiction`, eindeutige H2-Abschnitte,
  alle vor `## Zusätzliche Kommentare` (`INP_H1_CONTRACT`,
  `INP_DUPLICATE_SECTION`, `INP_SECTION_AFTER_COMMENTS`).
- Fremdanweisungsgate: `#userinput` außerhalb des Kommentarabschnitts blockiert
  mit Zeilenangabe (`INP_FOREIGN_INSTRUCTION`); innerhalb ist es zulässig und
  wird nie Mappinginput.
- Sollmenge: die gebildeten IDs müssen mengengleich zu
  `data/config/committee_matcher_unit_ids_v1.yaml` sein; zusätzlich werden beide
  Kanonisierungsversionen, `unit_count`, `unit_counts` sowie `kind` und `chamber`
  je Eintrag gegen die gebildeten Units geprüft (`UNIT_MISSING`,
  `UNIT_UNEXPECTED`, `UNIT_ID_COLLISION`, `UNIT_BASELINE_VERSION`,
  `UNIT_BASELINE_COUNT`, `UNIT_BASELINE_MISMATCH`).

Warnung statt Blocker: ein als `jurisdiction_text` deklarierter Text unterhalb
`COMMITTEE_MATCHER_MIN_EVIDENCE_CHARS` meldet `INP_EVIDENCE_TOO_SHORT` (Exit 2).
Aktuell trifft das zwei abgeschnittene Appropriations-Abschnitte
(House THUD, Senate Homeland Security) — Corpuspflege, kein Codefehler.

Artefakt: `data/committee_matcher_v2/reports/input_validate/units.json` mit allen
225 Units inklusive `unit_id`, `kind`, `input_basis`, `parent_unit_id`,
`mapping_input_hash` und `source_bytes_sha256`.

**Kanonisierung `unit-id-v1`** (versioniert, durch Golden-Tests eingefroren):
Amtspräfix entfernen (`Committee on`, `Subcommittee on`, `Permanent Select` …),
NFKC, Typografie vereinheitlichen, `&` → `and`, Kleinschreibung, alles außer
`[a-z0-9]` zu `-`; ID ist `<kammer>--<committee-slug>[--<subcommittee-slug>]`.
**`mapping-input-v1`** hasht den kanonischen JSON-Datensatz aus Unit-Identität,
Basis, normalisiertem Evidenztext und — bei Subcommittees — dem Parent-Kontext
(Committee-Name plus Committee-Jurisdiktionstext). Der Parent-Text ist Kontext
und nie Beleg: eine Zuordnung muss der eigene Name oder der eigene H2-Text
tragen, `name_only` ausschließlich der Name. Er geht trotzdem in den Hash ein,
damit eine Änderung am Parent-Text den Cache aller Kinder invalidiert.
Kosmetischer Whitespace ändert den Hash nicht, jede inhaltliche Änderung schon. Eine Änderung an einer der beiden Regeln
erfordert eine neue Versionsnummer plus neu eingefrorene Baseline.

### 15b. `roster validate` — Coverage gegen die Rosterdaten (P3)

Vergleicht die Committee-/Subcommittee-Vorkommen der Rosterdateien
(`HOUSE_COMMITTEES_FILE`, `SENATE_COMMITTEES_FILE`) mit den Units. Zuordnung
erfolgt ausschließlich über den kanonischen Matchkey oder eine explizite Alias-
Regel; Fuzzy-Treffer werden nur als Vorschlag ausgewiesen und nie automatisch
gemappt.

- **Joint-Cross-Chamber-Regel:** findet sich ein Committee nicht unter der
  Rosterkammer, wird gegen `joint` aufgelöst. Beide Kammern zeigen damit auf
  dieselbe Joint-Unit; die Rosterkammer bleibt als Herkunft erhalten.
- **Regeldatei** `data/config/committee_matcher_roster_rules.yaml`
  (`rules_version: roster-rules-v1`): `aliases` (Rosterschreibweise → `unit_id`)
  und `exclusions` (Vorkommen ohne Unit), jeweils mit Pflichtfeld `reason`.
  Fehlende Datei warnt (`ROS_RULES_MISSING`), unvollständige Einträge blockieren
  (`ROS_RULES_ENTRY`, `ROS_RULES_REASON`, `ROS_ALIAS_UNKNOWN`).
- **Beide Lückenrichtungen sind Befunde:** ein Rostervorkommen ohne Unit meldet
  `ROS_UNIT_MISSING`, eine Unit ohne Rostervorkommen `ROS_UNIT_WITHOUT_ROSTER` —
  beide als Warnung, damit der Lauf nicht fälschlich mit Exit 0 endet.
- **Aktueller Stand:** 230 Vorkommen, 227 `mapped`, 3 `missing` (Select
  Subcommittee January 6, Commission on Security and Cooperation in Europe,
  Senate Caucus on International Narcotics Control) und 2 Units ohne
  Rostervorkommen (`house--ways-and-means--tax-policy`,
  `house--ways-and-means--trade`). Ob daraus Alias, Ausschluss oder offener Punkt
  wird, entscheidet Gate C — deshalb Warnungen (Exit 2) statt Blocker.

Artefakt: `data/committee_matcher_v2/reports/roster_validate/roster_coverage.json`
mit `chamber`, `committee_name`, `unit_name`, `coverage_status`, `unit_id`,
`match_basis`, `hint` und `fuzzy_suggestion` je Zeile sowie der Liste
`units_without_roster_occurrence`.

### 15c. `snapshot build|approve|resume` — yfinance-Taxonomie (P4)

Ruft die Industry-Taxonomie frisch bei yfinance ab (gepinnt auf `yfinance==1.3.0`),
prüft sie und friert sie als Snapshot des Runs ein.

Ablauf (Plan 2.4): gepinnte Sector-Seeds (11) → `Sector(key).industries` entdeckt
den Live-Bestand → `Industry(key).overview` liefert die Beschreibung. Zwischen
zwei Requests liegt `COMMITTEE_MATCHER_YFINANCE_REQUEST_PAUSE_S`, je Request
gelten `..._MAX_RETRIES` Wiederholungen, und alle `..._CHECKPOINT_EVERY`
Industries wird der run-lokale Checkpoint geschrieben. Eine leere Sectorantwort
gilt als Drosselung und fällt in die Retrylogik.

- **All-or-nothing:** persistiert wird nur ein vollständiger, kollisionsfreier
  Snapshot. Fehlende Felder, doppelte oder kollidierende Industry-IDs und ein
  leerer Snapshot blockieren (`TAX_FIELD_MISSING`, `TAX_ID_DUPLICATE`,
  `TAX_ID_COLLISION`, `TAX_SNAPSHOT_EMPTY`). Ein Abbruch hinterlässt nur den
  Checkpoint; `snapshot resume --run-id <revision>` setzt daraus fort. Aus dem
  Checkpoint kommt dabei ausschließlich die teure Beschreibung — die Sectorlisten
  und damit Name und Sektor jeder Industry holt auch der Resume-Lauf frisch,
  sonst liefe eine zwischen Abbruch und Fortsetzung umbenannte Industry mit
  ihrer alten Identität weiter und die Driftprüfung sähe die Änderung nicht.
  Der Checkpoint ist an seinen Run gebunden: ein Checkpoint mit fremder
  `run_id`, fremdem Inhaltsvertrag oder vertragswidrigen Einträgen wird
  verworfen (`TAX_CHECKPOINT_FOREIGN_RUN`, `TAX_CHECKPOINT_VERSION`,
  `TAX_CHECKPOINT_MALFORMED`), der Abruf beginnt dann von vorn.
- **Keine Börsengrößen:** Market Cap, Market Weight, Firmen- und
  Mitarbeiterzahlen sowie Symbole verwirft der Adapter am Rand
  (`INDUSTRY_FIELDS` ist die abschließende Allowlist). Persistiert werden nur
  `industry_id`, `industry_name`, `sector_id`, `sector_name`, `description`.
- **`taxonomy-content-v1`:** SHA-256 über die kanonische JSON-Serialisierung der
  sortierten Industries. Abrufzeiten, Run- und Snapshot-IDs gehen nicht ein —
  zwei Abrufe derselben Taxonomie ergeben denselben Hash.
- **Erstfreigabe und Drift:** ohne freigegebene Baseline endet der Lauf mit
  `TAX_NO_BASELINE` in `awaiting_taxonomy_approval` (Exit 5). Danach blockiert
  Identitätsdrift ebenso (`TAX_DRIFT_ADDED`, `TAX_DRIFT_REMOVED`,
  `TAX_DRIFT_RENAMED`, `TAX_DRIFT_SECTOR`, alle Exit 5). Reine
  Beschreibungsänderungen melden `TAX_DRIFT_DESCRIPTION` als Warnung (Exit 2)
  und laufen durch; ihre Freigabepolicy legt Gate C fest.
- **Freigabe ist gebunden:** `snapshot approve --run-id <revision>` gibt genau
  den Snapshot dieses Runs frei. Geprüft werden Inhaltsvertrag
  (`TAX_SNAPSHOT_VERSION`), die Bindung `snapshot_id == run_id`
  (`TAX_SNAPSHOT_RUN_MISMATCH` — ein in ein anderes Runverzeichnis kopierter
  Snapshot ist damit nicht freigabefähig), die Feldmenge jedes Eintrags und der
  Content-Hash gegen den Inhalt (beides `TAX_SNAPSHOT_TAMPERED`). Fehlt der Run,
  blockiert `TAX_SNAPSHOT_MISSING`.
- **Baseline wird nachgerechnet:** vor jedem Driftvergleich prüft der Lauf die
  Baseline gegen ihren eigenen Hash und ihre Feldmenge. Eine am Hash vorbei
  editierte Baseline blockiert mit `TAX_BASELINE_TAMPERED`, statt die Drift zu
  verdecken, die sie melden soll; daneben greifen `TAX_BASELINE_MALFORMED`,
  `TAX_BASELINE_VERSION` und `TAX_BASELINE_EMPTY`.

```bash
python -m committee_matcher snapshot build --run-id run-2026-08-20   # Exit 5 beim ersten Mal
python -m committee_matcher snapshot approve --run-id run-2026-08-20 # Baseline setzen
python -m committee_matcher snapshot resume  --run-id run-2026-08-20 # nach Abbruch
```

Ohne `--run-id` erzeugt `snapshot build` eine Revision aus der UTC-Zeit
(`run-YYYYMMDDTHHMMSSZ`); `resume` und `approve` verlangen die Revision
ausdrücklich. Unzulässige Zeichen in der Revision blockieren
(`CLI_RUN_ID_INVALID`).

Artefakte je Run unter `data/committee_matcher_v2/runs/<run_id>/`:
`taxonomy_snapshot.json`, `taxonomy_catalog.md` (nach Sektoren gruppierter
Markdown-Katalog) und der volatile `taxonomy_checkpoint.json`. Die freigegebene
Baseline liegt unter `data/committee_matcher_v2/taxonomy/approved_baseline.json`.

**Schreibschutz:** `committee_matcher/paths.py` erlaubt Schreiben nur unter
`data/committee_matcher_v2/`. Die Inputbaseline und alle neun Pfade der
P0-Schutzbaseline sind gesperrt; fehlt oder bricht die Schutzbaseline,
blockiert die Wache (fail closed) statt ungeschützt zu schreiben.

Tests: `tests/test_committee_matcher_scaffold.py` (Gerüst),
`tests/test_committee_matcher_input_units.py` (Parser, Kanonisierung, Roster;
Golden-Tests für `unit-id-v1` und `mapping-input-v1`) und
`tests/test_committee_matcher_taxonomy_p4.py` (Adapterrand, Content-Hash,
Checkpoint/Resume, Drift- und Freigabegate gegen ein yfinance-Testdouble) —
alle netzwerkfrei.

### 15d. `provider check` — Routing-Preflight vor dem ersten Call

Prüft gegen den öffentlichen Endpointkatalog
`https://openrouter.ai/api/v1/models/<modell>/endpoints`, ob es zur
konfigurierten Kombination aus Modell, Structured-Output-Fähigkeit,
Quantisierung und Preisdeckel überhaupt einen Endpoint gibt. Der Abruf braucht
keinen API-Key, kostet nichts und ruft kein Modell auf.

Warum vor dem ersten Call: Eine leere Endpointmenge ist der einzige
Konfigurationsfehler, den `allow_fallbacks` nicht abfangen kann — Fallbacks
wählen innerhalb der gefilterten Menge, nicht daneben. Ohne Preflight scheitert
stattdessen der erste kostenpflichtige Call mit einer generischen SDK-Meldung.
Für P5 gilt deshalb als verbindliche Integrationsanforderung: Contract-Smoke
und jeder spätere Providerlauf rufen denselben Preflight vorgeschaltet auf und
brechen mit Exit 4 ab, bevor Kosten entstehen. Umgesetzt ist bislang nur das
eigenständige Kommando `provider check`; einen Contract-Smoke gibt es noch
nicht.

Jede Ursache einer leeren Menge wird **getrennt** gemeldet, damit im Terminal
steht, welches Kriterium geleert hat:

| Code | Ursache | Genannte Stellschraube |
|---|---|---|
| `PROV_CATALOG_UNREACHABLE` | Katalog nicht abrufbar (HTTP-Status, Timeout, unlesbares JSON) | Netzverbindung |
| `PROV_MODEL_UNKNOWN` | Modellslug im Katalog nicht vorhanden oder ohne Endpoints | Modellslug prüfen |
| `PROV_NO_STRUCTURED_OUTPUT` | kein Endpoint des Modells unterstützt striktes JSON-Schema | Modell tauschen (`COMMITTEE_MATCHER_MODEL`) |
| `PROV_NO_ENDPOINT` | Quantisierungsfilter leert die Menge (nennt gewünschte und vorhandene Quantisierungen) | `COMMITTEE_MATCHER_QUANTIZATIONS` lockern oder `None` |
| `PROV_NO_ENDPOINT` | Preisdeckel leert die Menge (nennt den günstigsten vorhandenen Endpoint) | `COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK` anheben |

Nennt der Katalog für einen Endpoint keinen lesbaren Preis, gilt er als
unendlich teuer und fällt durch jeden Deckel (fail closed); im Befund steht
dann `unbekannt` statt einer Zahl. Der günstigste Endpoint wird am Promptpreis
gemessen — er dominiert die Kosten des Matchers, da jeder Call denselben großen
Taxonomie- und Unitprefix trägt und nur kurzes JSON zurückgibt.

Alle fünf blockieren mit Exit 4 in Stage `provider`. Jeder Befund nennt
zusätzlich die beiden Stellschrauben, die die Workflowlogik nicht berühren:
Modellwechsel und `COMMITTEE_MATCHER_ZDR = False`.

**Was der Preflight nicht sehen kann:** Der Katalog weist die
ZDR-Eigenschaft je Endpoint nicht aus. Scheitert ein realer Call trotz grünem
Preflight am Routing, meldet `PROV_NO_ENDPOINT` alle wirksamen Bedingungen
(`require_parameters`, `zdr`, `data_collection`, `quantizations`, `max_price`)
und benennt `zdr=true` als wahrscheinlichste Ursache.

```bash
python -m committee_matcher provider check
# Modell:    deepseek/deepseek-v4-flash-0731
# Filter:    structured_outputs, quantizations=['fp8', 'bf16', 'fp16'], ...
# Endpoints: <N> von <M> zulaessig | guenstigster <anbieter>/<quant> (<preise> USD pro Mtok)
```

Die Zahlen sind Live-Werte des Katalogs und ändern sich ohne Zutun des
Projekts: am 2026-08-22 waren 7 von 30 Endpoints zulässig, günstigster zu
0.080 USD pro Mtok Prompt.

Tests: `tests/test_committee_matcher_provider_preflight.py` (netzwerkfrei, der
Katalogabruf wird injiziert).

### 15e. `provider smoke` — Contract-Smoke (kostenpflichtig)

Setzt **einen** Minimalcall gegen das konfigurierte Modell ab und prüft damit,
was der kostenlose Preflight nicht sehen kann: Authentifizierung, echte
Structured-Output-Fähigkeit des tatsächlich gerouteten Endpoints, gemeldete
Kosten und die ZDR-Verträglichkeit der Routingbedingungen.

Ablauf: `provider check`-Preflight → Systemprompts laden → ein Call mit einem
zweifeldrigen Schema (`ok`, `echo`), 64 Ausgabetokens. Kosten liegen im Bereich
weniger Zehntausendstel USD. Blockiert eine Vorbedingung, unterbleibt der Call.

```bash
python -m committee_matcher provider smoke
# Smoke:      Antwort gueltig, Schema eingehalten
# Routing:    <anbieter> | Modell <slug>
# Tokens:     <n> Prompt / <n> Completion | Kosten <x> USD
# Generation: <id>
```

Die Antwort wird gegen `SMOKE_SCHEMA` geprüft — Feldmenge, Typen und die
erwarteten Werte `ok=true` und `echo="contract-smoke"`. Ein beliebiges
JSON-Objekt besteht den Smoke also nicht: sonst könnte ein Endpoint, der
Structured Output nur nominell unterstützt, den Nachweis vortäuschen
(`PROV_SCHEMA_VIOLATED`, Exit 9).

Exit-Codes: 0 bei vertragsgemäßer Antwort, 4 wenn Key, Prompts oder
Endpointmenge blockieren, 9 bei einem Providerfehler oder Schemaverstoß.
Schlägt der Call mit einem Routingfehler fehl, ergänzt der Bericht den
ZDR-Befund aus 15d.

Fremdtexte aus Providerfehlern werden **nicht** übernommen, auch nicht
redigiert oder gekürzt: in Bericht und `errors.json` steht ausschließlich eine
selbst formulierte Diagnose aus `ERROR_MARKERS`. Eine Redigierung per
Mustersuche träfe nur die bekannten Schlüsselformen.

Der geroutete Provider steht in `openrouter_metadata.endpoints.available` als
Eintrag mit `selected=true`; `attempts` füllt sich erst, wenn ein Fallback
gegriffen hat, und gewinnt dann. Der Name wird als Fremdwert auf eine Zeile und
60 Zeichen begrenzt.

**Ausgeführt am 2026-08-22:** Antwort vertragsgemäß, Schema eingehalten,
Routing `Morph`, 0,000018 USD, Exit 0. Structured Output, Usage-Meldung und die
ZDR-Routingbedingungen sind damit live bestätigt.

**API-Key:** ausschließlich `OPENROUTER_API_KEY` in `.env` (gitignored). Er
steht nicht im `COMMITTEE_MATCHER_*`-Block, erscheint in keinem Artefakt, in
keinem Runmanifest und in keinem Terminalbericht.

### 15f. Systemprompts

Die beiden editierbaren Systemprompts liegen als UTF-8-Markdown mit
YAML-Frontmatter unter `data/config/committee_matcher_prompts/`:

| Datei | Zweck |
|---|---|
| `mapping_system.md` | Zuordnungsregel, Anzahlgrenzen, Confidence-Rubrik, Evidence- und Ausgabevertrag |
| `repair_system.md` | gezielte Korrektur einer formal ungültigen Antwort ohne Neuerfindung |

Frontmatter-Pflichtfelder: `prompt_id`, `prompt_version`, `schema_version`.
Die `schema_version` muss dem Ausgabevertrag des Codestands entsprechen
(`mapping-output-v1`), sonst blockiert der Lauf — ein Prompt gegen ein anderes
Schema erzeugt systematisch ungültige Antworten. Der Inhaltshash deckt nur den
Text ohne Frontmatter ab: eine reine Versionsangabe bewegt ihn nicht, jede
Textänderung schon. Version und Hash wandern in das Runmanifest.

Fehlercodes: `PRM_MISSING`, `PRM_MALFORMED`, `PRM_ID_MISMATCH`,
`PRM_SCHEMA_MISMATCH`, `PRM_EMPTY`, `PRM_TOO_LARGE`, `PRM_NOT_UTF8` — alle
Exit 4 in Stage `config`.

Zuständigkeitstexte und Taxonomie erreichen das Modell in markierten
Datenblöcken; eine Zeile im Fremdtext, die den Rahmen nachbildet, wird
entschärft, statt den Block vorzeitig zu schließen.

### 15g. Mappingkern — Batching, Validierung, Repair (P6)

Der Mappingkern ist implementiert, aber noch nicht über ein eigenes Kommando
aufrufbar: das Runkommando entsteht am Ende von P7. Runzustand, Auditspur und
Kostenwache stehen seit P7a bereit (Abschnitt 15h). Die folgenden Regeln gelten
ab sofort für jeden Mappingcall.

**Batchbildung und Context-Split.** `COMMITTEE_MATCHER_BATCH_SIZE` ist der
Wunschzuschnitt (1 oder 3). Passt ein Batch nicht in das Eingabebudget, wird er
3→2→1 geteilt. Das Eingabebudget ist
`ENDPOINT_CONTEXT_TOKENS − MAX_OUTPUT_TOKENS − REASONING_RESERVE_TOKENS`; die
Tokenschätzung rechnet bewusst mit `CHARS_PER_TOKEN = 3.0` statt der realen
~4 und überschätzt damit; erlaubt sind 1.0 bis 4.0, weil oberhalb von 4.0 die
Schätzung für lateinischen Text systematisch unter dem realen Verbrauch läge.
Ein zu kleiner Wert vergrößert nur die Batchzahl, ein zu großer riskiert
abgeschnittene Antworten.

Die Schätzung bleibt eine Heuristik ohne beweisbare Obergrenze: der echte
Tokenizer des Endpoints ist nicht bekannt, und ein Text aus vielen kurzen
Sondertoken kann den Schätzwert überschreiten. Die harte Sicherheitsgrenze ist
deshalb nicht die Schätzung, sondern die Ablehnung durch den Endpoint — ein
benannter Abbruch mit `PROVIDER_ERROR`, keine stille Kürzung. Sicherheit lässt
sich über einen kleineren `CHARS_PER_TOKEN` oder eine höhere
`REASONING_RESERVE_TOKENS` erkaufen, beides zulasten der Batchgröße.

Gekürzt wird nie. Passt eine einzelne Unit nicht, blockiert der Lauf:

| Code | Bedeutung | Exit |
|---|---|---|
| `MAP_CONTEXT_BUDGET_INVALID` | Reserven lassen kein Eingabebudget übrig | 4 |
| `MAP_UNIT_TOO_LARGE` | Einzelne Unit passt nicht in das Fenster | 4 |
| `MAP_CONTEXT_EXCEEDED` | Batch überschreitet das Budget; kein Call abgesetzt | – |
| `MAP_REPAIR_CONTEXT_EXCEEDED` | Reparaturcall würde das Budget überschreiten | – |

**Formale Prüfung.** Geprüft wird ausschließlich Formales; über die fachliche
Direktheit entscheiden Prompt, Gold-Set, Holdout und der manuelle Review.

| Code | Bedeutung |
|---|---|
| `MAP_SCHEMA_INVALID` | Antwort verletzt den Ausgabevertrag (Feldpfad wird genannt) |
| `MAP_BATCH_ID_MISMATCH` | `requested_batch_id` weicht von der Anfrage ab |
| `MAP_UNITS_MISSING` / `MAP_UNITS_UNEXPECTED` | Batch nicht deckungsgleich beantwortet |
| `MAP_UNKNOWN_INDUSTRY` | `industry_id` steht nicht zeichengenau im Snapshot |
| `MAP_EVIDENCE_UNSUPPORTED` | Evidence kommt nicht zusammenhängend in einer erlaubten Quelle vor |
| `MAP_EVIDENCE_FROM_PARENT` | Evidence stammt aus dem Text des übergeordneten Committees |
| `MAP_TOO_MANY_ASSIGNMENTS` | Obergrenze des Unittyps überschritten |
| `MAP_BROAD_SCOPE_NOT_ALLOWED` | Broad-Scope-Ausnahme für diesen Unittyp unzulässig |

Erlaubte Belegquellen je Unit: der eigene Name, bei `committee_direct` und
`committee_parent_core` zusätzlich der Committeename, und — außer bei
`name_only` — der eigene Zuständigkeitstext. Der Text des übergeordneten
Committees ist Kontext und nie Beleg. Verglichen wird nach Unicode-, Whitespace-
und Groß-/Kleinschreibungsnormalisierung als zusammenhängender Substring.

Obergrenzen: 1–2 Zuordnungen sind der Normalfall, 3–5 nur mit
`broad_scope_exception` und Confidence ≥ 3; `committee_parent_core` und
`name_only` bleiben immer bei 2; mehr als 5 ist immer ungültig.

**Repair.** Der Repair ist gezielt: zurück gehen nur die beanstandeten Units
mit ihrer Verstoßliste und ihrem bisherigen Ergebnis, höchstens
`COMMITTEE_MATCHER_MAX_REPAIR_ATTEMPTS` mal (Default 1, `0` schaltet ab). Die
unbeanstandeten Units behalten ihre Erstantwort und werden dem Modell gar nicht
erst erneut vorgelegt. Ein Verstoß ohne Unitbezug — unlesbares Schema, falsche
Batch-ID, fehlende Units — trifft den ganzen Batch und wiederholt ihn
vollständig.

Der zusammengeführte Stand durchläuft dieselbe Prüfung wie jede Modellantwort;
ein Ergebnis entsteht nie ungeprüft aus zwei Teilantworten. Bleibt ein Verstoß,
scheitert der Batch mit benannten Gründen. Es gibt keine automatische Kappung,
Löschung oder Confidence-Änderung.

Die Verstoßliste steht im Reparaturprompt außerhalb der Datenblöcke — sie ist
der verbindliche Auftrag. Deshalb enthält sie **keinen einzigen Wert aus der
Modellantwort**, auch keinen gekürzten: benannt werden ausschließlich
Positionen aus dem eigenen Vertrag (`units[3].assignments[1].confidence`,
„1. Zuordnung"). Ein vom Modell erfundener Feldname erscheint als
`<unbekanntes Feld>`.

Passt ein Reparaturcall nicht mehr in das Kontextfenster, scheitert der Batch
mit `MAP_REPAIR_CONTEXT_EXCEEDED`; er wird bewusst nicht noch einmal geteilt.
Der Fall setzt voraus, dass bereits der Erstcall am Limit lag.

**Boundary-Konflikte.** Dieselbe Industry in mehreren Units derselben
Committee-Familie (Parent-Core plus seine Subcommittees) wird gemeldet, nicht
entfernt: eine Überlappung ist der Regelfall, da getrennte Zuständigkeiten
dieselbe Branche berühren dürfen. Der Konflikt ist damit reine Information für
den Review; eine Entscheidung verlangt er nicht. Die Mitglieder eines Konflikts
sind nach Unit-ID sortiert, damit die Antwortreihenfolge des Providers die
Zeilenfolge nicht bestimmt.

### 15h. Runzustand, Auditspur und Kostenwache (P7a)

Noch kein eigenes Kommando; das Modul `committee_matcher/workflow.py` liefert
die Bausteine, die das Runkommando am Ende von P7 zusammensetzt.

**Artefakte je Lauf** unter `data/committee_matcher_v2/runs/<run_id>/mapping/`:

| Datei | Inhalt |
|---|---|
| `run_manifest.json` | eingefrorene Laufbedingungen: Modell, Routing, Kontext, Batching, Prompt-Hashes, Schema-Hash, Budget |
| `run_state.json` | Workflowstatus plus Stand je Unit (Status, Cache-Key, geprüftes Ergebnis, Verstoßcodes) |
| `calls/<call_id>.json` | Provenienz und rohe Antwortnutzlast eines Calls |

Alle drei werden atomar geschrieben. Die Calldatei entsteht **vor** der
fachlichen Prüfung — ein Absturz während der Validierung verliert keinen
bezahlten Call. Sie enthält bewusst weder Systemprompt noch Nutzdaten: der
Prompt liegt versioniert als Kontrollinput vor und ist über `prompt_sha256`
belegt.

**Cache und Resume.** Der Cache-Key einer Unit besteht aus
`mapping_input_hash`, `taxonomy_content_hash`, Prompt-ID und -Hash, Schemaname
und -Hash, Modell sowie den Inferenzparametern `max_output_tokens` und
`reasoning_reserve_tokens`. Volatile Provenienz — Run-, Snapshot-, Call- und
Evaluations-IDs — bleibt draußen; sonst wäre jeder Lauf ein vollständiger
Cache-Miss.

Der geroutete Endpoint gehört ebenfalls **nicht** in den Schlüssel. Plan 2.6
nennt ihn noch, das stammt aber aus der Zeit vor dem Gate-B-Beschluss vom
2026-08-21, der die Providerwahl freigegeben hat: bei freier Wahl ist der
Endpoint des nächsten Calls im Voraus unbekannt, ein Schlüssel mit Endpoint
wäre nie wieder treffbar. Der Endpoint bleibt Auditprovenienz je Call.

Wiederverwendet wird nur ein entschiedenes, vollständig geprüftes Ergebnis,
dessen Unitstatus zum Modellurteil passt — `mapped` zu `mapped`, `no_mapping`
zu `no_mapping`.

Ein vorhandener, aber unbrauchbarer `run_state.json` **hält den Lauf an**
(`RUN_STATE_MALFORMED`, Exit 4) und wird nicht überschrieben: die Datei belegt,
dass bereits gelaufen ist, und ein stiller Neustart würde bezahlte Calls ein
zweites Mal senden. Was bezahlt wurde, zeigen die Dateien unter `calls/`.
Teilweise übernommen wird nie — auch ein einzelner kaputter oder doppelter
Uniteintrag stoppt den ganzen Zustand. Fehlt die Datei dagegen ganz, beginnt
der Lauf frisch; dann ist auch nichts bezahlt worden.

Ein Resume gegen ein vorhandenes Manifest ist nur zulässig, solange
`taxonomy_content_hash`, `input_manifest_hash`, `model`, `prompts`, `schema`,
`batching`, `context` und `cache_key_version` unverändert sind; sonst blockiert
`RUN_MANIFEST_DRIFT` mit Exit 4. `context` gehört dazu, weil
`max_output_tokens` und `reasoning_reserve_tokens` auch im Cache-Key stehen —
sonst gäbe es einen Parameter, der den Cache verschiebt, einen Resume aber
durchließe. Ein anderes Budget oder eine andere
Snapshot-ID sind ausdrücklich kein Grund, bezahlte Ergebnisse wegzuwerfen.

**Kostenwache.** Gerechnet wird mit dem Preisdeckel
`COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK`, nicht mit einem erwarteten Preis:
er ist die einzige vor dem Call feststehende Zahl, und die Routingbedingungen
lassen keinen teureren Endpoint zu. Die Eingabe wird geschätzt (dieselbe
pessimistische Schätzung wie im Context-Gate), die Ausgabe mit
`MAX_OUTPUT_TOKENS` angesetzt.

| Buchung | Bedeutung |
|---|---|
| `spent_usd` | vom Provider gemeldete Kosten |
| `reserved_usd` | Worst Case für Calls ohne Kostenmeldung und für gescheiterte Calls |
| `committed_usd` | Summe beider; nur sie zählt gegen das Budget |

Ein Call ohne Kostenmeldung ist nicht kostenlos, nur ungeklärt. Passt der
nächste Call nicht mehr in `COMMITTEE_MATCHER_MAX_RUN_COST_USD`, wird er gar
nicht erst gesendet: `COST_BUDGET_EXCEEDED`, Exit 6.

Reihenfolge je Call: **prüfen, senden, buchen, persistieren.** Gebucht wird,
sobald die Antwort vorliegt — vor dem Schreiben der Auditdatei. Sonst bliebe
ein bezahlter Call bei einem Schreibfehler ungebucht, und das Budget hätte Luft,
die es nicht mehr hat.

Ein gescheiterter Call wird danach unterschieden, ob der Provider ihn
ausgeführt haben kann:

| Fall | Buchung | Wiederholung |
|---|---|---|
| Anfrage belegt abgewiesen (HTTP 400, 401, 402, 403, 404, 405, 413, 415, 422, 429) | keine | erlaubt |
| Antwort kam an und meldete ihre Kosten, war aber unbrauchbar | gemeldete Kosten gebucht | erlaubt |
| Ausgang offen (Timeout HTTP 408, Abbruch 499, HTTP 5xx, kein Status, Antwort ohne Kostenmeldung) | Worst Case reserviert | **nie automatisch** |

Fail closed: ohne belegte Nichtausführung gilt der Ausgang als offen. Deshalb
zählt eine Allowlist einzelner Statuscodes, kein 4xx-Bereich — HTTP 408 und 499
melden das Ende der Verbindung, nicht die Ablehnung der Anfrage; das Modell kann
gelaufen und abgerechnet worden sein.
`COMMITTEE_MATCHER_COST_GUARD = False` schaltet allein das Blockieren ab, nicht
die Buchführung — der Kostenbericht bleibt vollständig.

Kostenwache und Auditspur sitzen als Dekorator am Providerrand. Dadurch wird
jeder Call erfasst, auch ein Reparaturcall, und der Mappingkern bleibt frei von
Runzustand.

**Ungeklärte Calls.** Ein Timeout mit unbekannter Providerannahme wird
`RUN_OUTCOME_UNKNOWN` (Exit 8) und nie automatisch wiederholt. Abgeglichen wird
ausschließlich über eine vorhandene OpenRouter-`generation_id`; fehlt sie,
bleibt der Call dauerhaft offen. Erneutes Senden ist eine Entscheidung von Bene
— es kann doppelt abgerechnet werden.

Scheitert eine Antwort erst bei der Auswertung (leere Antwort, Verweigerung,
Abschneidung, ungültiges JSON), trägt der Fehler Generation-ID und gemeldete
Kosten mit; nur so bleibt ein bezahlter Call abgleichbar. Lässt sich die
Auditdatei nicht schreiben, wird der Call als `AUDIT_WRITE_FAILED` mit seiner
Generation-ID vorgemerkt — sonst wäre nach einem Abbruch nicht erkennbar, dass
er bereits gesendet und bezahlt wurde.

### 15i. `run plan|execute|reconcile` — Entwicklungsrun (P7c)

Drei Kommandos, eine Runrevision. `--run-id` ist bei allen dreien Pflicht.

```bash
python -m committee_matcher run plan --run-id run-1 --committee "Committee on Agriculture"
python -m committee_matcher run execute --run-id run-1
python -m committee_matcher run reconcile --run-id run-1   # nur nach Exit 8
```

| Kommando | Netzwerk | Ergebnis |
|---|---|---|
| `run plan` | nein | Runmanifest, Zielauswahl, Batchzahl und Worst-Case-Kosten; kein Modellaufruf |
| `run execute` | ja, kostenpflichtig | Mapping der geplanten Units, Laufzustand, Auditspur, Artefaktsatz |
| `run reconcile` | ja, kostenlos | klärt über die Generation-ID, was ein Call mit offenem Ausgang gekostet hat |

`run plan` friert die Zielauswahl (`--chamber`, `--committee`, `--unit-id`) im
Manifest ein. `run execute` übernimmt sie ohne Zielflags; abweichende Flags
verlangen eine neue Revision (`RUN_MANIFEST_DRIFT`, Exit 4). Eine Revision mit
vollständigem Artefaktsatz wird nie überschrieben (`RUN_REVISION_COMPLETE`).

**Wiederaufnahme.** `run execute` darf beliebig oft laufen: bezahlte Units
kommen aus dem Cache, nur offene Batches gehen erneut an das Modell. Kosten,
Reservierungen und Callzahlen früherer Durchläufe belasten die Kostenwache des
nächsten vor — das Budget gilt je Run, nicht je Durchlauf. Ein wiederholter
Batch bekommt ein eigenes Call-Präfix (`pass2-`, `retry1-`), damit keine
Call-ID zweimal gesendet wird.

Ein Providerfehler wird nur dann wiederholt, wenn er retrybar ist, der Ausgang
belegt feststeht **und** der Versuch nichts gekostet hat. Ist der Erstcall
bezahlt und scheitert erst der Reparaturcall, endet der Batch — ein Retry würde
den bezahlten Erstcall erneut senden.

**Offener Ausgang.** Solange ein Call weder Auditdatei noch Klärung trägt,
blockiert `run execute` mit `RUN_OUTCOME_UNKNOWN` (Exit 8) und sendet nichts.
`run reconcile` fragt die Generation ab und schreibt
`calls/<call_id>.reconciled.json`. Eine dem Provider unbekannte Generation gilt
erst nach `COMMITTEE_MATCHER_RECONCILE_MIN_AGE_S` Sekunden als „nicht
abgerechnet"; vorher bleibt sie offen. Ohne `OPENROUTER_API_KEY` blockiert der
Abgleich als Preflight (Exit 4), da kein Providercall stattgefunden hat.

Der Artefaktsatz entsteht erst, wenn der Lauf abgeschlossen ist: kein Abbruch,
kein offener Ausgang, jede Zielunit im Zustand. Sonst meldet der Lauf
`RUN_ARTIFACTS_DEFERRED` als Warnung — der Abschlussmarker würde die Revision
unveränderlich machen und den Resume verhindern.

### 15j. `review export|import` — XLSX-Roundtrip (P7d)

```bash
python -m committee_matcher review export --run-id run-1
# Workbook in Excel bearbeiten
python -m committee_matcher review import --run-id run-1
python -m committee_matcher review import --run-id run-1 --workbook ~/Downloads/review.xlsx
```

Ohne `--review-id` heißt die Revision `review-<run_id>`; sie liegt unter
`data/committee_matcher_v2/reviews/<review_id>/`. Weder ein Export noch ein
Import überschreibt eine bestehende Reviewrevision.

**Blätter des Workbooks.**

| Blatt | Zweck |
|---|---|
| `assignments` | die Arbeitstabelle; hier wird geändert |
| `boundary` | Übersicht doppelter Industries in einer Committee-Familie; kein Eingabefeld |
| `taxonomy` | alle zulässigen `industry_id` des Laufsnapshots; speist die Auswahlliste |
| `info` | Bindung an Lauf, Snapshot und Modell im Klartext |

Spalten des Blatts `assignments`: `unit_id`, `chamber`, `committee_name`,
`unit_name`, `unit_kind`, `industry_id`, `confidence`, `evidence_excerpt`,
`rationale_short`, `no_mapping_reason`.

**Bearbeitung.** Werte direkt ändern, sonst nichts. Es gibt keine Aktions-,
Entscheidungs- oder Notizspalte; was geschehen ist, rechnet der Import selbst
aus, indem er jede Zeile gegen die Artefakte des Laufs hält.

| Vorgang | Handgriff |
|---|---|
| Zuordnung übernehmen | nichts tun |
| Industry, Confidence, Evidence oder Rationale ändern | Zelle überschreiben |
| Zuordnung entfernen | Zeile löschen |
| Einheit ohne jede Zuordnung | `industry_id` leeren, `no_mapping_reason` optional |
| Neue Zuordnung | Zeile anhängen, `unit_id` und `industry_id` füllen |
| Boundary-Konflikt | nichts zu tun; Blatt `boundary` ist reine Information |

Keine Belegpflicht: Evidence, Rationale und Grund sind Textfelder, keine
Bedingungen. Eine geänderte oder neue Zeile bekommt automatisch
`assignment_source=manual` und keine Callprovenienz; eine unveränderte Zeile
behält beides.

Der Import meldet nach dem Schreiben die Änderungsbilanz gegen den Lauf
(`geändert | neu | entfernt`). Sie ist der Ersatz für die frühere
Löschbestätigung: Eine versehentlich gelöschte Zeile fällt an der Zahl auf.

**Was der Import ablehnt** (Exit 7, keine Datei wird geschrieben):

| Code | Bedeutung |
|---|---|
| `REV_STALE_BINDING` | Snapshot, Taxonomiehash, Inputmanifest oder Modell weichen vom Export ab |
| `REV_WORKBOOK_CONTRACT` | Blatt oder Spaltenkopf umbenannt, verschoben oder entfernt |
| `REV_FORMULA_CELL` | eine Zelle enthält eine Formel statt eines Werts |
| `REV_UNIT_MISSING` / `REV_UNIT_UNKNOWN` | Zeile ohne `unit_id` oder mit einer, die es nicht gibt |
| `REV_UNKNOWN_INDUSTRY` | `industry_id` steht nicht im Snapshot des Laufs |
| `REV_INDUSTRY_DUPLICATE` | dieselbe Industry zweimal bei derselben Einheit |
| `REV_CONFIDENCE_INVALID` | Confidence ist keine ganze Zahl 1..5 |

Nur als Warnung (Exit 2, Datei wird geschrieben): `REV_ASSIGNMENTS_ABOVE_LIMIT`
— mehr Zuordnungen als der Modellvertrag erlaubt. Die 0/2/5-Regel diszipliniert
das Modell; über den Einzelfall entscheidet der Review.

Angenommen entstehen `reviewed_assignments.csv`, `reviewed_unit_coverage.csv`
und `import_manifest.json`. Sektor- und Industrienamen leitet der Import aus dem
Snapshot ab, damit ein Tippfehler im Namen nicht ins Ergebnis wandert; die
`industry_id` ist die Wahrheit.

## 16. Ausgelagerter Analyse-Workflow

Der ehemalige Suspicious-Trades-Workflow ist kein Script oder internes Modul des
Congress Trackers mehr. Betrieb, Abnahme, Provider-Konfiguration und Artefakte
sind ausschließlich im eigenständigen Schwesterprojekt dokumentiert. Der
Congress Tracker stellt optional den neutralen CSV-Export aus Pipeline 10 bereit.

---

## Schnellreferenz

| Script | Befehl | Zweck |
|--------|--------|-------|
| Senate Scraper | `python scrapers/senate_scraper.py` | Senate-Trades scrapen |
| Senate PDF Extractor | `python scrapers/senate_pdf_extractor.py` | Paper-PTRs via OCR |
| Import Pipeline | `python scripts/import_trades.py` | Trades anreichern (Name-Match + Committees) |
| Senate Diagnose | `python scripts/diagnose_scraper.py` | Scraper-Tests |
| House Committee Parser | `python scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py --pdf ...` | House Committee PDF (scsoal.pdf) → F2-Roster |
| Senate Committee XML Fetch | `python scrapers/senate_committee_xml/fetch_senate_xml.py` | Senate-Committee-XML-Snapshots aktualisieren |
| Senate Committee Parser | `python scrapers/senate_committee_xml/parse_senate_xml.py [out]` | Senate-XML → F2-Roster |
| Committee-Struktur-Liste | `python scrapers/generate_committee_structure.py` | Chamber/Committee/Subcommittee-Liste (Validierung) |
| House Scraper | `python scrapers/house_scraper.py` | House PTR-Trades scrapen |
| House PDF Extractor | `python scrapers/house_pdf_extractor.py` | Hand-Delivered PDFs via OCR |
| Congress Data Fetcher (Delta) | `python scrapers/fetch_congress_data.py --update` | **Empfohlen**: nur geänderte Bills (~5-30 Min, nicht-destruktiv) |
| Congress Data Fetcher (Erstlauf/Resume) | `python scrapers/fetch_congress_data.py` | Erstlauf bzw. Checkpoint-Fortsetzung (~7-8h, nicht-destruktiv) |
| Congress Data Fetcher (Force-Rebuild) | `python scrapers/fetch_congress_data.py --force` | **DESTRUKTIV** (mit `JA`-Confirmation): JSONs archivieren + von 0 neu fetchen (~7-8h) |
| Tests | `pytest tests/ -v` | Alle Unit-Tests |
| Pipeline Reset | `python scripts/reset_pipeline.py --all --dry-run` | Logs/Caches/Stage/Processed reversibel zurücksetzen |
| Streamlit Dashboard | `venv/bin/streamlit run app/streamlit_app.py` | Web-UI (Trades / Assets / Members) auf Port 8501 |
