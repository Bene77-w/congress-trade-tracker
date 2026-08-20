# Congress Trade Tracker - Projektstatus

## Projektbeschreibung
Python + Streamlit App die US-Kongress-Mitglieder Stock-Trades trackt, analysiert und Trading-Empfehlungen erstellt.

## Wichtige Regeln
1. **Alle Filter-Parameter muessen konfigurierbar sein** (in config/settings.py)
2. **Bei komplexen Implementierungen Immer erst Implementierungsplan zeigen**, dann auf "OK" warten bevor Code geschrieben wird, Implementierungsplan kann bei kleinen unkomplexen Änderungen übersprungen werden
4. **Dokumentation** (Single-Source-of-Truth; Routing-Quelle: `organisation/DOKU_INDEX.md`) — **oberste Priorität ist kosteneffiziente, korrekte Dokumentierung**:
   - **Zwei Dokumentationsarten, der Implementierungsagent entscheidet eigenständig, welche zutrifft:**
     - **Große Architekturänderungen / Moduländerungen** (neues Modul, neue Stage, strukturelle Änderung an Pipeline/Datenmodell) → Doku **nicht selbst** schreiben, sondern Skill **`/doku-update`** aufrufen (git-diff-Scope + DOKU_INDEX-Routing + Briefing an den `doku-agent`, Default-Modell **Sonnet**; voller Ablauf + Modell-Overrides: `.claude/skills/doku-update/SKILL.md`).
     - **Kleine Detailanpassungen** (z. B. einzelnes Feld, kleiner Bugfix, kleines CLI-Flag) → der Implementierungsagent pflegt die betroffene(n) Doku-Datei(en) **selbst/manuell** — *welche*, nennt die „Aktualisieren bei"-Spalte in `DOKU_INDEX.md`. Faustregel: neue/geänderte Spalten/Datenstrukturen → `organisation/DATA_STRUCTURE.md`; neue/geänderte Scripts, Stages, Abläufe, Abhängigkeiten → `organisation/CLI_Bedienungsanleitung.md`.
   - **Neue projektrelevante Datei** (Daten/Config/Script) zusätzlich in `DATA_STRUCTURE.md` (Struktur, Spalten, Zweck, Beispiele) und bei ausführbaren Abläufen in `CLI_Bedienungsanleitung.md` dokumentieren.
   - **Plan-Dateien nicht recyceln**: für ein neues, unverwandtes Todo eine **neue** Plan-Datei anlegen statt eine bestehende fremde zu überschreiben (Token-Ersparnis — spart den Read-Roundtrip vor jeder Änderung).
5. **Systemanweisung**: Du bist ein erfahrener python Software-Entwickler. Um einen individuell passenden Plan zu erstellen frägst du den User nach konkreten Anweisungen und lässt wichtige Planungsfragen vom User beantworten. Du teilst jeden Gedankengang und Entscheidungsweg mit dem User. Falls Rückfragen oder Probleme auftreten, teile sie dem User mit.
6. **Fragen an dich**: Wenn der User nach deiner Einschätzung frägt, dann präsentiere zunächst deinen Gedankengang, verschiedene Lösungswege, Pro-/Kontra-Argumente und Konsequenzen jedes Lösungsweges und zuletzt deine begründete Empfehlung.
7. **Emojis-Verbot** Verwende keine Emojis für das frontend/streamlit
8. **Einsatz von subagents** Nutze für tokenintensive Aufgaben subagents, welche Teilaufgaben für dich erledigen. weiter unten eine Orientierung für die Modellwahl: 
9. **Git-Workflow im Worktree**. Du arbeitest in einem git-Worktree auf deinem eigenen Branch `agent/<name>`.
    - **Adden + Committen sind erlaubt** — committe deine Änderungen frei auf deinem eigenen Branch (`git add ...`, `git commit -m "..."`).
    - **Mergen nach `main` NUR auf ausdrücklichen Befehl des Users** — niemals eigenmächtig. Wenn der User es anweist, läuft es genau so:
      ```bash
      # 1. Commit auf dem eigenen Branch (im Worktree …-agent-<name>)
      git add -A
      git commit -m "..."
      # 2. Merge in main aus dem HAUPT-Worktree (dort ist main ausgecheckt)
      cd /home/bene/Nextcloud/backup_l9520/Projekte/bmb_trdprg/congress_tracker
      git merge --no-ff agent/<name> -m "Merge agent/<name>: ..."
      ```
    - NIEMALS `git push` und NIEMALS `git stash` ohne ausdrücklichen Befehl (Stash ist im Worktree-Setup zwischen allen Worktrees geteilt → gefährlich).
