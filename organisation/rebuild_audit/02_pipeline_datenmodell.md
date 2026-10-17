# Rebuild-Audit 02 – Hauptpipeline, Datenmodell, Architektur-Rückgrat

Stand: 2026-10-17 · Basis: Worktree `agent/committe_matcher`. Live-Daten stammen aus dem Haupt-Worktree `congress_tracker/data/` (letzter Vollauf am 2026-08-11, Quiver-XLSX vom 2026-08-03).
Prüfskripte (nur lesend): `/tmp/audit_pipeline/a1.py` bis `a6.py`. Nichts im Repo verändert, keine kostenpflichtigen Aufrufe.

---

## 1. Features

### 1.1 Datenfluss von der Quelle bis zum Master

```
systemd-Timer 03:00 (deploy/systemd/quiver-import.timer)
  -> scrapers/quiver_scraper.py  (Playwright-Login, XLSX-Export, atomarer os.replace)
  -> data/raw/congress-trading-all.xlsx   (114.347 Zeilen, 20 Spalten)
  -> scripts/import_trades.py --delta     (17 Stages, alles in einem Prozess)
  -> data/processed/trades_master_complete.csv  (89 Spalten, 645 MB)
     + trades_master.csv (kuratiert, 35 Spalten, 591 MB)
     + suspicious_events/assets/members.csv, *_events_stage.csv, unmatched_names.csv, delisted_trades.csv, manual_review_tickers.csv
```

**Primärquelle ist ausschließlich Quiver.** `import_trades.py` liest nur `QUIVER_TRADES_PATH` (`scripts/import_trades.py:101`, `:445`). Die eigenen Scraper `scrapers/house_scraper.py` und `scrapers/senate_scraper.py` (inkl. PDF-Extraktoren v1/v2) referenziert kein Pipeline-Code; sie sind ein toter Nebenpfad. `DATA_STRUCTURE.md` §1 „Trade-Daten (Scraped)“ beschreibt sie trotzdem noch als Trade-Quelle.

### 1.2 Stage-Tabelle (Reihenfolge wie in `run()` bzw. `run_delta()`)

| # | Stage (`--stage_*`) | Zweck | Externer Dienst | Code |
|---|---|---|---|---|
| 1 | rename | Quiver-XLSX laden, Spalten-Mapping, Chamber füllen | – | `import_trades.py:384` (Map), `:445` |
| 2 | date | Datum parsen; tx_date fehlt oder liegt nach disclosure -> disclosure_date | – | `:478` |
| – | (in 1/2) | trade_id = SHA256 über 8 Inhaltsfelder | – | `utils/trade_id.py:33`, `:68` |
| 3 | committees | Name-Matching -> Party/State/Committees/ExOfficio | – | `:562`, `utils/name_matcher.py:270` |
| 4 | tickerclassify | Regex-Entscheidungsbaum für Ticker-Typen | – | `:2049` (anderer Agent) |
| 5 | tickeropenfigi | FIGI, ticker_figi, issuer_ticker | OpenFIGI | `:2075` |
| 6 | issuer_group | CIK-basierter issuer_group_key | SEC EDGAR | `:2123` |
| 7 | tickeryfinance | sector/industry | yfinance | `:2158` |
| 8 | trade_volume | Amount-Range -> Mittelwert/Max | – | `:2273`, `utils/amount_parser.py:35` |
| 9 | post_trade_performance | Preise, Returns 30–365 d, Exit-Erkennung | yfinance (Parquet-Cache) | `:2202`, `scoring/post_trade_returns.py:640` |
| 9b | abnormal_returns | signierter AR vs. SPY | yfinance-Cache | `:2261` |
| 10 | asset_volume | Rolling-Summe je issuer_group_key | – | `:2297` |
| 11 | gates_tags | Asset-Class-Gate, Committee-Tag (Insider/Relevant_Sectors), Vote-Timing | Congress.gov (Pre-Hook) | `:1142`, `:1038`, `:1077` |
| 12 | branch1_signals | committee, ex_officio, floor_action, extreme_report, volume | – | `:1170` |
| 13 | sequence_signals | contrarian, accumulative | – | `:1226` |
| 14 | swarm_signals | swarm_buy/sell/conflict/paradox | – | `:1369` |
| 15 | scoring | trade_score, breakdown, warnings | – | `:2405` |
| 16 | suspicious_export | Events nach suspicious_*.csv, Asset-/Member-Views | – | `:2423`, `:1718`, `:1810` |
| 17 | save | Backup, Merge, Paper-Return, Sortierung, complete- und curated-Export | yfinance (Paper-Return) | `:2480`, `:975` |

