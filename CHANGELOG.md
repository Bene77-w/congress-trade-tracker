# Changelog

Historie abgeschlossener Module/Phasen. **Aktiver WIP-Modul-Stand** steht im „Aktueller Stand (WIP)"-Block von `AGENTS.md`; Datei-/Modul-Historie via `git log --oneline -- <pfad>`. Detail-Specs zu offenen Arbeiten in `~/.claude/plans/`.

## Konvention

- Eine Phase pro Block, Datum (ISO), Kurz-Beschreibung, Test-Stand.
- Keine Wiederholung der laufenden Doku — Code-Referenzen nur, wenn dauerhaft sichtbar (z. B. neue Util-Datei).
- Reihenfolge: neuestes oben.


---

## 2026-06-26 — Event-Study / Abnormal-Return-Modul (Insider-Aufdeckung, Discovery)

**Motivation:** Der rohe Kalender-Return (`post_return_{X}d`) ist kein scharfes
Lineal fur den Informationsvorsprung von Kongressmitgliedern — Marktbewegungen
(SPY) ueberlagern das Signal. Ein marktbereinigtes Lineal (signed BHAR) sollte
echter Alpha-Extraktion naherkommen.

**Gebaut:**
- **Neu** `scoring/abnormal_returns.py`: Berechnungs-Modul. Metrik pro Trade/Horizont H:
  `signed_ar = sell_sign × ( P_ticker(t0+H)/P_ticker(t0) − 1 − ( P_SPY(t0+H)/P_SPY(t0) − 1 ) )`.
  Zwei Konstrukte: A (Anker `transaction_date`, Spalten `signed_ar_{30,60,90,120,180}d`) und
  B (Anker `disclosure_date`, Spalten `signed_ar_disc_{30,90}d`). Reuse der Preis-Primitive aus
  `scoring/post_trade_returns.py`. Bootstrap-Helper `clustered_bootstrap_mean/diff` (member-geclustert
  uber `bio_guide_id`, ohne scipy).
- **Neu** `scripts/analyse_event_study.py`: eigenstandiges Analyse-Skript (keine Flags). Liest
  `trades_master_complete.csv`, berechnet AR in-memory, schreibt Report
  `organisation/analyse_event_study.md` + `data/processed/ar_outliers.csv`.
- **Geandert** `config/settings.py`: neuer `AR_*`-Block (Benchmark, Horizonte, Ausreißer-Schwellen,
  Bootstrap-Parameter).

**Architektur:** Keine Pipeline-Stage, kein Schreiben in den Master. AR-Spalten existieren nur
in-memory. Settings-Aenderung = Skript neu starten (nur Cache-Lookups).

**Kern-Befund (H=90, member-geclusterter Bootstrap):** Spearman(score, `signed_ar_90d`) = −0.0029
(roh: −0.0006) — ebenfalls ~0. Kein Signal hat einen statistisch belastbaren Edge (alle 95%-CIs
umschliessen 0). Die `committee_ex_officio`-Signifikanz aus dem Vorganger-Report (t−2.55) war ein
**Clustering-Artefakt** (Trade-Level-Bootstrap ohne Member-Clustering). Das +/−3%-Ausreißer-Band
flaggt 38% der Trades (kein scharfer Tail; heavy tails, p95 ≈ +/−17,5%). Scope dieses Reports:
nur 90d-Kalender-Lineal — Event-verankerte Lineale (Votes/Konstrukt C) aufgeschoben.

**Artefakte:** `organisation/analyse_event_study.md` (Report, Single-Source der Befunde),
`data/processed/ar_outliers.csv` (gitignored). Spalten + Metrik: `DATA_STRUCTURE.md` Abschnitt 7e.

---

## 2026-06-14 — Netz-Robustheit-Härtung (NetworkCanary)

**Motivation**: Ein leeres yfinance-Ergebnis (HTTP 200, keine Exception) ist
nicht von einem echten Delisting unterscheidbar. Bisher wurde aus diesem
mehrdeutigen Ergebnis zirkulär auf den Netz-Zustand geschlossen
(`_network_healthy`-Heuristik) — ein Netzwerkfehler konnte so fälschlich als
„delisted"/NaN persistiert werden.

