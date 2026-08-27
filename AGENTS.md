# Congress Trade Tracker

Python + Streamlit zur Analyse von Aktiengeschäften von US-Kongressmitgliedern.

## Verbindliche Regeln

- Filterparameter; vollständig in `config/settings.py` konfigurierbar
- Komplexe Implementierungen; Plan mit testbaren Phasen + Gates vorlegen, erst nach „OK“ implementieren
- Kleine Änderungen; Plan optional
- Frontend; keine Emojis
- Antworten; immer mit `Bene, ` beginnen
- Tokenintensive, unabhängige Teilaufgaben; Subagents einsetzen

## Dokumentation

Routing + „Aktualisieren bei“-Trigger: `organisation/DOKU_INDEX.md`.

- Strukturänderung: neues Modul, Stage, Pipeline- oder Datenmodell; Skill `/doku-update` nutzen
- Detailänderung: betroffene kanonische Doku selbst aktualisieren
- Neue/geänderte Datenstrukturen oder Spalten; `organisation/DATA_STRUCTURE.md`
- Neue/geänderte Scripts, Stages, Abläufe oder Abhängigkeiten; `organisation/CLI_Bedienungsanleitung.md`
- Neue Daten-, Config- oder Script-Datei; zusätzlich in `DATA_STRUCTURE.md`; ausführbare Abläufe zusätzlich in `CLI_Bedienungsanleitung.md`
- Neues, unabhängiges Vorhaben; neue Plan-Datei statt bestehende zu überschreiben

## Arbeitsgrundlage

- Eigener Branch; `git diff main...HEAD --stat` + `git status`
- Dateihistorie; `git log --oneline -- <pfad>` oder `CHANGELOG.md`
- Themendokumentation; `organisation/DOKU_INDEX.md`

Git:

- Commit auf `agent/<name>` erlaubt
- Kein Merge nach `main`, Push oder Stash ohne ausdrückliche Nutzerfreigabe
- Freigegebener Merge; aus Haupt-Worktree `/home/bene/Nextcloud/backup_l9520/Projekte/bmb_trdprg/congress_tracker`

```bash
git merge --no-ff agent/<name> -m "Merge agent/<name>: ..."
```

- Worktree- + Branch-Verwaltung; vorhandene Skripte vor manuellen Eingriffen prüfen und nutzen
- Worktree-Skripte; `~/Nextcloud/backup_l9520/Projekte/bmb_trdprg/congress_tracker/scripts/`
- Jeder Agent; eigener Worktree + Branch `agent/<name>`


## Aktueller Stand

- Committee Matcher V2; P0-P7d + S3 implementiert; Gate B am 2026-08-21 beschlossen (Modell `deepseek/deepseek-v4-flash-0731`, freie Providerwahl, Kostenwache)
- Providersettings gesetzt + Routing-Preflight `provider check` live; Contract-Smoke am 2026-08-22 live bestanden (Routing `Morph`, 0,000018 USD, Exit 0)
- Kern vollstaendig: `mapping.py` + `validation.py` (P6), `workflow.py` (P7a), `artifacts.py` (P7b), Laufkommandos `run plan|execute|reconcile` (P7c), Review-Roundtrip `review export|import` (P7d)
- Ablauf: Mapping -> Review in Excel -> `reviewed_assignments.csv` -> Uebernahme in `import_trades`
- Offen: Terminalstatus S4 (Fortschritt waehrend `run execute`, Statusmeldungen fuer `review`), P8 Entwicklungsrun mit vollem Roundtrip live, Phasenreviews (GPT Sol) fuer P7c-Nachpruefung, P7d und S3
- Entschieden 2026-08-27: CSV-Formelinterpretation bleibt unveraendert (Review laeuft ausschliesslich ueber die Excel), Confidence-Rubrik bleibt vierstufig, `unit_id` bleibt Modellfeld (Fehler faellt ueber Anzahl- und Evidenzpruefung auf), Boundary-Konflikte sind reine Information ohne Eingabefeld
- Detailstand + History; `CHANGELOG.md`; laufende Entscheidungen + Plaene; `organisation/` und `~/.claude/plans/`
