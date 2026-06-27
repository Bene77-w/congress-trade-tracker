# signal_info.md — Signal-System & Suspicious-Output-Dateien

**Zweck:** Vollständige Dokumentation des Signal-Systems — Architektur, Signal-Typen, Spalten-Schemas der Output-Dateien und Konfiguration.
**Trigger-Logik der einzelnen Signale:** siehe `../Filter_Instanz_Parameter.md`.

---

## Datei-Übersicht

| Datei | Pfad | Typ | Lebenszyklus |
|---|---|---|---|
| `branch1_events_stage.csv` | `data/processed/` | Stage-Zwischenoutput | wird pro Pipeline-Lauf neu geschrieben |
| `sequence_events_stage.csv` | `data/processed/` | Stage-Zwischenoutput | wird pro Pipeline-Lauf neu geschrieben |
| `swarm_events_stage.csv` | `data/processed/` | Stage-Zwischenoutput (P6) | wird pro Pipeline-Lauf neu geschrieben |
| **`suspicious_events.csv`** | `data/processed/` | **Master**, append-only | wächst, mit `(signal_event_id, trade_id)`-Dedup |
| **`suspicious_assets.csv`** | `data/processed/` | View, regeneriert | wird komplett aus events neu erzeugt, **Y-Tage-Verfall** |
| **`suspicious_members.csv`** | `data/processed/` | View, regeneriert | wird komplett aus events neu erzeugt, dauerhaft |

Die drei Stage-Dateien sind technisch identisch aufgebaut (gleiches 21-Spalten-Schema), trennen aber Branch-1-Per-Trade-Signale, Sequenz-Signale (Member×Asset) und Schwarm-Signale (Issuer-Group-Ebene), damit die Diagnostik einfacher ist. Alle drei werden in `suspicious_events.csv` zusammengeführt.

---

## Helper — Issuer-Auflösung

Zwei Ebenen der Issuer-Gruppierung werden genutzt:

| Helper | Ergebnis-Spalte | Ebene | Verwendung |
|---|---|---|---|
| `utils/issuer_ticker.py::resolve_issuer_ticker(row)` | `issuer_ticker` | Share-Class (FIGI → ticker_base → ticker) | Sequenz-Gruppierung (`contrarian`, `accumulative`), Suspicious-Asset-Aggregation |
| `utils/issuer_group_mapper.py::IssuerGroupMapper.resolve_issuer_group_key(row)` | `issuer_group_key` | Issuer-Level (CIK via SEC EDGAR, P5.6) | **Schwarm-Gruppierung** — Cross-Class (GOOGL+GOOG → `CIK:1652044`) |

`issuer_group_key`-Präfixe: `CIK:<cik>` (SEC-Treffer), `TICKER:<ticker>` (ETF/Foreign/Fallback), `SKIP:<type>` (Options, Bonds, etc. — werden in Schwarm-Berechnung ausgeschlossen).

---

## Signal-Typen (12 Stück)