10. **Beginne jede antwort mit 'Bene, '(...)**
11. **Kommunikations- und Planungsstil nach Benes Präferenzen**:
    - Ziele zu Beginn vollständig, aber komprimiert nennen; anschließend den Weg zur Zielerreichung klar beschreiben.
    - Positiv und handlungsorientiert formulieren: gewünschtes Ergebnis und Vorgehen erklären. Verbote nur nennen, wenn sie ein konkretes Risiko verhindern.
    - Inhalt strikt an Überschrift und Userfrage ausrichten; keine themenfremden Hinweise oder entbehrlichen Hintergrundinformationen ergänzen.
    - Kurze, direkte Sätze und Bulletpoints gegenüber Fließtext bevorzugen, ohne entscheidungs- oder umsetzungsrelevante Informationen auszulassen.
    - Bereits geltende globale oder lokale Regeln nicht in Plänen und Erklärungen wiederholen; nur bei unmittelbarer Relevanz darauf verweisen.
    - Hauptpläne auf Ziel, Kernentscheidungen und ausführbare Schritte begrenzen. Rechercheberichte, lange Abwägungen und frühe Detailplanung in separate verlinkte Dateien auslagern; Inhalte nicht duplizieren.
    - Getroffene Architekturentscheidungen kurz damit begründen, warum ihre Vorteile die Nachteile überwiegen. Bei offenen Entscheidungen nur relevante Alternativen mit kompakten Pro-/Contra-Punkten nennen.
    - Erklärungen auf die zum Verständnis nötigen Zusammenhänge reduzieren und mit der konkreten Antwort beginnen.
---

## Orientierungs-Workflow

Stand **nicht aus dem Gedächtnis** annehmen — aus git/Doku ableiten:
- **Was hat MEIN Branch geändert?** → `git diff main...HEAD --stat` + `git status`
- **Stand/History einer Datei oder eines fertigen Moduls?** → `git log --oneline -- <pfad>` oder `CHANGELOG.md`
- **Wo liegt Thema X / welche Doku pflegen?** → `organisation/DOKU_INDEX.md`
---

## Subagent-Modellwahl (nach Anspruchsgrad)

| Anspruchsgrad | Modell | Typische Aufgaben |
|---|---|---|
| **Trivial** | `haiku` | Datei-Suche, Grep-artige Exploration, einfache Lookups, simple Umbenennungen, Formatprüfungen |
| **Standard** | `sonnet` | Einfache klar spezifizierte code implementierung, Dokumentation |
| **Komplex / kritisch** | `opus` | Komplexe Implementierungen, Architekturentscheidungen, Security-Reviews, Debugging tiefer Bugs, mehrdeutige Bewertungen mit Abwägung |
---


## Aktueller Stand (WIP)

**Fertige Module + Phasen-Historie:** `CHANGELOG.md` bzw. `git log --oneline -- <pfad>`.
Detail, das **nicht** im CHANGELOG steht: Post-Trade-Performance (Modul 5) → `DATA_STRUCTURE.md`; Scheduler/Quiver-Auto-Download (Modul 11) → `deploy/systemd/README.md`.
**Offene Module** (10, Rebuild T4, genereller Scheduler): s. „Nächste Schritte" unten.

**Ausgelagert — Suspicious-Trades-Workflow:** Der eigenständige Analyse-Workflow
liegt im Schwesterprojekt
`/home/bene/Nextcloud/backup_l9520/Projekte/bmb_trdprg/suspicious-trades-workflow`
mit eigener Konfiguration, Datenablage und virtueller Umgebung. Der Congress
Tracker enthält davon keinen Code und keine Laufzeitabhängigkeit; verbleibende
Schnittstelle ist nur der optionale neutrale CSV-Export in `data/agent_exports/`.

