# Preis-Aktualisierung pro `import_trades.py`-Lauf

> **Frage, die dieses Dokument beantwortet:** *Welche Aktienpreise werden wann durch welchen
> `import_trades.py`-Lauf (Normal vs. `--delta`) aktualisiert?*
>
> **Stage-Interna** (Exit-Detection-Mechanik, yfinance-Robustheit, Spalten-Definitionen im Detail):
> `organisation/Briefing_stage_post_trade_performance.md`. **Spalten-Referenz:** `DATA_STRUCTURE.md`
> Abschnitt 6. **Stage-Reihenfolge / CLI:** `Pipelines.md` + `CLI_Bedienungsanleitung.md`.
> Dieses Dokument dupliziert das nicht, sondern erklärt nur den **Aktualisierungs-Zeitpunkt** der
> Preis-Felder je Lauf-Modus.

---

## TL;DR (Ein-Satz-Antwort)

Es gibt **zwei** Preis-Berechnungen mit **unterschiedlichem Aktualisierungs-Rhythmus**:

1. **Historische Returns** (`post_trade_baseline_price`, `post_price_{30..180}d`, `post_return_{30..180}d`,
   `exit_*`, `effective_return_*`) → werden **nur für die jeweils verarbeiteten Trades** geschrieben.
   Im **Normal-Lauf = alle Trades**; im **`--delta`-Lauf = nur die neuen Trades**. Bestehende Trades
   behalten im Delta-Lauf ihre alten Werte.
2. **Mark-to-Market** (`paper_return_pct`, `price_as_of`) → wird bei **jedem** Lauf (auch `--delta`)
   **für das ganze Recent-Fenster (180 Tage)** neu berechnet, weil sich der aktuelle Kurs täglich
   ändert. **Delta-unabhängig.**

Der **Preis-Cache** (`data/cache/price_history.parquet`) wird in beiden Modi refresht — aber nur für
die *relevanten Ticker des jeweiligen Laufs*, nicht zwingend für alle.

---

## 1. Die Preis-Variablen und woher sie kommen

Alle Preis-Felder entstehen in **einer einzigen Pipeline-Stage**: `--stage_post_trade_performance`
(Stage 9 von 17), `scripts/import_trades.py::_stage_post_trade_performance` (Z. 2150). Sie ruft zwei
Funktionen aus `scoring/post_trade_returns.py` auf.

| Spalte | Bedeutung | Preisquelle | Berechnet von |
|---|---|---|---|
| `post_trade_baseline_price` | Einstandskurs = erster Close am/nach `transaction_date` | Cache-Snapshot | `compute_post_trade_returns` (Z. 400) |
| `post_price_{30,60,90,120,180}d` | Close X Kalendertage nach dem Trade | Cache-Snapshot | `compute_post_trade_returns` |
| `post_return_{30,60,90,120,180}d` | `(post_price_Xd / baseline − 1) · 100 · sell_sign` (richtungsbewusst) | Cache-Snapshot | `compute_post_trade_returns` |
| `exit_status`, `exit_date`, `exit_price`, `exit_amount_max_usd`, `opposing_trade_ids`, `first_close_offset_days` | Exit-Detection (gegenläufiger Trade auf Issuer-Ebene) | Cache-Snapshot + DataFrame | `compute_post_trade_returns` → `_compute_exit_columns` |
| `effective_return_pct`, `effective_return_horizon_days` | **Realisierter** Return — nur bei `closed_full/closed_partial`, sonst NaN | Cache-Snapshot | `compute_post_trade_returns` |
| `paper_return_pct` | **Mark-to-Market**: `(jüngster Close / Live-Baseline − 1) · 100 · sell_sign`, nur Recent-Fenster | Live-Cache (täglich) | `compute_paper_returns` (Z. 660) |
| `price_as_of` | Datum des für `paper_return_pct` verwendeten jüngsten Close (zeigt Staleness) | Live-Cache (täglich) | `compute_paper_returns` |

**Richtungsbewusstsein:** Bei Verkäufen invertiert `_sell_sign_series` (Z. 383) das Vorzeichen aller
Return-Spalten — „positiv" bedeutet immer „gutes Timing".

---

## 2. Zwei Berechnungswege im Vergleich

