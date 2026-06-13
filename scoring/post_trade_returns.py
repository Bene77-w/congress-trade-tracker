"""Post-Trade Performance Tracking (Modul 9.x).

Ermittelt fuer jeden Trade den Aktienkurs in mehreren Zeithorizonten nach
dem Trade und erkennt gegensaetzliche Trades (Exit-Detection) innerhalb des
Fensters.

Pattern: scoring/asset_volume.py (reines Pandas, kein Side-Effect ausser Cache).

Robustheits-Hinweise:
- Ticker werden vor jedem yfinance-Aufruf strikt validiert (Datums-Strings
  wie '07/01/28' oder Sonderzeichen wie '3%' werden ausgesperrt).
- Cache wird inkrementell geflusht (alle POST_TRADE_CACHE_FLUSH_EVERY
  erfolgreiche Fetches), damit bei einer Exception nicht die gesamte
  Erstlauf-Arbeit (~1-2h) verloren geht.
- yfinance-Logger wird auf CRITICAL gesetzt; alle Fortschritts-Infos
  gehen ueber den eigenen logger / print.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf

from utils.ticker_routing import POST_TRADE_PRICE_TYPES

logger = logging.getLogger(__name__)

SKIP_PREFIX = 'SKIP:'
DELISTED_MARKER_DATE = pd.Timestamp('1900-01-01')
# String-Set, weil DataFrame-Spalte ticker_type Strings haelt.
_APPLICABLE_TYPE_STRINGS = frozenset(t.value for t in POST_TRADE_PRICE_TYPES)

# Ein valides yfinance-Symbol besteht aus Buchstaben/Ziffern und einer kleinen
# Whitelist an Sonderzeichen (Class-Shares wie BRK.B, BRK-B, Vorzugsaktien
# wie BAC^L, internationale wie SAP.DE).
_VALID_TICKER_RE = re.compile(r'^[A-Z0-9][A-Z0-9.\-^]{0,14}$')


def _is_valid_yf_ticker(ticker: object) -> bool:
    """Pruefung VOR jedem yfinance-Call.

    Filtert Datums-Strings ('07/01/28'), Sonderzeichen ('3%'),
    NaN/None und leere Strings aus. Yfinance interpretiert '/' als
    URL-Pfad-Trenner -> HTTP 500; '%' bricht JSON-Parsing.
    """
    if ticker is None:
        return False
    s = str(ticker).strip().upper()
    if not s or s in {'NAN', 'NONE'}:
        return False
    return bool(_VALID_TICKER_RE.match(s))


def _load_price_cache(cache_path: str) -> pd.DataFrame:
    """Laedt parquet falls existiert, sonst leerer DataFrame."""
    if os.path.isfile(cache_path):
        df = pd.read_parquet(cache_path)
        if not isinstance(df.index, pd.MultiIndex):
            df = df.set_index(['ticker', 'date'])
        return df
    idx = pd.MultiIndex.from_arrays(
        [[], []], names=['ticker', 'date']
    )
    return pd.DataFrame({'close': pd.Series(dtype='float64')}, index=idx)


def _save_price_cache(df: pd.DataFrame, cache_path: str) -> None:
    """Atomar speichern: write tmp + rename. Niemals halber Cache."""
    parent = os.path.dirname(cache_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = cache_path + '.tmp'
    df.to_parquet(tmp, index=True)
    os.replace(tmp, cache_path)


def _yf_close_series(hist: pd.DataFrame) -> pd.Series | None:
    """Extrahiere robust eine Close-Series aus dem yfinance-Resultat.

    yfinance liefert je nach Symbol mal Single-Ticker-DataFrame (Spalte 'Close'),
    mal Multi-Ticker-DataFrame (MultiIndex Spalten). Bei einem einzigen
    Handelstag kollabiert ``.squeeze()`` zum nackten Skalar — das hat
    historisch die Stage gecrasht. Wir gehen den sicheren Weg ueber
    expliziten Aufbau einer Series.
    """
    if hist is None or hist.empty:
        return None
    if isinstance(hist.columns, pd.MultiIndex):
        # Wir laden nur einen Ticker zur Zeit -> erste Ebene = Field, zweite = Ticker.
        if 'Close' in hist.columns.get_level_values(0):
            close = hist['Close']
        else:
            return None
        if isinstance(close, pd.DataFrame):
            if close.shape[1] != 1:
                return None
            close = close.iloc[:, 0]
    else:
        if 'Close' not in hist.columns:
            return None
        close = hist['Close']
    # An dieser Stelle ist `close` eine Series ODER ein 1-Zeilen-DataFrame.
    if isinstance(close, pd.DataFrame):
        if close.shape[1] != 1:
            return None
        close = close.iloc[:, 0]
    # Falls _doch_ ein Skalar kam (single-row + reduzierte Form), Series bauen.
    if np.isscalar(close) or isinstance(close, (np.floating, np.integer, float, int)):
        return pd.Series([float(close)], index=[hist.index[0]], name='close')
    series = pd.Series(close.values, index=hist.index, name='close')
    series.index = pd.to_datetime(series.index).normalize()
    return series.dropna()


def _ensure_cache_for_tickers(
    cache: pd.DataFrame,
    tickers: Iterable[str],
    fetch_years: int = 5,
    cache_ttl_days: int = 7,
    cache_path: str | None = None,
    flush_every: int = 50,
    cutoff_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Stellt sicher dass alle Ticker frisch im Cache sind.

    Refresh-Kriterium: max(date) pro Ticker < cutoff.
    Standard-Cutoff: ``today - cache_ttl_days`` (Kalendertage). Wird
    ``cutoff_date`` gesetzt, ueberschreibt es den Kalender-TTL — z.B. fuer die
    Recent-Monitoring-Frische, die "letzter abgeschlossener Handelstag" (BDay)
    statt fixer Kalendertage verlangt.
    Leere History -> Marker-Row (1900-01-01, NaN) = delisted.
    Inkrementelles Flushing: alle ``flush_every`` Ticker wird der Cache
    atomar nach ``cache_path`` geschrieben (falls Pfad gesetzt).
    """
    today = pd.Timestamp.now().normalize()
    if cutoff_date is not None:
        cutoff = pd.Timestamp(cutoff_date).normalize()
    else:
        cutoff = today - pd.Timedelta(days=cache_ttl_days)

    cache_idx_tickers = set(cache.index.get_level_values(0).unique()) if len(cache) else set()
    seen: set[str] = set()
    need_refresh: list[str] = []
    invalid_skipped = 0
    for raw in tickers:
        if not _is_valid_yf_ticker(raw):
            invalid_skipped += 1
            continue
        tkr = str(raw).strip().upper()
        if tkr in seen:
            continue
        seen.add(tkr)
        if tkr in cache_idx_tickers:
            try:
                max_date = cache.loc[tkr].index.max()
            except (KeyError, ValueError):
                max_date = None
            if max_date is not None and max_date >= cutoff:
                continue  # fresh
        need_refresh.append(tkr)

    if invalid_skipped:
        print(f"  Ticker-Validierung: {invalid_skipped} ungueltige Symbole "
              f"uebersprungen (Datums-Strings, Sonderzeichen).")

    if not need_refresh:
        print(f"  Cache warm: alle {len(seen)} validen Ticker frisch "
              f"(TTL {cache_ttl_days}d).")
        return cache

    n_need = len(need_refresh)
    print(f"  Cache-Refresh: {n_need} von {len(seen)} validen Tickern "
          f"brauchen yfinance-Fetch ({fetch_years}y daily).")
    print(f"  Inkrementelles Flushen alle {flush_every} Ticker -> "
          f"{cache_path or '(kein Save)'}.")

    new_rows: list[pd.DataFrame] = []
    fetched = 0
    delisted = 0
    errors = 0
    flushed_at = 0

    for i, tkr in enumerate(sorted(need_refresh), start=1):
        try:
            hist = yf.download(
                tkr,
                period=f'{fetch_years}y',
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.debug("yfinance-Exception fuer %s: %s", tkr, exc)
            _progress_tick(i, n_need, tkr, fetched, delisted, errors)
            continue

        series = _yf_close_series(hist)
        if series is None or series.empty:
            # Delisted: Marker-Row (1900-01-01, NaN)
            marker = pd.DataFrame(
                {'close': [np.nan]},
                index=pd.MultiIndex.from_tuples(
                    [(tkr, DELISTED_MARKER_DATE)],
                    names=['ticker', 'date'],
                ),
            )
            new_rows.append(marker)
            delisted += 1
        else:
            block = pd.DataFrame(
                {'close': series.values},
                index=pd.MultiIndex.from_arrays(
                    [[tkr] * len(series), series.index],
                    names=['ticker', 'date'],
                ),
            )
            new_rows.append(block)
            fetched += 1

        _progress_tick(i, n_need, tkr, fetched, delisted, errors)

        # Inkrementeller Flush
        if cache_path and (fetched + delisted - flushed_at) >= flush_every:
            cache = _merge_into_cache(cache, new_rows)
            new_rows = []
            try:
                _save_price_cache(cache, cache_path)
                flushed_at = fetched + delisted
                print(f"  [flush] Cache gespeichert nach {i}/{n_need} Tickern.")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Cache-Flush fehlgeschlagen: %s", exc)

    if new_rows:
        cache = _merge_into_cache(cache, new_rows)
        if cache_path:
            try:
                _save_price_cache(cache, cache_path)
                print(f"  [flush] Finaler Cache-Save ({n_need}/{n_need}).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Finaler Cache-Save fehlgeschlagen: %s", exc)

    print(f"  Cache-Refresh fertig: {fetched} gefetched, {delisted} delisted, "
          f"{errors} errors (von {n_need} requested).")
    return cache


def _merge_into_cache(cache: pd.DataFrame,
                     new_rows: list[pd.DataFrame]) -> pd.DataFrame:
    """Hilfsfunktion: konkateniert neue Bloecke in den Cache (dedup auf Index)."""
    if not new_rows:
        return cache
    combined = pd.concat([cache, *new_rows]) if len(cache) else pd.concat(new_rows)
    # Bei Doppel-Einträgen (z.B. erneuter Fetch nach TTL) den letzten gewinnen lassen.
    combined = combined[~combined.index.duplicated(keep='last')]
    return combined.sort_index()


def _progress_tick(i: int, total: int, tkr: str,
                  fetched: int, delisted: int, errors: int) -> None:
    """Schreibt eine neue Progress-Zeile alle 10 Ticker (fortlaufend, kein \\r)."""
    if total <= 0:
        return
    if i != total and i % 10 != 0:
        return
    pct = (i / total) * 100.0
    print(f"  [{i:>5d}/{total}] {pct:5.1f}%  "
          f"ok={fetched}  delisted={delisted}  err={errors}  "
          f"letzter={tkr}", flush=True)


def _price_at_or_after(
    cache: pd.DataFrame, ticker: object, target_date: pd.Timestamp,
) -> float | None:
    """Erster close >= target_date (Wochenende -> naechster Handelstag).

    None falls target > letztem Cache-Eintrag oder Ticker nicht im Cache.
    """
    if not _is_valid_yf_ticker(ticker):
        return None
    tkr = str(ticker).strip().upper()
    if tkr not in cache.index.get_level_values(0):
        return None
    series = cache.loc[tkr]
    # Marker-Row (1900-01-01) ausfiltern
    series = series[series.index != DELISTED_MARKER_DATE]
    if series.empty:
        return None
    target = pd.Timestamp(target_date).normalize()
    after = series[series.index >= target]
    if after.empty:
        return None
    val = after.iloc[0]['close']
    if pd.isna(val):
        return None
    return float(val)


def _build_price_lookup(cache: pd.DataFrame) -> dict[str, tuple | None]:
    """Baut EINMALIG ein Dict ``{ticker: (dates[datetime64[ns]], closes[float64])}``.

    Ersetzt den teuren per-Aufruf ``cache.index.get_level_values(0)``-Scan in
    den Hot-Loops (Baseline / Horizont / Exit). Vorher rematerialisierte jeder
    der ~400k Lookups ein 3,96-Mio.-Element-Index — quadratische Laufzeit.
    Danach: O(1)-Dict-Get + ``np.searchsorted`` (O(log n) pro Ticker).

    Delisted-Marker-Rows (1900-01-01, NaN) werden entfernt; ein Ticker, der
    nur aus dem Marker bestand, wird als ``None`` hinterlegt (= kein Preis).
    Pro Ticker nach Datum sortiert (verlaesst sich nicht auf Cache-Sortierung).
    """
    lookup: dict[str, tuple | None] = {}
    if cache is None or not len(cache):
        return lookup
    marker = np.datetime64(DELISTED_MARKER_DATE.to_datetime64())
    for tkr, sub in cache.groupby(level=0, sort=False):
        dates = np.asarray(
            sub.index.get_level_values(1).values, dtype='datetime64[ns]',
        )
        closes = sub['close'].to_numpy(dtype='float64')
        keep = dates != marker
        if not keep.all():
            dates = dates[keep]
            closes = closes[keep]
        if len(dates) == 0:
            lookup[str(tkr)] = None
            continue
        order = np.argsort(dates, kind='stable')
        lookup[str(tkr)] = (dates[order], closes[order])
    return lookup


def _price_at_or_after_fast(
    lookup: dict[str, tuple | None], ticker: object, target_date: pd.Timestamp,
) -> float | None:
    """Wie :func:`_price_at_or_after`, aber gegen das vorgebaute Lookup-Dict.

    Ergebnis-aequivalent: erster Close mit ``date >= target_date`` (Wochenende
    -> naechster Handelstag); ``None`` falls Ticker fehlt, delisted, Ziel hinter
    dem letzten Eintrag liegt oder der Close NaN ist.
    """
    if not _is_valid_yf_ticker(ticker):
        return None
    entry = lookup.get(str(ticker).strip().upper())
    if not entry:  # None (delisted/leer) oder Ticker nicht im Cache
        return None
    dates, closes = entry
    target = np.datetime64(pd.Timestamp(target_date).normalize().to_datetime64())
    pos = int(np.searchsorted(dates, target, side='left'))
    if pos >= len(dates):
        return None
    val = closes[pos]
    if np.isnan(val):
        return None
    return float(val)


def _phase_progress(
    done: int, total: int, label: str, t_start: float, every: int,
) -> None:
    """Fortschrittszeile alle ``every`` Items ODER bei jedem 10%-Schritt.

    Zeigt verarbeitete Anzahl, Prozent und Elapsed — damit bei stundenlangen
    Laeufen erkennbar ist, ob die Phase laeuft oder haengt.
    """
    if total <= 0:
        return
    decile = max(1, total // 10)
    if done != total and done % every != 0 and done % decile != 0:
        return
    pct = (done / total) * 100.0
    elapsed = time.perf_counter() - t_start
    print(f"    [{label}] {done:>7,}/{total:,}  {pct:5.1f}%  ({elapsed:5.1f}s)",
          flush=True)


def _sell_sign_series(df: pd.DataFrame) -> pd.Series:
    """Richtungsbewusstes Vorzeichen je Trade: Verkauf -> -1.0, sonst +1.0.

    Ein Verkauf invertiert das Vorzeichen, sodass "positiv" = vor einem
    Kursrueckgang verkauft (gutes Timing) bedeutet und ein Verkauf vor einem
    Kursanstieg korrekt negativ (entgangener Gewinn) wird. Kauf/Sonstige
    (Exchange) bleiben in der Long-Perspektive. Gilt fuer post_return_{X}d,
    effective_return_pct UND paper_return_pct — gemeinsame Quelle, damit das
    Vorzeichen nie auseinanderdriftet.
    """
    from signals.committee_signal import _direction_from_transaction_type
    direction = df['transaction_type'].map(_direction_from_transaction_type)
    return pd.Series(
        np.where(direction.values == 'sell', -1.0, 1.0), index=df.index,
    )


def compute_post_trade_returns(
    df: pd.DataFrame,
    horizons_days: list[int],
    cache_path: str,
    fetch_years: int = 5,
    cache_ttl_days: int = 7,
    exit_amount_tolerance: float = 0.0,
    cache_flush_every: int = 50,
    progress_every: int = 20000,
    exit_window_days: int | None = None,
) -> pd.DataFrame:
    """Erweitert df um Post-Trade-Spalten. Idempotent. Liefert NEUE Kopie.

    Erwartete Input-Spalten:
        trade_id, name, ticker, issuer_group_key, transaction_date,
        transaction_type, trade_amount_max_usd, ticker_type

    Output-Spalten (zusaetzlich):
        post_trade_baseline_price, post_price_{X}d, post_return_{X}d,
        exit_status, exit_date, exit_price, exit_amount_max_usd,
        effective_return_pct, effective_return_horizon_days, opposing_trade_ids,
        first_close_offset_days

    ``exit_window_days``: oberes Suchfenster der Exit-Detection in Tagen.
    None = unbegrenzt (gesamter Datensatz). ENTKOPPELT vom Return-Horizont —
    bestimmt, bis wann ein gegensaetzlicher Trade noch als Close zaehlt.
    """
    # yfinance- + urllib-Logger leiser stellen — die Stage hat ihre eigene Progress-Anzeige.
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    logging.getLogger('urllib3').setLevel(logging.CRITICAL)

    required = ['trade_id', 'name', 'ticker', 'issuer_group_key',
               'transaction_date', 'transaction_type',
               'trade_amount_max_usd', 'ticker_type']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"compute_post_trade_returns: fehlende Input-Spalten {missing}. "
            f"Stage 'trade_volume' muss vor 'post_trade_returns' laufen."
        )

    df = df.copy()
    n_total = len(df)

    df['transaction_date'] = pd.to_datetime(
        df['transaction_date'], errors='coerce',
    )

    # Richtungsbewusster Return (Verkauf invertiert) — zentrale Quelle, s.
    # _sell_sign_series. Gilt fuer post_return_{X}d UND effective_return_pct.
    sell_sign = _sell_sign_series(df)

    # Applicability-Maske VORAB — Tickers von nicht-applicable Trades
    # gar nicht erst an yfinance schicken.
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    print(f"  Applicable Trades: {int(df['_post_trade_applicable'].sum()):,} "
          f"/ {n_total:,}")

    # --- Cache aufbauen/aktualisieren (nur fuer applicable Trades) ---
    cache = _load_price_cache(cache_path)
    relevant = df.loc[df['_post_trade_applicable'], 'ticker']
    tickers_relevant = (
        relevant.dropna().astype(str).str.strip().str.upper().unique().tolist()
    )
    cache = _ensure_cache_for_tickers(
        cache,
        tickers_relevant,
        fetch_years=fetch_years,
        cache_ttl_days=cache_ttl_days,
        cache_path=cache_path,
        flush_every=cache_flush_every,
    )

    max_horizon = max(horizons_days)
    today = pd.Timestamp.now().normalize()

    # --- Preis-Lookup (einmalig) ---
    # Wandelt den MultiIndex-Cache in ein {ticker: (dates, closes)}-Dict um, damit
    # die folgenden Schleifen O(1)-Lookups statt O(N)-Index-Scans machen.
    print("  Baue Preis-Lookup (einmalig) ...")
    t0 = time.perf_counter()
    lookup = _build_price_lookup(cache)
    print(f"  Preis-Lookup bereit: {len(lookup):,} Ticker "
          f"({time.perf_counter() - t0:.1f}s).")

    # Applicable-Indizes einmal bestimmen (aendern sich nicht zwischen den Phasen).
    applicable_idx = df.index[df['_post_trade_applicable']]
    n_app = len(applicable_idx)

    # --- Baseline-Preis ---
    print(f"  Baseline-Preise berechnen ({n_app:,} applicable Trades) ...")
    df['post_trade_baseline_price'] = np.nan
    t0 = time.perf_counter()
    for done, idx in enumerate(applicable_idx, start=1):
        t_date = df.at[idx, 'transaction_date']
        if pd.notna(t_date):
            price = _price_at_or_after_fast(lookup, df.at[idx, 'ticker'], t_date)
            if price is not None:
                df.at[idx, 'post_trade_baseline_price'] = price
        _phase_progress(done, n_app, 'Baseline', t0, progress_every)

    # --- Horizont-Preise ---
    print(f"  Horizont-Preise berechnen (Horizonte: {horizons_days}) ...")
    for h in horizons_days:
        col_price = f'post_price_{h}d'
        col_return = f'post_return_{h}d'
        df[col_price] = np.nan
        df[col_return] = np.nan

        t0 = time.perf_counter()
        for done, idx in enumerate(applicable_idx, start=1):
            t_date = df.at[idx, 'transaction_date']
            if pd.isna(t_date):
                _phase_progress(done, n_app, f'Horizont {h}d', t0, progress_every)
                continue
            target = t_date + pd.Timedelta(days=h)
            if target > today:
                _phase_progress(done, n_app, f'Horizont {h}d', t0, progress_every)
                continue  # Zukunft -> NaN
            price = _price_at_or_after_fast(lookup, df.at[idx, 'ticker'], target)
            df.at[idx, col_price] = price
            baseline = df.at[idx, 'post_trade_baseline_price']
            if (price is not None and baseline is not None
                    and pd.notna(baseline) and baseline > 0):
                df.at[idx, col_return] = (price / baseline - 1.0) * 100.0
            _phase_progress(done, n_app, f'Horizont {h}d', t0, progress_every)

        # Vorzeichen richtungsbewusst (Verkauf invertiert). NaN * sign = NaN.
        df[col_return] = df[col_return] * sell_sign

    # --- Exit-Detection ---
    # Fenster ist vom Return-Horizont (max_horizon) ENTKOPPELT: exit_window_days
    # steuert, bis wann ein Gegen-Trade noch als Close gilt (None = unbegrenzt).
    print("  Exit-Detection ...")
    exit_cols = _compute_exit_columns(
        df, cache, exit_window_days, exit_amount_tolerance, lookup=lookup,
        progress_every=progress_every,
    )
    for col_name, series in exit_cols.items():
        df[col_name] = series.values

    # --- effective_return_pct + effective_return_horizon_days (vektorisiert) ---
    # REALISIERT-ONLY: nur closed_* bekommen einen effective_return (Return bis
    # Exit-Preis, Horizont = Tage bis Exit). held/delisted/unknown/not_applicable
    # -> NaN, weil es keinen realisierten Wert gibt. Mark-to-Market bleibt in
    # post_return_{X}d erhalten. (Frueher kopierte ein held-Fallback den
    # laengsten Horizont-Snapshot hierher — ein willkuerlicher Kalendertag, kein
    # realisierter Return; entfernt, weil Exit-Detection jetzt entkoppelt den
    # echten Close findet.)
    # Vorzeichen ist richtungsbewusst (s. sell_sign oben): closed_* wird hier
    # explizit mit sell_sign multipliziert.
    df['effective_return_pct'] = np.nan
    df['effective_return_horizon_days'] = np.nan
    status = df['exit_status']
    baseline_col = df['post_trade_baseline_price']

    closed_mask = (
        status.isin(['closed_full', 'closed_partial'])
        & baseline_col.notna() & (baseline_col > 0)
        & df['exit_price'].notna()
    )
    df.loc[closed_mask, 'effective_return_pct'] = (
        (df.loc[closed_mask, 'exit_price']
         / df.loc[closed_mask, 'post_trade_baseline_price'] - 1.0)
        * 100.0 * sell_sign.loc[closed_mask]
    )
    df.loc[closed_mask, 'effective_return_horizon_days'] = (
        (df.loc[closed_mask, 'exit_date']
         - df.loc[closed_mask, 'transaction_date']).dt.days
    )

    # --- Aufraeumen ---
    df.drop(columns=['_post_trade_applicable'], inplace=True)

    # --- Statistik ---
    status_counts = df['exit_status'].value_counts(dropna=False).to_dict()
    n_priced = int(df['post_trade_baseline_price'].notna().sum())
    n_closed = int(
        df['exit_status'].astype(str).str.startswith('closed_', na=False).sum()
    )
    print(f"  Post-Trade-Pricing: {n_priced:,}/{n_total:,} Trades mit "
          f"Baseline-Preis")
    print(f"  Exit-Status-Verteilung: {status_counts}")
    print(f"  Exits (closed_full + closed_partial): {n_closed:,}")

    return df


def _latest_close_map(cache: pd.DataFrame) -> dict[str, tuple[float, pd.Timestamp]]:
    """Letzter (juengster) gueltiger Close je Ticker: ``{ticker: (close, date)}``.

    Baut auf :func:`_build_price_lookup` (entfernt Delisted-Marker, sortiert nach
    Datum) und nimmt pro Ticker den letzten nicht-NaN Close. Ticker ohne
    gueltigen Preis erscheinen nicht im Dict. Rein (kein Netzwerk).
    """
    out: dict[str, tuple[float, pd.Timestamp]] = {}
    lookup = _build_price_lookup(cache)
    for tkr, entry in lookup.items():
        if not entry:
            continue
        dates, closes = entry
        i = len(closes) - 1
        while i >= 0 and np.isnan(closes[i]):
            i -= 1
        if i < 0:
            continue
        out[tkr] = (float(closes[i]), pd.Timestamp(dates[i]))
    return out


def _apply_paper_returns(
    df: pd.DataFrame,
    latest_map: dict[str, tuple[float, pd.Timestamp]],
    target_mask: pd.Series,
    sell_sign: pd.Series,
    baseline_override: pd.Series | None = None,
) -> pd.DataFrame:
    """Setzt ``paper_return_pct`` + ``price_as_of`` (rein, kein Netzwerk).

    Nur Zeilen mit ``target_mask=True`` bekommen Werte; alle anderen bleiben
    NaN/NaT. Papier-Return = ``(latest_close / baseline - 1) * 100 *
    sell_sign``. Der Einstandspreis (``baseline``) kommt standardmaessig aus
    der vorhandenen Spalte ``post_trade_baseline_price`` (kein Neu-Berechnen).
    Wird ``baseline_override`` uebergeben (Series, gleicher Index wie ``df``),
    wird stattdessen dessen Wert pro Zeile verwendet — z.B. eine live aus dem
    aktuellen Preis-Cache berechnete Baseline (split-immun, s.
    ``compute_paper_returns``). Default ``None`` = bisheriges Verhalten.
    Fehlt der letzte Close oder ein gueltiger Baseline-Preis -> NaN.
    """
    paper = pd.Series(np.nan, index=df.index, dtype='float64')
    asof = pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')

    if target_mask.any():
        idx = df.index[target_mask]
        tkr = df.loc[idx, 'ticker'].astype('string').str.strip().str.upper()
        close = tkr.map(
            lambda t: latest_map.get(t, (np.nan, pd.NaT))[0]
        ).astype('float64')
        date = pd.to_datetime(
            tkr.map(lambda t: latest_map.get(t, (np.nan, pd.NaT))[1])
        )
        if baseline_override is not None:
            baseline = pd.to_numeric(
                baseline_override.loc[idx], errors='coerce'
            )
        else:
            baseline = pd.to_numeric(
                df.loc[idx, 'post_trade_baseline_price'], errors='coerce'
            )
        ss = sell_sign.loc[idx]
        valid = close.notna() & baseline.notna() & (baseline > 0)
        pr = (close / baseline - 1.0) * 100.0 * ss
        paper.loc[idx] = pr.where(valid)
        asof.loc[idx] = date

    df['paper_return_pct'] = paper
    df['price_as_of'] = asof
    return df


def compute_paper_returns(
    df: pd.DataFrame,
    cache_path: str,
    recent_window_days: int = 180,
    max_price_age_bdays: int = 1,
    fetch_years: int = 5,
    cache_flush_every: int = 50,
) -> pd.DataFrame:
    """Voll-Pass: ``paper_return_pct`` + ``price_as_of`` fuer Recent-Trades.

    Recent = ``transaction_date >= today - recent_window_days`` UND
    Post-Trade-applicable. Mark-to-Market gegen den juengsten verfuegbaren Close.
    DELTA-UNABHAENGIG: laeuft bei jedem Pipeline-Lauf voll ueber das
    Recent-Fenster, weil der aktuelle Kurs (und damit der Papier-Return) taeglich
    wechselt — auch fuer laengst importierte offene Positionen.

    Frische: der Preis-Cache wird fuer die Recent-Ticker refetcht, wenn sein
    juengster Close aelter ist als der letzte abgeschlossene Handelstag
    (``today - BDay(max_price_age_bdays)``). Kein Feiertagskalender — nach dem
    Refetch wird der juengste verfuegbare Close genommen und ``price_as_of``
    macht die Staleness sichtbar.

    Erwartete Input-Spalten: ``transaction_date``, ``transaction_type``,
    ``ticker``, ``ticker_type``, ``post_trade_baseline_price``.
    Idempotent. Liefert NEUE Kopie.
    """
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    logging.getLogger('urllib3').setLevel(logging.CRITICAL)

    required = ['transaction_date', 'transaction_type', 'ticker',
                'ticker_type', 'post_trade_baseline_price']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"compute_paper_returns: fehlende Input-Spalten {missing}. "
            f"Stage 'post_trade_performance' muss vorher gelaufen sein."
        )

    df = df.copy()
    today = pd.Timestamp.now().normalize()
    tx = pd.to_datetime(df['transaction_date'], errors='coerce')
    recent_mask = tx >= (today - pd.Timedelta(days=recent_window_days))

    # Applicability nur fuer das Recent-Fenster pruefen (begrenzt den apply-Call).
    applicable = pd.Series(False, index=df.index)
    if recent_mask.any():
        applicable.loc[recent_mask] = (
            df.loc[recent_mask].apply(_is_applicable, axis=1)
        )
    target_mask = recent_mask & applicable
    sell_sign = _sell_sign_series(df)

    print(f"  Recent-Monitoring: {int(target_mask.sum()):,} Trades im "
          f"{recent_window_days}-Tage-Fenster (applicable).")

    # --- Cache fuer Recent-Ticker mit Business-Day-Frische refreshen ---
    cache = _load_price_cache(cache_path)
    recent_tickers = (
        df.loc[target_mask, 'ticker'].dropna().astype(str)
        .str.strip().str.upper().unique().tolist()
    )
    if recent_tickers:
        last_bday = (today - pd.offsets.BDay(max_price_age_bdays)).normalize()
        cache = _ensure_cache_for_tickers(
            cache,
            recent_tickers,
            fetch_years=fetch_years,
            cache_path=cache_path,
            flush_every=cache_flush_every,
            cutoff_date=last_bday,
        )

    latest_map = _latest_close_map(cache)

    # Baseline LIVE aus dem aktuellen Cache ableiten (identische Definition wie
    # die Pipeline-Baseline in compute_post_trade_returns:
    # _price_at_or_after_fast(lookup, ticker, transaction_date)). So stammen
    # Zaehler (latest_close) und Nenner (Baseline) aus DEMSELBEN Cache-Stand
    # -> split-immun. Die gespeicherte Master-Spalte post_trade_baseline_price
    # kann pre-split sein, falls der Cache rueckwirkend split-adjustiert wurde.
    lookup = _build_price_lookup(cache)
    baseline_live = pd.Series(np.nan, index=df.index, dtype='float64')
    if target_mask.any():
        for idx in df.index[target_mask]:
            t_date = df.at[idx, 'transaction_date']
            if pd.isna(t_date):
                continue
            price = _price_at_or_after_fast(
                lookup, df.at[idx, 'ticker'], pd.Timestamp(t_date),
            )
            if price is not None:
                baseline_live.at[idx] = price

    df = _apply_paper_returns(
        df, latest_map, target_mask, sell_sign,
        baseline_override=baseline_live,
    )

    n_priced = int(df['paper_return_pct'].notna().sum())
    print(f"  Papier-Return gesetzt: {n_priced:,}/{int(target_mask.sum()):,} "
          f"Recent-Trades mit aktuellem Preis.")
    return df