| #  | Signal-Typ             | Branch | Modul                                      | Trigger-Logik (Kurzform)                                                                                                          | Triggering-Date                              |
|----|------------------------|--------|--------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------|
| 1  | `committee`            | 1      | `signals/committee_signal.py`              | `len(Relevant_Sectors) > 0`                                                                                                       | `transaction_date`                           |
| 1b | `committee_full`       | 1      | `signals/committee_full_signal.py`         | mind. 1 Eintrag in `Relevant_Sectors` hat `match_type == 'full'`                                                                  | `transaction_date`                           |
| 2  | `committee_ex_officio` | 1      | `signals/committee_ex_officio_signal.py`   | **v2 (2026-06-12):** Signal 1 ausgelöst UND `ex_officio_role_keys` nicht leer (≥1 relevanter Sektor-Treffer trägt eine Führungsrolle; statt globalem `Member_ExOfficio`) | `transaction_date`                           |
| 3  | `floor_action`         | 1      | `signals/floor_action_signal.py`           | `vote_timing_hit_count >= VOTE_TIMING_HIT_THRESHOLD`                                                                              | `transaction_date`                           |
| 4  | `extreme_report`       | 1      | `signals/extreme_report_signal.py`         | `delay > EXTREME_REPORT_DAYS` ODER `delay <= EXTREME_REPORT_FAST_DAYS` (mit `delay = disclosure_date - transaction_date`; sehr späte ODER sehr schnelle Meldung) | `disclosure_date`                            |
| 4b | `trade_volume`         | 1      | `signals/trade_volume_signal.py`           | `trade_amount_max_usd > SCORING_TRADE_VOLUME_TRIGGER_FLOOR` (Default 15.000 USD). Score-Beitrag stufenweise aus `SCORING_TRADE_VOLUME_TIERS` | `transaction_date` |
| 5  | `contrarian`           | seq    | `signals/contrarian_signal.py`             | Buy↔Sell desselben Members auf demselben `issuer_ticker` innerhalb `CONTRARIAN_WINDOW_DAYS`                                       | Datum des Kontra-Trades                       |
| 6  | `accumulative`         | seq    | `signals/accumulative_signal.py`           | ≥ `ACCUMULATIVE_MIN_COUNT` Trades desselben Members auf demselben `issuer_ticker` (gleiche Richtung) in `ACCUMULATIVE_WINDOW_DAYS` | Datum des Trades, der die Schwelle erreicht  |
| 7  | `swarm_buy`            | 2      | `signals/swarm_buy_signal.py`              | ≥ `SWARM_MIN_MEMBERS` distinkte Members kaufen denselben `issuer_group_key` in rolling `SWARM_WINDOW_DAYS`                        | Datum des N-ten distinct Members             |
| 8  | `swarm_sell`           | 2      | `signals/swarm_sell_signal.py`             | Analog Signal 7 für Verkäufe                                                                                                       | Datum des N-ten distinct Members             |
| 9  | `swarm_conflict`       | 2      | `signals/swarm_conflict_signal.py`         | Schwarm aktiv UND **genau 1** Gegen-Trade im erweiterten Fenster `[first − BEFORE, last + AFTER]`                                  | Datum des Gegen-Trades                        |
| 10 | `swarm_paradox`        | 2      | `signals/swarm_paradox_signal.py`          | Schwarm aktiv UND **≥ `SWARM_PARADOX_MIN_CONFLICTS`** Gegen-Trades im erweiterten Fenster (mutually exclusive zu `swarm_conflict`) | Datum des Trades, der die Paradox-Schwelle erreicht |

---

## Snapshot-Konzept

**Wann wird gespeichert?**
In der Pipeline-Stage, die das jeweilige Signal auslöst (`_stage_branch1_signals`, `_stage_sequence_signals`, `_stage_swarm_signals`). Snapshot = die `trades_master`-Werte der beteiligten Trade(s) zum Zeitpunkt der Auslösung.

**Wie wird verhindert, dass ein Snapshot überschrieben wird?**
1. `suspicious_events.csv` ist Append-only.
2. Vor jedem Append wird `(signal_event_id, trade_id)` gegen den existierenden Bestand geprüft. Schon vorhanden → skip.
3. `signal_event_id = sha256(triggering_date + "|" + signal_type + "|" + scope_key)[:16]` ist deterministisch → ein Re-Run produziert denselben Hash, also greift automatisch der Duplikat-Schutz.

**Folge:** Snapshot friert zum Zeitpunkt der ersten Auslösung ein und ist gegen spätere Daten-Änderungen (Committee-Update, Sektor-Re-Mapping, neuer yfinance-Lookup) immun.

---

## Scope-Key-Konvention für `signal_event_id`

Der `scope_key` definiert, **welche Trades zu derselben Auslösung gehören** (gleiche `signal_event_id`).

| Signal-Familie | scope_key-Format | Folge |
|---|---|---|
| Branch-1 per-Trade: `committee`, `committee_full`, `committee_ex_officio`, `floor_action`, `extreme_report`, `trade_volume` | `trade_id` | Ein Trade → eine `signal_event_id` pro Signal-Typ. |
| Sequenz per-Member×Asset: `contrarian`, `accumulative` | `member_id + "\|" + issuer_ticker + "\|" + window_start_iso` | Alle Trades desselben Members auf demselben `issuer_ticker` im selben Fenster → gleiche ID. |
| Schwarm Buy/Sell: `swarm_buy`, `swarm_sell` | `issuer_group_key + "\|" + direction + "\|" + window_start_iso` | Alle Trades innerhalb desselben Schwarms (Cross-Class) → gleiche ID. |
| Schwarm Konflikt: `swarm_conflict` | `"conflict\|" + issuer_group_key + "\|" + swarm_direction + "\|" + window_start_iso` | Der einzelne Gegen-Trade zum referenzierten Schwarm. |
| Schwarm Paradox: `swarm_paradox` | `"paradox\|" + issuer_group_key + "\|" + swarm_direction + "\|" + window_start_iso` | Alle Gegen-Trades (≥ 2) zum referenzierten Schwarm → gleiche ID. |