**In Arbeit — Modul 9 (Streamlit-Dashboard):** `app/streamlit_app.py`, `score_recompute.py`, `scoring_presets.py`, `chart_helpers.py`, `member_metrics.py`, `member_detail.py`, `score_correlation.py`. **6 Tabs**: Trades / Assets / Members / Post Trade Performance / Recent Trades / Score-Analyse. Kernfeatures: unified Token-Suche (`_search_mask` über Name/Asset/Ticker/Hash-ID); globaler **Horizont-Sichtbarkeitsfilter** (`first_close_offset_days` ≤ Primary-Horizont, offene Positionen ausgeblendet); **Recent-Trades-Tab** für Monitoring offener Positionen (Mark-to-Market `paper_return_pct`, umgeht Horizont-Filter); **Score-Analyse-Tab** (Modul 9.x, `app/score_correlation.py`; bis 2026-07-03 zwei Tabs Score-Korrelation + Score-Analyse) zur `trade_score`-Validierung gegen den signierten Objective Return `post_return_{X}d` (Spearman als Rang-Pearson ohne scipy; umgeht den Horizont-Filter inkl. held; Kennzahlen + Scatter + Score-Buckets [Score 0 separat, Rest in festen 5er-Schritten] + Pro-Signal-Korrelation, alles filterbar nach Kammer/Partei, eigener Tab-Horizont mit Optionen 30…365d); Voll-Recompute bei Signal-Ausschluss; Sidebar-Form **„Score-Gewichte"** (alle `SCORING_WEIGHTS` + Tier-Editor, Preset-Persistenz `data/config/scoring_presets.json`); **Member-Detail-Popup** (Klick auf Members-Tab-Zeile → `@st.dialog`, live pro Member berechnet — keine neuen Master-Spalten/Pipeline-Lauf — Übersicht/Performance/Verläufe/Timing inkl. Spaghetti-Charts, Red-Flag-Ampel, Google-Namens-/Ticker-Links); Trades-Tab-Ticker-Spalte als Google-`LinkColumn`; alle Zeit-/Horizont-Widgets tragen Scope-Präfixe **[Global]/[Tab]/[Detail]** (Sidebar global, Tab-eigene Fenster tab, Popup-Controls detail); Member-Detail-Popup nutzt die **volle Member-Historie** (Sidebar-Zeitfenster wirkt nicht, Quartal-Slider = einziges Zeitfilter). Start: `streamlit run app/streamlit_app.py`. Datenspalten + Modul-Details: `DATA_STRUCTURE.md` (App-Konfiguration/Scope: Abschnitt „7b"; Member-Detail: „7c"; Score-Analyse: „7d"; Abnormal-Return-Anzeige: „7e"); Scoring: `Filter_Instanz_Parameter.md`.

**In Arbeit — Committee Matcher V2:** Der frühere Agent unter `scripts/committee_mapping_agent/` wurde vollständig entfernt (P1). Der bestehende Produktionsmaster bleibt bis zur separat freizugebenden V2-Integration unverändert; Schutzbaseline dafür: `data/config/committee_matcher_protected_baseline.yaml`. **P0 (Inputbaseline) abgeschlossen**: 46 Mapping-MDs auf `unit_input_basis` migriert (`input-basis-v1`, idempotent), `data/committee_matcher_inputs/corpus_manifest.yaml` erzeugt (46 `mapping_input` + 2 `source_only` + 46 `raw_source` + 1 `administrative_excluded` = 95 Dateien), Unit-Sollmenge 179 + 31 + 15 = **225** eingefroren. Skripte: `scripts/committee_matcher_migrate_inputs.py`, `scripts/committee_matcher_baseline.py` (CLI: `CLI_Bedienungsanleitung.md` 7c; Artefakte/Spalten: `DATA_STRUCTURE.md` 3a). **P2 (providerfreies Gerüst) abgeschlossen**: Paket `committee_matcher/` mit Ownership-Karte für 17 Zielmodule (Docstring in `__init__.py`); implementiert sind `status.py` (Workflowzustände + 11 Exit-Codes), `errors.py` (Befundsammlung + Terminalbericht), `paths.py` (Pfadwache, Schreiben nur unter `data/committee_matcher_v2/`, fail closed ohne Schutzbaseline), `settings_view.py` (typisierte `COMMITTEE_MATCHER_*`-Sicht, Providergate bis Gate B), `models.py` (Pydantic-Verträge inkl. formaler Mappingregeln 0/1–2/3–5) und `cli.py` (`python -m committee_matcher`, alle Familien aus Plan 2.7). CLI + Settingstabelle: `CLI_Bedienungsanleitung.md` 15. **P3 (Input, Identität, Roster) abgeschlossen**: `input_corpus.py` (strikter Parser, Manifestverifikation, Fremdanweisungsgate `#userinput`), `units.py` (Kanonisierung `unit-id-v1` + Inputhash `mapping-input-v1`, per Golden Test eingefroren; 225 IDs in `data/config/committee_matcher_unit_ids_v1.yaml`), `roster_coverage.py` (Coverage gegen House-/Senate-Roster, Joint-Cross-Chamber-Regel, Regeldatei `data/config/committee_matcher_roster_rules.yaml`). Läuft über `python -m committee_matcher input validate|roster validate` (Exit 2 wegen bewusst offener Warnungen: 3 Rostervorkommen ohne Unit, 2 Units ohne Rostervorkommen, 2 abgeschnittene Jurisdiktionstexte); Details `CLI_Bedienungsanleitung.md` 15a/15b. Nächste Phase: P4 (yfinance-Snapshot); Gate B und alles danach gesperrt. Grundlage: `organisation/committee_matcher_planung_entscheidungen_codex.md` und `organisation/committee_matcher_v2_implementierungsplan.md`.

