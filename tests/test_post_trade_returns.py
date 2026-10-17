"""Tests fuer scoring/post_trade_returns.py — Post-Trade Pricing + Exit-Detection."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from config import settings
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoring.post_trade_returns import (  # noqa: E402
    _apply_paper_returns,
    _build_price_lookup,
    _compute_exit_columns,
    _ensure_cache_for_tickers,
    _is_applicable,
    _is_valid_yf_ticker,
    _latest_close_map,
    _load_price_cache,
    _price_at_or_after,
    _price_at_or_after_fast,
    _save_price_cache,
    _sell_sign_series,
    _yf_close_series,
    compute_paper_returns,
    compute_post_trade_returns,
    DELISTED_MARKER_DATE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cache(records: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Hilfsfunktion: Liste von (ticker, date_str, close) -> MultiIndex-DF."""
    arrays = {'ticker': [], 'date': [], 'close': []}
    for tkr, d, c in records:
        arrays['ticker'].append(tkr)
        arrays['date'].append(pd.Timestamp(d))
        arrays['close'].append(float(c))
    idx = pd.MultiIndex.from_arrays(
        [arrays['ticker'], arrays['date']],
        names=['ticker', 'date'],
    )
    return pd.DataFrame({'close': arrays['close']}, index=idx)


def _make_trade_row(**overrides) -> dict:
    base = {
        'trade_id': 'T001',
        'name': 'Senator A',
        'ticker': 'AAPL',
        'issuer_group_key': 'CIK:0000320193',
        'transaction_date': '2025-06-02',  # Monday
        'transaction_type': 'Purchase',
        'trade_amount_max_usd': 50_000.0,
        'ticker_type': 'stock',
    }
    base.update(overrides)
    return base


@pytest.fixture
def sample_cache() -> pd.DataFrame:
    """AAPL: taeglich Close 100-130 ueber Juni 2025 (nur Wochentage)."""
    records = []
    price = 100.0
    for d in pd.date_range('2025-06-02', '2025-07-31', freq='B'):
        records.append(('AAPL', d.strftime('%Y-%m-%d'), price))
        price += 0.5
    return _make_cache(records)


# ---------------------------------------------------------------------------
# 1. _price_at_or_after — Wochenende / Cache-Miss
# ---------------------------------------------------------------------------

def test_price_at_or_after_weekday(sample_cache):
    """Montag -> exakter Close."""
    result = _price_at_or_after(sample_cache, 'AAPL', pd.Timestamp('2025-06-02'))
    assert result == 100.0


def test_price_at_or_after_weekend_forward(sample_cache):
    """Samstag -> naechster Handelstag (Montag)."""
    result = _price_at_or_after(sample_cache, 'AAPL', pd.Timestamp('2025-06-07'))
    # June 7 2025 is Saturday -> next trading day is June 9 (Monday)
    # June 2 = 100, June 3 = 100.5, June 4 = 101, June 5 = 101.5, June 6 = 102
    # June 9 = 102.5
    assert result == 102.5


def test_price_at_or_after_cache_miss(sample_cache):
    """Ticker nicht im Cache -> None."""
    result = _price_at_or_after(sample_cache, 'MSFT', pd.Timestamp('2025-06-02'))
    assert result is None


def test_price_at_or_after_empty_ticker(sample_cache):
    """Leerer Ticker -> None."""
    assert _price_at_or_after(sample_cache, '', pd.Timestamp('2025-06-02')) is None


def test_price_at_or_after_future_date(sample_cache):
    """Ziel-Datum nach letztem Cache-Eintrag -> None."""
    result = _price_at_or_after(sample_cache, 'AAPL', pd.Timestamp('2026-01-01'))
    assert result is None


# ---------------------------------------------------------------------------
# 2. Exit-Detection: keine opposing -> held
# ---------------------------------------------------------------------------

def test_exit_no_opposing_held(sample_cache):
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase'),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'held'
    assert pd.isna(cols['exit_date'].iloc[0])
    assert cols['opposing_trade_ids'].iloc[0] == ''


# ---------------------------------------------------------------------------
# 3. Exit-Detection: opposing >= threshold -> closed_full
# ---------------------------------------------------------------------------

def test_exit_opposing_sufficient_closed_full(sample_cache):
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'closed_full'
    assert cols['exit_date'].iloc[0] == pd.Timestamp('2025-06-20')
    assert cols['exit_amount_max_usd'].iloc[0] == 60_000.0
    assert cols['opposing_trade_ids'].iloc[0] == 'T2'


# ---------------------------------------------------------------------------
# 4. Exit-Detection: opposing < threshold -> closed_partial
# ---------------------------------------------------------------------------

def test_exit_opposing_insufficient_closed_partial(sample_cache):
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=30_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'closed_partial'
    assert cols['exit_amount_max_usd'].iloc[0] == 30_000.0
    assert cols['opposing_trade_ids'].iloc[0] == 'T2'


# ---------------------------------------------------------------------------
# 5. Exit-Detection: zwei opposing, kumuliert >= threshold -> closed_full
# ---------------------------------------------------------------------------