---

## `suspicious_events.csv` — Append-only Master (21 Spalten)

Eine Zeile = **ein Trade in einer Signal-Auslösung**. Wenn ein Trade von 3 Signalen erkannt wird, steht er 3 Mal drin (je einmal pro `signal_type`). Wenn ein Signal mehrere Trades gleichzeitig fängt (z. B. `accumulative`, `swarm_*`), erscheinen alle mit derselben `signal_event_id`.

Identische 21-Spalten-Struktur in `branch1_events_stage.csv`, `sequence_events_stage.csv` und `swarm_events_stage.csv`.

| # | Spalte | Typ | Inhalt | Funktion |
|---|---|---|---|---|
| 1 | `signal_event_id` | 16-Hex-SHA256 | ID der **Signal-Auslösung** (nicht des Trades) | Gruppiert mehrere Trades, die zusammen ein Signal auslösen. Deterministisch, reproduzierbar |
| 2 | `trade_id` | 16-Hex-SHA256 | ID des konkreten Trades | Join zu `trades_master.csv`; `(signal_event_id, trade_id)` ist der Dedup-Schlüssel beim Append |
| 3 | `signal_type` | str | siehe Signal-Typen-Tabelle (10 Werte) | Welche Signal-Familie ausgelöst hat |
| 4 | `triggering_date` | ISO-Date | Datum der Auslösung | siehe Signal-Typen-Tabelle Spalte „Triggering-Date" |
| 5 | `scope_key` | str | Vor-Hash-String für `signal_event_id` | Debugging — siehe Scope-Key-Konvention-Tabelle |
| 6 | `member_id` | str | `bio_guide_id` | eindeutige Kongress-ID des Members |
| 7 | `member_name` | str | Vollname | Lesbarkeit |
| 8 | `chamber` | `House`/`Senate` | Kammer | Filter im Dashboard |
| 9 | `ticker` | str | Originaler Ticker aus Trade | wie eingereicht (Suffix erhalten) |
| 10 | `issuer_ticker` | str | Share-Class-Level (FIGI → ticker_base → ticker) | Sequenz-Gruppierung, Suspicious-Asset-Aggregation |
| 11 | `issuer_group_key` | str | Issuer-Level (P5.6, Präfixe `CIK:`/`TICKER:`/`SKIP:`) | **Schwarm-Gruppierung** (Cross-Class: GOOGL+GOOG → CIK:1652044) |
| 12 | `asset_name_figi` | str | OpenFIGI-Name, Fallback `asset_name` | Lesbarer Asset-Name unabhängig vom Ticker-Symbol |
| 13 | `transaction_date` | ISO-Date | Tatsächliches Handelsdatum | Timing-Analyse |
| 14 | `disclosure_date` | ISO-Date | Meldedatum | Verzögerungs-Analyse, Basis für `extreme_report` |
| 15 | `transaction_amount` | str | Original-Range, z. B. `"$1,001 - $15,000"` | Volumen-Hinweis (keine Aggregation möglich) |
| 16 | `direction` | `buy`/`sell`/`other` | abgeleitet aus `transaction_type` | Schwarm-Filter, Conflict-Logik |
| 17 | `is_ex_officio` | bool | Member war zur Trade-Zeit in Leadership | Multiplikator im Scoring |
| 18 | `match_count` | int | Anzahl Einträge in `Relevant_Sectors` zum Trigger-Zeitpunkt | Wie viele Ausschuss-Sektoren mit dem Asset übereinstimmen |
| 19 | `vote_timing_hit_count` | int | Treffer im `VoteTimingTag` | Wie viele Roll-Calls im Fenster |
| 20 | `committee_subcommittee` | str | `Member_Committees_Full`-Snapshot | Welche Ausschüsse zur Trade-Zeit |
| 21 | `relevant_sectors` | str | matching Sektoren aus Mapping | Welche Sektoren der Ausschuss + das Asset abdecken |

