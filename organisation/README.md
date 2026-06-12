# Congress Trade Tracker — Projektübersicht

Python + Streamlit Anwendung zur Analyse von US-Kongress-Aktienhandel. Scrapt offizielle PTR-Dokumente (House + Senate), reichert Trades mit Ausschuss-Mitgliedschaften und Sektor-Mappings an und generiert Trading-Signale auf Basis von 7 konfigurierbaren Filtern.

**Stack:** Python 3.11 · Pandas · Playwright · pdfplumber · OpenCV · Claude Vision API · Streamlit

---

## Modul-Status (Stand: Juni 2026)

> Menschenlesbarer Überblick. Aktiver WIP-Detailstand: `../AGENTS.md` · Phasen-Historie: `../CHANGELOG.md`.

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| 1 | ✅ Fertig | Projektstruktur, Config, venv |
| 2 | ✅ Fertig | House Scraper (ZIP + XML + PDF) + PDF Extractor v2 (OpenCV Grid + Vision API, Formulartypen A–D) |
| 3 | ✅ Fertig | Senate Scraper (Playwright + DataTables API) |
| 4 | ✅ Fertig | Ausschuss-Sektor-Mapping (CommitteeMapper + CommitteeTag, sector/industry-Match × ex-officio-Multiplikator) |
| 5 | 🟡 Teilweise | Post-Trade-Performance-Tracking fertig (Stage `post_trade_performance`, richtungsbewusste Returns, yfinance-Preis-Cache); kein Live-Quote. Details: [Briefing_stage_post_trade_performance.md](Briefing_stage_post_trade_performance.md) |
| 6 | ✅ Fertig | Vote-Timing: VoteTimingTag extrahiert Roll-Calls aus `bill_actions.json`, FloorActionSignal triggert bei `Vote_Timing_Hit_Count ≥ 1` |
| 7 | ✅ Fertig | Signal-Architektur (Gate/Tag/Signal): 11 aktive Signale inkl. Schwarm-Signale (`swarm_buy`/`swarm_sell`/`swarm_conflict`/`swarm_paradox`) + Suspicious-Export; Cross-Class via `issuer_group_key` (SEC EDGAR CIK) |
| 8 | ✅ Fertig | Scoring-System: Pipeline **17 Stages**, `trade_score` + Tier-Signal `trade_volume`. Formel: [Filter_Instanz_Parameter.md](Filter_Instanz_Parameter.md) |
| 9 | 🔧 In Arbeit | Streamlit Dashboard, **5 Tabs** (Trades / Assets / Members / Post Trade Performance / Recent Trades): Token-Suche, Horizont-Sichtbarkeitsfilter, Recent-Trades-Monitoring offener Positionen, Score-Gewichte-Presets, Member-Detail-Popup (Live-Auswertung pro Abgeordnetem) |
| 10 | 🔧 Offen | E-Mail-Benachrichtigungen (SMTP) |
| 11 | 🟡 Teilweise | Automatisierung: Quiver-Auto-Download-Scraper + systemd-User-Timer (täglich 03:00) fertig; genereller Scheduler übriger Pipelines offen. Details: `../deploy/systemd/README.md` |

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

**Import-Pipeline:** 17 Stages — Reihenfolge, Stage-Indizes und Details: siehe [Pipelines.md](Pipelines.md). CLI-Flags: [CLI_Bedienungsanleitung.md](CLI_Bedienungsanleitung.md).

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

Trade-Score = gewichtete Summe der getriggerten Signale + Volume-Score + rollenbasierter Ex-Officio-Bonus (MAX über Führungsrollen-Boni), mit Paradox-Dämpfer auf den Schwarm-Anteil. Kein Cap.

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

## Aktuelle Ergebnisse (Stand: Juni 2026)

| Kennzahl | Wert |
|----------|------|
| Gesamt-Trades (`trades_master_complete.csv`) | ~112.700 |
| Datenquelle | Quiver (`congress-trading-all.csv`, Snapshot ab 2012) + eigene OCR-Extraktion |
| Fuzzy-Threshold (Name-Matching) | 85 % |

> Zahlen sind ein grober Stand; die exakte aktuelle Zeilenzahl liefert der letzte Pipeline-Lauf (`wc -l data/processed/trades_master_complete.csv`).

---

## Wichtige Regeln

1. **`transaction_date` verwenden**, nicht `disclosure_date` (45 Tage Meldefrist verfälscht timing)
2. **Alle Parameter in `config/settings.py`** — keine hardkodierten Schwellen in Filter-/Scoring-Logik
3. **Implementierungsplan zuerst** — vor jeder Code-Änderung zeigen und auf OK warten
4. **Dokumentation mitpflegen** — bei Änderungen immer mindestens eine Datei aktualisieren:
   - Neue Datenstrukturen → `DATA_STRUCTURE.md`
   - Pipeline-Änderungen → `Pipelines.md`
   - Filter/Scoring/Features → `Filter_Instanz_Parameter.md`

---

## Dokumentations-Index

Kanonische Quelle pro Thema + „Aktualisieren bei"-Trigger: **[DOKU_INDEX.md](DOKU_INDEX.md)**.
Schnell-Einstieg: Spalten → [DATA_STRUCTURE.md](DATA_STRUCTURE.md) · Pipeline-Stages → [Pipelines.md](Pipelines.md) · Gate/Tag/Signal + Score → [Filter_Instanz_Parameter.md](Filter_Instanz_Parameter.md) · CLI → [CLI_Bedienungsanleitung.md](CLI_Bedienungsanleitung.md) · Scraper-Status → [tool_specific_md/Trade_scraper_status.md](tool_specific_md/Trade_scraper_status.md) · Deployment → [DEPLOYMENT.md](DEPLOYMENT.md).
