# Briefing: Pipeline-Stage `post_trade_returns`

> **Zielgruppe**: Folge-Agenten, die an `--stage_post_trade_returns`
> debuggen, erweitern oder Architektur-Entscheidungen treffen.
> **Stand**: 2026-05-29 (nach Ticker-System-Refactor vom 28.05.2026).
> **Single-Source**: Pipeline-Details in `Pipelines.md`, Spalten in
> `DATA_STRUCTURE.md`. Dieses Briefing fasst aktiv genutzte Bezüge
> zusammen und markiert veraltete Konzepte.

---

## 0. WICHTIG — Veraltete Konzepte (Anti-Stale-Liste)

Diese Begriffe/Symbole tauchen ggf. in alten Chat-Kontexten,
Commit-Messages oder älteren Doku-Versionen auf — **sie existieren im
aktuellen Code nicht mehr**:

| Veraltetes Konzept | Aktueller Stand |
|---|---|
| `APPLICABLE_TYPES` (Set in `post_trade_returns.py`) | **Entfernt.** Ersetzt durch `POST_TRADE_PRICE_TYPES` aus `utils/ticker_routing.py` |
| Master-Spalte `api_strategy` | **Nicht mehr persistiert.** Wird vor jedem Master-Schreiben via `_strip_master_internal()` (import_trades.py Z. 111-114) gestrippt. Bleibt nur als Klassen-internes Feld in `TickerClassification` |
| `Vorzugsaktien_mapping.csv`, `unmatched_preferred.csv` | **Gelöscht.** Preferred-Resolution läuft jetzt automatisch über `parent_ticker` (Feld in `TickerClassification`), das in Sektor-Stage konsumiert wird |
| `_fetch_with_hyphen_fallback` (Slash-Retry `BRK-A → BRK/A`) | **Entfernt.** 0 Cache-Treffer in 2.730 Einträgen → toter Code |
| `_YF_ELIGIBLE_TYPES` (lokales Set in `ticker_sector.py`) | **Entfernt.** Ersetzt durch `SECTOR_LOOKUP_TYPES` aus `utils/ticker_routing.py` |
| `_resolve_preferred`, `_load_preferred_mapping`, `_write_unmatched_preferred` | **Entfernt** aus `utils/ticker_sector.py` |

Konkret für `post_trade_returns`: **wenn ein Pfad/Code irgendwo
`APPLICABLE_TYPES` referenziert, ist er stale**. Nur
`POST_TRADE_PRICE_TYPES` (TickerType-Enum-Set) und
`_APPLICABLE_TYPE_STRINGS` (String-Set für DataFrame-Vergleiche, Z. 37
in post_trade_returns.py) sind aktuell.

---

## 1. Was die Stage tut

Pro Trade werden Aktienkurse zu 30/60/90/120 Tagen nach dem Trade
ermittelt und gegensätzliche Trades desselben Politikers im
Issuer-Bucket erkannt (Exit-Detection).

**Pipeline-Position**: Stage #9 von 17 (Aufruf:
`scripts/import_trades.py::_stage_post_trade_returns`, Z. 2115-2130;
weitere Aufrufe in Voll-Lauf-Pfaden Z. 2522 und 2812).

**Implementierung**: `scoring/post_trade_returns.py::compute_post_trade_returns`
(Z. 297).

---

## 2. Input-Voraussetzungen

Aus `STAGE_SCHEMAS['post_trade_returns']` in `import_trades.py`
(Z. 261). Der DataFrame muss vor der Stage diese Spalten haben:

- Identität: `trade_id`, `name`, `ticker`, `issuer_group_key`
- Zeit: `transaction_date`
- Richtung: `transaction_type` (Purchase/Sale)
- Volumen: `trade_amount_max_usd` (kommt aus Stage `trade_volume`)
- Routing: `ticker_type`