Abhängigkeiten sind als Spaltenlisten in `STAGE_DEPS` deklariert (`import_trades.py:233`) und werden nur bei Einzel-Stages geprüft (`:1908`).

### 1.3 Betriebsmodi

| Modus | Pfad | Verhalten |
|---|---|---|
| Voll-Rebuild | `run()` `:2901` | alle Stages im Speicher, ersetzt den Master |
| Delta | `run_delta()` `:2549` | Multiset-Delta über trade_id (`utils/trade_id.py:83`), nur neue Zeilen werden angereichert, Historie dient als Kontext, danach `_merge_into_master` (`:1961`) |
| Einzel-Stage | `run_stage()` `:2527` | liest und schreibt **den kompletten 645-MB-Master** (oder `trades_stage.csv` bei delta/dry_run), Backup pro Stage (`:1945`) |
| dry_run | alle | schreibt nach `trades_stage.csv`, Promotion später mit `--stage_save` |
| Bonus | `--manual_review_only`, `--regenerate_curated` | `:3127`, `:3137` |
| Reset | `scripts/reset_pipeline.py` | Whitelist (`TARGETS`) + `PROTECTED_FILES`, verschiebt standardmäßig ins Archiv, `--purge` mit doppelter Bestätigung |

### 1.4 Dienste-Landkarte

| Dienst | Wofür | Datei | Key/Login | Kosten |
|---|---|---|---|---|
| Quiver Quantitative | einzige Trade-Quelle (XLSX-Export) | `scrapers/quiver_scraper.py:128`, `config/settings.py:455-489` | `QUIVER_EMAIL`/`QUIVER_PASSWORD`, Premium-Abo | kostenpflichtiges Abo |
| Playwright/Chromium | Browser-Automation für Quiver | `deploy/systemd/quiver-import.service` | – | frei |
| OpenFIGI | Ticker -> FIGI/Security-Type | `settings.py:345, 359` | `OPENFIGI_API_KEY` optional (höheres Rate-Limit) | frei |
| SEC EDGAR | company_tickers_exchange.json -> CIK | `settings.py:159`, `utils/issuer_group_mapper.py` | User-Agent | frei |
| yfinance | Sektor, Preis-History 5 J., Paper-Return | `utils/ticker_sector.py`, `scoring/post_trade_returns.py` | – | frei, inoffiziell, rate-limitiert |
| Congress.gov API | bill_actions/subjects für Vote-Timing | `import_trades.py:1077`, `scrapers/fetch_congress_data.py` | `CONGRESS_API_KEY` | frei |
| Anthropic/OpenRouter | nur Committee Matcher (anderer Bereich) | `settings.py:814` | `OPENROUTER_API_KEY` | kostenpflichtig |
| systemd-User-Timer | täglich 03:00, `Persistent=true` | `deploy/systemd/quiver-import.timer` | – | – |

### 1.5 Datenmodell (Ist)

- Eine flache CSV, **114.347 Zeilen x 89 Spalten, 645 MB ≈ 5,6 KB pro Trade**. Die Quiver-Rohdatei hat für dieselben Zeilen 12 MB.
- `Insider_Sectors` (JSON-Liste aller Committee-Sektoren des Members, `import_trades.py:1068`) ist im Schnitt **5.121 Byte pro Zeile, also rund 72 % der Datei**. Der Wert hängt nur vom Member ab und wird trotzdem pro Trade wiederholt. Dazu kommen `Vote_Timing_Hits` (541 B) und `Member_Committees_Full` (308 B).
- Der „kuratierte“ `trades_master.csv` enthält dieselben Blob-Spalten und spart deshalb kaum etwas (591 MB statt 645 MB).
- 30 Spalten sind Ticker- bzw. FIGI-Attribute, die pro Ticker konstant sind; 34 Spalten enthalten Preis-, Return- und Exit-Werte (zeitabhängig, gehören in eine eigene Tabelle).
- Backups: bei jedem Save und jeder Einzel-Stage beide Master kopiert (`:1894`), `archive/trades_master_backup` belegt aktuell 3,5 GB.

---

## 2. Funktionsprüfung

