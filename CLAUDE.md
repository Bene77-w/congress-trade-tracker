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
| 6 | UNVOLLSTÄNDIG | bill_actions.json gefetcht (12.135 Bills, 409 Roll-Calls House+Senate). Extraktion → votes_house/senate.json fehlt. fetch_congress_votes.py obsolet. Filter 4 Code vorhanden aber inaktiv. |
| 7 | FERTIG (P1–P7 ✅) | Signal-Architektur (Gate/Tag/Signal). Aktiv: AssetClassGate, CommitteeTag, VoteTimingTag, Branch-1 (P2), Sequenz (P3), Suspicious-Export (P4), OpenFIGI-Coverage + `share_class_figi` (P5), `issuer_ticker` Share-Class-Level (P5.5), Cross-Class Issuer Mapping via SEC EDGAR CIK (P5.6: `issuer_group_key` mit Präfixen `CIK:`/`TICKER:`/`SKIP:`), **Schwarm-Signale (P6)**: 4 Branch-2-Signale (`swarm_buy`/`swarm_sell` Greedy-Cluster, `swarm_conflict` 1 Gegen-Trade, `swarm_paradox` ≥2 Gegen-Trades mutually exclusive). Stage [11/13]. Voll-Pipeline-Run + Schema-Migration durchgeführt: 165.113 Events in `suspicious_events.csv` (24.926 P6-Events). Cross-Class verifiziert (GOOGL+GOOG → CIK:1652044). P7 Doku-Konsolidierung abgeschlossen (Mai 2026). |
| 8 | OFFEN | Scoring-System |
| 9 | OFFEN | Streamlit Dashboard |
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
- `gates/`   — binäre Hard-Ausschlüsse (z. B. `asset_class_gate.py`)
- `tags/`    — Kontext-Anreicherung mit Master-Spalten (z. B. `committee_tag.py`, `vote_timing_tag.py`)
- `signals/` — User-sichtbare Score-Beiträge. Aktiv: Branch-1 Per-Trade-Signale (`committee_signal.py`, `committee_ex_officio_signal.py`, `floor_action_signal.py`, `extreme_report_signal.py`) + Sequenz-Signale (`contrarian_signal.py`, `accumulative_signal.py`) + **Schwarm-Signale (P6)**: `swarm_buy_signal.py`, `swarm_sell_signal.py`, `swarm_conflict_signal.py`, `swarm_paradox_signal.py` (gruppiert nach `issuer_group_key`, Cross-Class).
- Details: siehe `organisation/Filter_Instanz_Parameter.md`

### Utils
- `utils/name_matcher.py` — Fuzzy-Matching mit rapidfuzz (85% Threshold)
- `utils/trade_id.py` — Hash-basierte stabile Trade-ID (16-Hex-SHA256)
- `utils/signal_event_id.py` — Hash-basierte Signal-Event-ID (16-Hex-SHA256), gruppiert mehrere Trades pro Auslösung
- `utils/issuer_ticker.py` — `resolve_issuer_ticker(row)`: Cross-Share-Class-Aufloesung mit Fallback-Kette `share_class_figi → ticker_base → ticker`. share_class_figi (OpenFIGI shareClassFIGI) ist Erstkandidat; bei Miss fallback auf ticker_base, dann ticker.
- `utils/issuer_group_mapper.py` (P5.6) — `IssuerGroupMapper.resolve_issuer_group_key(row)`: Issuer-Level-Aufloesung via SEC EDGAR CIK. Entscheidungsbaum: SKIP-Types → `SKIP:<type>`; ETF/Foreign → `TICKER:<issuer_ticker>`; CIK-Treffer → `CIK:<cik>`; Fallback → `TICKER:<ticker_base>`. Cache 7 Tage TTL, strikter Abbruch bei API-Failure. Doku `organisation/Ticker_Architektur.md`.
- `utils/ticker_classifier.py` — Offline-Regex-Klassifikator → `TickerClassification`
- `utils/ticker_normalizer.py` — OpenFIGI-Wrapper → `NormalizedTicker` (SQLite-Cache, 429-Backoff)
- `utils/ticker_sector.py` — yfinance-Wrapper → `SectorInfo` (Split-Cache positiv/null, Vorzugsaktien-Mapping)
- `utils/openfigi_client.py` — LEGACY (wird in Rebuild T4 entfernt)

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

1. **Modul 8 — Scoring-System** — Schwarm-Signale ins Gesamt-Scoring integrieren (Gewicht 35% laut README); Paradox als Signal-Dämpfer.
2. **Rebuild T4** — Migration alter Caches, Entfernen von `utils/openfigi_client.py` und alten Tabellen.
3. **Modul 6 abschließen** — Roll-Call-Extraktion aus `bill_actions.json`, damit der VoteTimingTag mit echten Vote-Daten läuft.
4. **Modul 9 — Streamlit Dashboard**
5. **Modul 10 — Email-Benachrichtigungen**, **Modul 11 — Automatisierung**

Historische Plan-Datei (P1–P7, abgeschlossen): `archive/alt/Implementierungsplan_Signals.md`.

---

## Detail-Doku

- `organisation/README.md` — Kurz-Überblick, Modul-Status, Datei-Index
- `organisation/DATA_STRUCTURE.md` — Vollständige Datenstruktur-Doku
- `organisation/Pipelines.md` — Stage-Pipeline und Abhängigkeiten
- `organisation/Filter_Instanz_Parameter.md` — Gate/Tag/Signal-Instanzen, Parameter, Scoring-Formel
- `organisation/CLI_Bedienungsanleitung.md` — CLI-Flags und Beispiele
