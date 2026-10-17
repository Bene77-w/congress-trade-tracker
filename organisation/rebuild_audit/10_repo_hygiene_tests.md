# 10 · Repo-Hygiene, Tests, Infra, Doku-Last

Stand: Commit `875ea55` (Branch `agent/committe_matcher`), Messung 2026-10-17. Nur Lesezugriff auf Code und Daten; keine API-Aufrufe.

## 1. Testsuite

| Kennzahl | Wert |
|---|---|
| Lauf | `pytest tests/ -q -p no:cacheprovider` (ohne `--timeout`, pytest-timeout fehlt) |
| Ergebnis | **2303 passed**, 0 failed, 0 errors, Exit 0 |
| Laufzeit | 185 s Testzeit (3:06 min Wall, davon 2:41 min User-CPU) |
| Testdateien | 61 (`tests/*.py`), 28 911 Zeilen |
| Fixtures | `tests/fixtures/` 16 KB |
| skip/xfail-Stellen | 48 Treffer (nicht einzeln klassifiziert) |

Fehlschläge: keine. Netzwerk-Tests (`test_network_*`, `test_post_trade_canary.py`) liefen mit, ohne Hänger.

### Test- zu Produktivcode je Bereich

| Bereich | Prod-Zeilen | Test-Zeilen | Test/Prod | Testdateien |
|---|---|---|---|---|
| `committee_matcher/` | 19 221 | 15 500 | **0,81** | 21 |
| `signals/`, `scoring/`, `utils/`, `tags/` | 8 194 | 6 172 | 0,75 | 19 |
| `scrapers/` (inkl. Unterordner) | 8 304 | 3 666 | 0,44 | 6 |
| `app/` | 5 143 | 2 308 | 0,45 | 9 |
| Gesamt (ohne venv, data) | ca. 49 244 | 28 911 | **0,59** | 61 |

Hinweis: Die Test-Zuordnung ist per Dateinamen-Präfix grob. `committee_matcher` ist am besten abgesichert, `scrapers` und `app` am dünnsten.

## 2. Code-Umfang und Struktur

| Verzeichnis | Python-Dateien | Zeilen |
|---|---|---|
| `committee_matcher/` | 24 | 19 221 |
| `scrapers/` | 15 | 8 304 |
| `scripts/` | 11 | 6 781 |
| `utils/` | 14 | 3 985 |
| `app/` | 10 | 5 143 |
| `signals/` | 13 | 1 576 |
| `scoring/` | 5 | 1 944 |
| `config/` | n. g. | 1 533 |
| `tags/` | 3 | 689 |
| `gates/` | 2 | 67 |
| `notifications/` | 1 | 1 (leer, `__init__.py`) |
| Gesamt inkl. Tests | | 78 155 |

## 3. Toter bzw. verwaister Code

Methode: Pro Modul Basename-Grep über `*.py`, `*.sh`, `*.yml`, `*.service` (ohne Tests, ohne sich selbst). Spalte "Refs" = Anzahl Fundstellen außerhalb der Datei selbst. Tests sind separat gezählt. Basename-Grep überschätzt bei generischen Namen (`errors`, `units`, `paths`); Spalte "Refs" ist deshalb eine Obergrenze.

### 3a. Kandidaten ohne Referenz im Code (nur Doku oder gar nichts)

| Datei | Refs Code/Config | Refs nur in MD | Einordnung |
|---|---|---|---|
| `scrapers/senate_pdf_parsing_tool/analyze_senate_pdf.py` | 0 | 0 | sehr wahrscheinlich tot |
| `scrapers/generate_committee_structure.py` | 0 | 5 | einmaliges Generatorskript |
| `scrapers/house_pdf_extractor_v2.py` | 0 | 5 | Vorgänger/Variante von `house_pdf_extractor.py` |
| `scrapers/house_pdf_parsing_tool/validate_house.py` | 0 | 3 | Einmal-Validierung |
| `scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py` | 0 | 3 | Einmal-Parser (Senate-PDFs) |
| `scrapers/senate_pdf_extractor.py` | 0 | 6 | Pendant zu house_pdf_extractor, nur Doku-Referenz |
| `scripts/build_ticker_liste_index.py` | 0 | 2 | einmaliger Index-Build |
| `scripts/diagnose_scraper.py` | 0 | 5 | Diagnose-Skript, Handlauf |