### Belegt funktionierend
- **Mengen-Integrität:** Master und Quiver-XLSX haben beide 114.347 Zeilen; der Zeitraum 2012-02-27 bis 2026-07-20 ist identisch (a1/a3).
- **trade_id/Delta:** Unit-Tests `tests/test_trade_id.py`, `test_amount_parser.py` und `test_delta_signal_history_context.py` laufen grün (46 passed). Die Multiset-Delta-Logik (`utils/trade_id.py:83`) ist korrekt für echte Doppel-Zeilen: 22.781 Zeilen teilen sich eine trade_id, und `DEDUP_STRATEGY='keep_all'` behält sie bewusst.
- **Amount-Parser:** Alle Quiver-Ranges werden geparst, nur 1 Zeile ergibt Volumen 0.
- **Atomarer Quiver-Download:** Temp-Datei plus `os.replace` (`quiver_scraper.py:44, :225`). Im systemd-Service läuft der Import nur, wenn der Scraper mit Exit 0 endet.
- **Datums-Fallbacks** sind dokumentiert und nachvollziehbar (`:478-518`).

### Belegt defekt
1. **Name-Matching ordnet ~6 % der Trades dem falschen Abgeordneten zu.** Das Fuzzy-Matching (`rapidfuzz.WRatio`, Schwelle 85, `name_matcher.py:335`, `settings.py:321`) prüft weder Bundesstaat noch BioGuideID. Gemessen über alle 419 Namensschlüssel (a3/a4):
   - 151 Fuzzy-Treffer; **67 Schlüssel / 6.859 Trades mit falschem Bundesstaat, davon 6.016 Trades mit fremden Committees**.
   - Beispiele: Thomas J. Rooney (FL) -> Chuck Fleischmann (TN), 625 Trades, 151 Committee-Signale; Lamar Smith (TX) -> Christopher H. Smith (NJ), 507 Trades; James Langevin (RI) -> James Comer (KY); Gary C. Peters (MI) -> Hank Johnson (GA); Kelly Loeffler -> Mark Kelly.
   - Die Matcher-Werte **überschreiben die korrekten Quiver-Felder** `Party`/`State` (`import_trades.py:612-613`). Im Master steht z. B. Lamar Smith mit State NJ.
   - Quiver liefert `BioGuideID` für jede Zeile mit; damit wäre ein exakter Join möglich (342 BioGuideIDs vs. 419 Namensvarianten).
2. **Aktueller Senator ohne Match:** `Mullin, Markwayne` (399 Trades) bleibt unmatched, hat keine Committees und keinen State, bekommt aber trotzdem 96 Trades mit Score > 0.
3. **Committee-Daten sind zeitlich falsch zugeordnet:** Die Member-Listen bilden nur die aktuelle Besetzung ab, die Trades reichen bis 2012 zurück. Ehemalige Mitglieder (`former_members.csv`, 110 Zeilen) bekommen `committees=''` (`name_matcher.py:216`). 19 % der Trades haben keine Committees, und bei historischen Trades aktiver Mitglieder wird die heutige Besetzung angenommen. Schnittstelle zum Agent „Committee-Listen“.
4. **Delta aktualisiert alte Trades nicht:** `run_delta` berechnet Exit-Erkennung und Returns zwar über Historie plus Delta, schreibt aber nur die Delta-Zeilen zurück (`import_trades.py:2716-2718`). Ein alter Kauf mit `exit_status=held` bleibt deshalb `held`, auch wenn der Verkauf im Delta ankommt. Für Sequenz- und Schwarm-Signale gilt dasselbe Muster (`:2755-2771`). Korrigiert wird das erst beim nächsten Voll-Rebuild.
5. **Quiver-Import läuft seit 2,5 Monaten nicht:** Die letzte XLSX stammt vom 2026-08-03. Danach scheitert der Download an Timeouts bzw. abgelaufenem Abo (09/2026) und seit 10/2026 an einem Cookie-Consent-Overlay (`logs/quiver_scraper.log`, `quiver_cron.log` mit 8.107 Zeilen). Der Timer läuft weiter, gemeldet wird nichts.
6. **Quiver-Korrekturen erzeugen Dubletten:** Die trade_id ist ein Inhalts-Hash (`trade_id.py:33`). Korrigiert Quiver z. B. den Betrag (Spalte `last_modified` existiert), gilt der Trade als neu, und die alte Version bleibt im Master. Delta löscht nie. Belegt per Code; Häufigkeit wegen fehlender Alt-Snapshots nicht messbar.

