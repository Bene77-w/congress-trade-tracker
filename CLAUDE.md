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
   - Mindestens eine der beiden Dateien ist bei jeder relevanten Änderung anzupassen.
6. **Systemanweisung**: Du bist ein erfahrener Software-Entwickler. Du erstellst vor jeder Code-Änderung einen Implementierungsplan, bevor du diesen strikt umsetzt. Um einen individuell passenden Plan zu erstellen frägst du den User nach konkreten Anweisungen und lässt wichtige Planungsfragen vom User approven. Du teilst jeden Gedankengang und Entscheidungsweg mit dem User. Falls Rückfragen oder Probleme auftreten, teile sie dem User mit.
7. **Fragen an dich**: Wenn der User nach deiner Einschätzung frägt, dann präsentiere zunächst deinen Gedankengang, verschiedene Lösungswege, Pro-/Kontra-Argumente und Konsequenzen jedes Lösungsweges und zuletzt deine begründete Empfehlung.
8. **Plan-Dateien nicht recyceln**: Wenn ein komplett neuer Implementierungsplan zu einem neuen Todo erstellt wird (und keine themenrelevante Plan-Datei existiert), darf KEINE vorhandene Plan-Datei aus einem unverwandten Vorgang überschrieben werden. Stattdessen neue Plan-Datei anlegen. Begründung: Token-Ersparnis — der Main-Agent muss eine Datei vor jeder Änderung erst komplett einlesen; eine frische Datei umgeht diesen Read-Roundtrip.

---

