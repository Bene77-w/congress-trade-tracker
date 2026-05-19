# Filter-Instanz-Parameter

**Letzte Aktualisierung:** 2026-05-17

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

## Noch zu definieren: Signals (User-Sicht: „Filter 1 / 5 / 6 / 7")

Diese Filter waren in der alten Spec enthalten, sind aber **weder im Code noch
in `config/settings.py`** vorhanden. Sie werden im neuen Architekturplan neu
diskutiert und definiert (Schwellen, Logik, Gewichtung, Reihenfolge).

| User-Sicht | Code-Konzept | geplantes Modul |
|------------|--------------|-----------------|
| „Filter 1" — Handelsvolumen           | Signal | `signals/volume_signal.py` |
| „Filter 2" — Schwarm-Verhalten        | Signal | ✅ **P6 implementiert** — siehe Schwarm-Sektion oben |
| „Filter 5" — Contrarian               | Signal | `signals/contrarian_signal.py` *(implementiert als Sequenz-Signal, P3)* |
| „Filter 6" — Long ↔ Short Wechsel     | Signal | `signals/long_short_signal.py` |
| „Filter 7" — Position vergrößert      | Signal | `signals/position_increase_signal.py` |

---

## Gesamt-Scoring

**Status:** Wird im neuen Architekturplan komplett neu konzipiert. Bis dahin
existiert weder ein `WEIGHTS`-Dict noch ein `SCORE_THRESHOLDS`-Dict in
`config/settings.py`, und es gibt kein Scoring-Modul.

**Bisheriger Grundsatz, der mit übernommen werden kann:**
- Committee-Tag = Auto-Flag (Trade automatisch als verdächtig markiert,
  unabhängig vom Score).
- Asset-Class-Gate = Hard-Ausschluss (Nicht-Aktien fließen gar nicht erst
  ins Scoring).