**Snapshot-Eigenschaft:** Die Spalten 6–21 sind **eingefroren zum Auslöse-Zeitpunkt**. Auch wenn der Member später aus dem Ausschuss ausscheidet, bleibt der Snapshot stabil.

**Append-Logik:** Bei jedem Pipeline-Lauf werden die drei Stage-CSVs (branch1, sequence, swarm) konkateniert und nur die `(signal_event_id, trade_id)`-Paare, die noch nicht in `suspicious_events.csv` stehen, angehängt. Idempotent.

---

## `suspicious_assets.csv` — Asset-View mit Y-Tage-Verfall (15 Spalten, Modul 8.4)

Eine Zeile = **ein Asset, das aktuell verdächtig ist**.
Filter: `last_event_date >= today - SUSPICIOUS_ASSET_PERSISTENCE_DAYS` (Default 30).
Veraltete Einträge fallen aus dieser View, bleiben aber in `suspicious_events.csv` erhalten.

| # | Spalte | Typ | Inhalt | Funktion |
|---|---|---|---|---|
| 1 | `issuer_ticker` | str | Asset-Stamm (Helper-aufgelöst) | Primärschlüssel der View |
| 2 | `asset_name_figi` | str | aus jüngstem Event (Fallback `asset_name`) | Lesbarer Asset-Name |
| 3 | `event_count` | int | Anzahl Event-Zeilen für dieses Asset (im Y-Tage-Fenster) | Wie oft aufgetaucht |
| 4 | `member_count` | int | distinct `member_id` | Wie viele verschiedene Members beteiligt |
| 5 | `signal_types` | str (Counter) | z. B. `"committee:5,accumulative:2"` | Welche Signal-Familien beteiligt |
| 6 | `last_event_date` | ISO-Date | jüngstes `triggering_date` | Basis für Y-Tage-Filter |
| 7 | `first_event_date` | ISO-Date | ältestes `triggering_date` | Wie lange schon „auffällig" |
| 8 | `directions` | str (Counter) | z. B. `"buy:4,sell:1"` | Hinweis auf Schwarm-/Conflict-Muster |
| 9 | `chambers` | str (Counter) | z. B. `"House:3,Senate:1"` | Welche Kammer dominant |
| 10 | `last_signal_type` | str | Signal-Typ des jüngsten Events | „Was war zuletzt los?" |
| 11 | `score_avg` | float | Modul 8.4 — siehe `organisation/Filter_Instanz_Parameter.md` |  |
| 12 | `score_max` | int | Modul 8.4 |  |
| 13 | `score_total` | int | Modul 8.4 |  |
| 14 | `total_volume_usd` | int | Modul 8.4 |  |
| 15 | `warnings` | str | Modul 8.4 |  |

**Regenerationslogik:** Wird bei jedem Pipeline-Lauf komplett neu aus `suspicious_events.csv` aggregiert. Score-Spalten werden via `trade_id` aus `trades_master_complete.csv` gejoint. Datei wird überschrieben.

---

## `suspicious_members.csv` — Member-View, dauerhaft (13 Spalten, Modul 8.4)

Eine Zeile = **ein Member, der je in einem Signal auftauchte**. Kein Verfall — historisch komplett.

| # | Spalte | Typ | Inhalt | Funktion |
|---|---|---|---|---|
| 1 | `member_id` | str | `bio_guide_id` | Primärschlüssel |
| 2 | `member_name` | str | aus jüngstem Event | Lesbarkeit |
| 3 | `chamber` | str | aus jüngstem Event | House/Senate |
| 4 | `event_count` | int | gesamte Anzahl Event-Zeilen | Wie aktiv im Signal-System |
| 5 | `asset_count` | int | distinct `issuer_ticker` | Wie viele verschiedene Assets |
| 6 | `signal_types` | str (Counter) | z. B. `"committee:8,contrarian:3"` | Profil der Auffälligkeiten |
| 7 | `last_event_date` | ISO-Date | jüngstes `triggering_date` | „Wie frisch?" |
| 8 | `first_event_date` | ISO-Date | ältestes Event | „Wie lange auffällig?" |
| 9 | `is_ex_officio_ever` | bool | bool-OR über alle Events | Mindestens einmal in Leadership |
| 10 | `event_trade_pairs` | JSON-Liste | Alle `[signal_event_id, trade_id]`-Paare des Members | Vollständige Rückverfolgung zu den auslösenden Trades |
| 11 | `score_avg` | float | Modul 8.4 — siehe `organisation/Filter_Instanz_Parameter.md` |  |
| 12 | `score_max` | int | Modul 8.4 |  |
| 13 | `score_total` | int | Modul 8.4 |  |