**Lösung**: Out-of-Band-Gesundheitssignal über ein neues NetworkCanary-
Verdikt-Orakel.

- **Neu** `utils/network_canary.py`: probt garantiert-live Ticker
  (SPY/AAPL/MSFT) als unabhängiges Signal, GRÜN/ROT-Verdikt TTL-gecacht
  (60s), `verdict()`/`force_reprobe()`/`wait_then_reprobe()`.
- **Neu** `utils/network_retry.py`: zentraler `retry_call`-Helfer (R1-R6)
  mit typ-basierter transient-Klassifikation, Backoff, `NetworkRetryExhausted`.
- **`scoring/post_trade_returns.py`**: Variante-B-Fenster-State-Machine —
  ganzes Preis-Fenster vorläufig, ROT/Throttle (inkl. HTTP 429) → gesamtes
  Fenster verworfen + ab Fenster-Beginn im selben Lauf neu verarbeitet
  (rückwirkende Invalidierung). Fail-closed nach `CANARY_MAX_RED_ROUNDS` (30)
  ROT-Runden → `RuntimeError`, keine degradierten Daten geschrieben.
- **Marker-Schema-Wechsel** im Preis-Cache: Delisted-Tombstone war
  `(ticker, date=1900-01-01, close=NaN)`, jetzt
  `(ticker, last_checked_date=heute, close=NaN)`. Erkennung über NaN-close.
  Alte 1900-Marker migrieren automatisch. Neue `last_checked`-TTL
  (`POST_TRADE_DELISTED_RECHECK_DAYS = 30`). E5: Marker-Prune bei echten
  Kursen + Marker-Kollaps (nur jüngster NaN-Marker je Ticker).
- **`exit_status='delisted'`** wird jetzt aus dem Preis-Lookup abgeleitet
  (None ⇒ delisted) statt aus rohem 1900-Marker-Slice — heilt zuvor „stuck"
  gebliebene `delisted`-Werte selbst (Befund-2-Fix).
- **`utils/ticker_sector.py`**: Canary als Verdikt-Autorität (ersetzt
  `_network_healthy`, jetzt LEGACY); vor dem Schreiben von
  `delisted_trades_stage.csv` wird eine frische `force_reprobe()` erzwungen.
- **R1-R6**: `utils/ticker_normalizer.py` + `utils/issuer_group_mapper.py`
  auf zentrale transient-Klassifikation + `retry_call` umgestellt.

**Neue Settings** (`config/settings.py`): `CANARY_TICKERS`,
`CANARY_MIN_POSITIVE`, `CANARY_VERDICT_TTL_SECONDS`, `CANARY_PROBE_BACKOFFS`,
`CANARY_MAX_RED_ROUNDS`, `POST_TRADE_DELISTED_RECHECK_DAYS`.
`POST_TRADE_NETWORK_HEALTHY_TOLERANCE` als LEGACY markiert.

**Tests**: 948 passed (2 vorbestehende, fremde Scraper-Failures außerhalb
des Scopes). Details: `organisation/Briefing_stage_post_trade_performance.md`,
`organisation/Ticker_Architektur.md`, `organisation/Pipelines.md`,
`organisation/Preis_Aktualisierung.md`.

---

## 2026-06-12 — Committee-Quelldaten F2 + Ex-Officio-Signal v2

**Motivation:** Das alte `Member_Committees_Full`-Format war mehrdeutig (`;`
trennte Committees UND Subcommittees) — der Pipeline-Parser verlor
systematisch Subcommittee→Sektor-Zuordnungen (z. B. Walberg 4 statt 20
Sektoren). Lösung: Committee-Quelldaten von Grund auf neu erzeugt im
eindeutigen **F2-Format**, plus Überarbeitung des Ex-Officio-Bonus von
binär/member-global auf **rollen- und ebenen-differenziert**.

**F2-Format** (Spec: `scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md`):
`;` trennt nur noch Committees, `:` leitet die Sub-Liste ein, `~` (NEU)
trennt Subcommittees, `|` trennt Name von Rolle. Rollen sind ebenen-abhängig
(`Chairman`/`Vice Chairman`/`Ranking Member`/`Ex Officio`/`Cochairman`/leer
auf Committee-Ebene; `Chair`/`Vice Chair`/`Ranking Member`/`Ex Officio`/leer
auf Sub-Ebene).

