"""Tests fuer app/chart_helpers.py — Teil-/Vollverkaufs-Marker.

Kernzusicherung: Teilverkauf und Vollverkauf werden mit UNTERSCHIEDLICHEN
Symbolen markiert, Nicht-Verkaeufe gar nicht, und fehlende Datum/Preis-Werte
werden uebersprungen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chart_helpers import (  # noqa: E402
    SALE_KIND_MARKERS,
    build_partial_close_rows,
    build_recent_pool,
    build_sale_kind_traces,
    sale_kind_marker,
)


# --- sale_kind_marker ----------------------------------------------------

def test_partial_and_full_use_distinct_symbols():
    """Feature-Kern: Teilverkauf != Vollverkauf im Symbol."""
    partial = sale_kind_marker('Sale (Partial)')
    full = sale_kind_marker('Sale (Full)')
    assert partial is not None and full is not None
    assert partial['symbol'] != full['symbol']
    assert partial['label'] != full['label']


def test_partial_label_and_symbol():
    style = sale_kind_marker('Sale (Partial)')
    assert style['label'] == 'Teilverkauf'
    assert style['symbol'] == 'triangle-down-open'


def test_non_sale_types_return_none():
    for tt in ['Purchase', 'Sale', 'Exchange', 'PURCHASE', None, '', float('nan')]:
        assert sale_kind_marker(tt) is None


def test_returns_fresh_copy_no_aliasing():
    style = sale_kind_marker('Sale (Partial)')
    style['symbol'] = 'MUTATED'
    assert SALE_KIND_MARKERS['Sale (Partial)']['symbol'] != 'MUTATED'
    assert sale_kind_marker('Sale (Partial)')['symbol'] != 'MUTATED'


# --- build_sale_kind_traces ----------------------------------------------

def _rec(date, price, tt, name='X'):
    return {'date': date, 'price': price, 'transaction_type': tt, 'name': name}


def test_empty_input_returns_empty_list():
    assert build_sale_kind_traces([]) == []


def test_only_non_sales_returns_empty():
    recs = [_rec('2024-01-01', 10.0, 'Purchase'),
            _rec('2024-02-01', 11.0, 'Sale')]
    assert build_sale_kind_traces(recs) == []


def test_partial_and_full_produce_two_distinct_traces():
    recs = [
        _rec('2024-03-01', 50.0, 'Sale (Partial)'),
        _rec('2024-04-01', 55.0, 'Sale (Full)'),
    ]
    traces = build_sale_kind_traces(recs)
    assert len(traces) == 2
    assert all(isinstance(t, go.Scatter) for t in traces)
    symbols = {t.marker.symbol for t in traces}
    assert symbols == {'triangle-down-open', 'x'}
    # Deterministische Reihenfolge: Teilverkauf zuerst.
    assert traces[0].name == 'Teilverkauf'
    assert traces[1].name == 'Vollverkauf'


def test_same_kind_grouped_into_single_trace():
    recs = [
        _rec('2024-03-01', 50.0, 'Sale (Partial)', name='A'),
        _rec('2024-03-15', 52.0, 'Sale (Partial)', name='B'),
    ]
    traces = build_sale_kind_traces(recs)
    assert len(traces) == 1
    assert list(traces[0].y) == [50.0, 52.0]
    assert len(traces[0].x) == 2


def test_missing_date_or_price_skipped():
    recs = [
        _rec(pd.NaT, 50.0, 'Sale (Partial)'),
        _rec('2024-03-01', float('nan'), 'Sale (Partial)'),
        _rec(None, 50.0, 'Sale (Full)'),
    ]
    assert build_sale_kind_traces(recs) == []


def test_hover_text_includes_name_and_label():
    traces = build_sale_kind_traces(
        [_rec('2024-03-01', 50.0, 'Sale (Partial)', name='Boozman, John')])
    assert traces[0].text[0] == 'Boozman, John · Teilverkauf'


# --- build_partial_close_rows --------------------------------------------

def _opening(**ov):
    base = {
        'trade_id': 'abc123',
        'transaction_date': '2024-01-01',
        'baseline_price': 100.0,
        'volume': 50_000.0,
    }
    base.update(ov)
    return base


def _event(date, volume, price, trade_id='X'):
    return {'trade_id': trade_id, 'date': date, 'volume': volume, 'price': price}


def test_single_full_close_one_row():
    """Ein Schliessungs-Event -> genau eine Zeile mit -pc1-ID + parent."""
    rows = build_partial_close_rows(
        _opening(), [_event('2024-03-01', 50_000.0, 110.0)], sell_sign=1.0)
    assert len(rows) == 1
    assert rows[0]['trade_id'] == 'abc123-pc1'
    assert rows[0]['parent_trade_id'] == 'abc123'
    # (110/100 - 1) * 100 * 1 = +10 %
    assert rows[0]['effective_return_pct'] == pytest.approx(10.0)


def test_two_partial_closes_chronological_ids():
    """Zwei Teil-Closes -> zwei Zeilen, -pc1/-pc2, chronologisch."""
    rows = build_partial_close_rows(
        _opening(),
        [_event('2024-02-01', 20_000.0, 105.0),
         _event('2024-03-01', 20_000.0, 110.0)],
        sell_sign=1.0,
    )
    assert [r['trade_id'] for r in rows] == ['abc123-pc1', 'abc123-pc2']
    assert all(r['parent_trade_id'] == 'abc123' for r in rows)
    assert rows[0]['close_offset_days'] == 31   # 2024-01-01 -> 2024-02-01
    assert rows[1]['close_offset_days'] == 60   # 2024-01-01 -> 2024-03-01


def test_volume_capped_to_opening():
    """Summe der exit_volume <= Eroeffnungs-Volumen (Lite-Deckelung)."""
    rows = build_partial_close_rows(
        _opening(volume=50_000.0),
        [_event('2024-02-01', 40_000.0, 105.0),
         _event('2024-03-01', 40_000.0, 110.0)],  # zusammen 80k > 50k
        sell_sign=1.0,
    )
    total = sum(r['exit_volume'] for r in rows)
    assert total == pytest.approx(50_000.0)
    assert rows[0]['exit_volume'] == pytest.approx(40_000.0)
    assert rows[1]['exit_volume'] == pytest.approx(10_000.0)  # gedeckelt


def test_sell_opening_inverts_sign():
    """Verkauf-Eroeffnung (sell_sign=-1) spiegelt den Return."""
    buy = build_partial_close_rows(
        _opening(), [_event('2024-03-01', 50_000.0, 110.0)], sell_sign=1.0)
    sell = build_partial_close_rows(
        _opening(), [_event('2024-03-01', 50_000.0, 110.0)], sell_sign=-1.0)
    assert sell[0]['effective_return_pct'] == pytest.approx(
        -buy[0]['effective_return_pct'])


def test_nan_price_event_skipped_and_numbering():
    """Event mit NaN-Preis wird uebersprungen; -pc-Nummerierung laeuft ueber
    nur die emittierten Zeilen weiter."""
    rows = build_partial_close_rows(
        _opening(),
        [_event('2024-02-01', 10_000.0, float('nan')),  # uebersprungen
         _event('2024-03-01', 10_000.0, 110.0)],
        sell_sign=1.0,
    )
    assert len(rows) == 1
    assert rows[0]['trade_id'] == 'abc123-pc1'


def test_invalid_baseline_returns_empty():
    """Ungueltige baseline (NaN / <=0) -> keine Zeilen."""
    assert build_partial_close_rows(
        _opening(baseline_price=float('nan')),
        [_event('2024-03-01', 50_000.0, 110.0)], 1.0) == []
    assert build_partial_close_rows(
        _opening(baseline_price=0.0),
        [_event('2024-03-01', 50_000.0, 110.0)], 1.0) == []


def test_empty_events_returns_empty():
    assert build_partial_close_rows(_opening(), [], 1.0) == []


# --- build_recent_pool ---------------------------------------------------

def _recent_df():
    ref = pd.Timestamp('2026-06-06')
    return pd.DataFrame({
        'trade_id': ['A', 'B', 'C'],
        'transaction_date': [
            ref - pd.Timedelta(days=3),    # innerhalb 7d
            ref - pd.Timedelta(days=20),   # ausserhalb 7d, innerhalb 30d
            ref - pd.Timedelta(days=200),  # ausserhalb 30d
        ],
        'first_close_offset_days': [float('nan'), 5.0, 10.0],
    })


def test_recent_pool_window_filters_by_transaction_date():
    """Fenster 7d -> nur Trade A; Bezugsdatum explizit."""
    pool = build_recent_pool(_recent_df(), 7,
                             reference_date=pd.Timestamp('2026-06-06'))
    assert list(pool['trade_id']) == ['A']


def test_recent_pool_includes_open_positions():
    """Offene Position (first_close_offset_days = NaN) bleibt sichtbar."""
    pool = build_recent_pool(_recent_df(), 30,
                             reference_date=pd.Timestamp('2026-06-06'))
    assert set(pool['trade_id']) == {'A', 'B'}
    # Trade A ist held (NaN) und trotzdem enthalten.
    assert pd.isna(pool.loc[pool['trade_id'] == 'A',
                            'first_close_offset_days'].iloc[0])


def test_recent_pool_adds_days_since_trade():
    pool = build_recent_pool(_recent_df(), 30,
                             reference_date=pd.Timestamp('2026-06-06'))
    assert pool.loc[pool['trade_id'] == 'A', 'days_since_trade'].iloc[0] == 3
    assert pool.loc[pool['trade_id'] == 'B', 'days_since_trade'].iloc[0] == 20


def test_recent_pool_missing_date_column():
    """Fehlende date_col -> leeres Ergebnis (kein Crash)."""
    pool = build_recent_pool(pd.DataFrame({'x': [1]}), 30)
    assert pool.empty