| Aspekt | `compute_post_trade_returns` (historisch) | `compute_paper_returns` (Mark-to-Market) |
|---|---|---|
| **Was** | Baseline + feste Horizonte (30/60/90/120/180d) + Exit-Detection + realisierter Return | Aktueller Papier-Gewinn/-Verlust offener Positionen |
| **Welche Trades** | alle *post-trade-applicable* Trades des übergebenen Frames | nur Trades mit `transaction_date ≥ heute − 180 Tage` (`RECENT_MONITORING_WINDOW_DAYS`) |
| **Baseline (Nenner)** | gespeichert: erster Close ≥ `transaction_date` | **live aus aktuellem Cache** neu abgeleitet (erster Close ≥ `transaction_date`) |
| **Zähler** | Close zum Horizont X bzw. am Exit-Datum | jüngster verfügbarer Close |
| **Split-Verhalten** | gespeicherter Wert kann nach rückwirkendem Split *stale* sein (s. §5) | **split-immun** — Zähler und Nenner aus demselben Cache-Stand (Härtung `d1a2cf5`) |
| **Aktualisierungs-Rhythmus** | nur für die im Lauf verarbeiteten Trades | **delta-unabhängig**, jeden Lauf über das ganze Recent-Fenster |

Der „delta-unabhängig"-Vertrag steht explizit im Docstring von `compute_paper_returns`
(`scoring/post_trade_returns.py:672`) und in `_compute_recent_paper_returns`
(`scripts/import_trades.py:2131`).

---

## 3. Der Preis-Cache (`data/cache/price_history.parquet`)

- **Format:** Parquet, MultiIndex `(ticker, date)`, Spalte `close` (split-/dividenden-adjustiert,
  `yf.download(..., auto_adjust=True)`).
- **Quelle:** yfinance, pro Ticker einzeln, 5-Jahres-Historie (`POST_TRADE_PRICE_FETCH_YEARS = 5`),
  inkrementelles Flushing alle 50 Ticker (`POST_TRADE_CACHE_FLUSH_EVERY = 50`).
- **Refresh-Logik** (`_ensure_cache_for_tickers`, Z. 122) — **zwei verschiedene Frische-Schwellen:**

| Aufrufer | Frische-Schwelle | Effekt |
|---|---|---|
| `compute_post_trade_returns` | Ticker refetchen, wenn jüngster Close älter als `POST_TRADE_PRICE_CACHE_TTL_DAYS = 7` Tage | historische Horizonte brauchen keine Tagesaktualität |
| `compute_paper_returns` | Ticker refetchen, wenn jüngster Close älter als letzter Handelstag (`heute − BDay(RECENT_PRICE_MAX_AGE_BUSINESS_DAYS=1)`) | Mark-to-Market braucht den aktuellen Kurs |
| **delistete Ticker** (NaN-Marker, seit 2026-06-14) | Re-Check nur, wenn `last_checked_date` älter als `POST_TRADE_DELISTED_RECHECK_DAYS = 30` Tage | verhindert wiederholte yfinance-Calls für tote Ticker, ohne sie dauerhaft auszuschließen |

Echte Kurse folgen weiterhin der Preis-TTL (Zeile 1/2); nur die Re-Check-Frequenz
**delisteter** Ticker ist von der separaten `last_checked`-TTL gesteuert. Details
zum NetworkCanary-gestützten Marker-Schema: `organisation/Briefing_stage_post_trade_performance.md`.

> **Wichtig:** Refresht werden in einem Lauf nur die **Ticker, die der Lauf gerade berührt** — im
> Normal-Lauf alle applicable Ticker, im `--delta`-Lauf die Ticker aus *History ∪ Delta* (Historie
> nötig fürs Exit-Detection-Fenster) bzw. fürs Mark-to-Market die Recent-Ticker.

---

## 4. Lauf-Modi → welche Preise werden aktualisiert

