"""Event-Study / Abnormal Return (Insider-Aufdeckung — Discovery-Modul).

Berechnet pro Trade den signierten, marktbereinigten Abnormal Return (signed
BHAR) ueber mehrere Event-Horizonte und markiert Ausreisser. Zweck: ein
schaerferes Lineal als der rohe Kalender-Return, um aussergewoehnliche Trades zu
entdecken und zu pruefen, ob die Verdachts-Signale beim AR einen Edge zeigen
(Diskriminator: Signale-sind-Rauschen vs. falsches-Lineal).

Pattern: scoring/post_trade_returns.py (reines Pandas, idempotent, neue Kopie,
kein Side-Effect). Die Preis-Primitive werden von dort IMPORTIERT (nicht
reimplementiert): _load_price_cache, _build_price_lookup, _sell_sign_series.

Zwei getrennte Konstrukte (sie messen Verschiedenes):
  A) signed_ar_{H}d       — Anker transaction_date  (Insider, primaer)
  B) signed_ar_disc_{H}d  — Anker disclosure_date    (Copycat/Follower, sekundaer)

Formel pro Trade/Horizont H (t0 = Anker-Datum):
    raw_trade = P_ticker(t0+H) / P_ticker(t0) - 1
    raw_bench = P_bench (t0+H) / P_bench (t0) - 1
    signed_ar = sell_sign * (raw_trade - raw_bench)
sell_sign (Verkauf -> -1) wird GENAU EINMAL auf das Abnormal angewandt — nicht je
Bein. signed_ar ist ein BRUCHTEIL (0.03 = 3%), nicht Prozent; die Outlier-Schwellen
in config.settings (AR_OUTLIER_*) sind in derselben Einheit.

Beide Beine (Ticker + Benchmark) und beide Anker werden aus DEMSELBEN Preis-Lookup
gezogen (eine _build_price_lookup-Passage) — Zaehler und Nenner stammen aus demselben
Cache-Stand (split-konsistent). Die gespeicherte Master-Spalte
post_trade_baseline_price wird bewusst NICHT verwendet (kann pre-split sein).

Right-Censoring: liegt t0+H hinter dem letzten Cache-Datum (oder fehlt ein Preis),
ist der AR NaN. Konstrukt B ist staerker zensiert als A (Melde-Latenz).
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from scoring.post_trade_returns import (
    _build_price_lookup,
    _load_price_cache,
    _max_gap,
    _sell_sign_series,
)

logger = logging.getLogger(__name__)


def _prices_for_dates(
    entry: Tuple[np.ndarray, np.ndarray], targets: np.ndarray,
) -> np.ndarray:
    """Vektor-Lookup: erster Close mit ``date >= target`` je Eintrag.

    ``entry`` = ``(dates[datetime64[ns]], closes[float64])`` aus
    :func:`_build_price_lookup` (bereits NaN-gefiltert + nach Datum sortiert).
    ``targets`` = ``datetime64[ns]``-Array. NaT-Targets und Targets hinter dem
    letzten Cache-Datum oder mehr als ``POST_TRADE_MAX_PRICE_GAP_DAYS`` vor dem
    naechsten Close -> NaN. Ergebnis-aequivalent zu ``_price_at_or_after_fast``,
    nur vektorisiert (ein ``np.searchsorted`` statt N Einzel-Lookups).
    """
    dates, closes = entry
    out = np.full(len(targets), np.nan, dtype='float64')
    valid_t = ~np.isnat(targets)
    if not valid_t.any():
        return out
    pos = np.searchsorted(dates, targets, side='left')
    ok = valid_t & (pos < len(dates))
    # Liegt der naechste Close zu weit hinter dem Ziel (Cache beginnt spaeter),
    # ist der Preis unbekannt - wie in _price_at_or_after_fast.
    ok[ok] = (dates[pos[ok]] - targets[ok]) <= _max_gap()
    out[ok] = closes[pos[ok]]
    return out


def _ticker_groups(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """``{TICKER_UPPER: positional-index-array}`` — einmal pro Anker gebaut.

    NA-Ticker fallen via groupby(dropna) automatisch raus.
    """
    ticker_up = df['ticker'].astype('string').str.strip().str.upper()
    groups: Dict[str, np.ndarray] = {}
    for tkr, idx in ticker_up.groupby(ticker_up, dropna=True).groups.items():
        if tkr is None:
            continue
        groups[str(tkr)] = df.index.get_indexer(idx)
    return groups


def _anchor_signed_ar(
    df: pd.DataFrame,
    anchor_col: str,
    horizons: Iterable[int],
    sell_sign: pd.Series,
    lookup: Dict[str, Tuple | None],
    bench_entry: Tuple[np.ndarray, np.ndarray],
    col_template: str,
) -> Dict[str, pd.Series]:
    """signed_ar je Horizont fuer EINEN Anker (transaction_date / disclosure_date).

    Vektorisiert je Ticker-Gruppe (Baseline + Horizont aus demselben Lookup-Dict).
    Benchmark ist ein einzelner Ticker -> in einem Rutsch ueber alle Zeilen.
    """
    n = len(df)
    t0 = pd.to_datetime(df[anchor_col], errors='coerce').values.astype('datetime64[ns]')
    groups = _ticker_groups(df)
    ss = sell_sign.values

    # Baseline P(t0): Ticker je Gruppe, Benchmark vektorisiert.
    base_ticker = np.full(n, np.nan, dtype='float64')
    for tkr, pos in groups.items():
        entry = lookup.get(tkr)
        if not entry:
            continue
        base_ticker[pos] = _prices_for_dates(entry, t0[pos])
    base_bench = _prices_for_dates(bench_entry, t0)
    base_ticker = np.where(base_ticker > 0, base_ticker, np.nan)
    base_bench = np.where(base_bench > 0, base_bench, np.nan)

    out: Dict[str, pd.Series] = {}
    for h in horizons:
        targets = t0 + np.timedelta64(int(h), 'D')
        p_ticker = np.full(n, np.nan, dtype='float64')
        for tkr, pos in groups.items():
            entry = lookup.get(tkr)
            if not entry:
                continue
            p_ticker[pos] = _prices_for_dates(entry, targets[pos])
        p_bench = _prices_for_dates(bench_entry, targets)
        with np.errstate(invalid='ignore', divide='ignore'):
            raw_trade = p_ticker / base_ticker - 1.0
            raw_bench = p_bench / base_bench - 1.0
            signed = ss * (raw_trade - raw_bench)
        out[col_template.format(h=int(h))] = pd.Series(signed, index=df.index)
    return out


def compute_abnormal_returns(
    df: pd.DataFrame,
    cache_path: str,
    benchmark_ticker: str = 'SPY',
    horizons_days: Iterable[int] = (30, 60, 90, 120, 180),
    headline_horizon_days: int = 120,
    disclosure_horizons_days: Iterable[int] = (30, 90),
    outlier_mode: str = 'absolute',
    outlier_upper: float = 0.03,
    outlier_lower: float = -0.03,
    outlier_pct_upper: float = 0.95,
    outlier_pct_lower: float = 0.05,
) -> pd.DataFrame:
    """Erweitert ``df`` um Abnormal-Return-Spalten. Idempotent. Liefert NEUE Kopie.

    Erwartete Input-Spalten:
        ticker, transaction_date, disclosure_date, transaction_type

    Output-Spalten (zusaetzlich):
        signed_ar_{H}d           (Konstrukt A, Anker transaction_date)
        signed_ar_disc_{H}d      (Konstrukt B, Anker disclosure_date)
        ar_outlier_flag          (bool, auf signed_ar_{headline}d)
        ar_outlier_direction     ('positive' | 'negative' | '')

    Parameter werden bewusst EXPLIZIT uebergeben (nicht das settings-Modul) —
    wie ``compute_post_trade_returns``: testbar + spaeter stage-faehig.
    """
    required = ['ticker', 'transaction_date', 'disclosure_date', 'transaction_type']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"compute_abnormal_returns: fehlende Input-Spalten {missing}."
        )

    df = df.copy()
    # format='mixed': disclosure_date enthaelt gemischte Formate ('YYYY-MM-DD' UND
    # vereinzelt 'YYYY-MM-DD HH:MM:SS'). pandas 2.x leitet sonst EIN Format vom
    # ersten Wert ab und coerced den Rest zu NaT (haette Konstrukt B auf N=0 gesetzt).
    df['transaction_date'] = pd.to_datetime(
        df['transaction_date'], errors='coerce', format='mixed')
    df['disclosure_date'] = pd.to_datetime(
        df['disclosure_date'], errors='coerce', format='mixed')
    sell_sign = _sell_sign_series(df)

    cache = _load_price_cache(cache_path)
    lookup = _build_price_lookup(cache)
    bench_key = str(benchmark_ticker).strip().upper()
    bench_entry = lookup.get(bench_key)
    if not bench_entry:
        raise ValueError(
            f"compute_abnormal_returns: Benchmark '{bench_key}' nicht im Preis-Cache "
            f"{cache_path} (oder ohne gueltige Preise). Benchmark muss gecacht sein."
        )

    headline = int(headline_horizon_days)
    # Konstrukt A (transaction-anchored). Headline immer mitberechnen.
    a_horizons = sorted(set(int(h) for h in horizons_days) | {headline})
    for col, series in _anchor_signed_ar(
        df, 'transaction_date', a_horizons, sell_sign, lookup, bench_entry,
        'signed_ar_{h}d',
    ).items():
        df[col] = series

    # Konstrukt B (disclosure-anchored, Copycat).
    b_horizons = sorted(set(int(h) for h in disclosure_horizons_days))
    for col, series in _anchor_signed_ar(
        df, 'disclosure_date', b_horizons, sell_sign, lookup, bench_entry,
        'signed_ar_disc_{h}d',
    ).items():
        df[col] = series

    # --- Ausreisser-Flag auf dem Headline-Horizont (Konstrukt A) ---
    head_col = f'signed_ar_{headline}d'
    s = df[head_col]
    if str(outlier_mode).lower() == 'percentile':
        upper = float(s.quantile(outlier_pct_upper))
        lower = float(s.quantile(outlier_pct_lower))
    else:
        upper = float(outlier_upper)
        lower = float(outlier_lower)
    pos_mask = s.notna() & (s >= upper)
    neg_mask = s.notna() & (s <= lower)
    flag = pd.Series(False, index=df.index)
    flag[pos_mask | neg_mask] = True
    direction = pd.Series('', index=df.index, dtype=object)
    direction[pos_mask] = 'positive'
    direction[neg_mask] = 'negative'
    df['ar_outlier_flag'] = flag
    df['ar_outlier_direction'] = direction

    n_eval = int(s.notna().sum())
    logger.info(
        "Abnormal Return: headline=%s, evaluierbar=%d/%d, Outlier (%s) "
        "[%.4f .. %.4f]: +%d / -%d",
        head_col, n_eval, len(df), outlier_mode, lower, upper,
        int(pos_mask.sum()), int(neg_mask.sum()),
    )
    return df


# ---------------------------------------------------------------------------
# Member-geclusterter Block-Bootstrap (ohne scipy)
# ---------------------------------------------------------------------------
# Resamplet ganze Member (Cluster), nicht Einzeltrades — Swarm = Clustering per
# Definition; naives Row-Resampling erzeugt Schein-Signifikanz (das t=-22 im
# Vorgaenger-Report). Optimierung: pro Cluster (sum, count) vorberechnen, jede
# Iteration ist dann O(#Member) statt O(#Trades).


def _cluster_sum_count(
    values: np.ndarray, clusters: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(members, sums, counts)`` ueber endliche Werte, gruppiert nach Cluster."""
    v = np.asarray(values, dtype='float64')
    finite = np.isfinite(v)
    v = v[finite]
    c = np.asarray(clusters)[finite]
    if len(v) == 0:
        empty = np.array([])
        return empty, empty, empty
    members, inv = np.unique(c, return_inverse=True)
    sums = np.zeros(len(members), dtype='float64')
    counts = np.zeros(len(members), dtype='float64')
    np.add.at(sums, inv, v)
    np.add.at(counts, inv, 1.0)
    return members, sums, counts