### Vermutet / nicht verifiziert
- Der Save-Schritt schreibt den Master direkt mit `to_csv` (`:2509`, `:2813`, zusätzlich `save_results` `:2862-2900`, vermutlich ungenutzt), nicht atomar. Ein Abbruch während des Schreibens hinterlässt eine kaputte Datei (abgefedert durch Backups).
- Laufzeit: Das Matching läuft zeilenweise über 114k Trades statt über 419 eindeutige Namen (`:600`). Der Checkpoint pickelt alle 5.000 Zeilen den kompletten DataFrame (`:528`, `:628`).
- `STAGE_DEPS` ist veraltet: Es deklariert `MatchScore`/`MatchSource` (`:323`), die im Master fehlen, und nicht die vorhandenen `post_*_270d/365d`. Die Abhängigkeitsprüfung ist also nur grob.
- `pipeline_state.json` ist laut Code „informell, kein Gate“ (`:2537`) und enthält die nicht mehr existierende Stage `filter`. Der State hat keine Funktion.

---

## 3. Code-Qualität

| Befund | Beleg | Bewertung |
|---|---|---|
| Pipeline dreifach implementiert | `run()` `:2901-3126`, `run_delta()` `:2549-2861`, `_stage_*` `:1993-2525`. Inline-Spaltenzuweisungen werden wiederholt, z. B. `_apply_branch1_signals` aufgerufen in `:2361/2750/3048`, `compute_trade_scores` in `:2412/2775/3060` | Hauptursache der Aufblähung; Drift-Risiko |
| Uneinheitliche Stage-Nummerierung | „10/17“, „9b/17“, „16/16“, im Full-Run fehlt [10/17] | kosmetisch, zeigt aber den Drift |
| God-Class `TradeImporter` | 3.277 Zeilen; Import, Enrichment, Signal-Orchestrierung, Suspicious-Views, Schema-Migration (`:1527`) und IO in einer Klasse | nicht testbar |
| Einzel-Stage-Modus mit 645-MB-Roundtrip plus Backup pro Stage | `:1917-1959` | Komplexität ohne Nutzen gegenüber einem schnellen In-Memory-Lauf |
| Zeilenweise `iterrows`/`df.at` | `:600`, `:1038-1073` | langsam; die Werte sind pro Member bzw. pro Ticker konstant |
| Breite `except Exception` (10x) | z. B. `:1072` verschluckt Relevance-Fehler pro Zeile nur als Warning, `:1138` | Fehler werden still zu leeren Feldern |
| Toter Nebenpfad | House/Senate-Scraper, PDF-Extraktor v1+v2, `scrapers/Recherche-Aufgabe in Datei - Claude_files`, `scripts/excel_debug_output.txt`, `trades_master_complete.pre_f2.csv`, `suspicious_events.csv.pre-p6-migration.bak` (zusammen ca. 450 MB) | aufräumen |
| Wiederholte Logik | Datums-Fallback als Kopie in `scripts/diagnose_master_quiver_delta.py` (Kommentar `:503`) | Drift |
| Doku-Volumen | `DATA_STRUCTURE.md` 2.565 Zeilen, `CLI_Bedienungsanleitung.md` 2.117 Zeilen | für ein Ein-Personen-Projekt kaum wartbar |
| Positiv | `reset_pipeline.py` (Whitelist + Protected + Archiv-Move), atomarer Download, trade_id mit sauber dokumentierter Semantik, Multiset-Delta, Promotion der Problem-Outputs (`:865`) | solide |

---

## 4. Wiederverwendbares Wissen für einen Rebuild

