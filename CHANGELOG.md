# Changelog

Historie abgeschlossener Module/Phasen. **Aktueller Status** steht in `CLAUDE.md` (Modul-Tabelle). Detail-Specs zu offenen Arbeiten in `~/.claude/plans/`.

## Konvention

- Eine Phase pro Block, Datum (ISO), Kurz-Beschreibung, Test-Stand.
- Keine Wiederholung der laufenden Doku — Code-Referenzen nur, wenn dauerhaft sichtbar (z. B. neue Util-Datei).
- Reihenfolge: neuestes oben.


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