| Lauf | Verarbeitete Trades | Historische Returns (`post_*`, `exit_*`, `effective_*`) | Mark-to-Market (`paper_return_pct`, `price_as_of`) | Cache-Refresh | Master |
|---|---|---|---|---|---|
| `import_trades.py` (Normal / Full Rebuild) | **alle** | **für alle Trades neu** | für ganzes Recent-Fenster | alle applicable Ticker (TTL 7d) | komplett neu geschrieben |
| `import_trades.py --delta` | **nur neue** (via `trade_id`) | **nur für neue Trades**; bestehende bleiben unverändert | **für ganzes Recent-Fenster** (delta-unabhängig) | Ticker aus History ∪ Delta | Merge via `trade_id` |
| `import_trades.py --dry_run` | alle | für alle (in Staging) | ganzes Recent-Fenster (Stage-Frame) | wie Normal | `trades_stage.csv`, Master unberührt |
| `import_trades.py --stage_post_trade_performance` (Einzel-Stage, ohne `--delta`) | alle (voller Master) | **für alle Trades neu** | ganzes Recent-Fenster | alle applicable Ticker | s. Stage-I/O |

**Wie der Delta-Lauf die historischen Returns berechnet** (`scripts/import_trades.py:2551`): er baut
die Union aus Master-Historie + Delta, ruft `compute_post_trade_returns` darauf (deshalb wird der
Cache auch für Historie-Ticker refresht — Exit-Detection braucht das Fenster) und **slict danach nur
die Delta-Zeilen zurück** ins Ergebnis. Die für Historie-Trades neu berechneten Werte werden also
**verworfen** — bestehende Master-Zeilen behalten ihre gespeicherten Werte.

---

## 5. Die Delta-Falle: rückwirkende Kurs-Korrekturen (Splits/Backfills)

Aus §4 folgt eine wichtige Konsequenz:

> Im `--delta`-Lauf werden die **historischen** Preis-Spalten alter Trades **nie neu geschrieben** —
> selbst wenn der Cache-Refresh eine rückwirkende Korrektur (z. B. einen Aktiensplit) einsammelt.

Konkret: Wird ein Ticker rückwirkend split-adjustiert (Beispiel KLAC 10:1), enthält der Cache danach
post-split-Kurse, aber `post_trade_baseline_price` / `post_return_{X}d` alter Trades stehen noch auf
**pre-split**-Werten → verfälschte Returns um den Split-Faktor.

- **`paper_return_pct` ist davon NICHT betroffen** — es leitet die Baseline live aus dem aktuellen
  Cache ab (Härtung `d1a2cf5`, §2), Zähler und Nenner stammen aus demselben Stand → split-immun.
- **Die historischen `post_*`-Spalten müssen aktiv repariert werden:** ein **voller** Lauf der
  Stage, der *alle* Trades neu berechnet — nicht `--delta`:
  ```bash
  python scripts/import_trades.py --stage_post_trade_performance
  ```
  (oder ein kompletter Full Rebuild). Erst dieser schreibt split-konsistente Baselines + Horizonte
  für den gesamten Bestand zurück.

---

## 6. Automatisierung (systemd)

Der nächtliche Lauf (`deploy/systemd/quiver-import.{service,timer}`, täglich 03:00) ruft:

```
quiver_scraper.py  &&  import_trades.py --delta
```

→ also **immer `--delta`**. Daraus folgt für den Normalbetrieb:

- Neue Trades bekommen jede Nacht ihre historischen Returns.
- `paper_return_pct` aller offenen Positionen im 180-Tage-Fenster wird jede Nacht frisch
  Mark-to-Market bewertet.
- **Rückwirkende Korrekturen alter Trades passieren nachts NICHT** — dafür ist gelegentlich ein
  manueller voller `--stage_post_trade_performance`-Lauf nötig (s. §5).

---

## Querverweise

- **Stage-Interna / Exit-Detection / yfinance-Robustheit:** `organisation/Briefing_stage_post_trade_performance.md`
- **Spalten-Definitionen:** `organisation/DATA_STRUCTURE.md` (Abschnitt 6 + Cache Abschnitt 3)
- **Stage-Reihenfolge:** `organisation/Pipelines.md`
- **CLI / Delta-Modus:** `organisation/CLI_Bedienungsanleitung.md`
- **Code:** `scoring/post_trade_returns.py` (`compute_post_trade_returns`, `compute_paper_returns`),
  `scripts/import_trades.py` (`_stage_post_trade_performance`, `_compute_recent_paper_returns`, `run`, `run_delta`)
- **Settings:** `config/settings.py` (Z. 527–579: Horizonte, Cache-TTL, Recent-Fenster)