def test_exit_two_opposing_cumulative_closed_full(sample_cache):
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-15',
                        transaction_type='Sale', trade_amount_max_usd=20_000),
        _make_trade_row(trade_id='T3', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=35_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'closed_full'
    # exit_date = zweiter Sell (T3), weil da die Schwelle ueberschritten wird
    assert cols['exit_date'].iloc[0] == pd.Timestamp('2025-06-20')
    assert cols['exit_amount_max_usd'].iloc[0] == 55_000.0
    assert cols['opposing_trade_ids'].iloc[0] == 'T2;T3'
    # T2 hat keinen opposing -> held
    assert cols['exit_status'].iloc[1] == 'held'


# ---------------------------------------------------------------------------
# 6. SKIP-Bucket -> not_applicable
# ---------------------------------------------------------------------------

def test_skip_bucket_not_applicable(sample_cache):
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', issuer_group_key='SKIP:Bond',
                        transaction_type='Purchase'),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    assert bool(df['_post_trade_applicable'].iloc[0]) is False
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'not_applicable'


# ---------------------------------------------------------------------------
# 7. Exchange-Transaction-Type -> not_applicable (Exit), aber Horizont ok
# ---------------------------------------------------------------------------

def test_exchange_no_exit_but_priced(sample_cache):
    """Exchange: _is_applicable = True (Horizont-Preise), aber Exit = not_applicable."""
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_type='Exchange'),
    ])
    # _is_applicable returns True for Exchange (we want horizon prices)
    # but _compute_exit_columns filters out non-Purchase/Sale
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    assert bool(df['_post_trade_applicable'].iloc[0]) is True
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'not_applicable'


# ---------------------------------------------------------------------------
# 8. Horizont-Berechnung mit synthetischem Cache
# ---------------------------------------------------------------------------

def test_horizon_calculation(sample_cache, monkeypatch):
    """Buy am Mo 2025-06-02. +30d = 2025-07-02 (Mi)."""
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_load_price_cache', lambda path: sample_cache)
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)
    monkeypatch.setattr(
        ptr, '_ensure_cache_for_tickers',
        lambda cache, tickers, **kw: cache,
    )
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase'),
    ])
    result = compute_post_trade_returns(
        df,
        horizons_days=[30],
        cache_path='/tmp/_test_ptr_cache_delete_me.parquet',
    )
    # Baseline = June 2 close = 100.0
    assert result['post_trade_baseline_price'].iloc[0] == 100.0
    # +30d: June 2 + 30 cal days = July 2 (Wed). 22 business days = 100 + 22*0.5 = 111.0
    baseline = result['post_trade_baseline_price'].iloc[0]
    p30 = result['post_price_30d'].iloc[0]
    assert p30 is not None
    assert p30 > baseline
    # Return check
    expected_return = (p30 / baseline - 1.0) * 100.0
    assert result['post_return_30d'].iloc[0] == pytest.approx(expected_return)
    # held -> effective_return_pct ist jetzt NaN (realisiert-only; kein
    # willkuerlicher Horizont-Snapshot mehr).
    assert result['exit_status'].iloc[0] == 'held'
    assert pd.isna(result['effective_return_pct'].iloc[0])
    # Cleanup
    if os.path.isfile('/tmp/_test_ptr_cache_delete_me.parquet'):
        os.remove('/tmp/_test_ptr_cache_delete_me.parquet')


# ---------------------------------------------------------------------------
# 8b. Richtungsbewusster Return (Bug B): Verkauf invertiert das Vorzeichen
# ---------------------------------------------------------------------------

def test_sell_return_is_direction_aware(monkeypatch, sample_cache):
    """Ein Verkauf muss das Return-Vorzeichen invertieren.

    ``sample_cache`` steigt monoton (100 -> ...). Ein Kauf am 2025-06-02 hat
    daher POSITIVEN Return (gut gekauft), ein Verkauf am selben Tag NEGATIVEN
    (verkauft vor dem Anstieg = entgangener Gewinn). Bei gleicher Baseline +
    Horizont-Preis muss der Sell-Return exakt das Negative des Buy-Returns sein.
    Gilt fuer post_return_{X}d UND effective_return_pct (beide held).
    """
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_load_price_cache', lambda path: sample_cache)
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)
    monkeypatch.setattr(
        ptr, '_ensure_cache_for_tickers',
        lambda cache, tickers, **kw: cache,
    )
    df = pd.DataFrame([
        _make_trade_row(trade_id='B1', transaction_date='2025-06-02',
                        transaction_type='Purchase'),
        _make_trade_row(trade_id='S1', transaction_date='2025-06-02',
                        transaction_type='Sale (Partial)'),
    ])
    result = compute_post_trade_returns(
        df, horizons_days=[30],
        cache_path='/tmp/_test_ptr_sell_delete_me.parquet',
    )
    buy = result[result['trade_id'] == 'B1'].iloc[0]
    sell = result[result['trade_id'] == 'S1'].iloc[0]
    # Kurs steigt -> Buy positiv, Sell negativ.
    assert buy['post_return_30d'] > 0
    assert sell['post_return_30d'] < 0
    # Exakt gespiegelt (gleiche baseline + Horizontpreis).
    assert sell['post_return_30d'] == pytest.approx(-buy['post_return_30d'])
    # Beide sind 'held' -> effective_return realisiert-only -> NaN.
    assert pd.isna(buy['effective_return_pct'])
    assert pd.isna(sell['effective_return_pct'])
    if os.path.isfile('/tmp/_test_ptr_sell_delete_me.parquet'):
        os.remove('/tmp/_test_ptr_sell_delete_me.parquet')