def _is_applicable(row: pd.Series) -> bool:
    """Prueft ob Post-Trade-Pricing auf diesen Trade anwendbar ist."""
    tkr_type = str(row.get('ticker_type', '')).strip().lower()
    if tkr_type not in _APPLICABLE_TYPE_STRINGS:
        return False
    igk = str(row.get('issuer_group_key', '') or '')
    if igk.startswith(SKIP_PREFIX):
        return False
    if not _is_valid_yf_ticker(row.get('ticker')):
        return False
    t_date = row.get('transaction_date')
    if pd.isna(t_date):
        return False
    today = pd.Timestamp.now().normalize()
    if pd.Timestamp(t_date) > today:
        return False
    # Exchange/sonstiges: applicable fuer Horizont-Preise, aber Exit wird
    # spaeter ausgefiltert (siehe _compute_exit_columns).
    return True


def _compute_exit_columns(
    df: pd.DataFrame,
    cache: pd.DataFrame,
    exit_window_days: int | None,
    exit_amount_tolerance: float,
    lookup: dict[str, tuple | None] | None = None,
    progress_every: int = 20000,
) -> dict[str, pd.Series]:
    """Exit-Detection: gegensaetzliche Trades im Fenster erkennen.

    Returns dict mit Series (exit_status, exit_date, exit_price,
    exit_amount_max_usd, opposing_trade_ids, first_close_offset_days).
    Jeder Trade unabhaengig betrachtet (kein FIFO).

    ``exit_window_days``: oberes Suchfenster in Tagen ab transaction_date.
    None = unbegrenzt (kein oberer Filter). ENTKOPPELT vom Return-Horizont.

    ``lookup`` ist das vorgebaute Preis-Dict (:func:`_build_price_lookup`).
    Wird es nicht uebergeben (z.B. direkter Test-Aufruf), wird es hier aus
    ``cache`` gebaut — so bleibt die alte Positional-Signatur kompatibel.
    """
    if lookup is None:
        lookup = _build_price_lookup(cache)
    exit_status = pd.Series('not_applicable', index=df.index, dtype='object')
    exit_date = pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')
    exit_price = pd.Series(np.nan, index=df.index, dtype='float64')
    exit_amount = pd.Series(np.nan, index=df.index, dtype='float64')
    opposing_ids = pd.Series('', index=df.index, dtype='object')
    # Tage bis zum ERSTEN gegensaetzlichen Trade (unabh. von Voll-/Teil-Close);
    # NaN bei held / not_applicable / delisted / unknown. Guenstige Filter-
    # Metadaten-Spalte (ein Skalar pro Trade) fuer den Horizont-Sichtbarkeits-
    # filter im Dashboard.
    first_close_offset = pd.Series(np.nan, index=df.index, dtype='float64')

    # Cache-Ticker-Set vorab — vermeidet teure .index.get_level_values()-Calls
    # pro Trade.
    cache_tickers = set(cache.index.get_level_values(0).unique()) if len(cache) else set()
    delisted_tickers = set()
    if cache_tickers:
        try:
            marker_slice = cache[cache.index.get_level_values(1) == DELISTED_MARKER_DATE]
            delisted_tickers = set(marker_slice.index.get_level_values(0).unique())
        except Exception:  # noqa: BLE001
            delisted_tickers = set()

    # Nur applicable Trades mit kanonischer Buy/Sell-Richtung fuer Exit-Suche.
    # _direction_from_transaction_type normalisiert Quiver-Varianten:
    # 'Sale (Full)'/'Sale (Partial)'/'SALE' -> 'sell', 'PURCHASE' -> 'buy',
    # 'Exchange' -> 'other' (bleibt not_applicable). Lokaler Import wie in
    # import_trades.py (vermeidet Modul-Lade-Reihenfolge-Probleme).
    from signals.committee_signal import _direction_from_transaction_type
    direction_all = df['transaction_type'].map(_direction_from_transaction_type)
    mask = (
        df['_post_trade_applicable']
        & direction_all.isin(['buy', 'sell'])
        & df['transaction_date'].notna()
    )
    if not mask.any():
        return {
            'exit_status': exit_status,
            'exit_date': exit_date,
            'exit_price': exit_price,
            'exit_amount_max_usd': exit_amount,
            'opposing_trade_ids': opposing_ids,
            'first_close_offset_days': first_close_offset,
        }

    work = df.loc[mask, [
        'trade_id', 'name', 'issuer_group_key', 'ticker',
        'transaction_date', 'transaction_type', 'trade_amount_max_usd',
    ]].copy()
    # Defensive Type-Konvertierung — falls Caller _compute_exit_columns direkt
    # aufruft (Tests), ist transaction_date sonst noch String.
    work['transaction_date'] = pd.to_datetime(
        work['transaction_date'], errors='coerce',
    )
    work['_dir'] = work['transaction_type'].map(_direction_from_transaction_type)
    work['_grp'] = (
        work['name'].astype(str).str.strip() + '|||'
        + work['issuer_group_key'].astype(str).str.strip()
    )

    grouped = work.groupby('_grp')
    n_groups = grouped.ngroups
    t0 = time.perf_counter()
    for gi, (grp_key, grp) in enumerate(grouped, start=1):
        _phase_progress(gi, n_groups, 'Exit-Gruppen', t0, progress_every)
        # Trades mit unbekannter Identitaet -> not_applicable
        if grp_key.startswith('nan|||') or grp_key.endswith('|||nan') or grp_key.startswith('|||') or grp_key.endswith('|||'):
            for idx in grp.index:
                exit_status.at[idx] = 'not_applicable'
            continue

        grp_sorted = grp.sort_values('transaction_date')
        for _, trade in grp_sorted.iterrows():
            idx = trade.name
            t_date = trade['transaction_date']
            t_dir = trade['_dir']
            t_max_usd = trade['trade_amount_max_usd']
            if pd.isna(t_max_usd) or t_max_usd <= 0:
                exit_status.at[idx] = 'not_applicable'
                continue

            tkr = str(trade['ticker']).strip().upper()
            if tkr in delisted_tickers:
                exit_status.at[idx] = 'delisted'
                continue
            if tkr not in cache_tickers:
                exit_status.at[idx] = 'unknown'
                continue

            opposing_dir = 'sell' if t_dir == 'buy' else 'buy'
            opposing = grp_sorted[
                (grp_sorted['_dir'] == opposing_dir)
                & (grp_sorted['transaction_date'] > t_date)
            ]
            if exit_window_days is not None:
                window_end = t_date + pd.Timedelta(days=exit_window_days)
                opposing = opposing[opposing['transaction_date'] <= window_end]

            if opposing.empty:
                exit_status.at[idx] = 'held'
                continue

            opposing = opposing.sort_values('transaction_date')
            # Tage bis zum ERSTEN Gegen-Trade (egal ob er voll/teilweise schliesst).
            first_close_offset.at[idx] = float(
                (opposing.iloc[0]['transaction_date'] - t_date).days
            )
            cum_amount = 0.0
            opp_ids: list[str] = []
            threshold = float(t_max_usd) * (1.0 - exit_amount_tolerance)
            closed = False
            last_exit_date = None

            for _, opp_trade in opposing.iterrows():
                opp_max = opp_trade['trade_amount_max_usd']
                if pd.isna(opp_max):
                    continue
                cum_amount += float(opp_max)
                opp_ids.append(str(opp_trade['trade_id']))
                last_exit_date = opp_trade['transaction_date']
                if cum_amount >= threshold:
                    closed = True
                    break

            if not opp_ids:
                # alle opposing-Trades hatten NaN-Amount -> nicht aussagekraeftig
                exit_status.at[idx] = 'held'
                first_close_offset.at[idx] = np.nan  # held -> kein Offset
                continue

            exit_amount.at[idx] = float(cum_amount)
            opposing_ids.at[idx] = ';'.join(opp_ids)
            exit_status.at[idx] = 'closed_full' if closed else 'closed_partial'
            exit_date.at[idx] = last_exit_date
            ep = _price_at_or_after_fast(lookup, tkr, last_exit_date)
            if ep is not None:
                exit_price.at[idx] = ep

    return {
        'exit_status': exit_status,
        'exit_date': exit_date,
        'exit_price': exit_price,
        'exit_amount_max_usd': exit_amount,
        'opposing_trade_ids': opposing_ids,
        'first_close_offset_days': first_close_offset,
    }
