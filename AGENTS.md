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
 Single-Source-of-Truth pro Thema | Spalten → DATA_STRUCTURE.md · Score/Settings → config/settings.py · Pipeline-Reihenfolge → Pipelines.md · Signal-Schema → tool_specific_md/signal_info.md · Deployment → DEPLOYMENT.md |

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
- Freigegebener Merge; aus Haupt-Worktree `/path/to/congress_tracker`

```bash
git merge --no-ff agent/<name> -m "Merge agent/<name>: ..."
```

- Worktree- + Branch-Verwaltung; vorhandene Skripte vor manuellen Eingriffen prüfen und nutzen
- Worktree-Skripte; `~/Nextcloud/Projekte/bmb_trdprg/congress_tracker/scripts/`
- Jeder Agent; eigener Worktree + Branch `agent/<name>`


## Aktueller Stand

- Programm läuft: Pipeline, Scoring und Dashboard funktionsfähig
- Dashboard seit 2026-08-30 live: `https://congress-trading-tracker.com` (privater VPS, Docker + Caddy, Datenstand 2026-07-21); Serverdetails liegen nur in `.deploy.env`
- Committee Matcher V2; P0-P8 + S1-S5 implementiert; Gate B am 2026-08-21 beschlossen (Modell `deepseek/deepseek-v4-flash-0731`, freie Providerwahl, Kostenwache)
- Providersettings gesetzt + Routing-Preflight `provider check` live; Contract-Smoke am 2026-08-22 live bestanden (Routing `Morph`, 0,000018 USD, Exit 0)
- Kern vollstaendig: `mapping.py` + `validation.py` (P6), `workflow.py` (P7a), `artifacts.py` (P7b), Laufkommandos `run plan|execute|reconcile` (P7c), Review-Roundtrip `review export|import` (P7d), Live-Terminalstatus fuer alle Vorgaenge (S4)
- Review-Workbook ist eine Arbeitstabelle ohne Steuerspalten: Wert aendern, Zeile loeschen, Zeile anhaengen; Provenienz + Aenderungsbilanz rechnet der Import aus dem Vergleich gegen die Laufartefakte
- Ablauf: Mapping -> Review in Excel -> `reviewed_assignments.csv` -> Uebernahme in `import_trades`
- P8 am 2026-08-28 live bestanden: Lauf `p8-run2` (7 Units House Agriculture, 6 mapped, 1 no_mapping, 0,003685 USD) plus Roundtrip `review-p8-run2`; Denktiefe je Call ist seither ueber `COMMITTEE_MATCHER_REASONING_EFFORT` begrenzt, gesendet wird `max_output_tokens + reasoning_reserve_tokens`
- Phasenreviews abgeschlossen: P7c-Nachpruefung, S3, S5 (je nicht abgenommen, Befunde behoben) und P8 (mit Auflagen abgenommen, Auflagen erledigt)
- Entschieden 2026-08-27: CSV-Formelinterpretation bleibt unveraendert (Review laeuft ausschliesslich ueber die Excel), Confidence-Rubrik bleibt vierstufig, `unit_id` bleibt Modellfeld (Fehler faellt ueber Anzahl- und Evidenzpruefung auf), Boundary-Konflikte sind reine Information ohne Eingabefeld
- Fullrun `map-fullrun-20261016` (225 Units, 522 Zuordnungen) ist aktive Revision (`COMMITTEE_MAPPING_REVIEW_ID`); Gate C am 2026-10-17 beschlossen: neues Mapping übernommen, Confidence-Gewichtung (P4) verworfen
- Offen: voller Pipeline-Rebuild mit neuem Mapping; P9 Gold-Set + Evaluation (kostenpflichtig, Gate)
- Befund: `trade_score` korreliert kaum mit dem Return — Konstrukt-Konflikt, kein Bug
- Detailstand + History: `CHANGELOG.md`; Entscheidungen + Pläne: `organisation/`, `~/.claude/plans/`
