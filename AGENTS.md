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
7. **Emojis-Verbot** Verwende keine Emojis für das frontend/streamlit
8. **Einsatz von subagents** Nutze für tokenintensive und gleichzeitig nicht anspruchsvolle Aufgaben subagents, welche eine Teilaufgabe für dich übernehmen. z.b. den explore subagent zum indentifizieren von dateien oder finden von wichtigen Codestellen unter vielen verschiedenen Dateien.
9. **Git-Workflow im Worktree**. Du arbeitest in einem git-Worktree auf deinem eigenen Branch `agent/<name>`.
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
10. **Beginne jede antwort mit 'Bene, '(...)**
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

**In Arbeit — Modul 9 (Streamlit-Dashboard):** `app/streamlit_app.py`, `score_recompute.py`, `scoring_presets.py`, `chart_helpers.py`, `member_metrics.py`, `member_detail.py`, `score_correlation.py`. **6 Tabs**: Trades / Assets / Members / Post Trade Performance / Recent Trades / Score-Analyse. Kernfeatures: unified Token-Suche (`_search_mask` über Name/Asset/Ticker/Hash-ID); globaler **Horizont-Sichtbarkeitsfilter** (`first_close_offset_days` ≤ Primary-Horizont, offene Positionen ausgeblendet); **Recent-Trades-Tab** für Monitoring offener Positionen (Mark-to-Market `paper_return_pct`, umgeht Horizont-Filter); **Score-Analyse-Tab** (Modul 9.x, `app/score_correlation.py`; bis 2026-07-03 zwei Tabs Score-Korrelation + Score-Analyse) zur `trade_score`-Validierung gegen den signierten Objective Return `post_return_{X}d` (Spearman als Rang-Pearson ohne scipy; umgeht den Horizont-Filter inkl. held; Kennzahlen + Scatter + gleich breite Score-Buckets + Pro-Signal-Korrelation, alles filterbar nach Kammer/Partei, eigener Tab-Horizont); Voll-Recompute bei Signal-Ausschluss; Sidebar-Form **„Score-Gewichte"** (alle `SCORING_WEIGHTS` + Tier-Editor, Preset-Persistenz `data/config/scoring_presets.json`); **Member-Detail-Popup** (Klick auf Members-Tab-Zeile → `@st.dialog`, live pro Member berechnet — keine neuen Master-Spalten/Pipeline-Lauf — Übersicht/Performance/Verläufe/Timing inkl. Spaghetti-Charts, Red-Flag-Ampel, Google-Namens-/Ticker-Links); Trades-Tab-Ticker-Spalte als Google-`LinkColumn`; alle Zeit-/Horizont-Widgets tragen Scope-Präfixe **[Global]/[Tab]/[Detail]** (Sidebar global, Tab-eigene Fenster tab, Popup-Controls detail); Member-Detail-Popup nutzt die **volle Member-Historie** (Sidebar-Zeitfenster wirkt nicht, Quartal-Slider = einziges Zeitfilter). Start: `streamlit run app/streamlit_app.py`. Datenspalten + Modul-Details: `DATA_STRUCTURE.md` (App-Konfiguration/Scope: Abschnitt „7b"; Member-Detail: „7c"; Score-Analyse: „7d"; Abnormal-Return-Anzeige: „7e"); Scoring: `Filter_Instanz_Parameter.md`.

**In Arbeit — Primär-/Hint-Scoring (Branch `agent/new_scoring`):** `config/settings.py`, `scoring/trade_score.py`, `app/streamlit_app.py`, `app/score_recompute.py`, `app/glossary.py`. Hard-Gate: triggert ein Trade kein Primärsignal (`SCORING_PRIMARY_SIGNALS = ('committee_full', 'swarm_buy')`), ist `trade_score = 0`. Hints (alle übrigen Signale) Default-Gewicht 0, nur im Trade-Detail-Popup. Ex-Officio-Rollenboni alle 0; `SCORING_PARADOX_DAMPER` default 1.0; `SCORING_TRADE_VOLUME_ENABLED` default `False`. Codebase-weites Debugging (Intervalle 1–3: Delta-Signal-Kontext, Paradox-Reichweite, Rolling-Window-Schwärme, App-Fixes — Details `CHANGELOG.md`) ist auf dem Branch committed, Intervall 4 (Utils/Scripts) läuft noch. 1019 Tests grün, Master-Re-Scoring (`--stage_scoring --regenerate_curated`) ausstehend (wird vom User im Rahmen der Committee-Remap-Sequenz ausgeführt). NICHT gemergt.