# ---------------------------------------------------------------------------
# 9. Tolerance > 0 verschiebt closed_partial -> closed_full
# ---------------------------------------------------------------------------

def test_tolerance_makes_partial_full(sample_cache):
    """Mit 20% Toleranz: 30k >= 50k*0.8 = 40k? Nein. 45k >= 40k? Ja."""
    # Ohne Toleranz: 30k < 50k -> partial
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=45_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)

    # strict: 45k < 50k -> partial
    cols_strict = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols_strict['exit_status'].iloc[0] == 'closed_partial'

    # 20% tolerance: threshold = 50k * 0.8 = 40k; 45k >= 40k -> full
    cols_tol = _compute_exit_columns(df, sample_cache, 120, 0.2)
    assert cols_tol['exit_status'].iloc[0] == 'closed_full'


# ---------------------------------------------------------------------------
# 10. Cache save/load roundtrip
# ---------------------------------------------------------------------------

def test_cache_roundtrip():
    cache = _make_cache([
        ('AAPL', '2025-06-02', 100.0),
        ('AAPL', '2025-06-03', 101.0),
    ])
    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as f:
        tmp = f.name
    try:
        _save_price_cache(cache, tmp)
        loaded = _load_price_cache(tmp)
        assert len(loaded) == 2
        assert loaded.loc[('AAPL', pd.Timestamp('2025-06-02'))]['close'] == 100.0
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# 11. Ticker leer/NaN -> not_applicable
# ---------------------------------------------------------------------------

def test_nan_ticker_not_applicable(sample_cache):
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', ticker=''),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    assert bool(df['_post_trade_applicable'].iloc[0]) is False


# ---------------------------------------------------------------------------
# 12. issuer_group_key SKIP -> _is_applicable False
# ---------------------------------------------------------------------------

def test_skip_issuer_group_not_applicable():
    row = pd.Series(_make_trade_row(
        issuer_group_key='SKIP:Bond', ticker_type='stock',
    ))
    assert _is_applicable(row) is False


# ---------------------------------------------------------------------------
# 13. Nicht-applicable ticker_type
# ---------------------------------------------------------------------------

def test_non_applicable_ticker_type():
    row = pd.Series(_make_trade_row(ticker_type='mutual_fund'))
    assert _is_applicable(row) is False


# ---------------------------------------------------------------------------
# 14. Delisted marker im Cache -> exit_status delisted
# ---------------------------------------------------------------------------

def test_delisted_marker_triggers_delisted_status():
    cache = _make_cache([
        ('DEAD', '1900-01-01', np.nan),
    ])
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', ticker='DEAD',
                        issuer_group_key='CIK:0000000001',
                        transaction_type='Purchase'),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'delisted'


# ---------------------------------------------------------------------------
# 15. compute_post_trade_returns Integration: volle Pipeline
# ---------------------------------------------------------------------------

def test_compute_post_trade_returns_integration(sample_cache, monkeypatch):
    """Integrationstest mit monkeypatch fuer Cache-Load/Save."""
    tmp_cache = '/tmp/_test_ptr_integration.parquet'
    if os.path.isfile(tmp_cache):
        os.remove(tmp_cache)

    # Pre-seed cache
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_load_price_cache', lambda path: sample_cache)
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)
    monkeypatch.setattr(
        ptr, '_ensure_cache_for_tickers',
        lambda cache, tickers, **kw: cache,
    )

    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=60_000),
    ])

    result = compute_post_trade_returns(
        df,
        horizons_days=[30, 60],
        cache_path=tmp_cache,
        fetch_years=5,
        cache_ttl_days=7,
        exit_amount_tolerance=0.0,
    )

    # Spalten-Check
    assert 'post_trade_baseline_price' in result.columns
    assert 'post_price_30d' in result.columns
    assert 'post_return_30d' in result.columns
    assert 'post_price_60d' in result.columns
    assert 'exit_status' in result.columns
    assert 'effective_return_pct' in result.columns

    # T1: closed_full, T2: held
    assert result['exit_status'].iloc[0] == 'closed_full'
    assert result['exit_status'].iloc[1] == 'held'

    # effective_return_pct T1 = (exit_price / baseline - 1) * 100
    assert result['effective_return_pct'].iloc[0] is not None
    assert result['effective_return_pct'].iloc[0] > 0  # price steigt


# ---------------------------------------------------------------------------
# 16. Ticker-Validierung — Datums-Strings + Sonderzeichen werden abgewiesen
# ---------------------------------------------------------------------------

def test_invalid_tickers_rejected_before_yfinance():
    # gueltig
    assert _is_valid_yf_ticker('AAPL') is True
    assert _is_valid_yf_ticker('BRK.B') is True
    assert _is_valid_yf_ticker('BRK-B') is True
    assert _is_valid_yf_ticker('BAC^L') is True
    assert _is_valid_yf_ticker('SAP.DE') is True
    # ungueltig (Datums-Strings, Sonderzeichen, leer)
    assert _is_valid_yf_ticker('07/01/28') is False
    assert _is_valid_yf_ticker('12/01/37') is False
    assert _is_valid_yf_ticker('3%') is False
    assert _is_valid_yf_ticker('') is False
    assert _is_valid_yf_ticker(None) is False
    assert _is_valid_yf_ticker(float('nan')) is False
    # Leerzeichen + lowercase werden normalisiert
    assert _is_valid_yf_ticker(' aapl ') is True


