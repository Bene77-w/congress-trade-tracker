# Congress Trade Tracker - Projektstatus

## Projektbeschreibung
Python + Streamlit App die US-Kongress-Mitglieder Stock-Trades trackt, analysiert und Trading-Empfehlungen erstellt.

## Wichtige Regeln
1. **Alle Filter-Parameter muessen konfigurierbar sein** (in config/settings.py)
2. **Immer erst Implementierungsplan zeigen**, dann auf "OK" warten bevor Code geschrieben wird
3. **transaction_date verwenden**, nicht disclosure_date (45-Tage Meldefrist)
4. **Neue Dateien dokumentieren**: Bei Erstellung oder Verwendung neuer projektrelevanter Dateien (Daten, Config, Scripts) → in `organisation/DATA_STRUCTURE.md` (Struktur, Spalten, Zweck, Beispiele) und `organisation/Pipelines.md` (Zweck, Postion in Pipeline) aufnehmen 
5. **Dokumentation aktualisieren**: Bei jeder Code-Änderung die betroffenen Erklärungs-Dateien mitpflegen:
   - Neue/geänderte Datenstrukturen oder Spalten → `organisation/DATA_STRUCTURE.md`
   - Neue/geänderte Scripts, Ablauf oder Abhängigkeiten → `organisation/Pipelines.md`
   - Neue Features, Filter, Scoring oder Workflow-Änderungen → `organisation/BMB_Tradingprogramm_Anleitung.md`
   - Mindestens eine der drei Dateien ist bei jeder relevanten Änderung anzupassen.
6. **Sytemanweisung**: Du bist ein erfahrener Software Entwickler. Du estellst vor jeder code Code-Änderung einen Implementierungsplan, bevor du diesen strikt umsetzt. Um einen individuell passenden Plan zu erstellen frägst du den User nach konkreten Anweisungen und frägst bei wichtigen Planungsfragen deine Entschiedung vom User approven. Du teilst jeden Gedankengang und Entscheidungsweg mit mir. Falls Rückfragen oder Probleme auftreten, teile mir diese mit. 
7. **Fragen an dich** wenn ich nach deriner Einschätzung frage, dann präsentiere mir zunächst deinen Gedankengang; verschiedene Lösungswege; pro-/kontra Argumente und konsequenzen jedes Lösungsweges und zuletzt deine begründete Empfehlung 

---

## Modul-Status

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| 1 | FERTIG | Projektstruktur |
| 2 | FERTIG | House Trades Scraping (ZIP + PDF-Parsing, 126 Tests, subholding-Fix Feb 2026) + House PDF Extractor v2 (OpenCV Grid + Pixel-Density + Vision API Batch, alle Formulartypen A/B/C/D, Mrz 2026) |
| 3 | FERTIG | Senate Trades Scraping (Playwright + DataTables API). senate_pdf_extractor liest gescannte PDFs fehlerhaft (Vision API), aber irrelevant - einziger Senator mit Paper-PTRs investiert nur in Fonds |
| 4 | FERTIG | Ausschuss-Sektor-Mapping (CommitteeMapper + CommitteeFilter implementiert) |
| 5 | ÜBERSPRUNGEN | Kursdaten (Yahoo Finance) - vorerst übersprungen |
| 6 | UNVOLLSTÄNDIG | bill_actions.json gefetcht (12.135 Bills, 409 Roll-Calls House+Senate). Extraktion → votes_house/senate.json fehlt. fetch_congress_votes.py obsolet. Filter 4 Code vorhanden aber inaktiv. |
| 7 | OFFEN | Filter-Logik: Filter 3 ✅, Filter 4 Code ✅/Daten ❌, Filter 1/2/6/7 Code fehlt |
| 8 | OFFEN | Scoring-System |
| 9 | OFFEN | Streamlit Dashboard |
| 10 | OFFEN | Email-Benachrichtigungen |
| 11 | OFFEN | Automatisierung & Scheduler |
| Rebuild T1 | FERTIG (25.04.2026) | Foundation: `utils/trade_id.py` (Hash-basierte Trade-ID, 13 Tests) + `utils/ticker_classifier.py` (Offline-Regex-Klassifikator, 67 Tests) |
| Rebuild T2 | FERTIG (25.04.2026) | Network-Wrapper: `utils/ticker_normalizer.py` (OpenFIGI, 6 Tests) + `utils/ticker_sector.py` (yfinance, 6 Tests). Defensiv-Mechanismen §11 vollständig. NICHT integriert in import_trades.py. |
| Rebuild T3 | OFFEN | Integration der Rebuild-Module in `scripts/import_trades.py` (ersetzt `_stage_tickertype` und `_stage_tickerindustry` durch neue Stages) |
| Rebuild T4 | OFFEN | Migration: alte Caches → neue Schema-Form, alte Module entfernen |