**In Arbeit — Committee-Mapping-Agent:** `scripts/committee_mapping_agent/` — 3-Rollen-LLM-Agent (DeepSeek V4), erzeugt `subcommittee_sector_industry_mapping.csv` (Master des Committee-Tags) gegroundet + reviewbar (xlsx-Round-Trip) neu. Einstieg: `scripts/committee_mapping_agent/run.py` (`python -m scripts.committee_mapping_agent.run <subcommand>`). Status: Probelauf 3 Committees abgeschlossen, Full-Run + `promote` ausstehend. Details: `CLI_Bedienungsanleitung.md` Abschnitt 15, `DATA_STRUCTURE.md` Abschnitt 3a, `Pipelines.md` Pipeline 11, Review-Prozess: `manual_review_anleitung.md` Prozess 4.

**Abgeschlossen — Abnormal Return (Discovery + Integration, Branch `agent/new_scoring`):** `scoring/abnormal_returns.py` (signierter, marktbereinigter BHAR vs. SPY; 2 Konstrukte: A = `transaction_date`, B = `disclosure_date`; member-geclusterter Block-Bootstrap ohne scipy). **Deskriptiv — KEIN Scoring-Input.** Zwei Nutzungen: (1) **produktive Pipeline-Stage `abnormal_returns`** (`scripts/import_trades.py`, Full-Run [9b]/Delta [10b], `--stage_abnormal_returns`) schreibt die Konstrukt-A-Spalten `signed_ar_{30,60,90,120,180}d` in den Master; Anzeige NUR in den Detail-Popups, AR-Horizont dort per UI wählbar (`st.radio`, Optionen `AR_HORIZONS_DAYS`, Default `AR_HEADLINE_HORIZON_DAYS`/120d — gilt für Trade- UND Member-Detail: Trade-Detail: AR {H}d + zweiseitiger Perzentil-Rang gegen alle Trades + Roh-vs-AR-Tabelle; Member-Detail: Median-AR {H}d + Ausreißer-Quote pos/neg; Helper in `app/member_metrics.py`). Outlier-Flag/Rang bei Anzeige abgeleitet (nicht im Master). (2) **Standalone-Analyse** `scripts/analyse_event_study.py` (Diskriminator + Konstrukt B + Bootstrap, kein Master-Schreiben). Master-Backfill (`--stage_abnormal_returns`) ausstehend. Befund-Report: `organisation/analyse_event_study.md`; Spalten/Anzeige: `DATA_STRUCTURE.md` Abschnitt 7e; Stage: `Pipelines.md` Pipeline 5 [8b]; Settings: `AR_*`-Block in `config/settings.py`.

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

**Committee-Roster** (`data/mappings/{house,senate}_member&(sub)committees.csv`, F2-Format) werden von eigenen Producern erzeugt — `scrapers/senate_committee_xml/` (Senate, XML) und `scrapers/house_pdf_parsing_tool/` (House, PDF), Details: `organisation/Pipelines.md` Pipeline 3c. Nach einer Roster-Änderung gilt die Re-Run-Sequenz `--stage_committees → --stage_gates_tags → --stage_branch1_signals → --stage_sequence_signals → --stage_swarm_signals → --stage_scoring (+ --regenerate_curated)`.

---

## Nächste Schritte

1. **Modul 9 — Streamlit-Dashboard** (in Arbeit): nächste Iteration `effective_return_horizon_days`-Spalte einblenden, sobald Plan A sie in den Master schreibt (Platzhalter-Check im Code vorhanden).
2. **Rebuild T4**: Migration alter Caches, `utils/openfigi_client.py` + alte Tabellen entfernen.
3. **Modul 10** (Email-Benachrichtigungen), **Modul 11** (genereller Scheduler übriger Pipelines).
4. **Event-Study-Folgeschritte (Discovery):** Perzentil-Rerun (`AR_OUTLIER_MODE='percentile'`, Schwellen `AR_OUTLIER_PCT_UPPER/LOWER`); Konstrukt C (Vote-Event als Anker statt Kauf-/Verkaufsdatum). (Stage-9b-Integration erledigt — s. „Abgeschlossen — Abnormal Return".)

Historische Plan-Datei (P1–P7, abgeschlossen): `archive/alt/Implementierungsplan_Signals.md`.

---

## Detail-Doku

Schneller Themen-Einstieg (kanonische Quelle pro Thema):
- **Spalten** → `DATA_STRUCTURE.md` · **Gate/Tag/Signal + Score** → `Filter_Instanz_Parameter.md` · **Pipeline-Stages** → `Pipelines.md` · **Signal-Schema** → `tool_specific_md/signal_info.md` · **Ticker/Issuer** → `Ticker_Architektur.md` · **CLI** → `CLI_Bedienungsanleitung.md` · **Phasen-Historie** → `CHANGELOG.md` · **Offene Pläne** → `~/.claude/plans/*.md`

hallo

**Voller Doku-Index + „Aktualisieren bei"-Trigger pro Datei:** `organisation/DOKU_INDEX.md` (Single-Source-of-Truth-Konvention + Routing-Quelle für Regel 4 und Skill `/doku-update`).
