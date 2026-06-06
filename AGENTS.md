# Congress Trade Tracker - Projektstatus

## Projektbeschreibung
Python + Streamlit App die US-Kongress-Mitglieder Stock-Trades trackt, analysiert und Trading-Empfehlungen erstellt.

## Wichtige Regeln
1. **Alle Filter-Parameter muessen konfigurierbar sein** (in config/settings.py)
2. **Immer erst Implementierungsplan zeigen**, dann auf "OK" warten bevor Code geschrieben wird
3. **transaction_date verwenden**, nicht disclosure_date (45-Tage Meldefrist)
4. **Neue Dateien dokumentieren**: Bei Erstellung oder Verwendung neuer projektrelevanter Dateien (Daten, Config, Scripts) → in `organisation/DATA_STRUCTURE.md` (Struktur, Spalten, Zweck, Beispiele) und `organisation/Pipelines.md` (Zweck, Position in Pipeline) aufnehmen
5. **Dokumentation aktualisieren**: Bei jeder Code-Änderung die betroffenen Erklärungs-Dateien mitpflegen:
   - Neue/geänderte Datenstrukturen oder Spalten → `organisation/DATA_STRUCTURE.md`
   - Neue/geänderte Scripts, Ablauf oder Abhängigkeiten → `organisation/Pipelines.md`
   - Genaue BEschriebung zu jeder Doku datei ist unten in detail doku
6. **Systemanweisung**: Du bist ein erfahrener Software-Entwickler. Du erstellst vor jeder Code-Änderung einen Implementierungsplan, bevor du diesen strikt umsetzt. Um einen individuell passenden Plan zu erstellen frägst du den User nach konkreten Anweisungen und lässt wichtige Planungsfragen vom User approven. Du teilst jeden Gedankengang und Entscheidungsweg mit dem User. Falls Rückfragen oder Probleme auftreten, teile sie dem User mit.
7. **Fragen an dich**: Wenn der User nach deiner Einschätzung frägt, dann präsentiere zunächst deinen Gedankengang, verschiedene Lösungswege, Pro-/Kontra-Argumente und Konsequenzen jedes Lösungsweges und zuletzt deine begründete Empfehlung.
8. **Plan-Dateien nicht recyceln**: Wenn ein komplett neuer Implementierungsplan zu einem neuen Todo erstellt wird (und keine themenrelevante Plan-Datei existiert), darf KEINE vorhandene Plan-Datei aus einem unverwandten Vorgang überschrieben werden. Stattdessen neue Plan-Datei anlegen. Begründung: Token-Ersparnis — der Main-Agent muss eine Datei vor jeder Änderung erst komplett einlesen; eine frische Datei umgeht diesen Read-Roundtrip.
9. **Dokumentation nach Modulabschluss via Doku-Agent**: Nach Abschluss eines Moduls die Dokumentation nicht selbst schreiben, sondern den Sub-Agenten starten. Der Agent erwartet ein strukturiertes, vollständiges und ausführliches Briefing (geänderte Dateien, neue Spalten, neue Scripts/Stages, neue Signale, betroffene Doku-Dateien). Vorlage + Regeln: `.claude/agents/doku-agent.md`.
   - `model`-Feld ist **nicht** in `.claude/settings.json` gesetzt → Override erfolgt im Agent-Call:
     - **Anthropic-Session:** `model: "haiku"`
     - **DeepSeek-Session:** `model: "flash"`
   - Aufruf: `Agent({ subagent_type: "doku-agent", model: "haiku"|"flash", prompt: "## Briefing: ..." })`
10. **Einsatz von subagents** Nutze für tokenintensive und gleichzeitig nicht anspruchsvolle Aufgaben subagents, welche eine Teilaufgabe für dich übernehmen. z.b. den explore subagent zum indentifizieren von dateien oder finden von wichtigen Codestellen unter vielen verschiedenen Dateien.

---

