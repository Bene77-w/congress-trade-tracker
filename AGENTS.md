# Congress Trade Tracker

Python + Streamlit zur Analyse von Aktiengeschäften von US-Kongressmitgliedern.

## Verbindliche Regeln

- Filterparameter; vollständig in `config/settings.py` konfigurierbar
- Komplexe Implementierungen; Plan mit testbaren Phasen + Gates vorlegen, erst nach „OK“ implementieren
- Kleine Änderungen; Plan optional
- Frontend; keine Emojis
- Antworten; immer mit `Bene, ` beginnen
- Tokenintensive, unabhängige Teilaufgaben; Subagents einsetzen
- Für neues Todo neue Plan-Datei anlegen statt unverwandte überschreiben (spart Read-Roundtrip).



## Dokumentation

Routing + „Aktualisieren bei“-Trigger: `organisation/DOKU_INDEX.md`.
 Single-Source-of-Truth pro Thema | Spalten → DATA_STRUCTURE.md · Score/Settings → Filter_Instanz_Parameter.md · Pipeline-Reihenfolge → Pipelines.md · Signal-Schema → tool_specific_md/signal_info.md · READMEs nur 1-Satz + Link |

- Strukturänderung: neues Modul, Stage, Pipeline- oder Datenmodell; Skill `/doku-update` nutzen
- Detailänderung: betroffene kanonische Doku selbst aktualisieren
- Neue/geänderte Datenstrukturen oder Spalten; `organisation/DATA_STRUCTURE.md`
- Neue/geänderte Scripts, Stages, Abläufe oder Abhängigkeiten; `organisation/CLI_Bedienungsanleitung.md`
- Neue Daten-, Config- oder Script-Datei; zusätzlich in `DATA_STRUCTURE.md`; ausführbare Abläufe zusätzlich in `CLI_Bedienungsanleitung.md`
- Single-Source pro Thema, Status-Tabellen statt Prosa, Historie → CHANGELOG.md; Optik/Doku erst im finalen Doku-Schritt


## Arbeitsgrundlage

- Eigener Branch; `git diff main...HEAD --stat` + `git status`
- Dateihistorie; `git log --oneline -- <pfad>` oder `CHANGELOG.md`
- Themendokumentation; `organisation/DOKU_INDEX.md`

Git:

- Commit auf `agent/<name>` erlaubt
- Kein Merge nach `main`, Push oder Stash ohne ausdrückliche Nutzerfreigabe
- Freigegebener Merge; aus Haupt-Worktree `/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker`

```bash
git merge --no-ff agent/<name> -m "Merge agent/<name>: ..."
```

- Worktree- + Branch-Verwaltung; vorhandene Skripte vor manuellen Eingriffen prüfen und nutzen
- Worktree-Skripte; `~/Nextcloud/Projekte/bmb_trdprg/congress_tracker/scripts/`
- Jeder Agent; eigener Worktree + Branch `agent/<name>`


## Aktueller Stand

- Programm läuft: Pipeline, Scoring und Dashboard funktionsfähig
- In Arbeit: Deployment sowie Committee-Mapping-Workflow (offen: P9 Gold-Set + Evaluation, kostenpflichtig → Gate)
- Befund: `trade_score` korreliert kaum mit dem Return — Konstrukt-Konflikt, kein Bug
- Detailstand + History: `CHANGELOG.md`; Entscheidungen + Pläne: `organisation/`, `~/.claude/plans/`