**Abgeschlossen — Abnormal Return (Discovery + Integration):** `scoring/abnormal_returns.py` (signierter, marktbereinigter BHAR vs. SPY; 2 Konstrukte: A = `transaction_date`, B = `disclosure_date`; member-geclusterter Block-Bootstrap ohne scipy). **Deskriptiv — KEIN Scoring-Input.** Zwei Nutzungen: (1) **produktive Pipeline-Stage `abnormal_returns`** (`scripts/import_trades.py`, Full-Run [9b]/Delta [10b], `--stage_abnormal_returns`) schreibt die Konstrukt-A-Spalten `signed_ar_{30,60,90,120,180}d` in den Master; Anzeige NUR in den Detail-Popups, AR-Horizont dort per UI wählbar (`st.radio`, Optionen `AR_HORIZONS_DAYS`, Default `AR_HEADLINE_HORIZON_DAYS`/120d — gilt für Trade- UND Member-Detail: Trade-Detail: AR {H}d + zweiseitiger Perzentil-Rang gegen alle Trades + Roh-vs-AR-Tabelle; Member-Detail: Median-AR {H}d + Ausreißer-Quote pos/neg; Helper in `app/member_metrics.py`). Outlier-Flag/Rang bei Anzeige abgeleitet (nicht im Master). (2) **Standalone-Analyse** `scripts/analyse_event_study.py` (Diskriminator + Konstrukt B + Bootstrap, kein Master-Schreiben). Master-Backfill (`--stage_abnormal_returns`) durchgelaufen — `signed_ar_*d`-Spalten sind im Master befüllt (NaN nur bei Trades, die den jeweiligen Horizont noch nicht erreicht haben). Spalten/Anzeige: `DATA_STRUCTURE.md` Abschnitt 7e; Settings: `AR_*`-Block in `config/settings.py`.

---

## Wichtige Dateien (Einstieg)

**Pipeline-Einstieg:** `scrapers/quiver_scraper.py` (Auto-Download Congress-Trading-XLSX, täglich 03:00 via systemd, Exit-Codes 0/1/2/3) → `scripts/import_trades.py` (Haupt-Import-Pipeline, nach jedem Scraper-Lauf). Scheduler-Vorlagen + README: `deploy/systemd/`. House/Senate-Scraper s. Import-Workflow unten.

**Code-Konvention Gate / Tag / Signal:** `gates/` = binäre Hard-Ausschlüsse · `tags/` = Kontext-Anreicherung mit Master-Spalten · `signals/` = User-sichtbare Score-Beiträge. Instanzen + Parameter + Score-Formel: `organisation/Filter_Instanz_Parameter.md`.

**Config:** `config/settings.py` (alle konfigurierbaren Parameter), `config/committee_mapping.py` (O(1) Subcommittee→Sektor-Lookup).

**Utils, `scoring/`, App-Module + alle Datendateien** (Zweck, Spalten, Beispiele): `organisation/DATA_STRUCTURE.md`; Ticker/Issuer-Architektur: `organisation/Ticker_Architektur.md`. Modul-8-Scoring-Formel (`scoring/asset_volume.py`, `scoring/trade_score.py`): `Filter_Instanz_Parameter.md`. `utils/openfigi_client.py` = LEGACY (Rebuild T4 entfernt sie).

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
- **Spalten** → `DATA_STRUCTURE.md` · **Gate/Tag/Signal + Score** → `Filter_Instanz_Parameter.md` · **Pipeline-/CLI-Abläufe** → `CLI_Bedienungsanleitung.md` · **Signal-Schema** → `tool_specific_md/signal_info.md` · **Ticker/Issuer** → `Ticker_Architektur.md` · **Phasen-Historie** → `CHANGELOG.md` · **Offene Pläne** → `~/.claude/plans/*.md`
**Voller Doku-Index + „Aktualisieren bei"-Trigger pro Datei:** `organisation/DOKU_INDEX.md` (Single-Source-of-Truth-Konvention + Routing-Quelle für Regel 4 und Skill `/doku-update`).
