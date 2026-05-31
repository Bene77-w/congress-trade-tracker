# Congress Trade Tracker — Projektübersicht

Python + Streamlit Anwendung zur Analyse von US-Kongress-Aktienhandel. Scrapt offizielle PTR-Dokumente (House + Senate), reichert Trades mit Ausschuss-Mitgliedschaften und Sektor-Mappings an und generiert Trading-Signale auf Basis von 7 konfigurierbaren Filtern.

**Stack:** Python 3.11 · Pandas · Playwright · pdfplumber · OpenCV · Claude Vision API · Streamlit

---

## Modul-Status (Stand: Mai 2026)

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| 1 | ✅ Fertig | Projektstruktur, Config, venv |
| 2 | ✅ Fertig | House Scraper (ZIP + XML + PDF) + PDF Extractor v2 (OpenCV Grid + Vision API, 4 Formulartypen, 126 Tests) |
| 3 | ✅ Fertig | Senate Scraper (Playwright + DataTables API) |
| 4 | ✅ Fertig | Ausschuss-Sektor-Mapping (CommitteeMapper + CommitteeTag, 861 Mappings, sector/industry-Match × ex-officio-Multiplikator) |
| 5 | ⏸️ Übersprungen | Kursdaten (Yahoo Finance) — vorerst nicht benötigt |
| 6 | ✅ Fertig | Vote-Timing aktiv. VoteTimingTag extrahiert Roll-Calls direkt aus `bill_actions.json` (Floor Votes in `actions[*].roll_call[*]`). Sektor-Match über `legislative_subject_mapping_complete.csv`. FloorActionSignal triggert bei `Vote_Timing_Hit_Count ≥ 1`. Smoke-Run 20.05.2026: 5.357 Hits / 111.986 Trades. |
| 7 | ✅ Fertig (P1–P7) | Signal-Architektur (Gate/Tag/Signal): Gates+Tags ✅, Branch-1 Signale ✅ (P2+P3+P3.5), Suspicious-Export ✅ (P4), OpenFIGI-Coverage + `share_class_figi` ✅ (P5), `issuer_ticker` Share-Class-Level ✅ (P5.5), Cross-Class Issuer Mapping via SEC EDGAR CIK ✅ (P5.6), **Schwarm-Signale ✅ (P6)** — 4 Signale auf Issuer-Group-Ebene: `swarm_buy`/`swarm_sell` (Greedy-Cluster), `swarm_conflict` (1 Gegen-Trade im erweiterten Fenster), `swarm_paradox` (≥2 Gegen-Trades, mutually exclusive). Cross-Class via `issuer_group_key` (GOOGL+GOOG → CIK:1652044). Smoke-Run: 24.926 Schwarm-Events auf 111.986 Trades. |
| 8 | 🔧 In Arbeit | Scoring-System. Phasen 8.1–8.5 ✅. Pipeline 16 Stages, 5 Trade-Spalten + Aggregat-Spalten in Views. 61 Tests grün. Phase 8.6 (Voll-Pipeline-Smoke-Run) offen. Formel + Settings: [Filter_Instanz_Parameter.md](Filter_Instanz_Parameter.md). Historie: `../CHANGELOG.md`. |
| 9 | 🔧 In Arbeit (MVP + Post-Trade-Performance-Tab FERTIG) | Streamlit Dashboard. 4 Tabs (Trades / Assets / Members / Post Trade Performance). Trade-Tab mit `trade_id` (Hash-ID). Post-Trade-Performance-Tab: Top-25 nach Kapital + Suche + Charts. |
| 10 | 🔧 Offen | E-Mail-Benachrichtigungen (SMTP) |
| 11 | 🔧 Offen | Automatisierung & Scheduler (APScheduler) |

---

## Datenfluss

```
Quelle                          Skript                              Ausgabe
──────────────────────────────────────────────────────────────────────────────
disclosures-clerk.house.gov  →  scrapers/house_scraper.py        →  data/raw/house_trades.csv
efdsearch.senate.gov         →  scrapers/senate_scraper.py       →  data/raw/senate_trades.csv
Fehler-PDFs (OCR)            →  scrapers/house_pdf_extractor_v2  →  (append) house_trades.csv
api.data.gov (Congress.gov)  →  scrapers/fetch_congress_data.py  →  data/congress_api/bill_*.json
                                                                      ↓
house_trades.csv + senate_trades.csv  →  scripts/import_trades.py  →  data/processed/trades_master.csv
                                                                         (36 Spalten, ~9.591 Trades)
data/mappings/*.xlsx          →  scripts/convert_xlsx_to_csv.py  →  data/mappings/*.csv
```

**Import-Pipeline:** 16 Stages — Reihenfolge, Stage-Indizes und Details: siehe [Pipelines.md](Pipelines.md). CLI-Flags: [CLI_Bedienungsanleitung.md](CLI_Bedienungsanleitung.md).

---

## Wichtigste Dateien

### Scripts
| Datei | Zweck |
|-------|-------|
| `scripts/import_trades.py` | Haupt-Import-Pipeline (nach jedem Scraper-Lauf ausführen) |
| `scrapers/house_scraper.py` | House PTR Scraper (ZIP + XML + PDF) |
| `scrapers/senate_scraper.py` | Senate PTR Scraper (Playwright + DataTables API) |
| `scrapers/house_pdf_extractor_v2.py` | OCR für handübergebene House PTR PDFs |
| `scrapers/fetch_congress_data.py` | Congress.gov API (Bills, Votes, Subjects) |
| `scripts/convert_xlsx_to_csv.py` | Mapping-Excel-Dateien in CSV konvertieren |
| `scripts/diagnose_scraper.py` | QA-Tests für Senate-Scraper |
| `scripts/reset_pipeline.py` | Granularer Reset (Logs/Caches/Stage/Processed); Default reversibler Move nach `archive/reset_pipeline/`, optional `--purge` |