# ---------------------------------------------------------------------------
# 16b. Refetch-Fenster (Option A): refresh_eligible begrenzt den TTL-Refresh
# ---------------------------------------------------------------------------

def test_refresh_eligible_gate_excludes_closed_positions(monkeypatch):
    """Option A (POST_TRADE_REFRESH_WINDOW_DAYS -> refresh_eligible):

    - veralteter, NICHT aktiver Ticker (alle Positionen geschlossen/abgelaufen)
      -> KEIN erneuter Fetch (Cache bleibt unveraendert),
    - aktiver veralteter Ticker -> Fetch,
    - noch nicht gecachter Ticker -> IMMER Fetch (Full-Rebuild-Erstbefuellung),
      unabhaengig vom Fenster.
    """
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)

    calls: list[str] = []

    def _fake_download(tkr, fetch_years, on_attempt_failed=None):
        calls.append(tkr)
        return pd.DataFrame(
            {'Close': [111.0]},
            index=pd.DatetimeIndex(
                [pd.Timestamp.now().normalize()], name='Date'),
        )

    monkeypatch.setattr(ptr, '_download_with_retry', _fake_download)

    class _GreenCanary:
        """Haelt den Fetch-Commit-Pfad offline (kein echtes yfinance-Probe)."""
        saw_throttle = False

        def verdict(self):
            return ptr.GREEN

        def force_reprobe(self):
            return ptr.GREEN

        def wait_then_reprobe(self):
            return ptr.GREEN

    # 'OLD' im Cache mit echtem, VERALTETEM Preis (2020) -> stale ggue. TTL=1.
    cache = _make_cache([('OLD', '2020-01-02', 100.0)])

    # (1) Gate aktiv, OLD nicht aktiv -> KEIN Fetch, Cache unveraendert.
    out = _ensure_cache_for_tickers(
        cache, ['OLD'], cache_ttl_days=1,
        refresh_eligible=set(), canary=_GreenCanary())
    assert calls == []
    assert out.equals(cache)

    # (2) OLD aktiv (im refresh_eligible-Set) -> Fetch ausgeloest.
    calls.clear()
    _ensure_cache_for_tickers(
        cache, ['OLD'], cache_ttl_days=1,
        refresh_eligible={'OLD'}, canary=_GreenCanary())
    assert calls == ['OLD']

    # (3) 'NEW' ist nicht gecacht -> IMMER Fetch, auch wenn nicht refresh_eligible.
    #     'OLD' bleibt geskippt (stale + inaktiv).
    calls.clear()
    _ensure_cache_for_tickers(
        cache, ['OLD', 'NEW'], cache_ttl_days=1,
        refresh_eligible=set(), canary=_GreenCanary())
    assert calls == ['NEW']


# ---------------------------------------------------------------------------
# 17. Single-row yfinance-Resultat — kein Crash mehr durch squeeze()
# ---------------------------------------------------------------------------

def test_yf_close_series_single_row():
    """Single-row DataFrame -> Series mit 1 Eintrag, KEIN Skalar."""
    hist = pd.DataFrame(
        {'Close': [123.45]},
        index=pd.DatetimeIndex(['2025-06-02'], name='Date'),
    )
    series = _yf_close_series(hist)
    assert isinstance(series, pd.Series)
    assert len(series) == 1
    assert series.iloc[0] == 123.45


def test_yf_close_series_empty():
    assert _yf_close_series(pd.DataFrame()) is None
    assert _yf_close_series(None) is None


def test_yf_close_series_multiindex():
    """yfinance liefert manchmal MultiIndex-Spalten (auch bei single ticker)."""
    hist = pd.DataFrame(
        {('Close', 'AAPL'): [100.0, 101.0]},
        index=pd.DatetimeIndex(['2025-06-02', '2025-06-03'], name='Date'),
    )
    hist.columns = pd.MultiIndex.from_tuples([('Close', 'AAPL')])
    series = _yf_close_series(hist)
    assert isinstance(series, pd.Series)
    assert len(series) == 2


# ---------------------------------------------------------------------------
# 18. _compute_exit_columns auf String-Dates (Robustness)
# ---------------------------------------------------------------------------

def test_exit_columns_accepts_string_dates(sample_cache):
    """Production-Path konvertiert transaction_date oben, aber _compute_exit_columns
    muss auch defensiv damit umgehen koennen — Test ruft den Helper direkt auf."""
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase'),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'held'


# ---------------------------------------------------------------------------
# 19. Fehlende Input-Spalte trade_amount_max_usd -> klare Fehlermeldung
# ---------------------------------------------------------------------------

def test_missing_input_column_raises_clear_error(monkeypatch, sample_cache):
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_load_price_cache', lambda path: sample_cache)
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)
    monkeypatch.setattr(
        ptr, '_ensure_cache_for_tickers',
        lambda cache, tickers, **kw: cache,
    )
    df = pd.DataFrame([{
        'trade_id': 'T1', 'name': 'Senator A', 'ticker': 'AAPL',
        'issuer_group_key': 'CIK:1', 'transaction_date': '2025-06-02',
        'transaction_type': 'Purchase', 'ticker_type': 'stock',
        # 'trade_amount_max_usd' fehlt absichtlich!
    }])
    with pytest.raises(ValueError, match='trade_amount_max_usd'):
        compute_post_trade_returns(df, horizons_days=[30],
                                   cache_path='/tmp/_irrelevant.parquet')