**Hinweis:** Members-View enthält bewusst KEIN `total_volume_usd` und KEIN `warnings` (reiner Score-Indikator).

**Format `event_trade_pairs`:**
```json
[["a3f9bc12...", "b8d2ee44..."], ["a3f9bc12...", "c1f7aa90..."], ["d2bb1290...", "b8d2ee44..."]]
```
- Äußerer Array: alle Events des Members
- Innerer Tupel: `[signal_event_id, trade_id]`
- Gleiche `signal_event_id` kann mehrfach vorkommen (Sequenz-/Schwarm-Signale haben mehrere Trades pro Auslösung)
- Gleiche `trade_id` kann mehrfach vorkommen (ein Trade kann mehrere Signale auslösen)

**Regenerationslogik:** Wird bei jedem Pipeline-Lauf komplett neu aus `suspicious_events.csv` aggregiert. Datei wird überschrieben.

---

## Beziehungs-Diagramm

```
branch1_events_stage.csv   ─┐
sequence_events_stage.csv  ─┼─►  suspicious_events.csv  (Master, append-only)
swarm_events_stage.csv     ─┘            │  concat + (signal_event_id, trade_id)-Dedup
                                         │
                                         │  groupby issuer_ticker + Y-Tage-Filter
                                         ▼
                                 suspicious_assets.csv  (View, verfallend)
                                         │
                                         │  groupby member_id (kein Filter)
                                         ▼
                                 suspicious_members.csv (View, dauerhaft)
```

---

## Verschachteltes Beispielszenario

**Setup:**
- `SWARM_MIN_MEMBERS = 2`, `SWARM_WINDOW_DAYS = 7` (rolling)
- `SWARM_CONFLICT_WINDOW_DAYS_BEFORE = 3`, `SWARM_CONFLICT_WINDOW_DAYS_AFTER = 3`
- `SWARM_PARADOX_MIN_CONFLICTS = 2`
- `CONTRARIAN_WINDOW_DAYS = 30`
- Asset `Z` mit `issuer_ticker = Z` und `issuer_group_key = CIK:9999`

**Zeitachse:**

| Tag | Ereignis                       | trade_id    |
|-----|--------------------------------|-------------|
| 0   | Member **X** kauft Z           | `t_X_buy`   |
| 1   | Member **W** kauft Z           | `t_W_buy`   |
| 3   | Member **X** verkauft Z        | `t_X_sell`  |

**Was triggert?**

1. **Tag 1 — `swarm_buy` aktiviert.** Im rolling Window `[Tag −6, Tag 1]` zwei distinkte Members (X, W) auf `CIK:9999`, Direction `buy`.
   → `signal_event_id_A = sha256("2026-XX-01|swarm_buy|CIK:9999|buy|2026-XX-00")[:16]`
   → 2 Zeilen in `suspicious_events.csv`: `t_X_buy` und `t_W_buy` mit `triggering_date = Tag 1`.

2. **Tag 3 — `swarm_conflict`.** Erweitertes Fenster `[Tag 0 − 3, Tag 1 + 3] = [Tag −3, Tag 4]`. Gegen-Trade gefunden: X verkauft Z am Tag 3. Anzahl Gegen-Trades = 1 (unter Paradox-Schwelle 2 → conflict, nicht paradox).
   → `signal_event_id_B = sha256("2026-XX-03|swarm_conflict|conflict|CIK:9999|buy|2026-XX-00")[:16]`
   → 1 Zeile in `suspicious_events.csv`: `t_X_sell` mit `triggering_date = Tag 3`.

3. **Tag 3 — `contrarian`.** Member X hat Z am Tag 0 gekauft und am Tag 3 verkauft (Δ = 3 Tage ≤ `CONTRARIAN_WINDOW_DAYS = 30`).
   → `signal_event_id_C = sha256("2026-XX-03|contrarian|X|Z|2026-XX-00")[:16]`
   → 2 Zeilen in `suspicious_events.csv`: `t_X_buy` und `t_X_sell` mit `triggering_date = Tag 3`.