**Harte Abhängigkeit**: `trade_volume` (Stage #8) muss vorher gelaufen
sein. Wenn `trade_amount_max_usd` fehlt → Exit-Detection ist defekt
(siehe `import_trades.py` Kommentare Z. 257, 2500, 2803:
`MUSS vor post_trade_returns laufen`).

Validierung: `_check_stage_inputs` (im import_trades.py-Stage-Runner).

---

## 3. Output (14 neue Spalten)

| Spalte | Inhalt |
|---|---|
| `post_trade_baseline_price` | Close am/nach Trade-Tag |
| `post_price_30d`/`_60d`/`_90d`/`_120d` | Close X Tage nach Trade |
| `post_return_30d`/`_60d`/`_90d`/`_120d` | `(price/baseline-1)*100` |
| `exit_status` | `closed_full` / `closed_partial` / `held` / `unknown` / `delisted` / `not_applicable` |
| `exit_date` | Datum des gegensätzlichen Trades |
| `exit_price` | Close am Exit-Datum |
| `exit_amount_max_usd` | Kumulierte Gegenseite-USD |
| `effective_return_pct` | Return bis Exit (oder bis Max-Horizont bei `held`) |
| `opposing_trade_ids` | Semikolon-separierte trade_ids der Gegen-Trades |

Spalten-Definitionen sind in `DATA_STRUCTURE.md` (Post-Trade-Sektion)
maßgeblich; bei Diskrepanz gilt das, was `compute_post_trade_returns`
tatsächlich schreibt.

---

## 4. Filter-Logik (`_is_applicable`, Z. 439)

Ein Trade durchläuft die Stage NUR, wenn alle vier Bedingungen
erfüllt sind:

1. `ticker_type ∈ POST_TRADE_PRICE_TYPES` →
   `{STOCK, SHARECLASS, FOREIGN_STOCK, ETF}`
   (siehe `utils/ticker_routing.py` Z. 25-30)
2. `issuer_group_key` startet NICHT mit `'SKIP:'`
3. `ticker` passt auf Regex `^[A-Z0-9][A-Z0-9.\-^]{0,14}$`
   (`_VALID_TICKER_RE`, Z. 42)
4. `transaction_date` ist nicht NaT und ≤ heute

**Warum kein `NUMERIC_SUFFIX`**: Symbole wie `MTCH1`, `BAC1` sind
Reverse-Split-/Reorg-Artefakte; yfinance liefert für sie historische
Preise, die zur aktuellen Listing-Identität nicht passen
(Code-Kommentar `utils/ticker_routing.py` Z. 23-24).

**Skip-Counter**: Print `_post_trade_applicable` zeigt Anteil.

---

## 5. Grouping & Exit-Detection

**Gruppen-Key**: `name + '|||' + issuer_group_key` (Z. 513-516).
Begründung: Insider-Exits sind auf Issuer-Level zu erkennen
(Multi-Class wie GOOGL/GOOG werden über `issuer_group_key` als
ein Bucket gesehen). Siehe `Ticker_Architektur.md` für Details.

**Defensive Maskierung** (Z. 520): Zeilen mit `nan|||*` oder
`*|||nan` werden ausgeklammert — sonst würde NaN-Identität zu
falschen Gruppen führen.

**Exit-Fenster**: `[transaction_date, transaction_date + max_horizon]`,
chronologisch durchlaufen; akkumuliert Gegen-Trade-USD bis Schwelle
(`trade_amount_max_usd` ± `POST_TRADE_EXIT_AMOUNT_TOLERANCE`).
Status:
- `closed_full` — Gegenseite ≥ Original-Amount
- `closed_partial` — Gegenseite > 0, aber < Original
- `held` — keine Gegenseite im Fenster
- `delisted` — Cache hat Marker-Row (siehe §6)
- `unknown` — Ticker kein Preis-Hit
- `not_applicable` — Trade hat Filter aus §4 nicht passiert

---

## 6. Preis-Cache `data/cache/price_history.parquet`

**Schema**: MultiIndex `[ticker, date]`, Spalte `close` (float64).

**Pfad**: `settings.POST_TRADE_PRICE_CACHE = 'data/cache/price_history.parquet'`
(`config/settings.py` Z. 472).

**TTL**: `POST_TRADE_PRICE_CACHE_TTL_DAYS = 7` (Z. 476). Refresh,
wenn `max(date) pro Ticker < today - 7d`.

**Inkrementelles Flushing**: alle `POST_TRADE_CACHE_FLUSH_EVERY = 50`
erfolgreichen Fetches wird der Cache atomar geschrieben
(tmp + `os.replace()`, Z. 78-80). Schützt vor Datenverlust bei
Exception in Stunden-langen Erst-Läufen.

**Delisted-Marker**: Spezial-Row `(ticker, 1900-01-01, NaN)`
(Z. 199-205). Verhindert wiederholten yfinance-Call für tote Ticker.

**Fetch-Horizont**: `POST_TRADE_PRICE_FETCH_YEARS = 5` (Z. 474).

---

## 7. yfinance-Robustheit

`_is_valid_yf_ticker()` (Z. 45) ist die einzige Stelle, an der
yfinance-Input gefiltert wird. Wenn dort ein Bug ist, schlägt die
ganze Stage Stunden später mit HTTP 500 fehl.

**Ausgesperrte Inputs**:
- `None`, `NaN`, Strings `'nan'` / `'none'`, leer
- Tickers mit `/` (yfinance → HTTP 500, URL-Pfad-Trenner)
- Tickers mit `%` (bricht JSON-Parsing)
- Datums-Strings wie `'07/01/28'` (Regex matcht nicht)

**Logger**: yfinance + urllib3 auf `CRITICAL` (Z. 318-319), sonst
Spam pro Ticker. Fortschritts-Output läuft über eigenen `print`/`logger`.

---

## 8. Settings (`config/settings.py`)

| Konstante | Wert | Zweck |
|---|---|---|
| `POST_TRADE_HORIZONS_DAYS` | `[30, 60, 90, 120]` | Welche Horizonte ausgewertet werden |
| `POST_TRADE_PRICE_CACHE` | `'data/cache/price_history.parquet'` | Pfad zum Preis-Cache |
| `POST_TRADE_PRICE_FETCH_YEARS` | `5` | yfinance Fetch-Tiefe |
| `POST_TRADE_PRICE_CACHE_TTL_DAYS` | `7` | Cache-Refresh-Schwelle |
| `POST_TRADE_EXIT_AMOUNT_TOLERANCE` | `0.0` | Schwelle für `closed_full` vs. `closed_partial` |
| `POST_TRADE_CACHE_FLUSH_EVERY` | `50` | Flush-Frequenz |

---

## 9. Pipeline-Reihenfolge (17 Stages)

```
1. rename → 2. date → 3. committees → 4. tickerclassify →
5. tickeropenfigi → 6. issuer_group → 7. tickeryfinance →
8. trade_volume   ← MUSS vor #9
9. post_trade_returns
10. asset_volume → 11. gates_tags → 12-15. signals →
16. scoring → 17. save
```

Vor Stage #9 müssen also alle Ticker-/Issuer-/Volume-Spalten gesetzt
sein. `issuer_group_key` kommt aus Stage #6 (SEC EDGAR CIK-Lookup);
ohne diese Spalte versagt die Gruppierung.

---

## 10. Defensiv-Patterns im Code

Nicht offensichtliche Robustheits-Mechanismen — vor Refactoring genau
ansehen, sonst Regressionsrisiko:

- **Z. 114-115**: Single-Row-yfinance-Resultat kollabiert zu Skalar →
  expliziter Rebuild zu Series.
- **Z. 94-107**: MultiIndex-Spalten-Handling für Multi-Ticker-Download
  (yfinance liefert je nach Anzahl unterschiedliche Strukturen).
- **Z. 386-387**: Zukunfts-Preise (`transaction_date + horizon > heute`)
  → NaN, nicht 0.
- **Z. 504-507**: Defensive `.copy()` + erneute `transaction_date`-
  Konvertierung. Hintergrund: Test-Caller, der `_compute_exit_columns`
  direkt aufruft, liefert evtl. String-Datum.
- **Cache-Schreib-Pfad** ist atomar (`tmp + os.replace`), nie direkt
  in die Ziel-Datei.

---

## 11. Verwandte Module / Cross-Cutting-Bezüge

| Modul | Beziehung |
|---|---|
| `utils/ticker_routing.py` | Liefert `POST_TRADE_PRICE_TYPES` (nicht `APPLICABLE_TYPES`) |
| `utils/ticker_classifier.py` | Setzt `ticker_type` und `parent_ticker`; letzteres ist für post_trade_returns **nicht** relevant (nur für Sektor-Stage) |
| `utils/issuer_group_mapper.py` | Liefert `issuer_group_key` — Grouping-Basis für Exit-Detection |
| `scoring/asset_volume.py` | Ähnliches Pattern (Pandas + Cache). Stage #10, läuft NACH post_trade_returns |
| `scoring/trade_score.py` | Konsumiert `effective_return_pct`? — nein, Score läuft auf Signals, nicht direkt auf Returns |

---

## 12. Bekannte preexisting Issues (nicht durch Refactor verursacht)

Beim aktuellen Pytest-Lauf (29.05.2026):

- `tests/test_house_scraper.py::TestSave::test_dedup_subset_matches_settings` —
  `DEDUP_SUBSET` (House-Scraper) und `settings.DUPLICATE_CHECK_COLUMNS`
  sind out-of-sync (zusätzliche Spalten `asset_type`, `owner`, `ptr_link`
  vs. `subholding`).
- `tests/test_senate_scraper.py::TestDuplicateCheckColumnsIncludesOwner` —
  `'owner'` fehlt in `DUPLICATE_CHECK_COLUMNS`.

Beide betreffen Scraper-Dedup-Logik, nicht post_trade_returns oder
das Ticker-System. Nicht durch Stage #9 fixen.

---

## 13. Schnell-Checks beim Debugging

1. **`exit_status` ist überall `not_applicable`** → §4 prüfen
   (TickerType-Set? Regex? `transaction_date` NaT?)
2. **`exit_status` ist überall `unknown`** → §6 prüfen (Cache leer?
   Delete `data/cache/price_history.parquet` und neu fetchen).
3. **`exit_status` ist überall `held`** → Grouping-Key falsch?
   `issuer_group_key` für die Trades anzeigen, ggf. `SKIP:`-Prefix.
4. **HTTP 500 / 404 von yfinance** → `_is_valid_yf_ticker`
   (§7) hat ein Loch. Regex erweitern, nicht Try/Except.
5. **Stage läuft, aber `trade_amount_max_usd` ist NaN** →
   `trade_volume` (Stage #8) ist nicht gelaufen. Stage-Reihenfolge prüfen.
6. **`MASTER_INTERNAL_COLUMNS` enthält `api_strategy`** — das ist
   so gewollt (siehe §0). Niemals die Spalte zum Master hinzufügen.