def clustered_bootstrap_mean(
    values: np.ndarray, clusters: np.ndarray,
    n_iter: int = 2000, seed: int = 0,
) -> Dict[str, float]:
    """Member-geclusterter Bootstrap des Mittelwerts.

    Returns dict: point, ci_lo, ci_hi (2.5/97.5%), n (Trades), n_clusters.
    """
    members, sums, counts = _cluster_sum_count(values, clusters)
    m = len(members)
    if m == 0:
        return {'point': float('nan'), 'ci_lo': float('nan'),
                'ci_hi': float('nan'), 'n': 0, 'n_clusters': 0}
    point = float(sums.sum() / counts.sum())
    rng = np.random.default_rng(seed)
    boot = np.empty(n_iter, dtype='float64')
    for i in range(n_iter):
        pick = rng.integers(0, m, m)
        tc = counts[pick].sum()
        boot[i] = sums[pick].sum() / tc if tc > 0 else np.nan
    lo, hi = np.nanpercentile(boot, [2.5, 97.5])
    return {'point': point, 'ci_lo': float(lo), 'ci_hi': float(hi),
            'n': int(counts.sum()), 'n_clusters': m}


def clustered_bootstrap_diff(
    values: np.ndarray, clusters: np.ndarray, mask: np.ndarray,
    n_iter: int = 2000, seed: int = 0,
) -> Dict[str, float]:
    """Differenz der Mittelwerte (``mask`` True minus False), member-geclustert.

    Pro Cluster werden (sum, count) getrennt fuer with/without vorberechnet; jede
    Bootstrap-Iteration resamplet Member und bildet die Differenz der gepoolten
    Mittelwerte. ``p`` = zweiseitiger Bootstrap-p (Anteil der Resamples, der 0
    ueberschreitet, x2, gekappt).

    Returns dict: point, ci_lo, ci_hi, p, n_with, n_without, n_clusters.
    """
    v = np.asarray(values, dtype='float64')
    m = np.asarray(mask, dtype=bool)
    c = np.asarray(clusters)
    finite = np.isfinite(v)
    v = v[finite]
    m = m[finite]
    c = c[finite]
    nan = float('nan')
    if len(v) == 0:
        return {'point': nan, 'ci_lo': nan, 'ci_hi': nan, 'p': nan,
                'n_with': 0, 'n_without': 0, 'n_clusters': 0}
    members, inv = np.unique(c, return_inverse=True)
    k = len(members)
    ws = np.zeros(k); wc = np.zeros(k)
    os_ = np.zeros(k); oc = np.zeros(k)
    np.add.at(ws, inv[m], v[m])
    np.add.at(wc, inv[m], 1.0)
    np.add.at(os_, inv[~m], v[~m])
    np.add.at(oc, inv[~m], 1.0)

    def _diff(sel: np.ndarray) -> float:
        ac = wc[sel].sum(); bc = oc[sel].sum()
        if ac <= 0 or bc <= 0:
            return np.nan
        return ws[sel].sum() / ac - os_[sel].sum() / bc

    point = _diff(np.arange(k))
    if not np.isfinite(point):
        return {'point': nan, 'ci_lo': nan, 'ci_hi': nan, 'p': nan,
                'n_with': int(wc.sum()), 'n_without': int(oc.sum()),
                'n_clusters': k}
    rng = np.random.default_rng(seed)
    boot = np.empty(n_iter, dtype='float64')
    for i in range(n_iter):
        boot[i] = _diff(rng.integers(0, k, k))
    bb = boot[np.isfinite(boot)]
    lo, hi = np.nanpercentile(boot, [2.5, 97.5])
    if len(bb) == 0:
        p = nan
    else:
        frac = (bb <= 0).mean() if point > 0 else (bb >= 0).mean()
        p = float(min(1.0, 2.0 * frac))
    return {'point': float(point), 'ci_lo': float(lo), 'ci_hi': float(hi),
            'p': p, 'n_with': int(wc.sum()), 'n_without': int(oc.sum()),
            'n_clusters': k}