## Modul-Status

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| 1 | FERTIG | Projektstruktur |
| 2 | FERTIG | House Trades Scraping (ZIP + PDF-Parsing, 126 Tests, subholding-Fix Feb 2026) + House PDF Extractor v2 (OpenCV Grid + Pixel-Density + Vision API Batch, alle Formulartypen A/B/C/D, Mrz 2026) |
| 3 | FERTIG | Senate Trades Scraping (Playwright + DataTables API). senate_pdf_extractor liest gescannte PDFs fehlerhaft (Vision API), aber irrelevant — einziger Senator mit Paper-PTRs investiert nur in Fonds |
| 4 | FERTIG | Ausschuss-Sektor-Mapping (CommitteeMapper + CommitteeTag implementiert) |
| 5 | TEILWEISE (Post-Trade-Pricing FERTIG, kein Live-Quote) | Kursdaten (Yahoo Finance) — Post-Trade-Performance-Tracking abgeschlossen (Stage post_trade_performance, 21 Master-Spalten = 16 bisher + 5 neue: `post_price_180d`, `post_return_180d`, `first_close_offset_days`, `paper_return_pct`, `price_as_of`; Exit-Detection entkoppelt, yfinance-Preis-Cache). **Exit-Fenster-Entkopplung** (2026-06-04): `EXIT_DETECTION_WINDOW_DAYS` (Default None=unbegrenzt) entkoppelt vom Return-Horizont → Exits werden korrekt detektiert auch wenn >120 Tage später; 180-Tage-Horizont neu. **Richtungsbewusste Return-Semantik**: `post_return_{30,60,90,120,180}d` und `effective_return_pct` mit Vorzeichen-Inversion für Verkäufe (sell_sign = −1). **Realisiert-Only**: `effective_return_pct` nur für closed_full/closed_partial (held/delisted/unknown/not_applicable → NaN). Neue Spalte `first_close_offset_days` (Tage bis erstem Gegen-Trade). **Horizont-Sichtbarkeitsfilter (Dashboard)**: Global (alle 4 historischen Tabs) filtert Trades mit `first_close_offset_days` ≤ Primary-Horizont (design: öffene/held-Positionen mit NaN werden AUSGEBLENDET; Monitoring offener Positionen erfolgt über neuem „Recent Trades"-Tab). **Partial-Close-Zerlegung (Display-only)**: Post-Trade-Tab zeigt pro Eröffnungs-Trade ggf. mehrere Zeilen (trade_id `{parent}-pc{n}`, `parent_trade_id`-Rückverweis). Details: `organisation/DATA_STRUCTURE.md` (Post-Trade-Spalten inkl. 180d/first_close_offset_days/paper_return_pct/price_as_of) + Tests (test_post_trade_returns.py, test_chart_helpers.py). |
| 6 | FERTIG | Vote-Timing aktiv. VoteTimingTag liest Roll-Calls direkt aus bill_actions.json + bill_subjects.json (kein separater Vote-Endpoint nötig). FloorActionSignal triggert auf Vote_Timing_Hit_Count ≥ 1; SCORING_WEIGHTS['floor_action']=15. Smoke-Run (20.05.2026): 5.357 Hits / 111.986 Trades (4,8%). Score-Effekt: mean=64,6 vs 21,6 ohne. Details: `organisation/Filter_Instanz_Parameter.md` (Tag-Sektion). |
| 7 | FERTIG | Signal-Architektur (Gate/Tag/Signal). Drei Klassen: `gates/` / `tags/` / `signals/`. 11 aktive Signale + Suspicious-Export-Pipeline + Cross-Class Issuer Mapping. Committee-Score auf einheitliche Match-Liste vereinheitlicht (`Relevance_Score` entfernt, `committee_full`-Signal hinzugefügt). Details: `CHANGELOG.md` (P1–P7) + `organisation/Filter_Instanz_Parameter.md`. |
| 8 | FERTIG | Scoring-System. Pipeline 17 Stages, 6 Trade-Spalten (`trade_volume_usd`, `trade_amount_max_usd`, `asset_tradingvolume_usd`, `trade_score`, `trade_score_breakdown`, `trade_warnings`) + Aggregat-Spalten in `suspicious_assets.csv` und `suspicious_members.csv`. **Volume-Signal-Redesign (27.05.2026):** linearer `volume_part = max(0, asset_tradingvolume_usd − U) × SLOPE` ersetzt durch vollwertiges Tier-Signal `trade_volume` auf `trade_amount_max_usd`. Trigger ab >15.000 USD, Default-Tiers (Vorschlag C, konservativ): ≤50k→+2, ≤100k→+4, ≤250k→+7, ≤500k→+10, ≤1M→+13, >1M→+16. Smoke-Run 27.05.2026 (112.658 Trades): mean=13.0 (vorher 23.7), max=83 (vorher 1705) — Top-Trades sind jetzt durchweg Multi-Signal-Treffer, nicht mehr reine Volume-Trades. 84 Unit-Tests grün (Scoring + Signal). Score-Formel + Settings: `organisation/Filter_Instanz_Parameter.md`. |
| 9 | IN ARBEIT (MVP + Post-Trade-Performance-Tab + Horizont-Filter + Recent-Trades-Tab FERTIG) | Streamlit Dashboard. `app/streamlit_app.py` + `app/score_recompute.py` + `app/scoring_presets.py` + `app/chart_helpers.py`. **Fünf Tabs** (Trades / Assets / Members / Post Trade Performance / Recent Trades), Sidebar-Filter inkl. **unified Suche** (`_search_mask`: token-basiert/UND-verknüpft, reihenfolge-unabhängig über Name/**Asset**/Ticker/Hash-ID — durchsucht `['name', 'asset_name', 'ticker', 'issuer_ticker', 'trade_id']`, findet z.B. "pfizer" → einzelne Assets oder "smith pfizer" → Member + Asset kombiniert; ersetzt die alten Felder „Member (Substring)"+„Ticker (Substring)"); wirkt via `apply_prefilter` auf alle vier historischen Tabs (Datums-/Score-Filter bleiben aktiv, kein Bypass). **Horizont-Sichtbarkeitsfilter (Sidebar-Toggle, Standard an)**: begrenzt die vier historischen Tabs global auf Trades mit `first_close_offset_days` ≤ Primary-Horizont, d.h. nur geschlossene Trades deren Exit im Zeitfenster liegt (Design: offene/held-Positionen mit NaN werden per `off.notna()` AUSGEBLENDET). Voll-Recompute bei Signal-Ausschluss. Asset- und Member-Tab live aus gefiltertem Trade-Subset aggregiert (reaktiv auf alle Filter). **Trade-Tab**: abgeleitete Spalte `relevant_sectors` + neue Spalte `trade_id` (Hash-ID). **Post-Trade-Performance-Tab**: Top-N nach **`trade_score`** sortiert + Ausklapp-Buttons (+100 / Alle) + Performance-Charts (Horizont-Linien bis 180d, **Teil-/Vollverkaufs-Marker via `transaction_type`** statt `exit_status`, `Sale (Partial)` → violettes Dreieck, `Sale (Full)` → oranges X; Funktionen `sale_kind_marker()` und `build_sale_kind_traces()`). **Partial-Close-Zerlegung-Tabelle** (unterhalb Performance-Chart): pro Öffnungs-Trade ggf. mehrere Display-Zeilen (neue Spalten: `parent_trade_id`, `first_close_offset_days`; reine UI-Ausgabe via `build_partial_close_rows`, keine Master-Persistierung). **Neu: Recent Trades-Tab** — Monitoring frischer/offener Positionen, unabhängig vom Horizont-Filter (`apply_prefilter` umgangen). Fenster-Wahl (RECENT_MONITORING_WINDOW_OPTIONS) Sidebar, nutzt globale Suche mit, zeigt BEIDE geschlossene und offene (held) Trades mit neuesten Mark-to-Market Papier-Returns (Spalten `paper_return_pct`, `price_as_of`); Ranking nach `trade_score` + Pagination. Metriken: Recent Trades / davon offen (held) / mit Paper Return. **Sidebar-Form „Score-Gewichte"**: alle `SCORING_WEIGHTS` (inkl. `trade_volume`), Paradox-Damper, Trade-Volume-Tier-Editor (`st.data_editor`, dynamische Zeilen), Trigger-Floor, Enabled-Flag und Ex-Officio-Bonus live override-bar; Apply/Defaults/Speichern-Buttons, Preset-Persistenz in `data/config/scoring_presets.json` (Tier-Liste serialisiert mit `null` als `inf`-Catch-all). Start: `/opt/venv/bin/streamlit run app/streamlit_app.py`. |
| 10 | OFFEN | Email-Benachrichtigungen |
| 11 | TEILWEISE (Quiver-Auto-Download-Scraper + Scheduler FERTIG) | Automatisierung & Scheduler. Quiver-Auto-Download-Scraper (`scrapers/quiver_scraper.py`): headless Playwright-Login (realistischer User-Agent), tägliche automatisierte XLSX-Downloads, atomare Datei-Speicherung (`os.replace`), saubere Exit-Codes (0/1/2/3 für Erfolg / generischer Fehler / Login-Fehler / Download-Fehler=Abo), 8 Unit-Tests. **Scheduler aktiv**: systemd-User-Timer `quiver-import.timer` (täglich 03:00, `Persistent=true` holt verpasste Läufe nach — Laptop nachts aus); `quiver-import.service` führt Scraper + Import als zwei `ExecStart`-Schritte aus (Scraper-Exit ≠ 0 ⇒ kein Import). Vorlagen + Anleitung: `deploy/systemd/` (README = Single-Source). **Selektoren konfiguriert + end-to-end verifiziert (2026-06-04):** Flow Login → Cookie-Reject → direkt `/export/` → href-Selektor `a[href*="congress-trading-all.xlsx"]`; Post-Login-Check `#hamburger-icon` (abo-unabhängig); `--dry-run` lud erfolgreich 12 MB XLSX (Exit 0). Genereller Scheduler für übrige Pipelines noch offen. |
| Rebuild T1 | FERTIG (25.04.2026) | Foundation: `utils/trade_id.py` (13 Tests) + `utils/ticker_classifier.py` (67 Tests) |
| Rebuild T2 | FERTIG (25.04.2026) | Network-Wrapper: `utils/ticker_normalizer.py` (OpenFIGI) + `utils/ticker_sector.py` (yfinance). Defensiv-Mechanismen §11 vollständig. |
| Rebuild T3 | FERTIG | Integration der Rebuild-Module in `scripts/import_trades.py`. |
| Rebuild T4 | OFFEN | Migration: alte Caches → neue Schema-Form, alte Module entfernen |
| Rebuild T5 | FERTIG (P5 + P5.5 + P5.6 ✅) | P5: `share_class_figi`-Spalte aus OpenFIGI in `trades_master_complete.csv`, Router-Erweiterung (YFINANCE_DIRECT + MANUAL_REVIEW gehen jetzt durch OpenFIGI mit Negativ-Cache). P5.5: abgeleitete Spalte `issuer_ticker` (Share-Class-Level, Fallback `share_class_figi → ticker_base → ticker`) in `trades_master.csv` + `trades_master_complete.csv`, Signal-System komplett umgestellt. P5.6: Cross-Class via SEC EDGAR CIK — neue Stage `_stage_issuer_group`, neue Spalte `issuer_group_key` (Präfixe `CIK:` / `TICKER:` / `SKIP:`), ETF/Foreign-Bypass, strikter Abbruch bei SEC-API-Failure (kein silent Fallback auf veralteten Cache), TTL 7 Tage. `SWARM_GROUPING_KEY` → `issuer_group_key`. Zentrale Doku `organisation/Ticker_Architektur.md` mit Design-Decision-Log. |

---

## Wichtige Dateien

### Scripts & Scraper
- `scrapers/quiver_scraper.py` — Auto-Download der Congress-Trading-XLSX von Quiver (täglich 03:00 via systemd-User-Timer, Exit-Codes 0/1/2/3)
- `deploy/systemd/` — systemd-User-Timer/Service-Vorlagen + README für den Scheduler (Quiver-Download + Import)
- `scripts/import_trades.py` — Haupt-Import-Pipeline (nach jedem Scraper-Lauf ausführen)

### App
- `app/streamlit_app.py` — Streamlit Dashboard (Trades, Assets, Members, Post-Trade-Performance)
- `app/score_recompute.py` — Live-Score-Recompute bei Signal-Ausschluss
- `app/scoring_presets.py` — Preset-Verwaltung (Laden/Speichern von Score-Gewichten)
- `app/chart_helpers.py` — Streamlit-unabhängige Chart-Helfer (Teil-/Vollverkaufs-Marker aus `transaction_type` via `sale_kind_marker()` und `build_sale_kind_traces()`; Recent-Trades-Fensterfilter via `build_recent_pool()`)

### Code-Konvention: Gate / Tag / Signal
- `gates/`   — binäre Hard-Ausschlüsse
- `tags/`    — Kontext-Anreicherung mit Master-Spalten
- `signals/` — User-sichtbare Score-Beiträge
- Instanzen + Parameter + Score-Formel: `organisation/Filter_Instanz_Parameter.md`

### Utils-Übersicht
- `utils/name_matcher.py` — Fuzzy-Matching (rapidfuzz)
- `utils/trade_id.py`, `utils/signal_event_id.py` — Hash-basierte stabile IDs
- `utils/issuer_ticker.py`, `utils/issuer_group_mapper.py` — Cross-Share-Class / Cross-Class Auflösung; Architektur: `organisation/Ticker_Architektur.md`
- `utils/ticker_classifier.py`, `utils/ticker_normalizer.py`, `utils/ticker_sector.py` — Ticker-Pipeline (Regex / OpenFIGI / yfinance)
- `utils/amount_parser.py` — Quiver-Amount-Range → Mittelpunkt USD
- `scoring/asset_volume.py`, `scoring/trade_score.py` — Modul-8-Berechnungen; Formel + Settings in `organisation/Filter_Instanz_Parameter.md`
- `utils/openfigi_client.py` — LEGACY (Rebuild T4 entfernt diese Datei)

### Config
- `config/settings.py` — Alle konfigurierbaren Parameter
- `config/committee_mapping.py` — O(1) Subcommittee → Sektor-Lookup

### Dateien-Übersicht
Vollständige Liste aller Datendateien (Trades, Mappings, Caches, Outputs) inkl. Spaltenstrukturen: **`organisation/DATA_STRUCTURE.md`**.

---

## Import-Workflow (Kurzform)

```bash
cd congress_tracker && .\venv\Scripts\activate
python scrapers/house_scraper.py        # data/raw/house_trades.csv
python scrapers/senate_scraper.py       # data/raw/senate_trades.csv
python scripts/import_trades.py         # data/processed/trades_master*.csv
```

CLI-Optionen (Delta-Mode, einzelne Stages, Dry-Run, PDF-Extraction): siehe `organisation/CLI_Bedienungsanleitung.md`.

---

## Nächste Schritte

1. **Modul 9 — Streamlit Dashboard (in Arbeit, Post-Trade-Performance-Tab ✅)** — Nächste Iteration: `effective_return_horizon_days`-Spalte einblenden sobald Plan A diese in den Master schreibt (Platzhalter-Check bereits im Code).
2. **Post-Trade-Performance (FERTIG, Updates 2026-05-31 + Exit-Entkopplung 2026-06-04)** — Stage `post_trade_performance` (Stage 9 von 17). 19 Spalten in `trades_master_complete.csv` (16 bisher + 3 neu 2026-06-04: `post_price_180d`, `post_return_180d`, `first_close_offset_days`). Exit-Detection (closed_full/closed_partial/held) gruppiert nach (name, issuer_group_key); Suchfenster via `EXIT_DETECTION_WINDOW_DAYS` vom Return-Horizont entkoppelt. yfinance-Preis-Cache in `data/cache/price_history.parquet`. Details: `organisation/Briefing_stage_post_trade_performance.md`.
3. **Rebuild T4** — Migration alter Caches, Entfernen von `utils/openfigi_client.py` und alten Tabellen.
4. **Modul 10 — Email-Benachrichtigungen**, **Modul 11 — Automatisierung**

Historische Plan-Datei (P1–P7, abgeschlossen): `archive/alt/Implementierungsplan_Signals.md`.

---

## Detail-Doku (Index)

**Konvention (Single-Source-of-Truth):** Pro Thema EINE kanonische Datei.
Andere Dateien verlinken nur darauf, kein paralleles Duplizieren.

| Thema | Kanonische Quelle |
|---|---|
| Spalten-Definitionen (CSV/JSON/XLSX) | `organisation/DATA_STRUCTURE.md` |
| Gate/Tag/Signal-Instanzen + Score-Formel + Settings | `organisation/Filter_Instanz_Parameter.md` |
| Pipeline-Stage-Reihenfolge + Indizes | `organisation/Pipelines.md` |
| Signal-Schema (`suspicious_events.csv` & Views) | `organisation/tool_specific_md/signal_info.md` |
| Ticker-/Issuer-Architektur (3 Ebenen, Fallbacks) | `organisation/Ticker_Architektur.md` |
| CLI-Flags + Bedienung | `organisation/CLI_Bedienungsanleitung.md` |
| Phasen-Historie (abgeschlossene Module) | `CHANGELOG.md` |
| Aktueller Modul-Status | `CLAUDE.md` (Projekt-Root, Modul-Tabelle oben) |
| Offene Implementierungs-Pläne | `~/.claude/plans/*.md` |

Bei Code-Änderungen die folgende Tabelle prüfen — die
„Aktualisieren bei"-Spalte nennt konkrete Trigger, dadurch muss nicht
jede Datei einzeln gelesen werden.

### Top-Level

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `CLAUDE.md` (Projekt-Root) | Projektstatus, Modul-Tabelle, Wichtige Regeln, Doku-Index. | Neuer Modul-Status, neue Regel/Konvention, neues Util/Skript, Phase abgeschlossen. |

### `organisation/`

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `README.md` | Kurz-Überblick mit Datenfluss-Diagramm, Modul-Status, Quick-Start, Scoring-Tabelle. | Neuer Modul-Status, Datenfluss, Scoring-Formel/-Gewichte geändert. |
| `DATA_STRUCTURE.md` | Vollständige Spalten-Definition **aller** CSV/JSON/XLSX. | Neue/geänderte Spalte (CSV, Cache, JSON), neue Datendatei, Suffix-Konvention geändert. |
| `Pipelines.md` | Ausführungsreihenfolge aller 9 Pipelines + Import-Stages (aktuell **14**). | Neue/entfernte Stage, Stage-Abhängigkeiten geändert, neue Problem-Output-Datei, Dedup-Logik. |
| `CLI_Bedienungsanleitung.md` | Vollständige CLI-Referenz aller Scripts inkl. Flag-Matrix. | Neues CLI-Flag, neues Script, geänderter Default-Workflow. |
| `Filter_Instanz_Parameter.md` | Konkrete Gate/Tag/Signal-Instanzen + Parameter + Scoring-Formel. | Neuer Filter/Signal, Schwelle/Gewicht geändert, neue Scoring-Komponente. |
| `Ticker_Architektur.md` | Design-Doku der Ticker-/Issuer-Spalten (3 Ebenen, Fallback-Ketten). | Neuer Ticker-Spalten-Typ, Fallback-Kette geändert, neue Cross-Class-Logik. |
| `migration.md` | Plattformwechsel-Anleitung (Linux↔Windows, USB-Betrieb). | Neue Python-Abhängigkeit, neue ENV-Variable, neue plattformspezifische Permission. |
| `manual_review_anleitung.md` | Anleitung für 4 manuelle Review-Prozesse (Namen, Ticker, Preferred, Duplicate). | Neuer Manual-Review-Typ, Review-Datei-Struktur geändert, neue Mapping-CSV. |

### `organisation/tool_specific_md/`

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `signal_info.md` | **Vollständige Signal-System-Doku**: Architektur, 10 Signal-Typen, Snapshot-Konzept, Scope-Key-Konvention, 21-Spalten-Schema von `suspicious_events.csv`. | Neuer Signal-Typ, Trigger-Logik/Spalten geändert, Scope-Key-Format, Y-Tage-Persistence, Signal-Event-ID-Hash. |
| `HAND_DELIVERED_PDF_ANALYSE.md` | Analyse der 59 gescannten Hand-Delivered House PTR PDFs (Typ A–D). | Neuer PDF-Formulartyp, Layout-Wechsel, neue OCR-Schwierigkeit, neuer Member mit Hand-Delivered-Form. |
| `house_all_pdf_info.md` | House-Committee-PDF Layout + Parsing-Parameter (80 Seiten, Font-Größen, Schwellwerte). | PDF-Layout geändert, neue Rollen, neue Committee-Struktur, neuer Parsing-Schwellwert. |
| `modul2_walkthrough.md` | Walkthrough House Scraper (ZIP→XML→PDF→Block-Parser). | Neuer Filing-Type, Block-Parser-Logik, neue Skip-Pattern, Output-Spalte hinzu/entfernt. |
| `modul3_walkthrough.md` | Walkthrough Senate Scraper (Playwright + DataTables API). | API-Parameter geändert, Agreement-Flow, Report-Type-Filter, neue Test-Fälle. |
| `Scraper_problems.md` | Bekannte Scraper-Probleme + Status (aktiv/gelöst/irrelevant). | Neues Scraper-Problem, Status geändert, neue Gegenmaßnahme, neue Fehlerrate. |
| `Senate_website_structure.md` | efdsearch.senate.gov Selektoren + HTML-Layout. | Website-Layout/Selektoren geändert, Bond-Metadaten-Format, neue Asset-Type. |
| `vote_timing_audit.md` | Stichproben-Audit der Vote-Timing-Hits. | Neuer Smoke-Run mit relevant abweichender Hit-Anzahl, neue Audit-Punkte hinzu, Mapping-Änderungen. |

**Konvention**: Bei jeder Code-Änderung mindestens eine Doku-Datei anpassen
(s. Regel 5). Phase-Abschluss → `CLAUDE.md` Modul-Status + alle in der „Aktualisieren bei"-Spalte
genannten Dateien.