**Producer-Wechsel:**
- **Senate:** alter PDF-Parser (`scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py`)
  retiriert. Neu: `scrapers/senate_committee_xml/fetch_senate_xml.py`
  (dynamische Committee-Discovery aus senate.gov-Dropdown, curl_cffi
  Chrome-Impersonation gegen Akamai-403, 26 XML-Snapshots committet nach
  `scrapers/senate_committee_xml/raw/*.xml` + `_manifest.json` + Change-Report)
  → `scrapers/senate_committee_xml/parse_senate_xml.py` (XML→F2-CSV, 100
  Senatoren, Name „Last, First", Rollen-Mapping XML-`position`→F2
  ebenen-abhängig).
- **House:** `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py`
  generalisiert auf alle 20 Standing + 2 Select (Intelligence mit 6 Subs,
  China) + 4 Joint Committees (House-Hälfte, Senatoren kommen aus dem
  Senate-XML); 436 Member, 26 Committees. Parser-Änderungen:
  `LEFT_COL_THRESHOLD` 330→318; Select-Sub-Header sz 8.0 (statt 7.5)
  ALL-CAPS zentriert; 2-zeilige Committee-Header werden gemergt
  (China-Select); dedizierter `parse_joint`-Pfad mit HOUSE/SENATE-Spalten-Label
  pro Committee (Flip real, kein Ranking-Member bei Joints); Ex-Officio-
  Erkennung fordert jetzt das Wort „officio" (False-Positive-Fix
  „may attend"/„authorized to sit").
- Neu: `scrapers/generate_committee_structure.py` erzeugt
  `data/processed/committees_structure.csv` (Chamber, Committee,
  Subcommittee) aus beiden Quellen.
- Producer-Outputs `data/mappings/{house,senate}_member&(sub)committees.csv`
  (437/101 Zeilen) bleiben namens- und spaltengleich, Inhalt jetzt F2.

**Reader/Tag/Signal/Scoring/UI v2:**
- Neuer zentraler F2-Reader `utils/committee_parser.py` — einziger
  kanonischer Parser für `Member_Committees_Full`. `app/member_metrics.py`
  re-exportiert von dort; `tags/committee_tag.py::CommitteeParser` nutzt ihn
  ebenfalls (alte verlustbehaftete Abflachung entfernt).
- `tags/committee_tag.py`: jeder Sektor-Eintrag in `Insider_Sectors`/
  `Relevant_Sectors` trägt jetzt `committee_role` UND `subcommittee_role`
  statt eines vermischten `role`.
- Neue Master-Spalte `ex_officio_role_keys` (`;`-getrennt, z. B.
  `committee_chairman;subcommittee_ranking_member`), erzeugt vom CommitteeTag
  in Stage `gates_tags`.
- `signals/committee_ex_officio_signal.py` v2: triggert nur noch, wenn ≥1
  relevanter Sektor-Treffer eine Führungsrolle trägt (statt global
  `Member_ExOfficio`).
- `scoring/trade_score.py` + `config/settings.py`: `SCORING_EX_OFFICIO_BONUS`
  (alt, einzelner Wert 10) ersetzt durch `SCORING_EX_OFFICIO_ROLE_BONUSES`
  (8 Role-Keys, Werte 8–15) + `SCORING_EX_OFFICIO_COMMITTEE_GLOBAL` (Toggle).
  Bonus = MAX über die Boni der vorhandenen Role-Keys (nur wenn ein
  Trigger-Signal getriggert hat). Breakdown-Token unverändert `ex_officio:+N`.
- Streamlit Sidebar-Form „Score-Gewichte": Signal-Gewichte jetzt pro Signal
  in `st.expander` gruppiert; eigener Expander „committee_ex_officio" mit
  8 `number_input` (ein Bonus pro Role-Key) + Committee-Global-Toggle.
  Presets/Recompute wachsen entsprechend.