---

## Wichtige Dateien

### Scripts
- `scripts/import_trades.py` - Haupt-Import-Pipeline: liest senate_trades.csv + house_trades.csv (nach jedem Scraper-Lauf ausfuehren)

### Utils
- `utils/name_matcher.py` - Fuzzy-Matching mit rapidfuzz (80% Threshold)
- `utils/trade_id.py` - Hash-basierte stabile Trade-ID (16-Hex-SHA256, Rebuild Teil 1)
- `utils/ticker_classifier.py` - Offline-Klassifikator (Regex-Entscheidungsbaum, liefert TickerClassification mit ticker_type/api_strategy/confidence; Rebuild Teil 1)
- `utils/ticker_normalizer.py` - OpenFIGI-Wrapper, gibt NormalizedTicker zurück; SQLite-Cache (Tabelle `ticker_normalizer`), 429-Backoff, Network-Resilience (Rebuild Teil 2)
- `utils/ticker_sector.py` - yfinance-Wrapper, gibt SectorInfo zurück; JSON-Split-Cache (positiv/null), Vorzugsaktien-Mapping, Hyphen-Fallback BRK-B→BRK/B (Rebuild Teil 2)
- `utils/openfigi_client.py` - LEGACY (wird in Rebuild T3/T4 durch ticker_normalizer ersetzt)

### Config
- `config/settings.py` - Alle konfigurierbaren Parameter
- `data/mappings/name_aliases.csv` - Manuelle Namenskorrekturen

### Daten
- `data/mappings/house_member&(sub)committees.csv` - House Mitglieder mit Committees (aus PDF extrahiert)
- `data/mappings/senate_member&(sub)committees.csv` - Senate Mitglieder mit Committees (aus PDF extrahiert)
- `data/raw/member&(sub)committees_structure.md` - Strukturdefinition für House/Senate Committee CSVs
- `data/raw/house_trades.csv` - House PTR Trades (via house_scraper.py)
- `data/raw/senate_trades.csv` - Senate PTR Trades (via senate_scraper.py)
- `data/raw/congress_trading_data_quiver.xlsx` - Quiver Trade-Daten (legacy, nicht mehr als Import-Quelle genutzt)
- `data/mappings/yf_sectors_with_industries.csv` - Yahoo Finance Sektoren (11) mit Industries (176) **[CSV, empfohlen]**
- `data/mappings/yf_secotors_with_industries.xlsx` - Originaldatei (hierarchisch strukturiert)
- `data/mappings/subcommittee_sector_industry_mapping.csv` - Subcommittee→Sektor/Industry (861 Zeilen, normalisiert) **[CSV, empfohlen]**
- `data/mappings/subcommittee_sector_industry_mapping_v3.xlsx` - Originaldatei (6 Sheets: Industry_Mapping, Region_Mapping, etc.)
- `data/mappings/name_aliases.csv` - Manuelle Namenskorrekturen
- `data/mappings/vote_sector_mapping.py` - Policy Area→YF-Sektoren Mapping (Python)
- `data/processed/trades_master.csv` - Alle Trades mit Committee-Spalte
- `data/raw/former_members.csv` - Ehemalige Politiker (name, party, state, chamber)
- `data/processed/unmatched_names.csv` - Namen ohne Match