### 3b. Einmal-Migrations- und Diagnoseskripte (Kandidaten zum Archivieren)

| Skript | Refs Code | Refs MD | Einordnung |
|---|---|---|---|
| `scripts/migrate_add_trade_ids.py` | 3 | 2 | Einmal-Migration (Trade-IDs) |
| `scripts/committee_matcher_migrate_inputs.py` | 4 | 2 | Einmal-Migration (Committee-Inputs) |
| `scripts/diagnose_master_quiver_delta.py` | 2 | 2 | Diagnose |
| `scripts/analyse_event_study.py` | 3 | 5 | Analyse, kein Pipeline-Schritt |
| `scripts/report_ticker_types.py` | 1 | 0 | Einmal-Report |
| `scripts/excel_debug_output.txt` | n. g. | n. g. | Debug-Ausgabe, Ballast |

### 3c. Nur von Tests oder einer Stelle referenziert

| Datei | Refs Code | Refs Tests |
|---|---|---|
| `committee_matcher/metrics.py` | 1 | 1 |
| `scrapers/house_pdf_extractor.py` | 1 | 1 |
| `scrapers/senate_committee_xml/parse_senate_xml.py` | 1 | 1 |
| `scrapers/senate_committee_xml/fetch_senate_xml.py` | 1 | 0 (wird von requirements `curl_cffi` benötigt) |
| `scripts/reset_pipeline.py` | 2 | 0 |

### 3d. Zentral (keine Kandidaten)

`config/settings.py` (88 Refs), `utils/trade_id.py`, `utils/signal_event_id.py`, `utils/issuer_ticker.py`, `committee_matcher/paths.py`, `committee_matcher/status.py`, `committee_matcher/units.py`, `signals/committee_signal.py`, `scoring/trade_score.py`. Der Kern ist gut vernetzt.

Ergebnis: Grob 8 bis 12 Skripte (rund 10 bis 15 Prozent der Scraper-/Script-Dateien) sind tot oder Einmalware. Der Modulkern ist nicht betroffen.

## 4. Daten-Inventar (`data/`)

### 4a. Größe je Unterordner

| Ordner | Größe | Einordnung |
|---|---|---|
| `data/processed/` | 614 M | Laufzeit-Output; dominant `trades_master_complete.csv` (614 MB) |
| `data/cache/` | 24 M | `price_history.parquet` (24 MB), Cache |
| `data/committee_matcher_v2/` | 34 M | Committee-Matcher-V2-Artefakte, Goldset, Labeling-Tasks (23 Dateien) |
| `data/committee_matcher_inputs/` | 704 K | Referenz-Inputs |
| `data/mappings/` | 516 K | Mapping-Tabellen, teils Referenzwissen |
| `data/config/` | 72 K | Konfiguration |
| `data/raw/` | 12 K | Rohdaten-Platzhalter |

### 4b. Getrackt vs. ignoriert

| Kategorie | Dateien | Größe | Bemerkung |
|---|---|---|---|
| Getrackt (`git ls-files`) | 150 | 2,8 MB | Kleine Referenz- und Goldset-Daten |
| Ignoriert und ungetrackt | 76 | 639 MB | Laufzeitstände, Processed-Output |
| Weder getrackt noch ignoriert | 735 | n. g. | Unbeabsichtigter Dateirest, `git status` wird damit unübersichtlich |

### 4c. Backups und Altstände

| Artefakt | Einordnung |
|---|---|
| `data/committee_matcher_v2/fullrun/fullrun_review.20261004T215950Z.bak.xlsx` | einziges `.bak`; Backup, kann nach Verifikation weg |
| `logs/fullrun_label_20260905.log`, `logs/astra_test_20260905.log` | 20 KB Logs, Altstand |

### 4d. Unverzichtbares Referenzwissen (nicht löschen)

