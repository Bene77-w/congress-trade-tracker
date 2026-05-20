# Filter-Instanz-Parameter

**Letzte Aktualisierung:** 2026-05-19

Diese Datei dokumentiert die **konkreten Filter-Instanzen** des Congress Trade
Trackers. „Filter" ist hier das User-sichtbare Sammelwort — im Code wird
zwischen drei Klassen unterschieden:

| Code-Klasse | Rolle | Speicherort | Beispiele |
|-------------|-------|-------------|-----------|
| **Gate**   | binärer Hard-Ausschluss vor dem Scoring | `gates/`   | Asset-Class-Gate (User-Sicht: „Filter 0") |
| **Tag**    | Kontext-Anreicherung, schreibt Spalten an den Trade | `tags/`    | Committee-Tag („Filter 3"), Vote-Timing-Tag („Filter 4") |
| **Signal** | bewertender Score-Beitrag für die User-Empfehlung | `signals/` | Volumen, Schwarm, Long↔Short, Position+ (noch zu definieren) |

> **Lesehilfe:** „Parameter implementiert" = Konstanten/Schwellen liegen in
> `config/settings.py`. „Code implementiert" = das Modul in `gates/` bzw.
> `tags/` bzw. `signals/` existiert und schreibt die Output-Spalten in den Master.

---

## Aktive Instanzen (Code + Parameter implementiert)

### **Gate: Asset Class** (User-Sicht: „Filter 0")
**Status:** Parameter ✅ / Code ✅ — `gates/asset_class_gate.py` (`AssetClassGate`)

Schließt Nicht-Aktien (ETF, Option, Bond, Crypto, Unit, Warrant, when_issued, …)
vom Scoring aus. Schreibt `asset_class_excluded` in den Master. Behält per
Default `{stock, foreign_stock, shareclass}` (`settings.ASSET_CLASS_KEEP_TYPES`).

---

### **Tag: Committee** (User-Sicht: „Filter 3")
**Status:** Parameter ✅ / Code ✅ — `tags/committee_tag.py` (`CommitteeTag`)

Schreibt `Insider_Sectors`, `Relevant_Sectors`, `Relevance_Score` in den Master.
Match-Vergleich gegen `sector_yf` / `industry_yf`.

**Scoring-Formel (aktiv im Code):**
```python
sector_match   = (Committee-Sektor   == sector_yf)
industry_match = (Committee-Industry == industry_yf)
raw   = (33 if sector_match else 0) + (33 if industry_match else 0)
score = round(raw * (1.5 if ex_officio else 1.0))
# Mögliche Werte: 0, 33, 50, 66, 99
```

**Settings:**

| Konstante | Wert | Bedeutung |
|-----------|------|-----------|
| `COMMITTEE_SECTOR_MATCH_POINTS`    | 33  | Punkte bei Sektor-Match |
| `COMMITTEE_INDUSTRY_MATCH_POINTS`  | 33  | Punkte bei Industry-Match |
| `COMMITTEE_EX_OFFICIO_MULTIPLIER`  | 1.5 | Multiplikator bei Ex-Officio-Rolle |
| `COMMITTEE_EX_OFFICIO_ROLES`       | `[Chairman, Ranking Member, Vice Chairman, Cochairman, EXOfficio]` | Welche Rollen als Ex Officio zählen |

**Match-Modus** für `Relevant_Sectors` (Intersection Insider × Ticker):
- `'sector_or_industry'` — Eintrag matcht, wenn sector ODER industry zum Ticker passen *(Default)*
- `'sector_and_industry'` — Eintrag matcht nur, wenn sector UND industry passen
- `'hybrid'` — bevorzuge sector+industry-Matches, sonst Fallback auf `sector_or_industry`

→ `settings.RELEVANT_SECTORS_MATCH_MODE`

---

### **Tag: Vote Timing** (User-Sicht: „Filter 4")
**Status:** Parameter ✅ / Code ✅ — `tags/vote_timing_tag.py` (`VoteTimingTag`)

Erkennt Trades kurz vor / nach relevanten Abstimmungen via Congress.gov API.

| Parameter | Wert | Settings |
|-----------|------|----------|
| Fenster VOR Abstimmung   | 30 Tage | `VOTE_TIMING_WINDOW_BEFORE = 30` |
| Fenster NACH Abstimmung  | 5 Tage  | `VOTE_TIMING_WINDOW_AFTER = 5` |
| Min-Konfidenz Sektor-Match | 0.7    | `VOTE_MIN_POLICY_CONFIDENCE = 0.7` |
| Kongress                 | 119th   | `VOTE_CONGRESS_NUMBER = 119` |
| Auto-Refresh-TTL         | 24 h    | `VOTE_DATA_TTL_HOURS = 24` |

**Master-Output:** `Vote_Timing_Hit`, `Vote_Timing_Hits` (JSON, alle Treffer im
Fenster), `Vote_Timing_Hit_Count`.

**Datenquelle:** `data/congress_api/bill_actions.json` + `bill_subjects.json`
(gefetcht via `scrapers/fetch_congress_data.py`). Pre-Stage-Hook hält die Files
frisch (TTL = `settings.VOTE_DATA_TTL_HOURS`).

---

## Schwarm-Signale (P6, Branch 2)

Vier Signale auf Issuer-Group-Ebene (gruppiert nach `issuer_group_key` —
Issuer-Level via SEC EDGAR CIK, Cross-Class: GOOGL+GOOG sind ein Schwarm).
SKIP-Buckets (Options, Bonds, Cash) werden gebypassed.

| Signal | Trigger | scope_key | Event-Form |
|---|---|---|---|
| `swarm_buy`  | ≥ `SWARM_MIN_MEMBERS` distinkte Members kaufen denselben Issuer innerhalb `SWARM_WINDOW_DAYS` (Rolling-Greedy-Cluster, Anker = frühester Trade des Clusters) | `{igk}\|buy\|{window_start}` | 1 signal_event_id pro Cluster, N Event-Zeilen (eine pro beteiligtem Trade) |
| `swarm_sell` | Analog für Sells | `{igk}\|sell\|{window_start}` | wie buy |
| `swarm_conflict` | Bei aktivem Schwarm: GENAU 1 Gegen-Trade im erweiterten Fenster `[first_swarm - BEFORE, last_swarm + AFTER]`. Selbst-Umkehr (Member im Schwarm dreht später) zählt | `conflict\|{igk}\|{swarm_dir}\|{window_start}` | 1 Event-Zeile pro Konflikt-Trade |
| `swarm_paradox` | Bei aktivem Schwarm: ≥ `SWARM_PARADOX_MIN_CONFLICTS` Gegen-Trades im erweiterten Fenster. Mutually exclusive zu `swarm_conflict` pro Schwarm-Auslösung | `paradox\|{igk}\|{swarm_dir}\|{window_start}` | 1 signal_event_id mit N Event-Zeilen (eine pro Gegen-Trade) |

**Parameter (`config/settings.py`):**

| Parameter | Default | Wirkung |
|---|---|---|
| `SWARM_WINDOW_DAYS` | 7 | Rolling-Window für Schwarm-Cluster (Tage) |
| `SWARM_MIN_MEMBERS` | 2 | Mindestanzahl distinkter Members für Auslösung |
| `SWARM_GROUPING_KEY` | `'issuer_group_key'` | Aggregations-Spalte (Issuer-Level, P5.6) |
| `SWARM_TRACK_DIRECTIONS` | `['buy', 'sell']` | Welche Richtungen werden getrackt |
| `SWARM_FLAG_CONFLICTS` | `True` | Aktiviere conflict-/paradox-Erkennung |
| `SWARM_CONFLICT_WINDOW_DAYS_BEFORE` | 3 | Front-Running-Fenster vor erstem Schwarm-Trade |
| `SWARM_CONFLICT_WINDOW_DAYS_AFTER` | 3 | Nach-Reaktion-Fenster nach letztem Schwarm-Trade |
| `SWARM_PARADOX_MIN_CONFLICTS` | 2 | Schwelle: ab N Gegen-Trades wird `swarm_paradox` statt `swarm_conflict` erzeugt |

**Wichtige Design-Eigenschaften:**
- `swarm_paradox` ist symmetrisch: A+B kaufen + C+D verkaufen erzeugt 2 paradox-Events (eines aus Buy-Perspektive, eines aus Sell-Perspektive) — bewusste Doku-Trennung.
- `swarm_conflict` und `swarm_paradox` sind **pro Schwarm-Auslösung mutually exclusive** (entweder 1 Gegen-Trade → conflict, oder ≥ 2 → paradox).
- Append-only-Limitation: Wenn ein erster Lauf 1 Konflikt findet und ein späterer Lauf einen zweiten Gegen-Trade hinzunimmt, bleibt die alte `swarm_conflict`-Zeile in `suspicious_events.csv` neben dem neuen `swarm_paradox`-Eintrag. In `suspicious_assets.csv` dominiert paradox bei der Aggregation.

---

## Noch zu definieren: Signals (User-Sicht)

Diese Filter waren in der alten Spec enthalten und werden in Modul 8 (Scoring)
schrittweise integriert. Stand nach **Phase 8.1**:

| User-Sicht | Code-Konzept | Status |
|------------|--------------|--------|
| „Filter 1" — Handelsvolumen           | Volume-Komponente im Score-Modul | Foundation `trade_volume_usd` ✅ Phase 8.1, Aggregat `asset_tradingvolume_usd` ✅ Phase 8.2. Volume-Score-Berechnung folgt in Phase 8.3. |
| „Filter 2" — Schwarm-Verhalten        | Signal | ✅ **P6 implementiert** — siehe Schwarm-Sektion oben |
| „Filter 5" — Contrarian               | Signal | ✅ implementiert als Sequenz-Signal (P3) |
| „Filter 6" — Long ↔ Short Wechsel     | — | **verworfen** (phänomenologisch identisch mit `contrarian`) |
| „Filter 7" — Position vergrößert      | — | **verworfen** (phänomenologisch identisch mit `accumulative`) |

---

## Modul 8 — Scoring-System (in Arbeit)

**Status:** Phasen 8.1 ✅ + 8.2 ✅ + 8.3 ✅ + 8.4 ✅ + 8.5 ✅ (Stand 19.05.2026).
Pipeline jetzt 16 Stages. 61 Unit-Tests grün. Phase 8.6 (Voll-Pipeline-Smoke-Run) offen.
**Plan:** `~/.claude/plans/crispy-growing-pizza.md`.

### Score-Formel (aktiv, Phase 8.3)

```python
swarm_signals = {'swarm_buy', 'swarm_sell', 'swarm_conflict'}

swarm_part  = Σ WEIGHTS[s] für s in signals_triggered ∩ swarm_signals
if 'swarm_paradox' in signals_triggered:
    swarm_part *= SCORING_PARADOX_DAMPER         # default 0.0

other_part  = Σ WEIGHTS[s] für s in signals_triggered \ swarm_signals \ {swarm_paradox}

volume_part = max(0, asset_tradingvolume_usd - SCORING_VOLUME_THRESHOLD_U)
              * SCORING_VOLUME_SLOPE
if SCORING_VOLUME_ENABLED is False:
    volume_part = 0

bonus_active = is_ex_officio AND (signals_triggered ∩ SCORING_EX_OFFICIO_TRIGGERS ≠ ∅)
bonus        = SCORING_EX_OFFICIO_BONUS if bonus_active else 0

trade_score  = round(swarm_part + other_part + volume_part + bonus)
# KEIN Cap auf 100 — Score wächst unbegrenzt.
```

### Settings-Block (aktiv ab Phase 8.1, in `config/settings.py`)

| Konstante | Default | Bedeutung |
|---|---|---|
| `SCORING_WEIGHTS['committee']`       | 15    | Branch-1 Per-Trade |
| `SCORING_WEIGHTS['floor_action']`    | 15    | Aktiv. Triggert bei Vote_Timing_Hit_Count ≥ VOTE_TIMING_HIT_THRESHOLD (=1) |
| `SCORING_WEIGHTS['extreme_report']`  |  5    | Branch-1 Per-Trade |
| `SCORING_WEIGHTS['contrarian']`      |  8    | Sequenz-Signal |
| `SCORING_WEIGHTS['accumulative']`    | 10    | Sequenz-Signal |
| `SCORING_WEIGHTS['swarm_buy']`       | 20    | Branch-2 Cluster |
| `SCORING_WEIGHTS['swarm_sell']`      | 20    | Branch-2 Cluster |
| `SCORING_WEIGHTS['swarm_conflict']`  |  5    | Gegen-Trade-Hinweis |
| `SCORING_EX_OFFICIO_BONUS`           | 10    | Einmaliger Summand pro Trade |
| `SCORING_EX_OFFICIO_TRIGGERS`        | `['committee', 'floor_action']` | Welche Signale den Bonus aktivieren |
| `SCORING_PARADOX_DAMPER`             | 0.0   | Multiplikator auf Schwarm-Anteil bei `swarm_paradox` |
| `SCORING_PARADOX_WARNING_TAG`        | `'paradox_swarm'` | Tag in `trade_warnings`-Spalte |
| `SCORING_VOLUME_ENABLED`             | `True` | Volume-Komponente an/aus |
| `SCORING_VOLUME_THRESHOLD_U`         | 100_000 | USD-Schwelle, unter U: 0 Punkte |
| `SCORING_VOLUME_SLOPE`               | 0.00002 | Punkte pro USD über U (1M$ ≈ 18 Punkte) |
| `SCORING_VOLUME_WINDOW_DAYS_Z`       | 90    | Rolling-Window für asset_tradingvolume_usd |

**Konvention:**
- Signal via `SCORING_WEIGHTS[s] = 0` deaktivierbar (Event bleibt in
  `suspicious_events.csv`, beeinflusst aber den Score nicht).
- `swarm_paradox` hat kein additives Gewicht — wirkt ausschließlich als
  Multiplikator auf die Schwarm-Komponente.
- `committee_ex_officio` hat kein eigenes Gewicht — Leadership-Effekt läuft
  über den einmaligen Ex-Officio-Bonus.
- Warning-Tag bleibt unabhängig vom gedämpften Schwarm-Score in
  `trade_warnings` sichtbar.

### Trade-Spalten (aktiv ab Phase 8.1–8.3)

| Spalte | Quelle | Inhalt |
|---|---|---|
| `trade_volume_usd` | Stage `trade_volume` (`utils/amount_parser.midpoint_usd`) | int64, Mittelpunkt der Disclosure-Amount-Range in USD. Beispiele: `$1,001 - $15,000` → 8000; `Over $50,000,000` → 50_000_000; leer/NaN → 0. |
| `asset_tradingvolume_usd` | Stage `asset_volume` (`scoring/asset_volume.compute_asset_tradingvolume`) | int64, Σ `trade_volume_usd` über `issuer_group_key` im Fenster `[T.date − Z, T.date]` (Z = `SCORING_VOLUME_WINDOW_DAYS_Z`, Default 90). Tages-Aggregation; SKIP-Buckets = 0. Sanity-Check (111k Trades): median $72k, p95 $912k, max $83.5M. |
| `trade_score` | Stage `scoring` (`scoring/trade_score.compute_trade_scores`) | int, Combined-Score laut Formel. Kein Cap. Sanity-Check (111k Trades): mean=21.6, median=13, max=1705. |
| `trade_score_breakdown` | Stage `scoring` | `;`-getrennt, deterministische Reihenfolge Branch-1 → Sequenz → Schwarm → ex_officio → volume. Beispiel: `committee:15;swarm_buy:20;ex_officio:+10;volume:8`. Null-Komponenten erscheinen explizit. |
| `trade_warnings` | Stage `scoring` | `;`-getrennt. Aktuell nur `paradox_swarm` wenn `swarm_paradox` getriggert hat. Unabhängig vom Score sichtbar. |

### Score-Aggregate in Views (aktiv ab Phase 8.4)

`scripts/import_trades.py::_materialize_suspicious_assets` und `_materialize_suspicious_members` joinen `trade_score`/`trade_volume_usd`/`trade_warnings` aus `trades_master_complete.csv` (Helper `_load_trade_scores`) auf **distinct trade_id** pro Issuer bzw. Member.

| View | Spalten | Aggregation |
|---|---|---|
| `suspicious_assets.csv` | `score_avg`, `score_max`, `score_total`, `total_volume_usd`, `warnings` | groupby `issuer_ticker` über distinct `trade_id` im Y-Tage-Fenster. `fillna(0)` für fehlende Master-Eintraege. `warnings` = `_join_distinct_warnings` (distinct, sortiert, leer-tokens raus). |
| `suspicious_members.csv` | `score_avg`, `score_max`, `score_total` | groupby `member_id` über distinct `trade_id` (kein Y-Filter). Nur drei Score-Spalten — bewusst kein `total_volume_usd` / `warnings`. |
