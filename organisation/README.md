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
| 6 | ⚠️ Unvollständig | Congress.gov API funktioniert (12.135 Bills, 409 Roll-Calls), aber vote-Extraktion → votes.json fehlt → Vote-Timing-Tag Code vorhanden, aber inaktiv |
| 7 | ✅ Fertig (außer P7 Doku-Finalisierung) | Signal-Architektur (Gate/Tag/Signal): Gates+Tags ✅, Branch-1 Signale ✅ (P2+P3+P3.5), Suspicious-Export ✅ (P4), OpenFIGI-Coverage + `share_class_figi` ✅ (P5), `issuer_ticker` Share-Class-Level ✅ (P5.5), Cross-Class Issuer Mapping via SEC EDGAR CIK ✅ (P5.6), **Schwarm-Signale ✅ (P6)** — 4 Signale auf Issuer-Group-Ebene: `swarm_buy`/`swarm_sell` (Greedy-Cluster), `swarm_conflict` (1 Gegen-Trade im erweiterten Fenster), `swarm_paradox` (≥2 Gegen-Trades, mutually exclusive). Cross-Class via `issuer_group_key` (GOOGL+GOOG → CIK:1652044). Smoke-Run: 24.926 Schwarm-Events auf 111.986 Trades. |
| 8 | 🔧 Offen | Scoring-System (gewichteter Calculator + Empfehlungs-Mapping) |
| 9 | 🔧 Offen | Streamlit Dashboard (sortierbare Tabelle, Filter-UI) |
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

**Import-Pipeline (13 Stages):**
1. CSV laden + Spalten umbenennen + trade_id vergeben
2. Datumsvalidierung & Normalisierung
3. Name-Matching (4-Tier: Alias → Exact → Former Members → Fuzzy ≥85%) + Committee-Anreicherung
4. Ticker-Typ-Klassifikation (Offline-Regex, `utils/ticker_classifier.py`)
5. OpenFIGI-Normalisierung (FIGI, ticker_figi, asset_name_figi, share_class_figi, issuer_ticker)
6. Cross-Class Issuer Mapping (SEC EDGAR CIK → `issuer_group_key`, P5.6)
7. Ticker-Sektor/Industry-Lookup (yfinance + Cache)
8. Gates + Tags: AssetClassGate, CommitteeTag, VoteTimingTag (+ Scoring)
9. Branch-1 Signale: committee, committee_ex_officio, floor_action, extreme_report → `branch1_events_stage.csv`
10. Sequenz-Signale: contrarian, accumulative → `sequence_events_stage.csv`
11. Schwarm-Signale (P6): swarm_buy, swarm_sell, swarm_conflict, swarm_paradox → `swarm_events_stage.csv` (gruppiert nach `issuer_group_key`, Cross-Class)
12. Suspicious-Export: append-only `suspicious_events.csv` + Y-Tage-View `suspicious_assets.csv` + Member-View `suspicious_members.csv`
13. trades_master[_complete].csv speichern + unmatched_names.csv

Details und CLI-Flags: `Pipelines.md`, `CLI_Bedienungsanleitung.md`.

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

## Scoring-System (Modul 4 — aktiv)

**Formel:** `score = base_score(rank) × confidence_mult × role_mult` (max 100)

| Parameter | Werte |
|-----------|-------|
| Rank Weights | Rank 1=100%, 2=75%, 3=50%, 4=25% |
| Confidence | Hoch=1.0×, Mittel=0.7×, Niedrig=0.4× |
| Role | Ex Officio (Chairman, Ranking Member, Vice Chairman) = 1.5× |

**Filter-Gewichte (Modul 7 — geplant):**

| Filter | Gewicht | Status |
|--------|---------|--------|
| Filter 1: Handelsvolumen | 7.5% | Offen |
| Filter 2: Schwarm-Verhalten | 35% | Offen |
| Filter 3: Ausschuss-Überschneidung | Auto-Flag | ✅ Aktiv |
| Filter 4: Timing vor Abstimmung | 35% | Code ✅, Daten fehlen |
| Filter 5: Contrarian | — | Übersprungen |
| Filter 6: Long↔Short Wechsel | 7.5% | Offen |
| Filter 7: Position vergrößert | 15% | Offen |

**Score-Schwellen:** ≥75 = ⭐⭐⭐ · 50–74 = ⭐⭐ · 30–49 = ⭐ · <30 = kein Signal

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
