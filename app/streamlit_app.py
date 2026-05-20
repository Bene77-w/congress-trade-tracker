"""Streamlit-Dashboard fuer den Congress Trade Tracker (Modul 9 MVP).

Start aus dem sandbox-Root:
    venv/bin/streamlit run app/streamlit_app.py

Drei Tabs (Trades / Assets / Members), alle reaktiv auf die Sidebar-Filter.
Asset- und Member-Sicht werden on-the-fly aus dem gefilterten Trade-Subset
aggregiert; die statischen ``suspicious_*.csv`` werden nicht mehr direkt
angezeigt, damit Filter konsistent durchschlagen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.score_recompute import available_signals, recompute_subset  # noqa: E402


TRADES_CSV = ROOT / 'data' / 'processed' / 'trades_master.csv'

TRADE_DISPLAY_COLS = [
    'transaction_date', 'disclosure_date', 'name', 'chamber', 'Member_Party',
    'ticker', 'issuer_ticker', 'transaction_type', 'amount',
    'trade_volume_usd', 'asset_tradingvolume_usd',
    'trade_score', 'relevant_sectors', 'signals_triggered',
    'trade_score_breakdown', 'trade_warnings',
]


def _parse_relevant_sectors(value: object) -> str:
    """JSON-Liste -> ', '-getrennte Liste eindeutiger Sektoren."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    if not isinstance(value, str) or not value.strip():
        return ''
    try:
        items = json.loads(value)
    except (ValueError, TypeError):
        return ''
    if not isinstance(items, list):
        return ''
    seen: set[str] = set()
    sectors: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        s = it.get('sector')
        if isinstance(s, str) and s and s not in seen:
            seen.add(s)
            sectors.append(s)
    return ', '.join(sectors)


# ---------------------------------------------------------------------------
# Loader (cached)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner='Lade trades_master.csv …')
def load_trades() -> pd.DataFrame:
    df = pd.read_csv(
        TRADES_CSV,
        parse_dates=['transaction_date', 'disclosure_date'],
        dtype={'ticker': 'string', 'issuer_ticker': 'string',
               'name': 'string', 'chamber': 'string',
               'Member_Party': 'string',
               'transaction_type': 'string', 'amount': 'string',
               'signals_triggered': 'string',
               'trade_score_breakdown': 'string',
               'trade_warnings': 'string',
               'Relevant_Sectors': 'string'},
        low_memory=False,
    )
    if 'Relevant_Sectors' in df.columns:
        df['relevant_sectors'] = df['Relevant_Sectors'].map(
            _parse_relevant_sectors
        ).astype('string')
    else:
        df['relevant_sectors'] = pd.Series([''] * len(df), dtype='string')
    return df


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _unique_join(series: pd.Series, sep: str = ',') -> str:
    vals = [v for v in series.dropna().astype(str).unique() if v]
    return sep.join(sorted(vals))


def _unique_signals(series: pd.Series) -> str:
    seen: set[str] = set()
    for cell in series.dropna().astype(str):
        for tok in cell.split(';'):
            if tok:
                seen.add(tok)
    return ';'.join(sorted(seen))


def aggregate_assets(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            'issuer_ticker', 'asset_name', 'trade_count', 'member_count',
            'score_max', 'score_avg', 'score_total', 'total_volume_usd',
            'last_date', 'first_date', 'chambers', 'sectors', 'signals',
        ])
    base = df.copy()
    if date_col not in base.columns:
        base[date_col] = pd.NaT
    grp = base.groupby('issuer_ticker', dropna=False)
    out = grp.agg(
        asset_name=('asset_name_figi', lambda s: s.dropna().iloc[0]
                    if s.dropna().size else ''),
        trade_count=('trade_id', 'count'),
        member_count=('name', 'nunique'),
        score_max=('trade_score', 'max'),
        score_avg=('trade_score', 'mean'),
        score_total=('trade_score', 'sum'),
        total_volume_usd=('trade_volume_usd', 'sum'),
        last_date=(date_col, 'max'),
        first_date=(date_col, 'min'),
        chambers=('chamber', _unique_join),
        sectors=('relevant_sectors', _unique_join),
        signals=('signals_triggered', _unique_signals),
    ).reset_index()
    out['score_avg'] = out['score_avg'].round(1)
    return out


def aggregate_members(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            'name', 'chamber', 'Member_Party', 'trade_count', 'asset_count',
            'score_max', 'score_avg', 'score_total', 'total_volume_usd',
            'last_date', 'first_date', 'signals',
        ])
    base = df.copy()
    if date_col not in base.columns:
        base[date_col] = pd.NaT
    grp = base.groupby(['name', 'chamber'], dropna=False)
    out = grp.agg(
        Member_Party=('Member_Party', lambda s: s.dropna().iloc[0]
                      if s.dropna().size else ''),
        trade_count=('trade_id', 'count'),
        asset_count=('issuer_ticker', 'nunique'),
        score_max=('trade_score', 'max'),
        score_avg=('trade_score', 'mean'),
        score_total=('trade_score', 'sum'),
        total_volume_usd=('trade_volume_usd', 'sum'),
        last_date=(date_col, 'max'),
        first_date=(date_col, 'min'),
        signals=('signals_triggered', _unique_signals),
    ).reset_index()
    out['score_avg'] = out['score_avg'].round(1)
    return out


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title='Congress Trade Tracker',
    page_icon='🏛️',
    layout='wide',
)

st.title('🏛️ Congress Trade Tracker')
st.caption('Modul 9 MVP — Interaktive Trade-Ansicht mit Live-Score-Recompute')

trades = load_trades()
max_observed_score = int(trades['trade_score'].max()) if 'trade_score' in trades.columns else 200

