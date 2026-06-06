# Congress Trade Tracker - Projektstatus

## Projektbeschreibung
Python + Streamlit App die US-Kongress-Mitglieder Stock-Trades trackt, analysiert und Trading-Empfehlungen erstellt.

## Wichtige Regeln
1. **Alle Filter-Parameter muessen konfigurierbar sein** (in config/settings.py)
2. **Immer erst Implementierungsplan zeigen**, dann auf "OK" warten bevor Code geschrieben wird
3. **transaction_date verwenden**, nicht disclosure_date (45-Tage Meldefrist)
4. **Dokumentation** (Single-Source-of-Truth; Routing-Quelle: `organisation/DOKU_INDEX.md`):
   - **Bei jeder Code-Änderung** die betroffene(n) Doku-Datei(en) mitpflegen — *welche*, nennt die „Aktualisieren bei"-Spalte in `DOKU_INDEX.md`. Faustregel: neue/geänderte Spalten/Datenstrukturen → `organisation/DATA_STRUCTURE.md`; neue/geänderte Scripts, Stages, Abläufe, Abhängigkeiten → `organisation/Pipelines.md`.
   - **Neue projektrelevante Datei** (Daten/Config/Script) zusätzlich aufnehmen in `DATA_STRUCTURE.md` (Struktur, Spalten, Zweck, Beispiele) **und** `Pipelines.md` (Zweck, Position in der Pipeline).
   - **Nach Modulabschluss / großer Änderung**: Doku **nicht selbst** schreiben → Skill **`/doku-update`** (git-diff-Scope + DOKU_INDEX-Routing + Briefing an den `doku-agent`, Default-Modell **Sonnet**; voller Ablauf + Modell-Overrides: `.claude/skills/doku-update/SKILL.md`).
   - **Plan-Dateien nicht recyceln**: für ein neues, unverwandtes Todo eine **neue** Plan-Datei anlegen statt eine bestehende fremde zu überschreiben (Token-Ersparnis — spart den Read-Roundtrip vor jeder Änderung).
5. **Systemanweisung**: Du bist ein erfahrener Software-Entwickler. Du erstellst vor jeder Code-Änderung einen Implementierungsplan, bevor du diesen strikt umsetzt. Um einen individuell passenden Plan zu erstellen frägst du den User nach konkreten Anweisungen und lässt wichtige Planungsfragen vom User approven. Du teilst jeden Gedankengang und Entscheidungsweg mit dem User. Falls Rückfragen oder Probleme auftreten, teile sie dem User mit.
6. **Fragen an dich**: Wenn der User nach deiner Einschätzung frägt, dann präsentiere zunächst deinen Gedankengang, verschiedene Lösungswege, Pro-/Kontra-Argumente und Konsequenzen jedes Lösungsweges und zuletzt deine begründete Empfehlung.
7. **Einsatz von subagents** Nutze für tokenintensive und gleichzeitig nicht anspruchsvolle Aufgaben subagents, welche eine Teilaufgabe für dich übernehmen. z.b. den explore subagent zum indentifizieren von dateien oder finden von wichtigen Codestellen unter vielen verschiedenen Dateien.
8. **Git-Workflow im Worktree**. Du arbeitest in einem git-Worktree auf deinem eigenen Branch `agent/<name>`.
    - **Adden + Committen sind erlaubt** — committe deine Änderungen frei auf deinem eigenen Branch (`git add ...`, `git commit -m "..."`).
    - **Mergen nach `main` NUR auf ausdrücklichen Befehl des Users** — niemals eigenmächtig. Wenn der User es anweist, läuft es genau so:
      ```bash
      # 1. Commit auf dem eigenen Branch (im Worktree …-agent-<name>)
      git add -A
      git commit -m "..."
      # 2. Merge in main aus dem HAUPT-Worktree (dort ist main ausgecheckt)
      cd /path/to/congress_tracker
      git merge --no-ff agent/<name> -m "Merge agent/<name>: ..."
      ```
    - NIEMALS `git push` und NIEMALS `git stash` ohne ausdrücklichen Befehl (Stash ist im Worktree-Setup zwischen allen Worktrees geteilt → gefährlich).

---

## Orientierungs-Workflow

Stand **nicht aus dem Gedächtnis** annehmen — aus git/Doku ableiten:
- **Was hat MEIN Branch geändert?** → `git diff main...HEAD --stat` + `git status`
- **Stand/History einer Datei oder eines fertigen Moduls?** → `git log --oneline -- <pfad>` oder `CHANGELOG.md`
- **Wo liegt Thema X / welche Doku pflegen?** → `organisation/DOKU_INDEX.md`

---

## Aktueller Stand (WIP)