### Datenstrukturen (xlsx & CSV)

#### Yahoo Finance Sektoren/Industries
- **xlsx**: `data/raw/yf_secotors_with_industries.xlsx` (hierarchisch organisiert)
- **CSV**: `data/raw/yf_sectors_with_industries.csv` (flache Struktur)
- **Struktur**: 2 Spalten (`Industry`, `Market Weight`)
  - Sektoren haben `100.00%` Market Weight (z.B. Technology, Financial Services)
  - Industries darunter haben prozentuale Anteile (z.B. Semiconductors 36.04%)
- **Inhalt**: 11 Haupt-Sektoren, ~176 Industries

#### Subcommittee → Sektor/Industry Mapping
- **xlsx**: `data/raw/subcommittee_sector_industry_mapping.xlsx` (6 Sheets)
- **CSV (empfohlen)**: `data/raw/subcommittee_sector_industry_mapping.csv` (Industry_Mapping Sheet)
- **Struktur**: 7 Spalten (`Chamber`, `Committee`, `Subcommittee`, `Sector`, `Industry`, `Sector_Rank`, `Konfidenz`)
- **Inhalt**: 861 Zeilen (180 Subcommittees, normalisiert)
- **Weitere Sheets im xlsx**:
  - `Overview`: Breite Ansicht (bis zu 6 Sektoren pro Subcommittee in einer Zeile)
  - `Region_Mapping`: 238 Zeilen (USA, Global, Asia, etc.)
  - `Korrekturen`: Audit Trail (36 Korrekturen)
  - `Statistik`: Validierungsstatistiken
  - `Manuelle Prüfung`: 6 Foreign Affairs Committees

**CSV-Generierung**: `python scripts/convert_xlsx_to_csv.py` (aus aktiviertem venv)

---

## Workflow fuer Import (nach jedem Scraper-Lauf)

```bash
cd congress_tracker
.\venv\Scripts\activate

# 1. Scraper ausfuehren (erzeugt/aktualisiert data/raw/house_trades.csv + senate_trades.csv):
python scrapers/house_scraper.py
python scrapers/senate_scraper.py

# 2. Import ausfuehren:
python scripts/import_trades.py

# 2a. (Optional) CSV-Versionen aktualisieren falls xlsx-Dateien geaendert wurden:
python scripts/convert_xlsx_to_csv.py
```

```bash
# 3. (Optional) Hand-Delivered PDFs via OCR extrahieren:
python scrapers/house_pdf_extractor_v2.py           # Alle fehler-PDFs (empfohlen)
python scrapers/house_pdf_extractor_v2.py --dry-run  # Trockenlauf
```

**Hinweis:** Member-Listen (CSVs) werden direkt aus PDF extrahiert und liegen bereits in `data/raw/`. Bei neuen Wahlen: Neue PDFs extrahieren und CSVs ersetzen.

**Leadership-Fix (07.03.2026):** `house_member&(sub)committees.csv` enthaelt 437 Members (432 aus Committee-Seiten + 5 Leadership ohne Committees). Leadership-Members (Speaker, Majority/Minority Leader, Whip, Speaker Emerita) werden via Alphabetical-List-Kreuzreferenz in `parse_house_committee_pdf.py` erkannt und mit `ExOfficio=true` eingetragen → 1.5x Scoring-Multiplikator.

---

## Letzte Ergebnisse (07.03.2026)

- Total Trades: 9.591
- Erfolgreich gematcht: 9.591 (100%)
- Nicht gefunden: 0 (0%)
- Eindeutige unmatched Namen: 0
- Datenquelle: Eigene Scraper (Quiver-Daten entfernt)
- House Members: 437 (inkl. 5 Leadership ex officio)
- Fuzzy-Threshold: 85%

---

## Rebuild Teil 1 + 2 — Foundation & Network-Wrapper (ABGESCHLOSSEN 25.04.2026)