**Ergebnis: 5 Zeilen in `suspicious_events.csv`:**

| signal_event_id | trade_id   | signal_type     | triggering_date | member |
|-----------------|------------|-----------------|-----------------|--------|
| A               | t_X_buy    | swarm_buy       | Tag 1           | X      |
| A               | t_W_buy    | swarm_buy       | Tag 1           | W      |
| B               | t_X_sell   | swarm_conflict  | Tag 3           | X      |
| C               | t_X_buy    | contrarian      | Tag 3           | X      |
| C               | t_X_sell   | contrarian      | Tag 3           | X      |

**Wirkung auf `suspicious_assets.csv`** (Aggregation nach `issuer_ticker = Z`):

| issuer_ticker | event_count | signal_types                                        | member_count | directions       |
|---------------|-------------|-----------------------------------------------------|--------------|------------------|
| Z             | 5           | swarm_buy:2, swarm_conflict:1, contrarian:2         | 2            | buy:3, sell:2    |

**Wirkung auf `suspicious_members.csv`:**

| member_id | event_count | signal_types                                        | asset_count |
|-----------|-------------|-----------------------------------------------------|-------------|
| X         | 4           | swarm_buy:1, swarm_conflict:1, contrarian:2         | 1           |
| W         | 1           | swarm_buy:1                                         | 1           |

**Erweiterung — Symmetrisches Paradox:** Wenn statt 1 Gegen-Trade ≥ 2 Gegen-Trades auftauchen (z. B. C und D verkaufen Z am Tag 3 und 4), wird statt `swarm_conflict` ein `swarm_paradox` erzeugt — und symmetrisch entsteht zusätzlich ein zweites Paradox aus Sell-Perspektive (C+D bilden einen `swarm_sell`-Schwarm, X+W sind dessen Gegen-Trades). → 2 distinkte `signal_event_id`s vom Typ `swarm_paradox`, 4 Event-Zeilen.

---

## Trade-Score-Formel

**Hard-Gate (2026-06-27):** Triggert ein Trade kein Primärsignal (`SCORING_PRIMARY_SIGNALS`), gibt `compute_trade_score` sofort `(0, '', '')` zurück — Score 0, leerer Breakdown/Warnings. Kanonische Quelle: [Filter_Instanz_Parameter.md](../Filter_Instanz_Parameter.md).

```
trade_score = other_part + swarm_part + ex_officio_bonus + volume_part
```

| Teil | Berechnung | Quell-Spalten |
|---|---|---|
| `other_part` | Σ WEIGHTS[s] für s ∈ Branch-1+Seq ∩ signals_triggered (ohne `trade_volume`) | `signals_triggered` |
| `swarm_raw` | Σ WEIGHTS[s] für s ∈ {swarm_buy, swarm_sell, swarm_conflict} ∩ signals_triggered | `signals_triggered` |
| `swarm_part` | `swarm_raw × PARADOX_DAMPER` wenn `swarm_paradox` getriggert, sonst `swarm_raw × 1.0` | — |
| `ex_officio_bonus` | **v2 (2026-06-12):** `max(SCORING_EX_OFFICIO_ROLE_BONUSES[k] für k in ex_officio_role_keys)` wenn die Role-Key-Menge nicht leer ist UND `SCORING_EX_OFFICIO_TRIGGERS ∩ triggered ≠ ∅`, sonst 0. Wenn `SCORING_EX_OFFICIO_COMMITTEE_GLOBAL == False`, werden `committee_*`-Keys vorher aus der Menge entfernt. MAX, nicht Summe. | `ex_officio_role_keys` |
| `volume_part` | Tier-Score aus `SCORING_TRADE_VOLUME_TIERS`, skaliert um `SCORING_WEIGHTS['trade_volume'] / max(tier_scores)`. Nur wenn `'trade_volume' ∈ triggered` und `SCORING_TRADE_VOLUME_ENABLED`. | `trade_amount_max_usd` |

**`trade_amount_max_usd`** = Obergrenze der Disclosure-Amount-Range (z.B. `$1,001 - $15,000` → 15.000). Befüllt von Stage `trade_volume` via `utils/amount_parser.max_usd`.