# ---------------------------------------------------------------------------
# 20. Fast-Lookup: Ergebnis-Aequivalenz zum Original _price_at_or_after
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_cache() -> pd.DataFrame:
    """Mehrere Ticker + Wochenend-Luecken + delisted-Marker + leerer Ticker."""
    records = []
    price = 100.0
    for d in pd.date_range('2025-06-02', '2025-07-31', freq='B'):  # AAPL Wochentage
        records.append(('AAPL', d.strftime('%Y-%m-%d'), price)); price += 0.5
    for d in pd.date_range('2024-01-02', '2024-03-29', freq='B'):  # MSFT anderer Zeitraum
        records.append(('MSFT', d.strftime('%Y-%m-%d'), 200.0))
    records.append(('TSLA', '2025-06-03', 300.0))                  # nur 1 Handelstag
    records.append(('DEAD', DELISTED_MARKER_DATE.strftime('%Y-%m-%d'), float('nan')))
    return _make_cache(records)


def test_fast_lookup_equivalent_to_original(multi_cache):
    """_price_at_or_after_fast muss bit-genau dasselbe liefern wie das Original.

    Deckt: Treffer, Wochenend-Forwarding, vor-erstem-Tag, hinter-letztem-Tag,
    fehlender Ticker, delisted, leerer/invalider Ticker.
    """
    lookup = _build_price_lookup(multi_cache)
    probes = [
        ('AAPL', '2025-06-02'),   # exakter Handelstag
        ('AAPL', '2025-06-07'),   # Samstag -> Montag
        ('AAPL', '2025-05-01'),   # weit vor erstem Tag -> None (Luecke)
        ('AAPL', '2026-01-01'),   # hinter letztem Tag -> None
        ('MSFT', '2024-02-15'),
        ('TSLA', '2025-06-03'),   # single-row Ticker
        ('TSLA', '2025-06-04'),   # hinter dem einzigen Tag -> None
        ('DEAD', '2024-01-01'),   # nur Marker -> None
        ('NOPE', '2025-06-02'),   # nicht im Cache -> None
        ('', '2025-06-02'),       # leer -> None
        ('07/01/28', '2025-06-02'),  # Datums-String -> None
    ]
    for tkr, d in probes:
        ts = pd.Timestamp(d)
        old = _price_at_or_after(multi_cache, tkr, ts)
        new = _price_at_or_after_fast(lookup, tkr, ts)
        assert old == new, f"{tkr}@{d}: old={old} new={new}"


def test_lookup_before_cache_start_is_unknown(multi_cache):
    """Beginnt der Cache erst nach dem Ziel, ist der Preis unbekannt.

    Frueher kam der erste Cache-Kurs zurueck; Baseline und Horizont eines
    alten Trades trafen damit denselben Kurs und ergaben 0 % Rendite.
    """
    lookup = _build_price_lookup(multi_cache)
    first = multi_cache.loc['AAPL'].dropna().index.min()
    near = first - pd.Timedelta(days=settings.POST_TRADE_MAX_PRICE_GAP_DAYS)
    far = near - pd.Timedelta(days=1)
    assert _price_at_or_after_fast(lookup, 'AAPL', near) is not None
    assert _price_at_or_after_fast(lookup, 'AAPL', far) is None
    assert _price_at_or_after(multi_cache, 'AAPL', far) is None


def test_build_price_lookup_drops_non_positive_close():
    idx = pd.MultiIndex.from_tuples(
        [('BAD', pd.Timestamp('2025-06-02')), ('BAD', pd.Timestamp('2025-06-03'))],
        names=['ticker', 'date'])
    cache = pd.DataFrame({'close': [-5.0, 10.0]}, index=idx)
    lookup = _build_price_lookup(cache)
    assert list(lookup['BAD'][1]) == [10.0]


def test_build_price_lookup_drops_delisted_marker(multi_cache):
    """DEAD-Ticker (nur Marker-Row) -> None im Lookup, nicht (leeres array)."""
    lookup = _build_price_lookup(multi_cache)
    assert lookup['DEAD'] is None
    assert lookup['TSLA'] is not None and len(lookup['TSLA'][0]) == 1


def test_build_price_lookup_empty_cache():
    empty = _load_price_cache('/tmp/_does_not_exist_xyz.parquet')
    assert _build_price_lookup(empty) == {}


# ---------------------------------------------------------------------------
# 21. transaction_type-Kanonisierung: 'Sale (Full)' / 'Sale (Partial)' / Caps
#     werden als opposing Sell erkannt (Fix Root Cause #2a).
# ---------------------------------------------------------------------------

def test_exit_sale_full_recognized_as_opposing(sample_cache):
    """'Sale (Full)' muss als Verkauf gegen einen Purchase zaehlen -> closed_full.

    Vorher (exakter String-Match 'Sale'): not_applicable / held.
    """
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale (Full)', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'closed_full'
    assert cols['opposing_trade_ids'].iloc[0] == 'T2'
    # Der Sell selbst hat keinen opposing Buy danach -> held (nicht not_applicable)
    assert cols['exit_status'].iloc[1] == 'held'


def test_exit_sale_partial_label_recognized(sample_cache):
    """'Sale (Partial)' als Label zaehlt ebenfalls als Sell-Richtung."""
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale (Partial)', trade_amount_max_usd=20_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'closed_partial'
    assert cols['opposing_trade_ids'].iloc[0] == 'T2'