- **Quiver-Schema und Mapping:** 20 Spalten inkl. `BioGuideID`, `District`, `Party`, `State`, `excess_return`, `last_modified` (`import_trades.py:384-405`). BioGuideID macht Name-Matching überflüssig.
- **Quiver-Download per Playwright:** Selektoren und Viewport in `settings.py:457-486`, Ablauf in `quiver_scraper.py:128-235` (Login -> Export-Seite -> `expect_download` -> Temp -> `os.replace`). Das Cookie-Overlay (`fc-consent-root`) muss vor dem Klick entfernt oder abgelehnt werden.
- **Datums-Regeln:** Fehlt tx_date oder liegt es nach disclosure_date, gilt disclosure_date (Anleihe-Fälligkeiten), `:478-518`.
- **Amount-Ranges:** Parser inkl. „Over $50,000,000“ und Single-Werte, `utils/amount_parser.py:35-103`; Testfälle in `tests/test_amount_parser.py`.
- **trade_id:** Hash-Felder und die Falle Timestamp vs. String beim Hashen (Docstring `utils/trade_id.py:1-20`), Multiset-Delta `:83-120`.
- **Name-Normalisierung** (falls doch nötig): Senate-Format „Last, First, Jr.“ (`name_matcher.py:78-105`), Trailing-Comma-Bereinigung, 16 manuelle Aliase in `data/mappings/name_aliases.csv`.
- **Exit-Erkennung:** Fenster plus kumulierte Gegen-Volumina mit Toleranz (`scoring/post_trade_returns.py:1051ff.`), Status-Werte in `DATA_STRUCTURE.md:1249-1256`.
- **Reset-Muster:** `scripts/reset_pipeline.py` (Archiv statt Löschen) unverändert übernehmbar.
- **systemd-Timer mit `Persistent=true`** für Laptop-Betrieb (`deploy/systemd/`).

---

## 5. Rebuild-Empfehlung

**Empfehlung: Rückgrat neu bauen, Fachlogik-Module (Parser, Exit-Erkennung, Signale) als Funktionen übernehmen.** Das jetzige Rückgrat hat drei parallele Orchestrierungen, ein aufgeblähtes Flat-File und einen Matching-Fehler, der direkt die Kernaussage verfälscht (falsche Committees -> falsche Committee-Signale -> falscher Score). Das lohnt keine Reparatur im Bestand.

### Schlankeres Datenmodell (SQLite oder DuckDB/Parquet statt einer CSV)

| Tabelle | Schlüssel | Inhalt | Größenordnung |
|---|---|---|---|
| `raw_trades` | trade_id + quiver_row_hash | Quiver-Spalten 1:1, `first_seen`, `last_seen`, `superseded_by` | ca. 15 MB |
| `members` | bioguide_id | Name, Partei, Staat, Chamber, Amtszeiten | ca. 600 Zeilen |
| `committee_memberships` | bioguide_id, committee_id, **congress (von–bis)** | zeitlich gültige Mitgliedschaften, ex_officio-Rolle | klein |
| `committee_sectors` | committee_id | Sektor-Mapping (statt `Insider_Sectors` pro Trade) | klein |
| `securities` | ticker | ticker_type, FIGI-Felder, issuer_group_key, sector/industry | ca. 5k Zeilen |
| `prices` | ticker, date | vorhandener Parquet-Cache | – |
| `trade_metrics` | trade_id | Returns, Exit, AR, Volumen (vollständig neu berechenbar) | schmal |
| `trade_signals` | trade_id, signal | Long-Format statt `signals_triggered`-String | – |
| `trade_scores` | trade_id, score_version | Score + Breakdown | – |

Die Dashboard-Sicht entsteht als View/Join. Geschätzt schrumpft der Bestand von 645 MB (plus 591 MB plus 3,5 GB Backups) auf unter 50 MB.

### Pipeline-Schnitt
1. `ingest`: Quiver-Download, Upsert in `raw_trades` (Versionierung über `last_modified`, Korrekturen ersetzen statt duplizieren).
2. `reference`: members/committees aus offiziellen Quellen, Join **über bioguide_id** (kein Fuzzy). Als Fallback Name+State, mit Report statt stiller Zuordnung.
3. `securities`: Classify -> OpenFIGI -> EDGAR -> yfinance, **pro eindeutigem Ticker** gecacht.
4. `metrics`: Preise/Returns/Exit **immer komplett neu** für alle Trades (114k Zeilen sind in Sekunden vektorisiert rechenbar, das braucht keine Delta-Logik und ist damit nicht inkonsistent).
5. `signals` + `score`: reine Funktionen auf den Tabellen, vollständig neu berechnet.

Inkrementell ist dann nur noch das teure Netz-IO (Download, Ticker-APIs, Preise); alles Abgeleitete wird deterministisch neu gerechnet. Damit entfallen der Delta-Pfad, der Einzel-Stage-Modus, `STAGE_DEPS`, `pipeline_state.json`, Checkpoints und die Backups des Masters (die Rohdaten genügen für einen Neuaufbau). Dazu ein Health-Check, der eine fehlgeschlagene oder veraltete Quiver-Abfrage sichtbar meldet (Dashboard-Banner „Daten vom …“).