- `data/committee_matcher_v2/goldset/` (Labeling-Tasks, Goldset-Basis)
- `data/mappings/` und `data/committee_matcher_inputs/` (Eingangs-Verträge)
- `data/config/`

Das 614-MB-CSV ist reproduzierbar aus dem Lauf und gehört nicht ins Repo-Verzeichnis, ist aber beim Rebuild eine Migrationsfrage.

## 5. Nicht-Code-Ballast im Code-Baum

| Pfad | Größe | Typ | Einordnung |
|---|---|---|---|
| `scrapers/senate_pdf_parsing_tool/` (inkl. `us_senat_(sub)committees_single_pdfs/`) | 2,6 MB | ca. 30 PDFs | Quell-PDFs, Parser-Testmaterial |
| `scrapers/house_pdf_parsing_tool/` | 648 KB | 2 PDFs, MD, Logik | Quell-PDFs |
| `scrapers/Recherche-Aufgabe in Datei - Claude_files/` | 1,2 MB | gespeicherte Webseite (`saved_resource.html`), CSS- und JS-Dateien ("Herunterladen") | Browser-Speicherung, **klarer Ballast** |
| `scrapers/senate_committee_xml/` | 296 KB | JSON-Manifest, Roh-XML | Quelle des Senate-Rosters |
| `scripts/excel_debug_output.txt` | 12 KB | Debug-Ausgabe | Ballast |
| `tests/fixtures/all_unknown_tickers.txt` | 12 KB | Fixture | okay |
| `organisation/committee_sources_probe.csv` | 12 KB | Probe-Ergebnis | Altstand |
| `logs/` | 20 KB | Logs | Altstand |
| `__pycache__/` | 12 Verzeichnisse | Bytecode | ignoriert |
| `.pytest_cache/` | vorhanden | Cache | ignoriert |

Gesamt Ballast ohne `data/`: ca. 5 MB, davon ca. 4 MB Quell-PDFs und gespeicherte Webseite.

## 6. Doku-Last

### 6a. Markdown-Dateien (ohne `data/`, `venv/`)