### Config & Utils
| Datei | Zweck |
|-------|-------|
| `config/settings.py` | **Alle** konfigurierbaren Parameter (Filter, Schwellen, Pfade) |
| `config/committee_mapping.py` | CommitteeMapper — O(1) Subcommittee-Lookup |
| `utils/name_matcher.py` | 4-Tier Fuzzy Name Matching (rapidfuzz ≥85%) |
| `utils/openfigi_client.py` | Ticker-Klassifikation via OpenFIGI API + SQLite-Cache |
| `tags/committee_tag.py` | CommitteeParser + CommitteeTag (Relevanz-Score 0–99) |

### Daten & Mappings
| Datei | Beschreibung |
|-------|-------------|
| `data/processed/trades_master.csv` | Alle angereicherten Trades (~29 MB, 36 Spalten) |
| `data/mappings/house_member&(sub)committees.csv` | 437 House Members mit Ausschüssen |
| `data/mappings/senate_member&(sub)committees.csv` | 100 Senatoren mit Ausschüssen |
| `data/mappings/subcommittee_sector_industry_mapping.csv` | 861 Zeilen: Subcommittee → Sektor/Industry |
| `data/mappings/name_aliases.csv` | 11 manuelle Namenskorrekturen |
| `data/congress_api/bill_actions.json` | Roll-Call-Votes (für Filter 4) |
| `data/raw/former_members.csv` | Ehemalige Abgeordnete |

---

## Scoring-System (Modul 8 — aktiv)

Trade-Score = gewichtete Summe der getriggerten Signale + Volume-Score + Ex-Officio-Bonus, mit Paradox-Dämpfer auf den Schwarm-Anteil. Kein Cap.

**Vollständige Formel, Signal-Gewichte und Aggregat-Spalten:** [Filter_Instanz_Parameter.md](Filter_Instanz_Parameter.md). Konfiguration: `config/settings.py` (`SCORING_*`-Block).

---

## Quick Start

```bash
cd congress_tracker
source venv/bin/activate          # Linux/Mac
# .\venv\Scripts\activate         # Windows

# 1. Scraper ausführen
python scrapers/house_scraper.py
python scrapers/senate_scraper.py

# 2. Import-Pipeline
python scripts/import_trades.py

# 3. Optional: Hand-Delivered PDFs per OCR
python scrapers/house_pdf_extractor_v2.py

# 4. Optional: Mapping-CSVs neu generieren (nach xlsx-Änderungen)
python scripts/convert_xlsx_to_csv.py
```

Vollständige CLI-Referenz: siehe [CLI_Bedienungsanleitung.md](CLI_Bedienungsanleitung.md)

---

## Aktuelle Ergebnisse (07.03.2026)

| Kennzahl | Wert |
|----------|------|
| Gesamt-Trades | 9.591 |
| Match-Rate | 100% (0 unmatched) |
| House Members | 437 (inkl. 5 Leadership ex officio) |
| Fuzzy-Threshold | 85% |
| Datenquelle | Eigene Scraper (kein Quiver) |

---

## Wichtige Regeln

1. **`transaction_date` verwenden**, nicht `disclosure_date` (45 Tage Meldefrist verfälscht timing)
2. **Alle Parameter in `config/settings.py`** — keine hardkodierten Schwellen in Filter-/Scoring-Logik
3. **Implementierungsplan zuerst** — vor jeder Code-Änderung zeigen und auf OK warten
4. **Dokumentation mitpflegen** — bei Änderungen immer mindestens eine Datei aktualisieren:
   - Neue Datenstrukturen → `DATA_STRUCTURE.md`
   - Pipeline-Änderungen → `Pipelines.md`
   - Filter/Scoring/Features → `BMB_Tradingprogramm_Anleitung.md`

---

## Dokumentations-Index

| Datei | Inhalt |
|-------|--------|
| [DATA_STRUCTURE.md](DATA_STRUCTURE.md) | Vollständige Spalten-Definitionen aller CSV/JSON/xlsx-Dateien |
| [Pipelines.md](Pipelines.md) | Ausführungsreihenfolge und Abhängigkeiten aller 7 Pipelines |
| [Filter_Instanz_Parameter.md](Filter_Instanz_Parameter.md) | Gate/Tag/Signal-Instanzen mit Parametern, Schwellen und Implementierungsstatus |
| [CLI_Bedienungsanleitung.md](CLI_Bedienungsanleitung.md) | Alle CLI-Befehle mit Flags und Beispielen |
| [Aufgaben_Uebersicht](Aufgaben_Uebersicht) | Detaillierter Entwicklungsplan für alle Module |
| [DEDUP_PROBLEM_PITCH.md](DEDUP_PROBLEM_PITCH.md) | Bekannte Deduplizierungs-Problematik |
| [HAND_DELIVERED_PDF_ANALYSE.md](HAND_DELIVERED_PDF_ANALYSE.md) | Analyse der OCR-Methodik für gescannte PDFs |
