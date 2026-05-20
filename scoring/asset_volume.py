"""Asset-Tradingvolume-Aggregation (Modul 8, Phase 8.2).

Berechnet pro Trade T die Summe aller ``trade_volume_usd``-Werte aller Trades
mit gleichem ``issuer_group_key`` (Cross-Class via SEC EDGAR CIK, P5.6)
innerhalb des Zeitfensters ``[T.transaction_date - Z, T.transaction_date]``.

SKIP-Buckets (``issuer_group_key`` mit Praefix ``SKIP:``) werden auf 0
gesetzt — gleiches Pattern wie ``signals/swarm_buy_signal._is_skip_bucket``.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

SKIP_PREFIX = 'SKIP:'


def _is_skip(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(SKIP_PREFIX)


def compute_asset_tradingvolume(df: pd.DataFrame,
                                window_days_z: int) -> pd.Series:
    """Pro-Zeile Sum(trade_volume_usd) ueber issuer_group_key im Fenster
    [transaction_date - Z, transaction_date].

    Erwartete Spalten: ``trade_volume_usd``, ``issuer_group_key``,
    ``transaction_date``. Liefert int64-Series in der Reihenfolge von
    ``df.index``. SKIP-Buckets und Zeilen ohne gueltiges Datum bekommen 0.
    """
    if df.empty:
        return pd.Series([], dtype='int64', index=df.index)

    work = df[['trade_volume_usd', 'issuer_group_key', 'transaction_date']].copy()
    work['transaction_date'] = pd.to_datetime(work['transaction_date'],
                                              errors='coerce')
    work['trade_volume_usd'] = pd.to_numeric(work['trade_volume_usd'],
                                             errors='coerce').fillna(0).astype('int64')

    result = pd.Series(0, index=df.index, dtype='int64')
    skip_mask = work['issuer_group_key'].map(_is_skip)
    eligible_mask = (~skip_mask) & work['transaction_date'].notna() & \
                    work['issuer_group_key'].notna() & \
                    (work['issuer_group_key'].astype(str) != '')
    if not eligible_mask.any():
        return result

    eligible = work[eligible_mask].copy()
    eligible['_orig_idx'] = eligible.index

    # Tagesebene: alle Trades am gleichen Tag in derselben Gruppe sehen das
    # gleiche Volumen (Schwarm-Konsistenz). Daily-Sum -> rolling -> per-row
    # join zurueck auf _orig_idx.
    daily = (
        eligible.groupby(['issuer_group_key', 'transaction_date'], sort=True,
                         as_index=False)['trade_volume_usd'].sum()
    )
    daily = daily.sort_values(['issuer_group_key', 'transaction_date'])

    window = f'{window_days_z}D'
    rolling = (
        daily.set_index('transaction_date')
        .groupby('issuer_group_key', sort=False)['trade_volume_usd']
        .rolling(window=window, closed='both').sum()
        .reset_index()
        .rename(columns={'trade_volume_usd': '_rolling_sum'})
    )
    # rolling-Series: index = (issuer_group_key, transaction_date) -> als
    # Lookup-Map per merge auf eligible zurueckspielen.
    merged = eligible.merge(rolling, on=['issuer_group_key', 'transaction_date'],
                            how='left')
    result.loc[merged['_orig_idx'].values] = \
        merged['_rolling_sum'].fillna(0).astype('int64').values
    return result