**Debugging-Fixes:** `config/committee_mapping.py::_normalize` strippt jetzt
führendes `the ` nach „committee on "-Präfix (Befund: „Committee on the
Judiciary" → 17 statt 0 Sektoren) und kollabiert innere Mehrfach-Whitespaces
(Quell-XML enthält Doppel-Leerzeichen, z. B. SSBK-Sub); Producer härten
Whitespace ebenfalls.

**Stage-Sequenz-Korrektur:** Nach einer Roster-/Committee-Daten-Änderung ist
die korrekte Single-Stage-Re-Run-Sequenz `--stage_committees →
--stage_gates_tags → --stage_branch1_signals → --stage_sequence_signals →
--stage_swarm_signals → --stage_scoring (+ --regenerate_curated)` — ohne die
anschließenden sequence/swarm-Stages verschwinden Sequenz-/Schwarm-Signale
aus dem Master, da `--stage_branch1_signals` `signals_triggered` von Grund
auf neu baut. Details: `organisation/Pipelines.md`,
`organisation/CLI_Bedienungsanleitung.md`.

**Validierung:** Struktur-Orakel House 0 Abweichungen (26 Committees,
Joint-Headcounts handverifiziert), Senate 70/70 Sub-Paare; Regression nur
Real-World-Delta; Testsuite 774 passed (2 vorbestehende Scraper-Failures,
nicht aus diesem Modul). Pipeline-Probelauf (112.728 Trades):
committee/committee_full-Signale 10.571→**18.702 (+77 %)**;
committee_ex_officio 10.571 (binär)→9.594 (rollenbasiert); Score-Summe
1.553.123→1.668.977; Stichproben: Walberg 4→20 Insider-Sektoren, Gottheimer
Intelligence korrekt eigenes Committee.

**Tests:** `tests/test_senate_xml_parser.py`, `tests/test_committee_tag.py`,
`tests/test_committee_mapping.py` (erweitert), Anpassungen in
`test_member_metrics.py`, `test_branch1_signals.py`, `test_trade_score.py`,
`test_score_recompute.py`.

---

## 2026-06-07 — Member-Detail-Popup (Modul 9 Iteration)

**Neues Feature im Streamlit-Dashboard:** Klick auf eine Zeile im Members-Tab
öffnet ein `@st.dialog`-Popup, das einen einzelnen Kongress-Abgeordneten
umfassend auf Insider-Trading-Auffälligkeit auswertet. Alles wird **live pro
Member berechnet** — keine neuen Master-Spalten, kein Pipeline-Lauf.
Datenbasis: gefiltertes Trade-Subset (`apply_prefilter(...,
apply_horizon_filter=False)`), bei aktivem Signal-Ausschluss per
`recompute_subset` neu gescort.

- **Phase 0 — reine Metrik-Logik + Tests:** neues Modul `app/member_metrics.py`
  (~563 Zeilen, kein Streamlit-Code): Committee-Parsing
  (`parse_committees_full`, eigener verschachtelnder Parser, bewusst nicht der
  kanonische `CommitteeParser`), Horizont-Attribution (`nearest_horizon` +
  Mittelpunkts-Schwellen 45/75/105/150 Tage, vektorisiert via
  `_nearest_horizon_array`), Verlaufs-/Spaghetti-Aggregation
  (`compute_member_trajectories`, Cap bei 180 Tagen, Ende am Voll-Close —
  bewusste Divergenz zur Horizont-Attribution), Sektor-Konzentration,
  Chart-Aggregationen und Controls/Helfer. 29 Tests grün
  (`tests/test_member_metrics.py`).
