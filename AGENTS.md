# Congress Trade Tracker

Python + Streamlit zur Analyse von Aktiengeschäften von US-Kongressmitgliedern.

## Status: eingefroren

Seit 2026-11-25 entsteht der Neubau in `Congresstracker_v2` (`/home/bene/Nextcloud/Projekte/bmb_trdprg/Congresstracker_v2`, GitHub `Bene77-w/Congresstracker_v2`). Dieses Repo ist nur noch Nachschlagewerk: keine neuen Features, keine Umbauten. Analysen für die Übernahme stehen im Neubau unter `docs/uebernahme/`.

## Verbindliche Regeln

- Filterparameter; vollständig in `config/settings.py` konfigurierbar
- Komplexe Implementierungen; Plan mit testbaren Phasen + Gates vorlegen, erst nach „OK“ implementieren
- Kleine Änderungen; Plan optional
- Frontend; keine Emojis
- Antworten; immer mit `Bene, ` beginnen
- Tokenintensive, unabhängige Teilaufgaben; Subagents einsetzen
- Für neues Todo neue Plan-Datei anlegen statt unverwandte überschreiben (spart Read-Roundtrip).
- Qualitätsschwelle; `CONSTRAINTS.md` vor dem Schreiben von Code lesen, nicht abschwächen, damit eine Änderung durchkommt; Prüflauf am Ticketende `tools/check.sh`



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


## Aktueller Stand (2026-11-27)

- Eingefroren, Neubau in `Congresstracker_v2` (siehe oben)
- Dashboard `https://congress-trading-tracker.com` auf privatem VPS (Docker + Caddy, Datenstand 2026-07-21); Serverdetails liegen nur in `.deploy.env`
- Quiver-Export liefert seit 2026-08-03 keine Daten; Timer für den Delta-Lauf deaktiviert
- Sanierungsstufe 1 abgeschlossen und in `main`; Review unter `docs/reviews/sanieren-stufe1/`
- Maßgeblicher Befund: `docs/sanierung/audit.md` (2026-11-20); der Plan für Stufe 2 ist verworfen (`docs/sanierung/plan-stufe2-verworfen.md`)
- Committee Matcher: aktive Revision `map-fullrun-20261016` (225 Units, 522 Zuordnungen, Codex-CLI-Volllauf); API-Pfad und P9 Gold-Set aufgegeben
- Ältere Statusangaben in `organisation/` und `CHANGELOG.md` sind historisch