def test_exit_uppercase_types_recognized(sample_cache):
    """'PURCHASE' / 'SALE' (Caps) werden kanonisiert."""
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='PURCHASE', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='SALE', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    assert cols['exit_status'].iloc[0] == 'closed_full'


def test_exit_exchange_stays_not_applicable_regression(sample_cache):
    """REGRESSION: 'Exchange' bleibt not_applicable (Decision: nicht auf buy/sell mappen)."""
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Exchange', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, 120, 0.0)
    # Exchange ist kein opposing -> Purchase bleibt held; Exchange selbst not_applicable
    assert cols['exit_status'].iloc[0] == 'held'
    assert cols['exit_status'].iloc[1] == 'not_applicable'


# ---------------------------------------------------------------------------
# 22. held-effective_return: REALISIERT-ONLY -> NaN (kein Horizont-Snapshot).
#     (Fix: held-Fallback entfernt; effective_return nur fuer closed_*.)
# ---------------------------------------------------------------------------

def test_held_effective_return_is_nan(sample_cache, monkeypatch):
    """held-Trade -> effective_return_pct UND effective_return_horizon_days NaN.

    Frueher kopierte ein Fallback den laengsten verfuegbaren Horizont-Snapshot
    (z.B. 30d) in effective_return — ein willkuerlicher Kalendertag. Dieser
    Fallback ist entfernt: ohne realisierten Close gibt es keinen Eff.-Return.
    Mark-to-Market bleibt in post_return_{X}d erhalten.
    """
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_load_price_cache', lambda path: sample_cache)
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)
    monkeypatch.setattr(
        ptr, '_ensure_cache_for_tickers',
        lambda cache, tickers, **kw: cache,
    )
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase'),
    ])
    result = compute_post_trade_returns(
        df, horizons_days=[30, 60, 90, 120],
        cache_path='/tmp/_test_ptr_held_horizon.parquet',
    )
    # Mark-to-Market bleibt erhalten (30d im Cache).
    assert pd.notna(result['post_return_30d'].iloc[0])
    # held -> kein realisierter Eff.-Return.
    assert result['exit_status'].iloc[0] == 'held'
    assert pd.isna(result['effective_return_pct'].iloc[0])
    assert pd.isna(result['effective_return_horizon_days'].iloc[0])
    if os.path.isfile('/tmp/_test_ptr_held_horizon.parquet'):
        os.remove('/tmp/_test_ptr_held_horizon.parquet')


def test_closed_effective_horizon_is_days_to_exit(sample_cache, monkeypatch):
    """Bei closed_* = Tage zwischen Trade und Exit."""
    import scoring.post_trade_returns as ptr
    monkeypatch.setattr(ptr, '_load_price_cache', lambda path: sample_cache)
    monkeypatch.setattr(ptr, '_save_price_cache', lambda df, path: None)
    monkeypatch.setattr(
        ptr, '_ensure_cache_for_tickers',
        lambda cache, tickers, **kw: cache,
    )
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=60_000),
    ])
    result = compute_post_trade_returns(
        df, horizons_days=[30, 60],
        cache_path='/tmp/_test_ptr_closed_horizon.parquet',
    )
    assert result['exit_status'].iloc[0] == 'closed_full'
    # 2025-06-02 -> 2025-06-20 = 18 Tage
    assert result['effective_return_horizon_days'].iloc[0] == 18
    if os.path.isfile('/tmp/_test_ptr_closed_horizon.parquet'):
        os.remove('/tmp/_test_ptr_closed_horizon.parquet')


# ---------------------------------------------------------------------------
# 23. Exit-Fenster ENTKOPPELT vom Return-Horizont (Fix Root Cause #1).
#     exit_window_days=None erkennt Close > 120d; ein Cap haelt ihn 'held'.
# ---------------------------------------------------------------------------

def _long_cache() -> pd.DataFrame:
    """AAPL, monoton steigend, Werktage 2025-01-01 .. 2026-06-30."""
    records = []
    price = 100.0
    for d in pd.date_range('2025-01-01', '2026-06-30', freq='B'):
        records.append(('AAPL', d.strftime('%Y-%m-%d'), price))
        price += 0.1
    return _make_cache(records)


def test_unbounded_window_detects_late_close():
    """Verkauf ~200 Tage nach Kauf: mit unbegrenztem Fenster -> closed_full.

    Frueher (Fenster = max_horizon = 120) wurde dieser echte Round-Trip
    faelschlich 'held' genannt und der effective_return war ein 120d-Snapshot.
    """
    cache = _long_cache()
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-01-06',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-07-25',  # ~200d
                        transaction_type='Sale', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, cache, None, 0.0)  # None = unbegrenzt
    assert cols['exit_status'].iloc[0] == 'closed_full'
    assert cols['exit_date'].iloc[0] == pd.Timestamp('2025-07-25')
    assert cols['opposing_trade_ids'].iloc[0] == 'T2'


def test_window_cap_keeps_late_close_held():
    """Mit exit_window_days=150 bleibt ein Close bei ~200d 'held' (ausserhalb)."""
    cache = _long_cache()
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-01-06',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-07-25',  # ~200d
                        transaction_type='Sale', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, cache, 150, 0.0)  # 150d Cap < 200d
    assert cols['exit_status'].iloc[0] == 'held'
    assert pd.isna(cols['first_close_offset_days'].iloc[0])