# --- Sidebar -------------------------------------------------------------
with st.sidebar:
    st.header('Filter')

    st.subheader('Datum')
    date_basis = st.radio(
        'Datumsbasis',
        options=['disclosure_date', 'transaction_date'],
        index=0,
        horizontal=False,
    )
    window_label = st.radio(
        'Zeitfenster',
        options=['7 Tage', '30 Tage', '365 Tage', 'Alles'],
        index=1,
        horizontal=True,
    )
    window_days = {'7 Tage': 7, '30 Tage': 30,
                   '365 Tage': 365, 'Alles': None}[window_label]

    st.subheader('Sortierung')
    sort_dir = st.radio(
        'trade_score',
        options=['absteigend', 'aufsteigend'],
        index=0,
        horizontal=True,
    )

    st.subheader('Signale ausschließen')
    excluded_signals = st.multiselect(
        'Diese Signale tragen 0 zum Score bei:',
        options=available_signals(),
        default=[],
    )

    st.subheader('Suche')
    member_query = st.text_input('Member (Substring)', value='').strip()
    ticker_query = st.text_input('Ticker (Substring)', value='').strip().upper()

    st.subheader('Score')
    min_score = st.slider(
        'Min-Score',
        min_value=0,
        max_value=max(50, max_observed_score),
        value=0,
        step=1,
    )


# --- Pre-Filter (Datum + Member + Ticker) --------------------------------

def apply_prefilter(df: pd.DataFrame) -> pd.DataFrame:
    out = df

    if window_days is not None and date_basis in out.columns:
        date_col = pd.to_datetime(out[date_basis], errors='coerce')
        max_date = date_col.max()
        if pd.notna(max_date):
            cutoff = max_date - pd.Timedelta(days=window_days)
            out = out[date_col >= cutoff]

    if member_query:
        mask = out['name'].astype('string').str.contains(
            member_query, case=False, na=False,
        )
        out = out[mask]

    if ticker_query:
        tkr = out['ticker'].astype('string').str.upper().fillna('')
        iss = out['issuer_ticker'].astype('string').str.upper().fillna('')
        out = out[tkr.str.contains(ticker_query, na=False)
                  | iss.str.contains(ticker_query, na=False)]

    return out


prefilt = apply_prefilter(trades)

# --- Score-Recompute (vor Min-Score, gilt fuer alle drei Tabs) -----------
if excluded_signals:
    with st.spinner(f'Score neu berechnen für {len(prefilt):,} Trades …'):
        prefilt = recompute_subset(prefilt, excluded_signals)

# --- Trade-Tab-Subset (mit Min-Score + Sortierung) -----------------------
trade_rows = prefilt
if min_score > 0:
    trade_rows = trade_rows[trade_rows['trade_score'] >= min_score]
trade_rows = trade_rows.sort_values(
    'trade_score', ascending=(sort_dir == 'aufsteigend'), kind='mergesort',
)


# --- Tabs ----------------------------------------------------------------
tab_trades, tab_assets, tab_members = st.tabs(['Trades', 'Assets', 'Members'])

with tab_trades:
    n = len(trade_rows)
    mean_score = float(trade_rows['trade_score'].mean()) if n else 0.0
    max_score = int(trade_rows['trade_score'].max()) if n else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Trades (gefiltert)', f'{n:,}')
    c2.metric('Mean Score', f'{mean_score:.1f}')
    c3.metric('Max Score', f'{max_score}')
    c4.metric('Signale ausgeschlossen', len(excluded_signals))

    display_cols = [c for c in TRADE_DISPLAY_COLS if c in trade_rows.columns]
    st.dataframe(
        trade_rows[display_cols],
        use_container_width=True,
        hide_index=True,
        height=600,
    )

with tab_assets:
    st.caption('Aggregation aus dem gefilterten Trade-Subset (reaktiv auf'
               ' Datum, Suche, Signal-Ausschluss). Min-Score wirkt auf'
               ' `score_max` des Assets.')
    assets_df = aggregate_assets(prefilt, date_basis)
    if min_score > 0 and not assets_df.empty:
        assets_df = assets_df[assets_df['score_max'] >= min_score]
    assets_df = assets_df.sort_values(
        'score_max',
        ascending=(sort_dir == 'aufsteigend'),
        kind='mergesort',
    )

    a1, a2, a3 = st.columns(3)
    a1.metric('Assets', f'{len(assets_df):,}')
    a2.metric('Max score_max',
              f'{int(assets_df["score_max"].max()) if len(assets_df) else 0}')
    a3.metric('Σ Volumen (USD)',
              f'{int(assets_df["total_volume_usd"].sum()) if len(assets_df) else 0:,}')

    st.dataframe(
        assets_df,
        use_container_width=True,
        hide_index=True,
        height=600,
    )

with tab_members:
    st.caption('Aggregation aus dem gefilterten Trade-Subset (reaktiv auf'
               ' Datum, Suche, Signal-Ausschluss). Min-Score wirkt auf'
               ' `score_max` des Members.')
    members_df = aggregate_members(prefilt, date_basis)
    if min_score > 0 and not members_df.empty:
        members_df = members_df[members_df['score_max'] >= min_score]
    members_df = members_df.sort_values(
        'score_max',
        ascending=(sort_dir == 'aufsteigend'),
        kind='mergesort',
    )

    m1, m2, m3 = st.columns(3)
    m1.metric('Members', f'{len(members_df):,}')
    m2.metric('Max score_max',
              f'{int(members_df["score_max"].max()) if len(members_df) else 0}')
    m3.metric('Σ Volumen (USD)',
              f'{int(members_df["total_volume_usd"].sum()) if len(members_df) else 0:,}')

    st.dataframe(
        members_df,
        use_container_width=True,
        hide_index=True,
        height=600,
    )
