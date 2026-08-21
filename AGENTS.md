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

- Committee Matcher V2; P0–P6 implementiert; Gate B am 2026-08-21 beschlossen (Modell `deepseek/deepseek-v4-flash-0731`, freie Providerwahl, Kostenwache)
- Providersettings gesetzt + Routing-Preflight `provider check` live
- P5 implementiert: Systemprompts, providerneutrales Interface, FakeProvider, OpenRouter-Adapter, `provider smoke`; Contract-Smoke noch nicht live ausgefuehrt (Gate)
- P6 implementiert: Mappingkern `mapping.py` + `validation.py` (Schema, Batching mit Context-Split, Repair ohne Silent-Fix, Boundary-Erkennung); naechste Phase P7
- Detailstand + History; `CHANGELOG.md`; laufende Entscheidungen + Pläne; `organisation/` und `~/.claude/plans/`