- **Phase 1–4 — Popup-UI:** neues Modul `app/member_detail.py` (~498 Zeilen,
  Plotly + Streamlit), Einstieg `render_member_detail`. Header mit
  verschachteltem Gremien-Panel + Google-Namenssuche; Controls
  (Quartal-Range-Slider, „Nur ≤180d"-Toggle, Schwellwert-x-Slider); 4 Tabs
  Übersicht/Performance/Verläufe/Timing mit Diagrammen D1/D2/D5/D6/D7/Z3 sowie
  Spaghetti-Charts D3b/D4 mit bidirektionalem Highlight (Liste ↔ Plotly via
  `on_select`); Timeline mit Vote-Timing-Markern; Red-Flag-Ampel.
- **Phase 5 — Trades-Tab Ticker-Links:** Ticker-Spalte im Trades-Tab als
  Google-`LinkColumn` (neue Helfer `_ticker_google_url`,
  `_TICKER_LINK_REGEX`; `_trade_column_config(..., ticker_as_link=False)`).
  Recent-Trades-Tab unverändert (TextColumn).
- **Settings:** neuer Parameter `DETAIL_RETURN_THRESHOLD_PCT = 10.0` —
  reiner Anzeige-Schwellwert (Anteil Trades mit `paper_return_terminal > x`
  für D1/D7), live im Popup per Slider überschreibbar, kein Recompute.
- `apply_prefilter` um Parameter `apply_horizon_filter` erweitert.

**Details/Funktionsgruppen:** `organisation/DATA_STRUCTURE.md` (Abschnitt
„7c. App-Module: Member-Detail-Popup"); Beispiele/Verhalten:
`tests/test_member_metrics.py`.

---

## 2026-06-04 — Post-Trade Exit-Detection Entkopplung + Horizont-Sichtbarkeitsfilter + Partial-Close-Zerlegung

**Modul 5 Refactor:** Exit-Fenster separiert vom Return-Horizont. Settings.EXIT_DETECTION_WINDOW_DAYS (Default None=unbegrenzt) bestimmt, wie weit die Exit-Suche reicht; Settings.POST_TRADE_HORIZONS_DAYS (Default [30,60,90,120,180]) bleibt für Return-Snapshots. Folge: 58.572 closed_full (vorher 29.642), 32.722 held (vorher 60.927) bei 112.692 Trades — deutlich realistische Exit-Detektionen.

**Semantik-Änderungen (Modul 5):**
- `effective_return_pct` jetzt REALISIERT-ONLY: nur bei closed_* gesetzt (vorher held=längster Horizont-Snapshot). Bei held/delisted/unknown/not_applicable → NaN.
- `effective_return_horizon_days` jetzt CLOSED-ONLY: nur bei closed_* gesetzt. Bei held → NaN (vorher: Horizont-Tage des Snapshots).
- Neue Spalte `first_close_offset_days`: Tage bis erstem Gegen-Trade. Basis für Horizont-Sichtbarkeitsfilter. NaN bei held/not_applicable/delisted/unknown.
- 180-Tage-Horizont: `post_price_180d`, `post_return_180d` dynamisch mit POST_TRADE_HORIZONS_DAYS (Smoke-Run 27.05.2026 → 28.06.2026: neue Spalten).

**Modul 9 Feature:** Horizont-Sichtbarkeitsfilter im Dashboard. Sidebar-Toggle (Standard an) filtert global (alle 4 Tabs) auf `first_close_offset_days ≤ primary_horizon`. Beispiel: beim 30d-Horizont sieht man nur Trades, deren Exits in 30 Tagen lagen; held bleiben immer sichtbar.

**Display-Feature:** Post-Trade-Performance-Tab — neue Partial-Close-Zerlegungs-Tabelle (Build-Funktion `build_partial_close_rows` in `app/chart_helpers.py`). Zerlegt Öffnungs-Trade mit `closed_partial` in mehrere Display-Zeilen (eine pro Close-Event, trade_id `{parent}-pc{n}`, neue Spalte `parent_trade_id`). Rein UI-Ausgabe, NICHT in Master persistiert.

**Tests:** 55 Modul-Tests grün (`test_post_trade_returns.py`: held→NaN angepasst + neue Tests für entkoppeltes Fenster/Cap/first_close_offset_days; `test_chart_helpers.py`: 7 neue Tests für `build_partial_close_rows`).

**Beispiel-Verifikation:** Ankhor Spanberger/LHX (Kauf 2022-09-01): vorher held mit −8,01% (120d-Snapshot), jetzt closed_full mit −6,26% realisiert (first_close_offset 182 Tage).

---

## 2026-05-28 — Ticker-System Vereinfachung (Preferred-Auto + Routing-Konsolidierung + Slash-Fallback Removal)

- **Säule 1 — Preferred-Auto-Resolution:** `TickerClassification.parent_ticker` neu gesetzt auf Common-Stamm für 6 PREFERRED-Notationen (z.B. `BAC-PRK` → `parent_ticker='BAC'`). Klassifikator + Sektor-Stage setzen automatisch Fallback; Coverage 100% der 38 Preferreds im Master. Manuelle CSV `Vorzugsaktien_mapping.csv` → obsolet (Datei gelöscht).
- **Säule 2 — Routing-Konsolidierung (R2.c):** Neue zentrale Datei `utils/ticker_routing.py` mit `SECTOR_LOOKUP_TYPES` (5 Typen) + `POST_TRADE_PRICE_TYPES` (4 Typen, ohne NUMERIC_SUFFIX). Konstante `_YF_ELIGIBLE_TYPES` entfernt. `api_strategy` bleibt intern in `TickerClassification`, wird NICHT mehr persistent in `trades_master_complete.csv` geschrieben. Interne Spalte `MASTER_INTERNAL_COLUMNS` eingeführt; `_strip_master_internal()` entfernt `api_strategy` vor allen CSV-Saves (4 Stellen).
- **Säule 3 — Slash-Fallback Removal (R4):** Automatischer Retry `BRK-A` → `BRK/A` entfernt (Empirisch: 0 Cache-Treffer in 2.730 Einträgen). Code war messbar tot.
- Daten-Dateien gelöscht: `data/mappings/Vorzugsaktien_mapping.csv`, `data/processed/unmatched_preferred.csv` (Output nicht mehr generiert).
- 652 von 654 Tests grün (2 vorbestehende Failures, unabhängig).
- Doku-Updates: `organisation/DATA_STRUCTURE.md`, `organisation/Pipelines.md`, `organisation/manual_review_anleitung.md`, `organisation/Ticker_Architektur.md`.

---

## 2026-05-21 — Committee-Score Vereinheitlichung

- Spalte `Relevance_Score` aus `trades_master.csv` entfernt; vier Settings
  (`COMMITTEE_THRESHOLD`, `COMMITTEE_SECTOR_MATCH_POINTS`,
  `COMMITTEE_INDUSTRY_MATCH_POINTS`, `COMMITTEE_EX_OFFICIO_MULTIPLIER`)
  gestrichen.
- `Relevant_Sectors`-JSON pro Eintrag um `match_type` (`'full'`/`'sector'`) erweitert.
- Committee-Signal-Trigger: `len(Relevant_Sectors) > 0` (vorher Punkte-Schwelle).
- Neues Signal `committee_full` triggert nur bei mind. einem Eintrag mit `match_type='full'`.
- `SCORING_WEIGHTS`: `committee` 15→10, `committee_full` +5 (Voll-Treffer = 15 wie bisher).
- `suspicious_events.csv`: Spalte `relevance_score` → `match_count`; Migration via
  `_migrate_suspicious_events_schema` beim nächsten Pipeline-Lauf.
- 100 Branch1+Scoring-Tests grün. Pipeline-Re-Run notwendig.

---

## 2026-05-21 — Modul 9 Iteration: reaktive Aggregate + relevant_sectors-Spalte

Asset- und Member-Tab aggregieren jetzt on-the-fly aus dem gefilterten Trade-Subset
(`suspicious_assets.csv` / `suspicious_members.csv` werden nicht mehr geladen).
Alle Sidebar-Filter (Datum, Member-Suche, Ticker-Suche, Signal-Ausschluss) wirken
reaktiv; Min-Score greift auf `score_max` des Aggregats. KPI-Zeile (Anzahl,
Max-Score, Σ Volumen) erscheint in allen drei Tabs. Trade-Tab hat neue abgeleitete
Spalte `relevant_sectors` (eindeutige `sector`-Namen aus `Relevant_Sectors`-JSON).

**Neue Helper in `app/streamlit_app.py`:** `_parse_relevant_sectors`, `_unique_join`,
`_unique_signals`, `aggregate_assets`, `aggregate_members`.

**Smoke-Test:** 2000-Row-Subsample, Top-Asset BBG001S5N8V8 (trade_count=9,
score_max=683, sectors='Technology').

---

## 2026-05-20 — Modul 9 (Streamlit Dashboard MVP)

Drei neue Dateien: `app/__init__.py`, `app/score_recompute.py`, `app/streamlit_app.py`.

**Hauptfunktionen:**
- **Datumsbasis:** Radio `disclosure_date` / `transaction_date` (Default disclosure_date)
- **Zeitfenster:** Radio 7 / 30 / 365 Tage / Alles (Default 30 Tage)
- **Sortierung:** absteigend / aufsteigend nach `trade_score` (Default absteigend)
- **Signal-Ausschluss:** Multiselect über alle 9 Signale — löst Voll-Recompute via `scoring/trade_score.py` aus (Gewicht ausgeschlossener Signale = 0; `swarm_paradox` excluded → `SCORING_PARADOX_DAMPER=1.0`)
- **Member-Suche / Ticker-Suche:** Substring-Filter auf `name` bzw. `ticker`/`issuer_ticker`
- **Min-Score-Slider:** 0 .. max(50, max(trade_score))

**Drei Tabs:** Trades (KPIs + 16-Spalten-Tabelle) | Assets (suspicious_assets.csv) | Members (suspicious_members.csv)

**Start:** `venv/bin/streamlit run app/streamlit_app.py` → http://localhost:8501

**Smoke-Test:** 200 Test-Rows, Recompute + alle Filter fehlerfrei. Streamlit 1.57.0, Pandas 3.0.0.

---

## 2026-05-20 — Modul 6 (Vote-Timing) Status-Korrektur

Modul 6 war als „UNVOLLSTÄNDIG" markiert, ist aber seit Modul-7-Rebuild vollständig funktional. `VoteTimingTag` liest Roll-Calls direkt aus `bill_actions.json` (Floor Votes in `actions[*].roll_call[*]`) — kein separater Vote-Endpoint nötig (`fetch_congress_votes.py` ist nie wieder eingeführt worden).

**Smoke-Run-Verifikation (111.986 Trades):**
- 5.357 Trades mit `Vote_Timing_Hit=True` (4,8%)
- 0 Inkonsistenzen zwischen `Vote_Timing_Hit_Count ≥ 1` und `floor_action` in `signals_triggered`
- Score-Effekt: mean `trade_score` mit floor_action = 64,6 vs. 21,6 ohne

**Cleanup:**
- Leere Platzhalter-Files `data/congress_api/votes_house.json` und `votes_senate.json` entfernt
- Doku-Status in CLAUDE.md, README.md, Filter_Instanz_Parameter.md, settings.py-Kommentar aktualisiert

**Neue Tests:** `tests/test_vote_timing_tag.py` (~12 Tests).

---

## 2026-05-20 — Modul 8 Phase 8.6 (Voll-Pipeline-Smoke-Run)

Voll-Pipeline-Lauf produzierte `trades_master_complete.csv` mit allen 5 Modul-8-Spalten. Score-Verteilung über 111.986 Trades: mean=23.7, median=13, max=1705 (MSFT-Cluster Nov 2021), 80,5% Score > 0, 3.085 paradox-Warnings.

**Bug-Fix:** Stage-Reihenfolge: Suspicious-Export lief vor Save → `_load_trade_scores` las die noch nicht geschriebene `trades_master_complete.csv` → alle Score-Aggregate in `suspicious_assets.csv` / `suspicious_members.csv` fielen auf 0.

Lösung: In-Flight-Hand-off via `self._inflight_trade_scores`. `_load_trade_scores` priorisiert den DataFrame aus dem laufenden Run gegenüber dem File-Read. Im Delta-Mode wurde Save vor Suspicious-Export verschoben, damit `updated_master` als Override bereitsteht. Neuer Regression-Test `test_inflight_override_uebersteuert_file`. 16/16 Modul-8.4-Tests grün.

Nach Fix: Top-Member Michael McCaul mit `score_total=649.758` (44.289 Events, `score_max=1136`), Josh Gottheimer mit `score_max=1705`.

---

## 2026-05-19 — Modul 8 (Scoring-System), Phasen 8.1–8.5

| Phase | Artefakte | Tests |
|---|---|---|
| 8.1 Trade-Volume | `utils/amount_parser.py`, Stage `_stage_trade_volume`, Spalte `trade_volume_usd` | 21 grün |
| 8.2 Asset-Volume | `scoring/asset_volume.py`, Stage `_stage_asset_volume`, Spalte `asset_tradingvolume_usd` (rolling 90d über `issuer_group_key`, Option-A-Delta-Backfill) | 10 grün |
| 8.3 Trade-Score | `scoring/trade_score.py`, Stage `_stage_scoring`, Spalten `trade_score` / `trade_score_breakdown` / `trade_warnings`. Pipeline 16 Stages. | 23 grün |
| 8.4 Score-Aggregation | Helper `_load_trade_scores`, `_join_distinct_warnings`. `suspicious_assets.csv` +5 Spalten (`score_avg`/`max`/`total`, `total_volume_usd`, `warnings`). `suspicious_members.csv` +3 Spalten (`score_avg`/`max`/`total`). | 7 grün |
| 8.5 Doku-Migration | Sieben Doku-Dateien aktualisiert; Single-Source-Konvention etabliert. | — |

---

## 2026-05 — Modul 7 (Signal-Architektur), P1–P7

Gate/Tag/Signal-Konvention eingeführt (`gates/` / `tags/` / `signals/`).

| Phase | Inhalt |
|---|---|
| P1 | Architektur-Refactor: alte `filters/` → Drei-Klassen-Struktur |
| P2 | Branch-1 Per-Trade-Signale: `committee`, `committee_ex_officio`, `floor_action`, `extreme_report` |
| P3 | Sequenz-Signale: `contrarian`, `accumulative` |
| P4 | Suspicious-Export: `suspicious_events.csv` + Views `suspicious_assets.csv` / `suspicious_members.csv` |
| P5 | OpenFIGI-Coverage 82,7%; Spalte `share_class_figi` |
| P5.5 | Abgeleitete Spalte `issuer_ticker` (Share-Class-Level, Fallback-Kette `share_class_figi → ticker_base → ticker`) |
| P5.6 | Cross-Class Issuer Mapping via SEC EDGAR CIK: Stage `_stage_issuer_group`, Spalte `issuer_group_key` (Präfix `CIK:` / `TICKER:` / `SKIP:`). GOOGL+GOOG → `CIK:1652044`. |
| P6 | Schwarm-Signale (Branch 2): `swarm_buy`/`swarm_sell` (Greedy-Cluster), `swarm_conflict` (1 Gegen-Trade), `swarm_paradox` (≥2 Gegen-Trades, mutex zu conflict). Gruppiert nach `issuer_group_key`. 24.926 P6-Events. |
| P7 | Doku-Konsolidierung |

---

## 2026-04 / 2026-05 — Rebuild T1–T5 (Ticker-Architektur)

| Stufe | Inhalt |
|---|---|
| T1 | Foundation: `utils/trade_id.py`, `utils/ticker_classifier.py` (Offline-Regex-Klassifikator) |
| T2 | Network-Wrapper: `utils/ticker_normalizer.py` (OpenFIGI + SQLite-Cache), `utils/ticker_sector.py` (yfinance) |
| T3 | Integration in `scripts/import_trades.py` (Stage-Pipeline) |
| T4 | OFFEN — Migration alter Caches, `utils/openfigi_client.py` entfernen |
| T5 | P5 + P5.5 + P5.6 (siehe Modul 7) |

Architektur-Doku: `organisation/Ticker_Architektur.md`.

---

## Vor 2026-04 — Module 1–4

- Modul 1: Projektstruktur, venv
- Modul 2: House Trades Scraping (ZIP + XML + PDF). PDF Extractor v2 (OpenCV Grid + Pixel-Density + Vision API).
- Modul 3: Senate Trades Scraping (Playwright + DataTables API)
- Modul 4: Ausschuss-Sektor-Mapping (`config/committee_mapping.py`, 861-Row Mapping)

Module 5 (Yahoo-Kursdaten) und 6 (Roll-Call-Votes) übersprungen / unvollständig.