**Breakdown-Spalte** (`trade_score_breakdown`): `;`-getrennte `key:value`-Paare in fester Reihenfolge (Branch-1 → Sequenz → Schwarm → `trade_volume` → `ex_officio`), z.B. `committee:10;accumulative:10;swarm_buy(x0):0;trade_volume:7;ex_officio:+10`. Nur getriggerte Signale erscheinen; `ex_officio` ist immer dabei (auch mit Wert 0). `trade_volume` erscheint nur wenn das Signal getriggert hat.

---

## Konfiguration (`config/settings.py`)

| Variable | Default | Wirkung |
|---|---|---|
| `SCORING_PRIMARY_SIGNALS` | `('committee_full', 'swarm_buy')` | **NEU (2026-06-27).** Hard-Gate-Tuple — s. Filter_Instanz_Parameter.md. |
| ~~`COMMITTEE_THRESHOLD`~~ | entfernt | Trigger-Logik: `len(Relevant_Sectors) > 0` (kein Schwellwert mehr) |
| `VOTE_TIMING_HIT_THRESHOLD` | 1 | Untergrenze `vote_timing_hit_count` für `floor_action` |
| `CONTRARIAN_WINDOW_DAYS` | 30 | Max Tage zwischen Buy und Gegen-Sell |
| `ACCUMULATIVE_WINDOW_DAYS` | 30 | Rolling-Window für mehrfaches Aufstocken |
| `ACCUMULATIVE_MIN_COUNT` | 2 | Mindestanzahl Trades im Window |
| `EXTREME_REPORT_DAYS` | 60 | Obere Schwelle `disclosure_date - transaction_date` (sehr späte Meldung) |
| `EXTREME_REPORT_FAST_DAYS` | 5 | Untere Schwelle `disclosure_date - transaction_date` (≤ X = sehr schnelle Meldung) |
| `SUSPICIOUS_ASSET_PERSISTENCE_DAYS` | 30 | Y-Tage-Filter für `suspicious_assets.csv` |
| `SWARM_WINDOW_DAYS` | 7 | Rolling-Window für Schwarm-Erkennung |
| `SWARM_MIN_MEMBERS` | 2 | Mindestanzahl distinct Members im Schwarm |
| `SWARM_GROUPING_KEY` | `"issuer_group_key"` | Gruppierungsspalte — Issuer-Level (CIK-basiert, Cross-Class) |
| `SWARM_TRACK_DIRECTIONS` | `["buy", "sell"]` | Welche Richtungen werten |
| `SWARM_FLAG_CONFLICTS` | True | Aktiviere `swarm_conflict` / `swarm_paradox` |
| `SWARM_CONFLICT_WINDOW_DAYS_BEFORE` | 3 | Tage vor erstem Schwarm-Trade, in denen Gegen-Trades als Konflikt zählen |
| `SWARM_CONFLICT_WINDOW_DAYS_AFTER` | 3 | Tage nach letztem Schwarm-Trade, in denen Gegen-Trades als Konflikt zählen |
| `SWARM_PARADOX_MIN_CONFLICTS` | 2 | Schwelle: ab N Gegen-Trades wird `swarm_paradox` statt `swarm_conflict` erzeugt |

---

## Stand der Implementierung

| Phase | Status | Output |
|---|---|---|
| P1 — `signal_event_id`-Utility + Settings | ✅ | `utils/signal_event_id.py` |
| P2 — Branch-1 Per-Trade-Signale + Stage | ✅ | `branch1_events_stage.csv` |
| P3 — Sequenz-Signale + Stage | ✅ | `sequence_events_stage.csv` |
| P3.5 — `issuer_ticker`-Helper (Fallback `ticker_base → ticker`) | ✅ | `utils/issuer_ticker.py` |
| P4 — Suspicious-Export-Stage | ✅ | `suspicious_events/assets/members.csv` |
| P5 — `share_class_figi`-Spalte + OpenFIGI-Router-Erweiterung | ✅ | trades_master_complete.csv |
| P5.5 — `issuer_ticker` Share-Class-Level | ✅ | abgeleitete Spalte |
| P5.6 — Cross-Class Issuer Mapping via SEC EDGAR CIK | ✅ | `utils/issuer_group_mapper.py`, `issuer_group_key`-Spalte |
| P6 — Schwarm-Signale (buy/sell/conflict/paradox) + Stage | ✅ | `swarm_events_stage.csv`, 4 `swarm_*` Signal-Typen |
| P7 — Doku-Finalisierung | 🔄 | Konsistenz-Check über alle `organisation/`-Dateien |