## Modul-Status

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| 1 | FERTIG | Projektstruktur |
| 2 | FERTIG | House Trades Scraping (ZIP + PDF-Parsing, 126 Tests, subholding-Fix Feb 2026) + House PDF Extractor v2 (OpenCV Grid + Pixel-Density + Vision API Batch, alle Formulartypen A/B/C/D, Mrz 2026) |
| 3 | FERTIG | Senate Trades Scraping (Playwright + DataTables API). senate_pdf_extractor liest gescannte PDFs fehlerhaft (Vision API), aber irrelevant — einziger Senator mit Paper-PTRs investiert nur in Fonds |
| 4 | FERTIG | Ausschuss-Sektor-Mapping (CommitteeMapper + CommitteeTag implementiert) |
| 5 | ÜBERSPRUNGEN | Kursdaten (Yahoo Finance) — vorerst übersprungen |
| 6 | FERTIG | Vote-Timing aktiv. VoteTimingTag liest Roll-Calls direkt aus bill_actions.json + bill_subjects.json (kein separater Vote-Endpoint nötig). FloorActionSignal triggert auf Vote_Timing_Hit_Count ≥ 1; SCORING_WEIGHTS['floor_action']=15. Smoke-Run (20.05.2026): 5.357 Hits / 111.986 Trades (4,8%). Score-Effekt: mean=64,6 vs 21,6 ohne. Details: `organisation/Filter_Instanz_Parameter.md` (Tag-Sektion). |
| 7 | FERTIG | Signal-Architektur (Gate/Tag/Signal). Drei Klassen: `gates/` / `tags/` / `signals/`. 11 aktive Signale + Suspicious-Export-Pipeline + Cross-Class Issuer Mapping. Committee-Score auf einheitliche Match-Liste vereinheitlicht (`Relevance_Score` entfernt, `committee_full`-Signal hinzugefügt). Details: `CHANGELOG.md` (P1–P7) + `organisation/Filter_Instanz_Parameter.md`. |
| 8 | FERTIG | Scoring-System. Phasen 8.1–8.6 ✅. Pipeline 16 Stages, 5 Trade-Spalten (`trade_volume_usd`, `asset_tradingvolume_usd`, `trade_score`, `trade_score_breakdown`, `trade_warnings`) + Aggregat-Spalten in `suspicious_assets.csv` und `suspicious_members.csv`. 100 Unit-Tests grün. Smoke-Run 20.05.2026 (111.986 Trades, mean=23.7, max=1705). SCORING_WEIGHTS: `committee=10`, `committee_full=5` (Voll-Treffer=15 wie vorher). Phasen-Details: `CHANGELOG.md`. Score-Formel + Settings: `organisation/Filter_Instanz_Parameter.md`. |
| 9 | IN ARBEIT (MVP) | Streamlit Dashboard. `app/streamlit_app.py` + `app/score_recompute.py`. Drei Tabs (Trades / Assets / Members), 7 Sidebar-Filter, Voll-Recompute bei Signal-Ausschluss. Asset- und Member-Tab live aus gefiltertem Trade-Subset aggregiert (reaktiv auf alle Filter). Trade-Tab enthält abgeleitete Spalte `relevant_sectors`. Start: `venv/bin/streamlit run app/streamlit_app.py`. |
| 10 | OFFEN | Email-Benachrichtigungen |
| 11 | OFFEN | Automatisierung & Scheduler |
| Rebuild T1 | FERTIG (25.04.2026) | Foundation: `utils/trade_id.py` (13 Tests) + `utils/ticker_classifier.py` (67 Tests) |
| Rebuild T2 | FERTIG (25.04.2026) | Network-Wrapper: `utils/ticker_normalizer.py` (OpenFIGI) + `utils/ticker_sector.py` (yfinance). Defensiv-Mechanismen §11 vollständig. |
| Rebuild T3 | FERTIG | Integration der Rebuild-Module in `scripts/import_trades.py`. |
| Rebuild T4 | OFFEN | Migration: alte Caches → neue Schema-Form, alte Module entfernen |
| Rebuild T5 | FERTIG (P5 + P5.5 + P5.6 ✅) | P5: `share_class_figi`-Spalte aus OpenFIGI in `trades_master_complete.csv`, Router-Erweiterung (YFINANCE_DIRECT + MANUAL_REVIEW gehen jetzt durch OpenFIGI mit Negativ-Cache). P5.5: abgeleitete Spalte `issuer_ticker` (Share-Class-Level, Fallback `share_class_figi → ticker_base → ticker`) in `trades_master.csv` + `trades_master_complete.csv`, Signal-System komplett umgestellt. P5.6: Cross-Class via SEC EDGAR CIK — neue Stage `_stage_issuer_group`, neue Spalte `issuer_group_key` (Präfixe `CIK:` / `TICKER:` / `SKIP:`), ETF/Foreign-Bypass, strikter Abbruch bei SEC-API-Failure (kein silent Fallback auf veralteten Cache), TTL 7 Tage. `SWARM_GROUPING_KEY` → `issuer_group_key`. Zentrale Doku `organisation/Ticker_Architektur.md` mit Design-Decision-Log. |

---

## Wichtige Dateien

### Scripts
- `scripts/import_trades.py` — Haupt-Import-Pipeline (nach jedem Scraper-Lauf ausführen)

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

1. **Modul 9 — Streamlit Dashboard (in Arbeit, MVP fertig)** — Nächste Iteration: Datum/Score-Filter in Tabs „Assets" und „Members" aktivieren.
2. **Rebuild T4** — Migration alter Caches, Entfernen von `utils/openfigi_client.py` und alten Tabellen.
3. **Modul 10 — Email-Benachrichtigungen**, **Modul 11 — Automatisierung**

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
| Aktueller Modul-Status | `CLAUDE.md` (sandbox, Modul-Tabelle oben) |
| Offene Implementierungs-Pläne | `~/.claude/plans/*.md` |

Bei Code-Änderungen die folgende Tabelle prüfen — die
„Aktualisieren bei"-Spalte nennt konkrete Trigger, dadurch muss nicht
jede Datei einzeln gelesen werden.

### Top-Level

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `CLAUDE.md` (sandbox) | Projektstatus, Modul-Tabelle, Wichtige Regeln, Doku-Index. | Neuer Modul-Status, neue Regel/Konvention, neues Util/Skript, Phase abgeschlossen. |

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