Spezifikation: `rebuild_pitch.md` (Sektionen 4–7, 11, 14, 16). Ziel: 293-Zeilen-
Monolith `enrich_with_ticker_industry()` durch saubere, austauschbare,
testbare Module ersetzen. Provider-Wechsel berührt später nur eine Klasse.

### Teil 1 — Foundation (offline, deterministisch)

| Modul | Tests | Zweck |
|-------|-------|-------|
| `utils/trade_id.py` | 13 | Hash-basierte stabile Trade-ID (`sha256[:16]`). Spalten-Reihenfolge ist Teil der Semantik (siehe Modul-Docstring). Ersetzt `_create_dup_key`. |
| `utils/ticker_classifier.py` | 67 | Regex-Entscheidungsbaum. Liefert `TickerClassification` (frozen) mit `ticker_type` (17 Werte) + `api_strategy` (6 Werte). 0% UNKNOWN-Quote durch Long-Alpha-Suspect + Manual-Review-Pfad. |

### Teil 2 — Network-Wrapper (mit Defensiv-Mechanismen §11)

| Modul | Tests | Zweck |
|-------|-------|-------|
| `utils/ticker_normalizer.py` | 6 | OpenFIGI-Wrapper. Returnt `NormalizedTicker` mit FIGI/canonical_name/security_type. Type-Korrektur STOCK→ETF bei `securityType2='ETP'`. Mapping→Search-Fallback. |
| `utils/ticker_sector.py` | 6 | yfinance-Wrapper. Returnt `SectorInfo` mit sector/industry/quote_type. Vorzugsaktien-Mapping (EP$C → KMI), Hyphen-Fallback (BRK-B → BRK/B), Delisted-Tracking. |

### Defensiv-Mechanismen (Pitch §11) — vollständig implementiert

- §11.1 Network-Resilience (3→Pause, 5→RuntimeError, Retry-Queue, NICHT cachen)
- §11.2 Cache-Checkpoints alle 100 Lookups
- §11.3 Cache-Split positiv/null (separate JSON-Dateien) — nur sector
- §11.5 Confirmation-Prompt bei ≥100 neuen Tickern (callback-injizierbar) — nur sector
- §11.6 yfinance-Logger CRITICAL während Loop, danach restore — nur sector
- §11.7 Delisted-Tracking → `delisted_trades_stage.csv` — nur sector
- §11.8 Vorzugsaktien-Mapping aus `Vorzugsaktien_mapping.csv` — nur sector
- §11.9 OpenFIGI 429 Backoff 2^n max 60s, 3 consecutive → RuntimeError — nur normalizer
- §11.10 Lazy Init (DB/Caches erst bei Bedarf)
- §11.4 (Pickle-Checkpoint Member-Enrichment) — nicht angefasst

### Architektur-Entscheidungen

- **SQLite**: neue Tabelle `ticker_normalizer` in derselben DB-Datei
  (`data/cache/ticker_type_cache.db`). Alte Tabelle `ticker_types` bleibt
  parallel — `OpenFigiClient` weiter lauffähig.
- **Cache-Toleranz**: `ticker_industry_cache.json`-Bestand ohne `cached_at`
  wird als ewig gültig akzeptiert (kein API-Sturm beim ersten Run).
- **Helper-Modul vermieden**: Network-Error-Pattern bewusst in beide Module
  dupliziert (Pitch §16, vereinfacht Provider-Wechsel).
- **Detaillierte Terminal-Ausgabe**: Jeder Defensiv-Mechanismus loggt
  per `print(...)` mit Ticker, Counter und nächstem Schritt — siehe
  Plan-File für vollständige Tabelle.

### Neue Konstante in settings.py

```python
TICKER_SECTOR_CACHE_TTL_DAYS = 30  # TTL fuer ticker_industry_cache (positive Eintraege)
```

### Tests-Status

```
pytest tests/test_trade_id.py            13 passed
pytest tests/test_ticker_classifier.py   67 passed
pytest tests/test_ticker_normalizer.py    6 passed (alle gemockt, keine API-Calls)
pytest tests/test_ticker_sector.py        6 passed (alle gemockt, keine API-Calls)
                                       ──────────
Total Foundation+Rebuild               92 passed, 0 failed
```