| Datei | Zeilen | KB | Typ |
|---|---|---|---|
| `organisation/DATA_STRUCTURE.md` | 2 565 | 136 | kanonisch (Spalten) |
| `organisation/CLI_Bedienungsanleitung.md` | 2 117 | 104 | kanonisch (CLI) |
| `CHANGELOG.md` | 1 509 | 88 | Historie |
| `organisation/committee_matcher_v2_implementierungsplan.md` | 673 | 40 | Plan, Status-Tabelle |
| `organisation/committee_matcher_goldset_labeling_plan.md` | 513 | 32 | Plan (vermutlich abgeschlossen) |
| `organisation/committee_matcher_pipeline_integration_plan.md` | 389 | 24 | Plan (vermutlich abgeschlossen) |
| `organisation/committee_matcher_gate_b_entscheidungsvorlage.md` | 355 | 20 | Entscheidungsvorlage |
| `organisation/Filter_Instanz_Parameter.md` | 343 | 28 | kanonisch (Score) |
| `organisation/Ticker_Architektur.md` | 322 | 20 | Design |
| `committee_matcher/SUBMAPPER_ANLEITUNG.md` | 380 | 20 | Bedienung |
| `organisation/tool_specific_md/signal_info.md` | 317 | 28 | kanonisch (Signal) |
| `organisation/DEPLOYMENT.md` | 271 | 12 | Deployment |
| `organisation/tool_specific_md/HAND_DELIVERED_PDF_ANALYSE.md` | 260 | 16 | Analyse (Altstand) |
| `organisation/committee_matcher_planung_entscheidungen_codex.md` | 199 | 36 | Entscheidungen |
| `organisation/manual_review_anleitung.md` | 219 | 16 | Anleitung |
| `organisation/committee_matcher_fullrun_plan.md` | 197 | 12 | Plan (vermutlich abgeschlossen) |
| `organisation/Preis_Aktualisierung.md` | 175 | 12 | Erklärung |
| `organisation/tool_specific_md/house_all_pdf_info.md` | 175 | 8 | Scraper-Detail |
| `scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md` | 183 | 8 | Scraper-Detail |
| `organisation/tool_specific_md/vote_timing_audit.md` | 157 | 12 | Audit-Stichprobe |
| `organisation/committee_matcher_mapping_prompt_v3_plan.md` | 136 | 8 | Plan (vermutlich abgeschlossen) |
| `organisation/README.md` | 140 | 12 | Überblick |
| `organisation/committee_matcher_p9_evaluation_plan.md` | 128 | 8 | Plan (P9 offen, Gate) |
| `organisation/tool_specific_md/Trade_scraper_status.md` | 108 | 12 | Status |
| `organisation/tool_specific_md/Senate_website_structure.md` | 102 | 8 | Scraper-Detail |
| `organisation/committee_matcher_goldset_stichprobe.md` | 111 | 8 | Stichprobe |
| `committee_matcher/README.md` | 111 | 4 | 1-Satz-README (konform) |
| `organisation/committee_matcher_terminalstatus_implementierungsplan.md` | 90 | 4 | Plan (vermutlich abgeschlossen) |
| `organisation/rebuild_audit/*.md` | 56 bis 172 | 4 bis 20 | dieser Audit |
| Restliche (AGENTS, CLAUDE, DOKU_INDEX, scrapers/*_info, etc.) | | | |
| **Summe (ohne `.pytest_cache/README.md`)** | **ca. 13 700** | **ca. 800** | |

Hinweis: `.pytest_cache/README.md` liegt im Arbeitsverzeichnis und zählt nicht zur Doku.

### 6b. Plan-Status

| Plan | Zeilen | Status laut Name und Historie |
|---|---|---|
| `committee_matcher_v2_implementierungsplan.md` | 673 | aktiv, Status-Tabelle vorhanden |
| `committee_matcher_p9_evaluation_plan.md` | 128 | offen (P9 Gold-Set, kostenpflichtig, Gate) |
| `committee_matcher_fullrun_plan.md` | 197 | vermutlich abgeschlossen (Fullrun-Verzeichnis ausgelagert) |
| `committee_matcher_goldset_labeling_plan.md` | 513 | vermutlich abgeschlossen |
| `committee_matcher_pipeline_integration_plan.md` | 389 | vermutlich abgeschlossen (P3 gemerged) |
| `committee_matcher_mapping_prompt_v3_plan.md` | 136 | vermutlich abgeschlossen |
| `committee_matcher_terminalstatus_implementierungsplan.md` | 90 | vermutlich abgeschlossen |

Die Abschluss-Einordnung ist aus Namen und Commit-Historie abgeleitet. Die Pläne selbst wurden nicht Zeile für Zeile gelesen. Abgeschlossene Pläne (ca. 1 500 Zeilen) sind Archivkandidaten.

### 6c. Verwaiste und tote Doku-Verweise

| Verweis | Wo | Befund |
|---|---|---|
| `Pipelines.md` | `AGENTS.md`, `committee_matcher_v2_implementierungsplan.md`, `CHANGELOG.md` | Datei existiert nicht (in Historie gelöscht) |
| `Briefing_stage_post_trade_performance.md` | Commit-Historie (10 Änderungen) | Datei existiert nicht |
| `~/.claude/plans/` | `DOKU_INDEX.md` als Quelle für "offene Pläne" | Verzeichnis außerhalb des Repos, im Repo nicht prüfbar |

### 6d. Doku-Churn in der Git-Historie

Von den 25 meistgeänderten Dateien sind 12 Doku-Dateien (CLI_Bedienungsanleitung 64, DATA_STRUCTURE 62, CHANGELOG 57, AGENTS 46, implementierungsplan 27, Pipelines 26, Filter_Instanz 16, DOKU_INDEX 12, README 13, goldset-plan 13, CLAUDE 10). Die Doku wird also bei jeder Code-Phase mitgezogen. Das ist Pflegeaufwand, der sich linear mit der Code-Historie aufbaut.

### 6e. Doku zu Code

| Metrik | Wert |
|---|---|
| Doku-Zeilen je Code-Zeile (ca. 800 KB / 49 k Code) | ca. 0,28 Zeilen je Codezeile (13 700 Zeilen MD zu 49 244 Prod-Zeilen) |
| DOKU_INDEX: Datei-Links ohne Treffer im Repo | 0 (die Sub-Links sind relativ zu `tool_specific_md/`, existieren dort) |
| Kanonische Dateien laut Index | DATA_STRUCTURE, Filter_Instanz, CLI, signal_info, Ticker_Architektur, SUBMAPPER_ANLEITUNG |

## 7. Infrastruktur

| Artefakt | Zustand | Befund |
|---|---|---|
| `Dockerfile` | python:3.12-slim, Tesseract, Playwright-Chromium, `streamlit run app/streamlit_app.py` | schlüssig; `.dockerignore` vorhanden |
| `docker-compose.yml` | Port 8501, `.env`, Volumes `data/` und `logs/`, `restart: "no"` | schlüssig, bewusst kein Autostart |
| `deploy/systemd/` | `quiver-import.service` plus `.timer`, `README.md` (51 Zeilen) | ruft `scrapers/quiver_scraper.py` und `scripts/import_trades.py --delta` auf; Platzhalter `__PROJECT_DIR__` |
| `linux_setup.sh` | 4,4 KB, Systempakete, venv, Playwright | schlüssig |
| `.env.example` | 6 Keys | alle 6 im Code verwendet (2 bis 5 Dateien je Key) |
| `.gitignore` | `.env`, `venv`, `data/processed/`, `data/cache/*.db`, Trades-Stages | ok |

### 7a. Abhängigkeiten gegen Imports

Ungenutzt oder nur transitiv (per Grep):

| Paket in requirements.txt | Direkte Imports | Einordnung |
|---|---|---|
| `beautifulsoup4==4.14.3` | 0 | **ungenutzt** (Kandidat zum Streichen) |
| `pytest-mock==3.15.1` | 0 (kein `mocker`-Fixture in `tests/`) | **ungenutzt** |
| `pytest==9.0.2` | 50 | Test-Abhängigkeit in Produktiv-Requirements |
| `cryptography`, `six`, `tornado`, `httpx`, `dateutil`, `frozendict`, `jsonpath-python`, `multitasking`, `pdfminer.six`, `pypdfium2` | 0 direkt | transitiv (anthropic, streamlit, pandas, pdfplumber, yfinance, openrouter) |
| `pydantic`, `anyio`, `h11`, `httpcore`, `typing_extensions`, u. a. | > 0 oder transitiv | ok |

Pakete mit Direktimport: anthropic (3), openrouter (1), yfinance (3), plotly (4), curl_cffi (1), opencv (1), pdfplumber (6), playwright (4), rapidfuzz (2), numpy (16), pyyaml (9), pandas (70), openpyxl (8), streamlit (3), requests (15), python-dotenv (2), pillow (3).

Im Requirements-Kommentar steht "Nachträglich ergänzt" für curl_cffi, streamlit, yfinance, plotly, multitasking. Die Liste ist also nicht sauber gepflegt. Zusätzlich liegt `pytest` im Produktiv-Requirements. Es fehlt kein importiertes Paket.

## 8. Git-Historie

### 8a. Commits pro Monat (seit 2026-01)

| Monat | Commits |
|---|---|
| 2026-02 | 1 |
| 2026-03 | 1 |
| 2026-04 | 1 |
| 2026-05 | 6 |
| 2026-06 | 67 |
| 2026-07 | 44 |
| 2026-08 | 82 |
| 2026-09 | 24 |
| **Gesamt** | **226** |

Die Arbeit startet effektiv im Juni 2026. Monate Juni bis August liefern 193 Commits, also ca. 85 Prozent der Historie.

### 8b. Meistgeänderte Dateien (Top 25)

| Anzahl Commits | Datei | Typ |
|---|---|---|
| 64 | `organisation/CLI_Bedienungsanleitung.md` | Doku |
| 62 | `organisation/DATA_STRUCTURE.md` | Doku |
| 57 | `CHANGELOG.md` | Doku |
| 48 | `config/settings.py` | Code |
| 46 | `AGENTS.md` | Doku |
| 31 | `app/streamlit_app.py` | Code |
| 27 | `organisation/committee_matcher_v2_implementierungsplan.md` | Doku |
| 26 | `organisation/Pipelines.md` | Doku (heute gelöscht) |
| 25 | `committee_matcher/cli.py` | Code |
| 19 | `scripts/import_trades.py` | Code |
| 16 | `tests/test_committee_matcher_scaffold.py` | Test |
| 16 | `organisation/Filter_Instanz_Parameter.md` | Doku |
| 16 | `.gitignore` | Config |
| 13 | `tests/test_goldset_label.py` | Test |
| 13 | `organisation/README.md` | Doku |
| 13 | `organisation/committee_matcher_goldset_labeling_plan.md` | Doku |
| 13 | `committee_matcher/workflow.py` | Code |
| 12 | `organisation/DOKU_INDEX.md` | Doku |
| 12 | `committee_matcher/settings_view.py` | Code |
| 11 | `organisation/committee_matcher_pipeline_integration_plan.md` | Doku |
| 10 | `tests/test_committee_matcher_workflow_p7.py` | Test |
| 10 | `organisation/Briefing_stage_post_trade_performance.md` | Doku (heute gelöscht) |
| 10 | `CLAUDE.md` | Doku |
| 9 | `tests/test_committee_matcher_provider_adapter_p5.py` | Test |

Von 25 Top-Dateien sind 12 Doku, 8 Code, 5 Tests oder Config. Die Hotspots sind `config/settings.py` und `cli.py` (zentrale Konfiguration und CLI) sowie der Committee Matcher.

### 8c. Branches und Worktrees

3 lokale Branches, 3 Worktrees (laut `git branch` und `git worktree list`).

## 9. Schlussbewertung: Aufräumen im Bestand vs. Neuanfang (Hygiene-Sicht)

| Dimension | Befund | Bewertung |
|---|---|---|
| Regressionsnetz | 2303 Tests grün, 185 s, Modulkern (Committee Matcher) bei Test/Prod 0,81 | **Vorhanden** |
| Toter Code | ca. 8 bis 12 Skripte (10 bis 15 Prozent der Scraper/Scripts), Kern gut vernetzt | **Lokal, leicht zu entfernen** |
| Einmal-Migrationen | 4 bis 6 Skripte | **Archivieren** |
| Ballast im Baum | ca. 5 MB (Quell-PDFs, gespeicherte Webseite), keine Code-Verseuchung | **Klein** |
| Daten | 614 MB CSV plus 639 MB ignoriert; 735 ungetrackte, nicht ignorierte Dateien | **Groß, aber außerhalb des Codes lösbar** |
| Doku | ca. 800 KB, 13 700 Zeilen, Churn in 12 von 25 Top-Dateien, 2 tote Verweise, 6 vermutlich abgeschlossene Pläne | **Hoher Pflegeaufwand, mechanisch aufräumbar** |
| Infra | Dockerfile, Compose, systemd und `.env.example` konsistent; 2 ungenutzte Pakete, `pytest` in Produktiv-Requirements | **Klein** |
| Git-Hygiene | 74 geänderte Dateien im Worktree, 3 Branches, 226 Commits | **Mittel, Commit-Disziplin nötig** |

**Fazit:** Aus Hygiene-Sicht ist der Bestand aufräumbar. Die Befunde sind lokal und mechanisch (tote Skripte, Ballast, Doku-Archiv, Daten raus aus dem Repo). Die Testsuite ist grün und dient als Sicherheitsnetz für ein Aufräumen. Der Aufwand liegt bei der Doku-Schicht (800 KB, Pflege bei jeder Phase) und bei den Daten (Verschiebung, nicht Löschung). Hygiene allein begründet keinen Neuanfang. Ein Rebuild wäre nur gerechtfertigt, wenn die Architektur- und Fachaudits andere Gründe liefern (z. B. Modulkopplung, Konfigurationsdichte in `config/settings.py`). Die Hygiene-Schulden wären in einer Neuaufsetzung nur durch Disziplin vermeidbar, nicht durch die Wahl eines neuen Stacks.