# ---------------------------------------------------------------------------
# 24. first_close_offset_days: Tage bis erstem Gegen-Trade; NaN bei held.
# ---------------------------------------------------------------------------

def test_first_close_offset_days(sample_cache):
    """Offset = Tage bis zum ersten gegensaetzlichen Trade; held -> NaN."""
    df = pd.DataFrame([
        _make_trade_row(trade_id='T1', transaction_date='2025-06-02',
                        transaction_type='Purchase', trade_amount_max_usd=50_000),
        _make_trade_row(trade_id='T2', transaction_date='2025-06-20',
                        transaction_type='Sale', trade_amount_max_usd=60_000),
    ])
    df['_post_trade_applicable'] = df.apply(_is_applicable, axis=1)
    cols = _compute_exit_columns(df, sample_cache, None, 0.0)
    # T1: erster Gegen-Trade 18 Tage spaeter (2025-06-02 -> 2025-06-20).
    assert cols['first_close_offset_days'].iloc[0] == 18
    # T2 (held, kein Gegen-Trade danach) -> NaN.
    assert pd.isna(cols['first_close_offset_days'].iloc[1])


# ---------------------------------------------------------------------------
# Recent-Monitoring: Papier-Return (compute_paper_returns + Helfer)
# ---------------------------------------------------------------------------

def _no_network(*_a, **_k):
    raise AssertionError('yfinance darf in diesem Test nicht aufgerufen werden')


def _fresh_cache(ticker: str = 'AAPL', last_close: float = 104.0) -> pd.DataFrame:
    """Cache mit 5 Handelstagen endend HEUTE (juengster Close = last_close)."""
    today = pd.Timestamp.now().normalize()
    days = pd.bdate_range(end=today, periods=5)
    base = last_close - (len(days) - 1)
    records = [
        (ticker, d.strftime('%Y-%m-%d'), float(base + i))
        for i, d in enumerate(days)
    ]
    return _make_cache(records)


def test_sell_sign_series_direction():
    """Verkauf -> -1.0, Kauf/Exchange -> +1.0."""
    df = pd.DataFrame({'transaction_type': ['Purchase', 'Sale', 'Exchange']})
    ss = _sell_sign_series(df)
    assert ss.iloc[0] == 1.0
    assert ss.iloc[1] == -1.0
    assert ss.iloc[2] == 1.0


def test_latest_close_map_basic():
    """Letzter Close je Ticker; Delisted-Marker-only -> nicht im Dict."""
    today = pd.Timestamp.now().normalize()
    cache = _make_cache([
        ('AAPL', '2025-06-02', 100.0),
        ('AAPL', today.strftime('%Y-%m-%d'), 130.0),
        ('DEAD', DELISTED_MARKER_DATE.strftime('%Y-%m-%d'), float('nan')),
    ])
    out = _latest_close_map(cache)
    assert out['AAPL'][0] == 130.0
    assert out['AAPL'][1] == today
    assert 'DEAD' not in out


def test_apply_paper_returns_purchase_and_sale():
    """Kauf: positiver Return bei Kursanstieg; Verkauf invertiert das Vorzeichen."""
    df = pd.DataFrame({
        'ticker': ['AAPL', 'AAPL'],
        'transaction_type': ['Purchase', 'Sale'],
        'post_trade_baseline_price': [100.0, 100.0],
    })
    latest = {'AAPL': (110.0, pd.Timestamp('2025-06-10'))}
    sell_sign = _sell_sign_series(df)
    target = pd.Series([True, True])
    out = _apply_paper_returns(df.copy(), latest, target, sell_sign)
    # Kauf: (110/100-1)*100 = +10
    assert out['paper_return_pct'].iloc[0] == pytest.approx(10.0)
    # Verkauf: invertiert -> -10
    assert out['paper_return_pct'].iloc[1] == pytest.approx(-10.0)
    assert (out['price_as_of'] == pd.Timestamp('2025-06-10')).all()


def test_apply_paper_returns_nan_without_price_or_baseline():
    """Kein Preis im Map oder ungueltiger Baseline -> NaN / NaT."""
    df = pd.DataFrame({
        'ticker': ['NOPRICE', 'AAPL'],
        'transaction_type': ['Purchase', 'Purchase'],
        'post_trade_baseline_price': [100.0, 0.0],  # 2. Baseline=0 -> ungueltig
    })
    latest = {'AAPL': (110.0, pd.Timestamp('2025-06-10'))}
    out = _apply_paper_returns(df.copy(), latest, pd.Series([True, True]),
                               _sell_sign_series(df))
    assert pd.isna(out['paper_return_pct'].iloc[0])  # Ticker fehlt im Map
    assert pd.isna(out['paper_return_pct'].iloc[1])  # Baseline 0


def test_apply_paper_returns_only_target_rows():
    """Nur Zeilen mit target_mask=True bekommen Werte."""
    df = pd.DataFrame({
        'ticker': ['AAPL', 'AAPL'],
        'transaction_type': ['Purchase', 'Purchase'],
        'post_trade_baseline_price': [100.0, 100.0],
    })
    latest = {'AAPL': (110.0, pd.Timestamp('2025-06-10'))}
    out = _apply_paper_returns(df.copy(), latest, pd.Series([True, False]),
                               _sell_sign_series(df))
    assert out['paper_return_pct'].iloc[0] == pytest.approx(10.0)
    assert pd.isna(out['paper_return_pct'].iloc[1])
    assert pd.isna(out['price_as_of'].iloc[1])


