# Filter-Instanz-Parameter

**Letzte Aktualisierung:** 2026-06-06

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

Schreibt `Insider_Sectors` und `Relevant_Sectors` in den Master.
`Relevance_Score` wurde entfernt (Refactor 2026-05-21).

**`Relevant_Sectors`-Einträge** haben jetzt ein Feld `match_type`:
- `'full'` — Eintrag deckt Sector **und** Industry des Assets ab
- `'sector'` — nur Sector trifft, Industry abweichend (nur bei `'sector_or_industry'`- oder `'hybrid'`-Modus möglich)

**Settings:**

| Konstante | Wert | Bedeutung |
|-----------|------|-----------|
| `COMMITTEE_EX_OFFICIO_ROLES`       | `[Chairman, Ranking Member, Vice Chairman, Cochairman, EXOfficio]` | Welche Rollen als Ex Officio zählen |

**Match-Modus** für `Relevant_Sectors` (Intersection Insider × Ticker):
- `'sector_or_industry'` — Eintrag matcht, wenn sector ODER industry zum Ticker passen
- `'sector_and_industry'` — Eintrag matcht nur, wenn sector UND industry passen *(Default)*
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

### **Signal: Trade Volume** (User-Sicht: „Filter 1")

Tier-basiertes Volume-Signal — ersetzt den früheren linearen Volume-Multiplikator,
der ohne sonstigen Signal-Treffer hohe Scores erzeugen konnte.