**Fertige Module + Phasen-Historie:** `CHANGELOG.md` bzw. `git log --oneline -- <pfad>`.
Detail, das **nicht** im CHANGELOG steht: Post-Trade-Performance (Modul 5) → `organisation/Briefing_stage_post_trade_performance.md` + `DATA_STRUCTURE.md`; Scheduler/Quiver-Auto-Download (Modul 11) → `deploy/systemd/README.md`.
**Offene Module** (10, Rebuild T4, genereller Scheduler): s. „Nächste Schritte" unten.

**In Arbeit — Modul 9 (Streamlit-Dashboard):** `app/streamlit_app.py`, `score_recompute.py`, `scoring_presets.py`, `chart_helpers.py`. **5 Tabs**: Trades / Assets / Members / Post Trade Performance / Recent Trades. Kernfeatures: unified Token-Suche (`_search_mask` über Name/Asset/Ticker/Hash-ID); globaler **Horizont-Sichtbarkeitsfilter** (`first_close_offset_days` ≤ Primary-Horizont, offene Positionen ausgeblendet); **Recent-Trades-Tab** für Monitoring offener Positionen (Mark-to-Market `paper_return_pct`, umgeht Horizont-Filter); Voll-Recompute bei Signal-Ausschluss; Sidebar-Form **„Score-Gewichte"** (alle `SCORING_WEIGHTS` + Tier-Editor, Preset-Persistenz `data/config/scoring_presets.json`). Start: `streamlit run app/streamlit_app.py`. Datenspalten: `DATA_STRUCTURE.md`; Scoring: `Filter_Instanz_Parameter.md`.

---

## Wichtige Dateien (Einstieg)

**Pipeline-Einstieg:** `scrapers/quiver_scraper.py` (Auto-Download Congress-Trading-XLSX, täglich 03:00 via systemd, Exit-Codes 0/1/2/3) → `scripts/import_trades.py` (Haupt-Import-Pipeline, nach jedem Scraper-Lauf). Scheduler-Vorlagen + README: `deploy/systemd/`. House/Senate-Scraper s. Import-Workflow unten.

**Code-Konvention Gate / Tag / Signal:** `gates/` = binäre Hard-Ausschlüsse · `tags/` = Kontext-Anreicherung mit Master-Spalten · `signals/` = User-sichtbare Score-Beiträge. Instanzen + Parameter + Score-Formel: `organisation/Filter_Instanz_Parameter.md`.

**Config:** `config/settings.py` (alle konfigurierbaren Parameter), `config/committee_mapping.py` (O(1) Subcommittee→Sektor-Lookup).

**Utils, `scoring/`, App-Module + alle Datendateien** (Zweck, Spalten, Beispiele): `organisation/DATA_STRUCTURE.md`; Ticker/Issuer-Architektur: `organisation/Ticker_Architektur.md`. Modul-8-Scoring-Formel (`scoring/asset_volume.py`, `scoring/trade_score.py`): `Filter_Instanz_Parameter.md`. `utils/openfigi_client.py` = LEGACY (Rebuild T4 entfernt sie).

---

## Import-Workflow (Kurzform)

```bash
cd congress_tracker && source venv/bin/activate
python scrapers/house_scraper.py        # data/raw/house_trades.csv
python scrapers/senate_scraper.py       # data/raw/senate_trades.csv
python scripts/import_trades.py         # data/processed/trades_master*.csv
```

CLI-Optionen (Delta-Mode, einzelne Stages, Dry-Run, PDF-Extraction): siehe `organisation/CLI_Bedienungsanleitung.md`.

---

## Nächste Schritte

1. **Modul 9 — Streamlit-Dashboard** (in Arbeit): nächste Iteration `effective_return_horizon_days`-Spalte einblenden, sobald Plan A sie in den Master schreibt (Platzhalter-Check im Code vorhanden).
2. **Rebuild T4**: Migration alter Caches, `utils/openfigi_client.py` + alte Tabellen entfernen.
3. **Modul 10** (Email-Benachrichtigungen), **Modul 11** (genereller Scheduler übriger Pipelines).

Historische Plan-Datei (P1–P7, abgeschlossen): `archive/alt/Implementierungsplan_Signals.md`.

---

## Detail-Doku

Schneller Themen-Einstieg (kanonische Quelle pro Thema):
- **Spalten** → `DATA_STRUCTURE.md` · **Gate/Tag/Signal + Score** → `Filter_Instanz_Parameter.md` · **Pipeline-Stages** → `Pipelines.md` · **Signal-Schema** → `tool_specific_md/signal_info.md` · **Ticker/Issuer** → `Ticker_Architektur.md` · **CLI** → `CLI_Bedienungsanleitung.md` · **Phasen-Historie** → `CHANGELOG.md` · **Offene Pläne** → `~/.claude/plans/*.md`

hallo

**Voller Doku-Index + „Aktualisieren bei"-Trigger pro Datei:** `organisation/DOKU_INDEX.md` (Single-Source-of-Truth-Konvention + Routing-Quelle für Regel 4 und Skill `/doku-update`).