def test_ensure_cache_cutoff_date_fresh_no_fetch(monkeypatch):
    """cutoff_date in Vergangenheit + frischer Cache -> kein yfinance-Call."""
    monkeypatch.setattr('scoring.post_trade_returns.yf.download', _no_network)
    cache = _fresh_cache('AAPL')
    out = _ensure_cache_for_tickers(
        cache, ['AAPL'], cutoff_date=pd.Timestamp.now().normalize(),
    )
    # Unveraendert zurueck (warm), ohne Netzwerk (sonst AssertionError).
    assert 'AAPL' in set(out.index.get_level_values(0))


def test_compute_paper_returns_offline(monkeypatch, tmp_path):
    """End-to-End offline: frischer Cache -> Papier-Return fuer Recent-Trade,
    alter Trade ausserhalb Fenster -> NaN."""
    monkeypatch.setattr('scoring.post_trade_returns.yf.download', _no_network)
    cache_path = tmp_path / 'price_history.parquet'
    fresh = _fresh_cache('AAPL', last_close=104.0)
    _save_price_cache(fresh, str(cache_path))
    # Baseline wird LIVE aus dem Cache abgeleitet (erster Close >=
    # transaction_date); transaction_date = erster Cache-Tag -> erster
    # Cache-Eintrag (aelteste Zeile, nach Datum sortiert).
    first_close = float(fresh.sort_index()['close'].iloc[0])

    today = pd.Timestamp.now().normalize()
    df = pd.DataFrame([
        _make_trade_row(trade_id='RECENT', ticker='AAPL',
                        transaction_date=pd.bdate_range(end=today, periods=5)[0],
                        transaction_type='Purchase'),
        _make_trade_row(trade_id='OLD', ticker='AAPL',
                        transaction_date=(today - pd.Timedelta(days=400)),
                        transaction_type='Purchase'),
    ])
    df['post_trade_baseline_price'] = [100.0, 100.0]

    out = compute_paper_returns(
        df, cache_path=str(cache_path),
        recent_window_days=180, max_price_age_bdays=1,
    )
    # Recent: (104/first_close-1)*100 ; price_as_of = juengster Handelstag im
    # Cache (am Wochenende = letzter Freitag, NICHT der Kalender-Heute).
    last_trading_day = pd.bdate_range(end=today, periods=5)[-1]
    expected = (104.0 / first_close - 1.0) * 100.0
    assert out.loc[out['trade_id'] == 'RECENT', 'paper_return_pct'].iloc[0] \
        == pytest.approx(expected)
    assert out.loc[out['trade_id'] == 'RECENT', 'price_as_of'].iloc[0] \
        == last_trading_day
    # Alt ausserhalb 180-Tage-Fenster -> NaN.
    assert pd.isna(out.loc[out['trade_id'] == 'OLD', 'paper_return_pct'].iloc[0])


def test_compute_paper_returns_split_immune(monkeypatch, tmp_path):
    """Stale (pre-split) post_trade_baseline_price wird ignoriert -> Baseline
    LIVE aus dem aktuellen Cache (split-immun).

    Simuliert KLAC-artigen 10:1-Split: post_trade_baseline_price = 1000.0
    (10x zu hoch, aus einem frueheren Pre-Split-Lauf). Der aktuelle Cache ist
    bereits post-split (Closes 100..104). Mit der alten Logik wuerde
    paper_return_pct = (104/1000-1)*100 = -89.6 berechnet -> krass falsch.
    Mit Live-Baseline (erster Cache-Close >= transaction_date = 100) ergibt
    sich (104/100-1)*100 = +4.0 -> korrekt, weil Zaehler+Nenner aus demselben
    (post-split) Cache-Stand stammen.
    """
    monkeypatch.setattr('scoring.post_trade_returns.yf.download', _no_network)
    cache_path = tmp_path / 'price_history.parquet'
    fresh = _fresh_cache('AAPL', last_close=104.0)
    _save_price_cache(fresh, str(cache_path))
    # transaction_date = erster Cache-Tag -> Live-Baseline = erster
    # Close im Cache (aelteste Zeile, nach Datum sortiert).
    first_close = float(fresh.sort_index()['close'].iloc[0])

    today = pd.Timestamp.now().normalize()
    df = pd.DataFrame([
        _make_trade_row(trade_id='RECENT', ticker='AAPL',
                        transaction_date=pd.bdate_range(end=today, periods=5)[0],
                        transaction_type='Purchase'),
    ])
    df['post_trade_baseline_price'] = [1000.0]

    out = compute_paper_returns(
        df, cache_path=str(cache_path),
        recent_window_days=180, max_price_age_bdays=1,
    )

    expected = (104.0 / first_close - 1.0) * 100.0
    assert out.loc[out['trade_id'] == 'RECENT', 'paper_return_pct'].iloc[0] \
        == pytest.approx(expected)
    # NICHT der falsche Wert, den die alte Master-Baseline-Logik liefern wuerde.
    wrong_value = (104.0 / 1000.0 - 1.0) * 100.0
    assert out.loc[out['trade_id'] == 'RECENT', 'paper_return_pct'].iloc[0] \
        != pytest.approx(wrong_value)