- **Code:** `signals/trade_volume_signal.py` (`TradeVolumeSignal`).
- **Trigger:** `trade_amount_max_usd > SCORING_TRADE_VOLUME_TRIGGER_FLOOR` (Default 15.000 USD).
- **Score-Beitrag:** diskret aus `SCORING_TRADE_VOLUME_TIERS` (nur höchste erreichte Stufe zählt). Default-Tiers (Vorschlag C, konservativ): ≤50k→+2, ≤100k→+4, ≤250k→+7, ≤500k→+10, ≤1M→+13, >1M→+16.
- **Score-Basis-Spalte:** `trade_amount_max_usd` (Obergrenze der Disclosure-Range, befüllt von Stage `trade_volume` via `utils/amount_parser.max_usd`).
- **UI-Override:** `SCORING_WEIGHTS['trade_volume']` skaliert den Maximalbeitrag linear. Tier-Tabelle live editierbar im Streamlit-Dashboard (Sidebar „Score-Gewichte" → Erweiterte Parameter).
- **Event-Persistenz:** Pro getriggertem Trade ein Event in `suspicious_events.csv` mit `signal_type='trade_volume'` und `match_count = Tier-Index` (1–6 bei Default-Tiers).

Verteilung in einem Smoke-Run (112.658 Trades, 27.05.2026): 29.340 Trades (26,0%) triggern `trade_volume`; 6.625 Trades (5,9%) haben **nur** `trade_volume` als Signal mit Score-Verteilung min=2/max=16/mean=3,3 — klein genug, dass Volume-only-Trades nicht mehr in die Top-Ränge der Ranking-Tabelle rutschen.

---

## Post-Trade-Performance (Modul 9.x — Daten-Anreicherung, KEIN Signal/Gate/Tag)

Dies ist eine reine Daten-Anreicherung (Pipeline Stage 9) — fliesst NICHT in den `trade_score` ein, sondern dient der UI/Analyse der historischen Performance von Trades.

### Post-Trade Returns (historische Performance geschlossener Positionen)

| Konstante | Default | Bedeutung |
|---|---|---|
| `POST_TRADE_HORIZONS_DAYS` | `[30, 60, 90, 120, 180]` | Tage nach `transaction_date`, fuer die Preis-Snapshots erfasst werden. Anpassbar — Spalten `post_price_{X}d` / `post_return_{X}d` werden dynamisch erzeugt. **Neu (2026-06-04):** 180-Tage-Horizont hinzugefügt. |
| `EXIT_DETECTION_WINDOW_DAYS` | `None` | **Neu (2026-06-04):** Exit-Fenster für Gegengeschäft-Suche. `None` = unbegrenzt (durchsucht alle nachfolgenden Trades). Integer = Cap in Tagen (z.B. 365 = sucht nur bis zu 1 Jahr nach dem Eröffnungs-Trade). **WICHTIG:** Entkoppelt vom Return-Horizont — Return-Snapshots bleiben an die `POST_TRADE_HORIZONS_DAYS` gebunden. Das Exit-Fenster bestimmt NUR, wie weit die Exit-Detection nach gegensätzlichen Trades sucht. |
| `POST_TRADE_PRICE_CACHE` | `'data/cache/price_history.parquet'` | Pfad zum yfinance-Preis-Cache |
| `POST_TRADE_PRICE_FETCH_YEARS` | `5` | yfinance `period` beim Erstabruf pro Ticker |
| `POST_TRADE_PRICE_CACHE_TTL_DAYS` | `7` | Refresh-Schwelle pro Ticker |
| `POST_TRADE_EXIT_AMOUNT_TOLERANCE` | `0.0` | 0.0 = strikt (kumulierter opposing-max muss >= original-max); >0.0 = relative Toleranz (z.B. 0.1 = 10% Unterdeckung erlaubt) |

### Paper Returns (Mark-to-Market für offene/frische Positionen, Modul 9 — 2026-06-06)

**Neu:** Papier-Return-Berechnung für den neuen „Recent Trades"-Dashboard-Tab (Modul 9). Diese Werte sind **Delta-unabhängig** und aktualisieren sich täglich, da die Kurse sich ständig ändern (auch für lange importierte offene Positionen).

| Konstante | Default | Bedeutung |
|---|---|---|
| `RECENT_MONITORING_WINDOW_DAYS` | `180` | Max-Fenster für Papier-Return-Berechnung (Tage, Bezug `transaction_date`). Trades älter als `now - RECENT_MONITORING_WINDOW_DAYS` bekommen `paper_return_pct = NaN`. Sinn: Recent-Trades-Tab zeigt nur Positions aus den letzten N Tagen (vorstellbar: "Letzte 6 Monate"). |
| `RECENT_MONITORING_WINDOW_OPTIONS` | `[7, 30, 60, 90, 120, 180]` | Wählbare Fenstergrößen im Dashboard (Streamlit Selectbox im Recent-Trades-Tab). User kann zwischen diesen Werten wechseln, ohne Code zu ändern. |
| `RECENT_PRICE_MAX_AGE_BUSINESS_DAYS` | `1` | Frische-Anforderung für yfinance-Cache bei Recent-Ticker. Wenn jüngstes Preis-Datum im Cache älter als `max_price_age_bdays` ist, wird yfinance neu abgefragt. **Relevanz:** Täglicher 03:00-Lauf sollte ≤24h werktags frische Kurse sehen. Bei Wochenende/Feiertag fällt 1 BDay=2 Kalendertage. Refresh erfolgt VOLL-PASS für Recent-Ticker. |

**Exit-Status-Werte**: `not_applicable` / `delisted` / `unknown` / `held` / `closed_full` / `closed_partial` — siehe DATA_STRUCTURE.md.

**Semantik-Update (2026-06-04):**
- `effective_return_pct`: Jetzt REALISIERT-ONLY. Bei `closed_full` oder `closed_partial` = Return bis `exit_price` (richtungsbewusst via `sell_sign`). Bei `held`, `delisted`, `unknown`, `not_applicable` = NaN. Frühere Semantik (held = längster verfügbarer Horizont-Snapshot) entfällt. Mark-to-Market bleibt in `post_return_{X}d`.
- `effective_return_horizon_days`: Bei `closed_*` = Tage zwischen `transaction_date` und `exit_date`. Bei `held` = NaN (nicht mehr: längster verfügbarer Horizont).
- Neue Spalte `first_close_offset_days`: Tage vom `transaction_date` bis zum ERSTEN gegensätzlichen Trade. Hilfs-Spalte für den Horizont-Sichtbarkeitsfilter im Dashboard. NaN bei `not_applicable` / `delisted` / `unknown` / `held`.

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
| „Filter 1" — Handelsvolumen           | `trade_volume`-Signal (Tier-basiert) | ✅ Vollwertiges Signal; triggert pro Trade auf `trade_amount_max_usd` (Obergrenze der Quiver-Range). Stufen: ≤50k→+2, ≤100k→+4, ≤250k→+7, ≤500k→+10, ≤1M→+13, >1M→+16 (Default Vorschlag C, in Settings + Streamlit anpassbar). Trigger-Floor 15.000 USD — Tier 0 löst nichts aus. |
| „Filter 2" — Schwarm-Verhalten        | Signal | ✅ **P6 implementiert** — siehe Schwarm-Sektion oben |
| „Filter 5" — Contrarian               | Signal | ✅ implementiert als Sequenz-Signal (P3) |
| „Filter 6" — Long ↔ Short Wechsel     | — | **verworfen** (phänomenologisch identisch mit `contrarian`) |
| „Filter 7" — Position vergrößert      | — | **verworfen** (phänomenologisch identisch mit `accumulative`) |

---

## Modul 8 — Scoring-System (in Arbeit)

**Status:** Phasen 8.1 ✅ + 8.2 ✅ + 8.3 ✅ + 8.4 ✅ + 8.5 ✅ (Stand 19.05.2026).
Pipeline jetzt 16 Stages. 61 Unit-Tests grün. Phase 8.6 (Voll-Pipeline-Smoke-Run) offen.
**Plan:** `~/.claude/plans/crispy-growing-pizza.md`.

### Score-Formel (aktiv)

```python
swarm_signals = {'swarm_buy', 'swarm_sell', 'swarm_conflict'}

swarm_part  = Σ WEIGHTS[s] für s in signals_triggered ∩ swarm_signals
if 'swarm_paradox' in signals_triggered:
    swarm_part *= SCORING_PARADOX_DAMPER         # default 0.0

other_part  = Σ WEIGHTS[s] für s in signals_triggered
              \ swarm_signals \ {swarm_paradox, trade_volume}

# Volume ist ein Tier-Signal — Beitrag NUR wenn das Signal getriggert hat.
# Der Tier-Index wird aus trade_amount_max_usd berechnet; der Punkte-Wert
# kommt aus SCORING_TRADE_VOLUME_TIERS, NICHT aus SCORING_WEIGHTS.
# SCORING_WEIGHTS['trade_volume'] wirkt als UI-Skalierungsfaktor:
#   scale = SCORING_WEIGHTS['trade_volume'] / max(tier_scores)
#   volume_part = tier_score(amount_max) * scale
if 'trade_volume' in signals_triggered and SCORING_TRADE_VOLUME_ENABLED:
    volume_part = tier_score_scaled(trade_amount_max_usd)
else:
    volume_part = 0

bonus_active = is_ex_officio AND (signals_triggered ∩ SCORING_EX_OFFICIO_TRIGGERS ≠ ∅)
bonus        = SCORING_EX_OFFICIO_BONUS if bonus_active else 0

trade_score  = round(swarm_part + other_part + volume_part + bonus)
# KEIN Cap auf 100 — Score wächst unbegrenzt.
```

> **Dashboard-Override:** Alle in der Tabelle unten gelisteten Konstanten sind
> im Streamlit-Dashboard (Sidebar-Form „Score-Gewichte") live anpassbar und als
> benannte Presets in `data/config/scoring_presets.json` persistierbar.
> `config/settings.py` definiert nur die Defaults — der Recompute baut bei
> Bedarf einen Settings-Proxy (`app/score_recompute.py:build_settings_proxy`).
> „Signale ausschließen" gewinnt dabei immer über einen positiven Override
> (Reihenfolge: Defaults → weights/params-Override → Exclude → 0).

### Settings-Block (aktiv ab Phase 8.1, in `config/settings.py`)

| Konstante | Default | Bedeutung |
|---|---|---|
| `SCORING_WEIGHTS['committee']`       | 10    | Branch-1 Per-Trade; triggert bei `len(Relevant_Sectors) > 0` |
| `SCORING_WEIGHTS['committee_full']`  |  5    | Branch-1 Per-Trade; zusätzlich wenn mind. 1 Eintrag `match_type='full'`. Voll-Treffer = 10+5=15 |
| `SCORING_WEIGHTS['floor_action']`    | 15    | Aktiv. Triggert bei Vote_Timing_Hit_Count ≥ VOTE_TIMING_HIT_THRESHOLD (=1) |
| `SCORING_WEIGHTS['extreme_report']`  |  5    | Branch-1 Per-Trade |
| `SCORING_WEIGHTS['contrarian']`      |  8    | Sequenz-Signal |
| `SCORING_WEIGHTS['accumulative']`    | 10    | Sequenz-Signal |
| `SCORING_WEIGHTS['swarm_buy']`       | 20    | Branch-2 Cluster |
| `SCORING_WEIGHTS['swarm_sell']`      | 20    | Branch-2 Cluster |
| `SCORING_WEIGHTS['swarm_conflict']`  | −5    | Gegenindiz — Abzug auf den einzelnen Contrarian-Trade im Schwarmfenster |
| `SCORING_WEIGHTS['trade_volume']`    | 16    | Nomineller Max-Beitrag des Tier-Signals (UI-Skalierung). Faktischer Beitrag pro Trade kommt aus `SCORING_TRADE_VOLUME_TIERS`. |
| `SCORING_EX_OFFICIO_BONUS`           | 10    | Einmaliger Summand pro Trade |
| `SCORING_EX_OFFICIO_TRIGGERS`        | `['committee', 'floor_action']` | Welche Signale den Bonus aktivieren |
| `SCORING_PARADOX_DAMPER`             | 0.0   | Multiplikator auf Schwarm-Anteil bei `swarm_paradox` |
| `SCORING_PARADOX_WARNING_TAG`        | `'paradox_swarm'` | Tag in `trade_warnings`-Spalte |
| `SCORING_TRADE_VOLUME_ENABLED`       | `True` | Trade-Volume-Signal an/aus |
| `SCORING_TRADE_VOLUME_TIERS`         | `[(50k,2),(100k,4),(250k,7),(500k,10),(1M,13),(inf,16)]` | Aufsteigend sortiert; nur höchste erreichte Stufe zählt. Letzter Eintrag muss `float('inf')` als Catch-all sein. |
| `SCORING_TRADE_VOLUME_TRIGGER_FLOOR` | 15_000 | Untergrenze für Trigger. Trades mit `trade_amount_max_usd <= floor` lösen kein `trade_volume`-Signal aus. |
| `SCORING_VOLUME_WINDOW_DAYS_Z`       | 90    | Rolling-Window für `asset_tradingvolume_usd` (weiterhin gebraucht für `total_volume_usd` in Asset-Aggregaten, fließt aber **nicht** in den `trade_score` ein). |

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
| `trade_volume_usd` | Stage `trade_volume` (`utils/amount_parser.midpoint_usd`) | int64, Mittelpunkt der Disclosure-Amount-Range in USD. Beispiele: `$1,001 - $15,000` → 8000; `Over $50,000,000` → 50_000_000; leer/NaN → 0. Weiter genutzt für `asset_tradingvolume_usd`-Aggregat und `total_volume_usd` in den View-CSVs. |
| `trade_amount_max_usd` | Stage `trade_volume` (`utils/amount_parser.max_usd`) | int64, **Obergrenze** der Disclosure-Amount-Range. Score-Basis für das `trade_volume`-Signal. Beispiele: `$1,001 - $15,000` → 15000; `Over $50,000,000` → 50_000_000; leer/NaN → 0. |
| `asset_tradingvolume_usd` | Stage `asset_volume` (`scoring/asset_volume.compute_asset_tradingvolume`) | int64, Σ `trade_volume_usd` über `issuer_group_key` im Fenster `[T.date − Z, T.date]` (Z = `SCORING_VOLUME_WINDOW_DAYS_Z`, Default 90). Tages-Aggregation; SKIP-Buckets = 0. **Wird seit dem Volume-Signal-Redesign nicht mehr direkt im `trade_score` verrechnet** — bleibt aber für `total_volume_usd` in `suspicious_assets.csv` und das Streamlit-Asset-Tab. |
| `trade_score` | Stage `scoring` (`scoring/trade_score.compute_trade_scores`) | int, Combined-Score laut Formel. Kein Cap. Sanity-Check (112k Trades, 27.05.2026): mean=13.0, median ≈ 9, max=83 (vorher: mean=21.6, max=1705 — der lineare Volume-Beitrag fiel weg). |
| `trade_score_breakdown` | Stage `scoring` | `;`-getrennt, deterministische Reihenfolge Branch-1 → Sequenz → Schwarm → trade_volume → ex_officio. Beispiel: `committee:10;committee_full:5;swarm_buy:20;trade_volume:7;ex_officio:+10`. `trade_volume`-Komponente erscheint nur wenn das Signal getriggert hat. |
| `trade_warnings` | Stage `scoring` | `;`-getrennt. Aktuell nur `paradox_swarm` wenn `swarm_paradox` getriggert hat. Unabhängig vom Score sichtbar. |

### Score-Aggregate in Views (aktiv ab Phase 8.4)

`scripts/import_trades.py::_materialize_suspicious_assets` und `_materialize_suspicious_members` joinen `trade_score`/`trade_volume_usd`/`trade_warnings` aus `trades_master_complete.csv` (Helper `_load_trade_scores`) auf **distinct trade_id** pro Issuer bzw. Member.

| View | Spalten | Aggregation |
|---|---|---|
| `suspicious_assets.csv` | `score_avg`, `score_max`, `score_total`, `total_volume_usd`, `warnings` | groupby `issuer_ticker` über distinct `trade_id` im Y-Tage-Fenster. `fillna(0)` für fehlende Master-Eintraege. `warnings` = `_join_distinct_warnings` (distinct, sortiert, leer-tokens raus). |
| `suspicious_members.csv` | `score_avg`, `score_max`, `score_total` | groupby `member_id` über distinct `trade_id` (kein Y-Filter). Nur drei Score-Spalten — bewusst kein `total_volume_usd` / `warnings`. |

---

## Post-Trade-Performance (Modul 9.x)

**Status:** Parameter ✅ / Code ✅ — `scoring/post_trade_returns.py`

**Kein Signal/Gate/Tag** — reine Daten-Anreicherung der `trades_master_complete.csv` mit Post-Trade-Preisen und Exit-Detection. Pipeline Stage 9 (`stage_post_trade_performance`), zwischen `tickeryfinance` und `trade_volume`.

### Settings (in `config/settings.py`)

| Konstante | Default | Bedeutung |
|---|---|---|
| `POST_TRADE_HORIZONS_DAYS` | `[30, 60, 90, 120]` | Zeithorizonte in Tagen fuer Preis-Lookups nach `transaction_date` |
| `POST_TRADE_EXIT_AMOUNT_TOLERANCE` | `0.0` | Toleranz in USD fuer Exit-Schwellwert (Summe gegensaetzlicher Trades) |
| `POST_TRADE_PRICE_CACHE` | `data/cache/price_history.parquet` | Pfad zum yfinance-Preis-Cache (Parquet, MultiIndex) |
| `POST_TRADE_PRICE_FETCH_YEARS` | `5` | Anzahl Jahre Daily-Close-Daten, die pro Ticker von yfinance geholt werden |
| `POST_TRADE_PRICE_CACHE_TTL_DAYS` | `7` | Cache-Gueltigkeit in Tagen; aeltere Eintraege werden bei naechstem Lauf erneuert |

### Neue Spalten (14 in `trades_master_complete.csv`)

Die Spalten werden zwischen den yfinance-Spalten (`quote_type_yf`) und den Volume-Spalten (`trade_volume_usd`) eingefuegt. Vollstaendige Beschreibung: `organisation/DATA_STRUCTURE.md` (Section 6, `trades_master_complete.csv`).

**exit_status-Werte:**
- `not_applicable` — ticker_type nicht in {stock, foreign_stock, shareclass, etf} oder issuer_group_key beginnt mit SKIP: oder Exchange-Trade
- `delisted` — yfinance liefert leere History
- `unknown` — yfinance Network/Rate-Limit-Error
- `held` — keine gegensaetzlichen Trades im Fenster
- `closed_full` — kumulierte opposing amount >= Schwellwert
- `closed_partial` — opposing vorhanden aber Schwellwert nicht erreicht