(Pre-existing 2 Failures in `test_house_scraper`/`test_senate_scraper` betreffen
`DUPLICATE_CHECK_COLUMNS`-Sync — out of scope, nicht durch Rebuild verursacht.)

---

## Modul 4 - Committee-Sektor-Mapping (ABGESCHLOSSEN 20.02.2026)

**Implementierte Komponenten:**
- ✅ `config/committee_mapping.py` - CommitteeMapper lädt Mapping-CSV (861 Zeilen, 143 Subcommittees)
- ✅ `filters/committee_filter.py` - CommitteeParser + CommitteeFilter (Relevanz-Scoring 0-100)
- ✅ `config/settings.py` - Scoring Weights (Rank, Confidence, Role)
- ✅ `scripts/import_trades.py` - Integration in enrich_with_committees()
- ✅ Neue Spalten in trades_master.csv: Member_Committees_Full, Insider_Sectors (alle Member-Sektoren), Relevant_Sectors (Intersection mit Ticker), Relevance_Score
- ✅ Match-Mode konfigurierbar in settings.RELEVANT_SECTORS_MATCH_MODE (Default 'sector_or_industry')
- ✅ Filter 4 (Vote-Timing) sammelt jetzt **alle** Treffer im Fenster in Vote_Timing_Hits (JSON), Anzahl in Vote_Timing_Hit_Count — frueher: nur naechstgelegener Treffer (Vote_Date/Vote_Bill_Title/Vote_Policy_Area/Days_To_Vote/Vote_Timing_Score entfernt)

**Scoring-Formel**: `score = base_score(rank) × confidence_mult × role_mult` (max 100)
- Rank Weights: 1=100%, 2=75%, 3=50%, 4=25%
- Confidence: Hoch=1.0x, Mittel=0.7x, Niedrig=0.4x
- Role: Alle Ex Officio (Chairman, Ranking Member, Vice Chairman) = 1.5x

**Tests**: ✅ CommitteeMapper + CommitteeFilter erfolgreich getestet

---

## Naechster Schritt

**Rebuild Teil 3: Integration in `scripts/import_trades.py`**
- `_stage_tickertype` und `_stage_tickerindustry` durch zwei neue Stages mit
  `TickerNormalizer` und `TickerSectorEnricher` ersetzen.
- Strategie-Routing: Classifier liefert `api_strategy`, Pipeline routet darüber.
- Bestehende `OpenFigiClient`-Aufrufe entfernen (in Teil 4).
- Lazy-Init beachten: erste Instanziierung kostet keinen API-Call.
- Cache-Pfade siehe Plan-File: `cache_db_path`, `cache_dir`,
  `preferred_mapping_path`, `delisted_output_path`, `unmatched_preferred_path`.

**Modul 7: Filter-Logik (Filter 1, 2, 4, 6, 7)**
- Filter 5 (Contrarian) vorerst übersprungen
- Filter 3 (Committee): kein Scoring-Gewicht – trifft ein Trade den Committee-Filter, gilt er **automatisch als verdächtig** (unabhängig vom Gesamt-Score)
- Filter 4 (Timing vor Abstimmung): jetzt aktiv – API verfügbar
- Neue Gewichtungen (Scoring-Filter):
  - Filter 1: Volumen (7.5%)
  - Filter 2: Schwarm-Verhalten (35%)
  - Filter 4: Timing vor Abstimmung (35%)
  - Filter 6: Long↔Short Wechsel (7.5%)
  - Filter 7: Position vergrößert (15%)

---

## Vollstaendige Uebersicht

kompakte übersicht reicht in der regel, bei rückfragen frage user, siehe: README.md

detallierte Modulübersicht, Siehe: `Aufgaben_Uebersicht` im Projektordner

**Datenstruktur-Dokumentation**: Siehe `organisation/DATA_STRUCTURE.md` - Vollständige Übersicht aller Dateien, Spalten-Strukturen, Formate und Verwendungszwecke

