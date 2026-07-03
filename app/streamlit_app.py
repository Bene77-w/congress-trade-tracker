"""Streamlit-Dashboard fuer den Congress Trade Tracker (Modul 9 MVP).

Start aus dem Projekt-Root:
    venv/bin/streamlit run app/streamlit_app.py

Drei Tabs (Trades / Assets / Members), alle reaktiv auf die Sidebar-Filter.
Asset- und Member-Sicht werden on-the-fly aus dem gefilterten Trade-Subset
aggregiert; die statischen ``suspicious_*.csv`` werden nicht mehr direkt
angezeigt, damit Filter konsistent durchschlagen.
"""
from __future__ import annotations

import json
import math
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chart_helpers import (  # noqa: E402
    build_partial_close_rows,
    build_recent_pool,
    build_sale_kind_traces,
)
from app.glossary import help_for  # noqa: E402
from app import member_metrics as mm  # noqa: E402
from app import score_correlation as score_corr  # noqa: E402
from app.score_recompute import available_signals, recompute_subset  # noqa: E402
from app.scoring_presets import (  # noqa: E402
    DEFAULT_PRESET_NAME,
    consume_corrupt_warning,
    default_payload,
    delete_preset,
    load_presets,
    save_preset,
)
from config import settings  # noqa: E402
from scoring.trade_score import split_primary_hint  # noqa: E402
from signals.committee_signal import (  # noqa: E402
    _direction_from_transaction_type,
)


TRADES_CSV = ROOT / 'data' / 'processed' / settings.TRADES_MASTER_COMPLETE_CSV

TRADE_DISPLAY_COLS = [
    # Default-Spalten in Anzeige-Reihenfolge (links -> rechts); die Display-
    # Reihenfolge des Trades-Tabs folgt dieser Liste (s. display_cols-Aufbau).
    'effective_return_pct', 'trade_score', 'ticker',
    'transaction_type', 'transaction_date', 'disclosure_date',
    'amount', 'exit_status', 'trade_id', 'name',
    'asset_name', 'signals_triggered', 'committee_match',
    'asset_tradingvolume_usd', 'relevant_sectors',
    # Optionale Zusatzspalten (per Multiselect zuschaltbar) -> rechts angehaengt.
    'chamber', 'Member_Party', 'issuer_ticker',
    'trade_volume_usd', 'trade_amount_max_usd',
    'trade_score_breakdown', 'trade_warnings',
    'post_return_30d', 'post_return_60d', 'post_return_90d',
    'post_return_120d', 'post_return_180d',
    'exit_date', 'first_close_offset_days',
    'paper_return_pct', 'price_as_of',
]

# Spalten fuer den Recent-Trades-Monitoring-Tab (Reihenfolge = Anzeige).
RECENT_DISPLAY_COLS = [
    'effective_return_pct', 'paper_return_pct', 'trade_score', 'ticker',
    'transaction_type', 'asset_name', 'signals_triggered', 'name',
    'relevant_sectors', 'committee_match', 'transaction_date',
    'disclosure_date', 'days_since_trade', 'chamber', 'Member_Party',
    'issuer_ticker', 'trade_id',
    'amount', 'trade_amount_max_usd', 'price_as_of', 'exit_status',
]

TRADE_DEFAULT_COLS = [
    'effective_return_pct', 'trade_score', 'ticker', 'asset_name', 'name',
    'signals_triggered', 'committee_match', 'trade_id',
    'transaction_date', 'disclosure_date', 'transaction_type', 'amount',
    'asset_tradingvolume_usd', 'relevant_sectors', 'exit_status',
]

ASSET_DISPLAY_COLS = [
    # Anzeige-Reihenfolge (links -> rechts); die Display-Reihenfolge des
    # Assets-Tabs folgt dieser Liste (s. asset_display_cols-Aufbau).
    'trade_count', 'asset_name', 'member_count',
    'score_max', 'score_avg', 'score_total',
    'avg_return_primary', 'max_return_primary', 'pct_profitable',
    'signals', 'total_volume_usd', 'sectors',
    'last_date', 'first_date', 'chambers', 'issuer_ticker',
]
ASSET_DEFAULT_COLS = list(ASSET_DISPLAY_COLS)

MEMBER_DISPLAY_COLS = [
    'name', 'chamber', 'Member_Party', 'trade_count', 'asset_count',
    'score_max', 'score_avg', 'score_total', 'total_volume_usd',
    'avg_return_primary', 'pct_profitable',
    'last_date', 'first_date', 'signals',
]
MEMBER_DEFAULT_COLS = list(MEMBER_DISPLAY_COLS)


def _parse_relevant_sectors(value: object) -> str:
    """JSON-Liste -> nach Sektor gruppierte Sektor/Industry-Strings.

    Format: "Sector1: Industry A, Industry B; Sector2: Industry C"
    Falls kein industry-Feld vorhanden, wird nur der Sector ausgegeben.
    """
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
    # sector -> geordnete Liste eindeutiger Industries
    sector_industries: dict[str, list[str]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        s = it.get('sector')
        if not isinstance(s, str) or not s:
            continue
        ind = it.get('industry')
        if s not in sector_industries:
            sector_industries[s] = []
        if isinstance(ind, str) and ind and ind not in sector_industries[s]:
            sector_industries[s].append(ind)
    parts: list[str] = []
    for sector, industries in sector_industries.items():
        if industries:
            parts.append(f'{sector}: {", ".join(industries)}')
        else:
            parts.append(sector)
    return '; '.join(parts)


def _parse_committee_matches(value: object) -> str:
    """JSON-Liste -> '; '-getrennte Match-Tupel.

    Jedes Tupel hat das Format ``Committee[/Subcommittee] → Sector[/Industry]``.
    Genau das, was das ``committee``-Signal triggert: pro Eintrag in
    ``Relevant_Sectors`` der konkrete Ausschuss + die abgedeckte Sektor-/
    Industry-Kombination. Duplikate werden entfernt, Reihenfolge bleibt stabil.
    """
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
    parts: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        comm = (it.get('committee') or '').strip()
        sub = (it.get('subcommittee') or '').strip()
        sec = (it.get('sector') or '').strip()
        ind = (it.get('industry') or '').strip()
        left = comm + (f'/{sub}' if sub else '')
        right = sec + (f'/{ind}' if ind else '')
        if not left and not right:
            continue
        token = f'{left} → {right}'
        if token in seen:
            continue
        seen.add(token)
        parts.append(token)
    return '; '.join(parts)


# ---------------------------------------------------------------------------
# Loader (cached)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner='Lade trades_master_complete.csv …')
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
        df['committee_match'] = df['Relevant_Sectors'].map(
            _parse_committee_matches
        ).astype('string')
    else:
        df['relevant_sectors'] = pd.Series([''] * len(df), dtype='string')
        df['committee_match'] = pd.Series([''] * len(df), dtype='string')
    # price_as_of optional (erst seit Recent-Monitoring im Master) -> defensiv.
    if 'price_as_of' in df.columns:
        df['price_as_of'] = pd.to_datetime(df['price_as_of'], errors='coerce')
    return df


@st.cache_data(show_spinner=False)
def _ar_sorted_dist(headline_h: int) -> np.ndarray:
    """Aufsteigend sortierte, NaN-freie Verteilung von ``signed_ar_{headline}d``
    ueber ALLE Trades (globale Grundgesamtheit fuer den Perzentil-Rang im
    Trade-Detail). Einmal pro Session gecacht; leeres Array, falls die Stage
    ``--stage_abnormal_returns`` noch nicht gelaufen ist."""
    col = f'signed_ar_{int(headline_h)}d'
    if col not in trades.columns:
        return np.array([], dtype='float64')
    s = pd.to_numeric(trades[col], errors='coerce').dropna().to_numpy('float64')
    s.sort()
    return s


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _unique_join(series: pd.Series, sep: str = ',') -> str:
    vals = [v for v in series.dropna().astype(str).unique() if v]
    return sep.join(sorted(vals))


def _unique_signals(series: pd.Series) -> str:
    """Aggregiert nur die PRIMAERSIGNALE (gescort) ueber alle Zellen.

    Hint-Signale (Default 0 Score-Einfluss) werden in Tabellen ausgeblendet und
    nur pro Trade im Detail-Popup ausgewiesen. Faellt ``SCORING_PRIMARY_SIGNALS``
    weg/leer, werden alle Signale gezeigt (Rueckwaerts-Kompatibilitaet).
    """
    primary_set = set(getattr(settings, 'SCORING_PRIMARY_SIGNALS', ()) or ())
    seen: set[str] = set()
    for cell in series.dropna().astype(str):
        for tok in cell.split(';'):
            if tok and (not primary_set or tok in primary_set):
                seen.add(tok)
    return ';'.join(sorted(seen))


def _primary_only(value) -> str:
    """``signals_triggered``-Zelle -> nur Primaersignale (``;``-getrennter String).

    Tabellen zeigen ausschliesslich Primaersignale; die Hints erscheinen nur im
    Trade-Detail-Popup (s. ``split_primary_hint``).
    """
    primary, _hint = split_primary_hint(value, settings.SCORING_PRIMARY_SIGNALS)
    return ';'.join(primary)


def aggregate_assets(df: pd.DataFrame, date_col: str,
                      primary_horizon: int = 90) -> pd.DataFrame:
    ret_col = f'post_return_{primary_horizon}d'
    has_returns = ret_col in df.columns

    if df.empty:
        cols = [
            'issuer_ticker', 'asset_name', 'trade_count', 'member_count',
            'score_max', 'score_avg', 'score_total', 'total_volume_usd',
            'last_date', 'first_date', 'chambers', 'sectors', 'signals',
        ]
        if has_returns:
            cols += ['avg_return_primary', 'max_return_primary',
                     'pct_profitable']
        return pd.DataFrame(columns=cols)
    base = df.copy()
    if date_col not in base.columns:
        base[date_col] = pd.NaT
    grp = base.groupby('issuer_ticker', dropna=False)
    agg_spec: dict = {
        'asset_name': ('asset_name_figi', lambda s: s.dropna().iloc[0]
                       if s.dropna().size else ''),
        'trade_count': ('trade_id', 'count'),
        'member_count': ('name', 'nunique'),
        'score_max': ('trade_score', 'max'),
        'score_avg': ('trade_score', 'mean'),
        'score_total': ('trade_score', 'sum'),
        'total_volume_usd': ('trade_volume_usd', 'sum'),
        'last_date': (date_col, 'max'),
        'first_date': (date_col, 'min'),
        'chambers': ('chamber', _unique_join),
        'sectors': ('relevant_sectors', _unique_join),
        'signals': ('signals_triggered', _unique_signals),
    }
    if has_returns:
        agg_spec['avg_return_primary'] = (ret_col, 'mean')
        agg_spec['max_return_primary'] = (ret_col, 'max')
        agg_spec['pct_profitable'] = (ret_col,
            lambda s: (s.dropna() > 0).mean() * 100.0)
    out = grp.agg(**agg_spec).reset_index()
    out['score_avg'] = out['score_avg'].round(1)
    if has_returns:
        out['avg_return_primary'] = out['avg_return_primary'].round(1)
        out['max_return_primary'] = out['max_return_primary'].round(1)
        out['pct_profitable'] = out['pct_profitable'].round(1)
    return out


def aggregate_members(df: pd.DataFrame, date_col: str,
                       primary_horizon: int = 90) -> pd.DataFrame:
    ret_col = f'post_return_{primary_horizon}d'
    has_returns = ret_col in df.columns

    if df.empty:
        cols = [
            'name', 'chamber', 'Member_Party', 'trade_count', 'asset_count',
            'score_max', 'score_avg', 'score_total', 'total_volume_usd',
            'last_date', 'first_date', 'signals',
        ]
        if has_returns:
            cols += ['avg_return_primary', 'pct_profitable']
        return pd.DataFrame(columns=cols)
    base = df.copy()
    if date_col not in base.columns:
        base[date_col] = pd.NaT
    grp = base.groupby(['name', 'chamber'], dropna=False)
    agg_spec: dict = {
        'Member_Party': ('Member_Party', lambda s: s.dropna().iloc[0]
                         if s.dropna().size else ''),
        'trade_count': ('trade_id', 'count'),
        'asset_count': ('issuer_ticker', 'nunique'),
        'score_max': ('trade_score', 'max'),
        'score_avg': ('trade_score', 'mean'),
        'score_total': ('trade_score', 'sum'),
        'total_volume_usd': ('trade_volume_usd', 'sum'),
        'last_date': (date_col, 'max'),
        'first_date': (date_col, 'min'),
        'signals': ('signals_triggered', _unique_signals),
    }
    if has_returns:
        agg_spec['avg_return_primary'] = (ret_col, 'mean')
        agg_spec['pct_profitable'] = (ret_col,
            lambda s: (s.dropna() > 0).mean() * 100.0)
    out = grp.agg(**agg_spec).reset_index()
    out['score_avg'] = out['score_avg'].round(1)
    if has_returns:
        out['avg_return_primary'] = out['avg_return_primary'].round(1)
        out['pct_profitable'] = out['pct_profitable'].round(1)
    return out


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title='Congress Trade Tracker',
    layout='wide',
    initial_sidebar_state='expanded',
)

st.markdown(
    """
    <style>
    /* ---- Base palette (Finance Dark) ---- */
    .stApp {
        background: #0b0f17;
        color: #e5e7eb;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "Helvetica Neue", Arial, sans-serif;
    }
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 100% !important;
    }

    /* ---- Title + subtitle ---- */
    h1 {
        color: #f3f4f6 !important;
        font-weight: 700 !important;
        letter-spacing: -0.5px;
        margin-bottom: 0.25rem !important;
    }
    .subtitle {
        color: #9ca3af;
        font-size: 14px;
        margin-bottom: 1.5rem;
        letter-spacing: 0.2px;
    }

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] {
        background: #111827;
        border-right: 1px solid #1f2937;
        min-width: 340px !important;
    }
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: #f3f4f6 !important;
        font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] .stExpander {
        background: #0f1622;
        border: 1px solid #1f2937;
        border-radius: 8px;
        margin-bottom: 8px;
    }
    section[data-testid="stSidebar"] .stExpander summary {
        font-weight: 600;
        color: #e5e7eb;
    }

    /* ---- Metric cards ---- */
    [data-testid="stMetric"] {
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 10px;
        padding: 14px 16px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.25);
    }
    [data-testid="stMetricLabel"] {
        color: #9ca3af !important;
        font-size: 12px !important;
        text-transform: uppercase;
        letter-spacing: 0.6px;
    }
    [data-testid="stMetricValue"] {
        color: #f9fafb !important;
        font-size: 26px !important;
        font-weight: 700 !important;
    }

    /* ---- Tabs ---- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        border-bottom: 1px solid #1f2937;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        color: #9ca3af;
        font-weight: 600;
        font-size: 15px;
        padding: 10px 18px;
        border-radius: 8px 8px 0 0;
    }
    .stTabs [aria-selected="true"] {
        color: #f3f4f6 !important;
        background: #0f1622 !important;
        border-bottom: 2px solid #4b5563 !important;
    }

    /* ---- Buttons ---- */
    .stButton > button, .stFormSubmitButton > button {
        background: #1e293b;
        color: #e5e7eb;
        border: 1px solid #334155;
        border-radius: 6px;
        font-weight: 500;
        transition: all 0.15s ease;
    }
    .stButton > button:hover, .stFormSubmitButton > button:hover {
        background: #22d3ee;
        color: #0b0f17;
        border-color: #22d3ee;
    }

    /* ---- DataFrame ---- */
    [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid #1f2937;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    }
    [data-testid="stDataFrame"] [role="gridcell"] {
        padding: 8px 12px !important;
        font-size: 16px;
    }
    [data-testid="stDataFrame"] [role="columnheader"] {
        background: #1f2937 !important;
        color: #f3f4f6 !important;
        font-weight: 600 !important;
        font-size: 15px !important;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        padding: 10px 12px !important;
    }

    /* ---- Inputs ---- */
    .stTextInput input, .stNumberInput input, .stSelectbox > div {
        background: #0f1622 !important;
        color: #e5e7eb !important;
        border-color: #64748b !important;
    }
    /* Sichtbarer Rahmen sitzt auf dem BaseWeb-Wrapper, nicht auf <input> */
    .stTextInput div[data-baseweb="input"],
    .stTextInput div[data-baseweb="base-input"],
    .stNumberInput div[data-baseweb="input"],
    .stSelectbox div[data-baseweb="select"] > div {
        border: 1px solid #64748b !important;
        border-radius: 8px !important;
        background: #0f1622 !important;
    }
    .stTextInput div[data-baseweb="input"]:focus-within,
    .stNumberInput div[data-baseweb="input"]:focus-within,
    .stSelectbox div[data-baseweb="select"] > div:focus-within {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 0 1px #3b82f6 !important;
    }

    /* ---- Caption ---- */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #9ca3af !important;
        font-style: normal;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title('Congress Trade Tracker')
st.markdown(
    '<div class="subtitle">US-Kongress-Trades · Live-Scoring · '
    'Signal-Filter · Asset- &amp; Member-Aggregation</div>',
    unsafe_allow_html=True,
)

trades = load_trades()
max_observed_score = int(trades['trade_score'].max()) if 'trade_score' in trades.columns else 200


# --- Score-Override State -----------------------------------------------

DEFAULTS = default_payload()

if 'scoring' not in st.session_state:
    st.session_state['scoring'] = default_payload()


def _state_payload() -> dict:
    """Aktueller State als Preset-Payload."""
    return dict(st.session_state['scoring'])


def _diff_weights(current: dict, defaults: dict) -> dict:
    return {k: v for k, v in current.items()
            if float(v) != float(defaults.get(k, 0))}


def _diff_params(current: dict, defaults: dict) -> dict:
    out = {}
    for k in ('paradox_damper', 'trade_volume_enabled', 'trade_volume_tiers',
              'trade_volume_trigger', 'ex_officio_role_bonuses',
              'ex_officio_committee_global'):
        if current.get(k) != defaults.get(k):
            out[k] = current.get(k)
    return out


def _scope_label(text: str, scope: str) -> str:
    """Widget-Label mit Scope-Praefix ([Global]/[Tab]/[Detail])."""
    return f"{settings.UI_SCOPE_PREFIX[scope]} {text}"


def _scope_help(scope: str, extra: str = '') -> str:
    """Tooltip-Text: Scope-Grundtext + optionaler Widget-spezifischer Zusatz."""
    base = settings.UI_SCOPE_HELP[scope]
    return f'{base} {extra}'.strip() if extra else base


# --- Sidebar -------------------------------------------------------------
with st.sidebar:
    st.header('Filter')

    with st.expander('Datum & Suche', expanded=True):
        date_basis = st.radio(
            _scope_label('Datumsbasis', 'global'),
            options=['disclosure_date', 'transaction_date'],
            index=1,  # Default transaction_date (Projektregel 3): das
                      # disclosure-Fenster zeigt sonst wegen 45-Tage-
                      # Meldefrist Transaktionen aus weit zurueckliegenden
                      # Jahren (z.B. 365d -> Trades aus 2024).
            horizontal=False,
            help=_scope_help('global', help_for('datumsbasis')),
        )
        window_label = st.radio(
            _scope_label('Zeitfenster', 'global'),
            options=['7 Tage', '30 Tage', '365 Tage', 'Alles'],
            index=1,
            horizontal=True,
            help=_scope_help('global', 'Datums-Cutoff (auf der gewaehlten '
                             'Datumsbasis) fuer Trades-/Assets-/Members-/'
                             'Performance-Tab. Wirkt NICHT im Member-Detail-'
                             'Popup (dort volle Member-Historie) und nicht im '
                             'Recent-Trades-Tab (eigenes Fenster).'),
        )
        window_days = {'7 Tage': 7, '30 Tage': 30,
                       '365 Tage': 365, 'Alles': None}[window_label]
        search_query = st.text_input(
            'Suche (Name / Asset / Ticker / Hash-ID)', value='',
            help='Mehrere Begriffe werden UND-verknuepft und sind '
                 'reihenfolge-unabhaengig (z.B. "tina smith" findet '
                 '"Smith, Tina"). Durchsucht Name, Asset, Ticker, Issuer '
                 'und Hash-ID. Wirkt in allen Tabs; Datums-/Score-Filter '
                 'bleiben aktiv.',
        ).strip()

    with st.expander('Score & Sortierung', expanded=True):
        sort_dir = st.radio(
            'Sortierrichtung (trade_score)',
            options=['absteigend', 'aufsteigend'],
            index=0,
            horizontal=True,
            help=help_for('score'),
        )
        min_score = st.slider(
            'Min-Score',
            min_value=0,
            max_value=max(50, max_observed_score),
            value=0,
            step=1,
            help=help_for('min_score'),
        )
        primary_horizon = st.selectbox(
            _scope_label('Primaerer Return-Horizont', 'global'),
            options=settings.POST_TRADE_HORIZONS_DAYS,
            index=2,  # Default 90d
            help=_scope_help('global', 'Doppelrolle: (1) bestimmt welcher '
                             'post_return_{X}d in Asset-/Member-Aggregaten '
                             'verwendet wird; (2) bei aktivem Horizont-Filter '
                             'die Grenze, bis wann der erste Close liegen darf, '
                             'damit ein Trade sichtbar bleibt. Der Score-'
                             'Analyse-Tab nutzt einen eigenen Horizont und '
                             'ignoriert diesen Wert.'),
        )
        # Der Score-Analyse-Tab umgeht den Horizont-Filter datenseitig ohnehin
        # (er wertet bewusst alle Trades inkl. offener Positionen aus). Ist er
        # aktiv, wird die Checkbox ausgegraut + der Filter effektiv aus ->
        # kein irrefuehrend „aktiver" Filter neben einer Auswertung, auf die
        # er gar nicht wirkt.
        _BYPASS_HORIZON_TABS = {'Score-Analyse'}
        horizon_tab_bypass = st.session_state.get('main_tabs') in _BYPASS_HORIZON_TABS
        horizon_filter_active = st.checkbox(
            _scope_label('Horizont-Filter aktiv', 'global'),
            value=True,
            disabled=horizon_tab_bypass,
            help=_scope_help('global', 'Blendet Trades aus, deren erster Close '
                             'nach dem Primaeren Return-Horizont liegt (bzw. nie '
                             'geschlossen wurde). Aus = alle Trades sichtbar. '
                             'Wirkt nicht im Member-Detail-Popup und im '
                             'Score-Analyse-Tab.'),
        )
        if horizon_tab_bypass:
            st.caption('Im Score-Analyse-Tab ohne Wirkung — er wertet bewusst '
                       'alle Trades inkl. offener Positionen aus.')
            horizon_filter_active = False

    with st.expander('Spalten', expanded=False):
        trade_cols_sel = st.multiselect(
            'Trades-Tab',
            options=TRADE_DISPLAY_COLS,
            default=TRADE_DEFAULT_COLS,
            key='cols_trades',
        )
        asset_cols_sel = st.multiselect(
            'Assets-Tab',
            options=ASSET_DISPLAY_COLS,
            default=ASSET_DEFAULT_COLS,
            key='cols_assets',
        )
        member_cols_sel = st.multiselect(
            'Members-Tab',
            options=MEMBER_DISPLAY_COLS,
            default=MEMBER_DEFAULT_COLS,
            key='cols_members',
        )

    with st.expander('Signale ausschließen', expanded=False):
        excluded_signals = st.multiselect(
            'Diese Signale tragen 0 zum Score bei:',
            options=available_signals(),
            default=[],
            help=help_for('signals'),
        )

    with st.expander('Score-Gewichte', expanded=False):
        # Preset-Auswahl (ausserhalb der Form: sofortiger Effekt)
        presets = load_presets()
        # Korrupte Presets-Datei? -> einmalige Warnung (Backup liegt als .bak)
        _corrupt_msg = consume_corrupt_warning()
        if _corrupt_msg:
            st.warning(_corrupt_msg)
        preset_options = [DEFAULT_PRESET_NAME] + sorted(
            n for n in presets.keys() if n != DEFAULT_PRESET_NAME
        )
        selected_preset = st.selectbox(
            'Preset laden',
            options=preset_options,
            index=0,
            key='preset_select',
            help=help_for('preset'),
        )
        load_clicked = st.button(
            f'Preset „{selected_preset}" laden', width='stretch',
        )
        if load_clicked:
            payload = presets.get(selected_preset, DEFAULTS)
            merged_weights = dict(DEFAULTS['weights'])
            merged_weights.update(payload.get('weights') or {})
            merged_ex_bonuses = dict(DEFAULTS['ex_officio_role_bonuses'])
            merged_ex_bonuses.update({
                str(k): float(v)
                for k, v in (payload.get('ex_officio_role_bonuses')
                             or {}).items()
            })
            st.session_state['scoring'] = {
                'weights': merged_weights,
                'paradox_damper': float(payload.get(
                    'paradox_damper', DEFAULTS['paradox_damper'])),
                'trade_volume_enabled': bool(payload.get(
                    'trade_volume_enabled',
                    DEFAULTS['trade_volume_enabled'])),
                'trade_volume_tiers': payload.get(
                    'trade_volume_tiers',
                    DEFAULTS['trade_volume_tiers']),
                'trade_volume_trigger': float(payload.get(
                    'trade_volume_trigger',
                    DEFAULTS['trade_volume_trigger'])),
                'ex_officio_role_bonuses': merged_ex_bonuses,
                'ex_officio_committee_global': bool(payload.get(
                    'ex_officio_committee_global',
                    DEFAULTS['ex_officio_committee_global'])),
            }
            st.rerun()

        if (selected_preset != DEFAULT_PRESET_NAME
                and selected_preset in presets):
            if st.button(f'Preset „{selected_preset}" löschen',
                         width='stretch'):
                delete_preset(selected_preset)
                st.rerun()

        with st.form('score_weights_form', clear_on_submit=False):
            state = st.session_state['scoring']

            # Pro Signal ein Expander: Gewicht + signal-eigene Parameter
            # (trade_volume: Enabled/Trigger/Tiers). Pseudo-Signale ohne
            # Gewicht (committee_ex_officio, swarm_paradox) bekommen eigene
            # Expander danach.
            form_weights: dict[str, float] = {}
            for sig in DEFAULTS['weights'].keys():
                with st.expander(sig):
                    form_weights[sig] = st.number_input(
                        'Gewicht',
                        value=float(state['weights'].get(
                            sig, DEFAULTS['weights'][sig])),
                        step=1.0,
                        format='%.2f',
                        key=f'w_{sig}',
                        help=help_for('gewicht'),
                    )
                    if sig != 'trade_volume':
                        continue
                    form_vol_enabled = st.checkbox(
                        'Trade-Volume-Signal aktiv',
                        value=bool(state['trade_volume_enabled']),
                        key='p_tv_enabled',
                        help=help_for('trade_volume_signal'),
                    )
                    form_vol_trigger = st.number_input(
                        'Trigger-Floor (USD) — Signal triggert ab > diesem '
                        'Wert',
                        value=float(state['trade_volume_trigger']),
                        min_value=0.0,
                        step=1_000.0,
                        format='%.2f',
                        key='p_tv_trigger',
                        help=(
                            'Trades mit trade_amount_max_usd <= Trigger-Floor '
                            'lösen kein trade_volume-Signal aus. Default '
                            '15000 (oberer Quiver-Bucket "$1,001 - $15,000").'
                        ),
                    )
                    # Tier-Editor: (max_inclusive, score). Letzter Eintrag mit
                    # max=None bedeutet Catch-all (float('inf')).
                    _state_tiers = state['trade_volume_tiers'] or []
                    _tier_rows = []
                    for upper, score in _state_tiers:
                        _tier_rows.append({
                            'max_inclusive_usd': (
                                None if upper is None
                                or (isinstance(upper, float)
                                    and upper == float('inf'))
                                else float(upper)),
                            'score': int(score),
                        })
                    tier_df_in = pd.DataFrame(
                        _tier_rows,
                        columns=['max_inclusive_usd', 'score'],
                    )
                    tier_df_out = st.data_editor(
                        tier_df_in,
                        width='stretch',
                        num_rows='dynamic',
                        key='p_tv_tiers',
                        column_config={
                            'max_inclusive_usd': st.column_config.NumberColumn(
                                'Obergrenze (USD)',
                                help=(
                                    'Trades mit trade_amount_max_usd <= '
                                    'dieser Schwelle bekommen den Tier-Score. '
                                    'Leer lassen für den Catch-all '
                                    '(unendlich).'
                                ),
                                format='%d',
                            ),
                            'score': st.column_config.NumberColumn(
                                'Score-Beitrag',
                                help='Punkte, die das Signal beiträgt.',
                                format='%d',
                                min_value=0,
                            ),
                        },
                    )

            with st.expander('committee_ex_officio'):
                form_ex_global = st.checkbox(
                    'Committee-Führung wirkt committee-weit',
                    value=bool(state.get(
                        'ex_officio_committee_global',
                        DEFAULTS['ex_officio_committee_global'])),
                    key='p_exo_global',
                    help=(
                        'AN: committee_*-Rollen (Chairman/Vice/Ranking/'
                        'Ex Officio) erhalten ihren Bonus für alle Sektoren '
                        'ihres Committees. AUS: nur Subcommittee-Führung '
                        'erhält Boni.'
                    ),
                )
                form_ex_bonuses: dict[str, float] = {}
                _state_ex = dict(state.get(
                    'ex_officio_role_bonuses',
                    DEFAULTS['ex_officio_role_bonuses']) or {})
                for role_key, default_bonus in (
                        DEFAULTS['ex_officio_role_bonuses'].items()):
                    form_ex_bonuses[role_key] = st.number_input(
                        role_key,
                        value=float(_state_ex.get(role_key, default_bonus)),
                        min_value=0.0,
                        step=1.0,
                        format='%.2f',
                        key=f'p_exo_{role_key}',
                    )

            with st.expander('swarm_paradox'):
                form_paradox = st.slider(
                    'Paradox-Damper (Schwarm-Mult. bei swarm_paradox)',
                    min_value=0.0, max_value=1.0,
                    value=float(state['paradox_damper']),
                    step=0.05,
                    key='p_damper',
                    help=help_for('paradox_damper'),
                )
            save_name = st.text_input(
                'Preset-Name (zum Speichern)', value='',
                key='preset_save_name',
            )

            col_a, col_b, col_c = st.columns(3)
            apply_clicked = col_a.form_submit_button('Anwenden')
            reset_clicked = col_b.form_submit_button('Defaults')
            save_clicked = col_c.form_submit_button('Speichern')

        if reset_clicked:
            st.session_state['scoring'] = default_payload()
            st.rerun()

        if apply_clicked or save_clicked:
            # Tier-Editor in serialisierbare Liste umwandeln. NaN/leer in der
            # max_inclusive-Spalte wird zu None (= Catch-all unendlich).
            tier_list_serial: list[list] = []
            try:
                tier_records = tier_df_out.to_dict(orient='records')
            except AttributeError:
                tier_records = list(tier_df_out or [])
            for rec in tier_records:
                upper = rec.get('max_inclusive_usd')
                score = rec.get('score')
                if score is None or (isinstance(score, float)
                                     and math.isnan(score)):
                    continue
                if upper is None or (isinstance(upper, float)
                                     and math.isnan(upper)):
                    upper_serial = None
                else:
                    upper_serial = float(upper)
                tier_list_serial.append([upper_serial, int(score)])

            st.session_state['scoring'] = {
                'weights': form_weights,
                'paradox_damper': float(form_paradox),
                'trade_volume_enabled': bool(form_vol_enabled),
                'trade_volume_tiers': tier_list_serial,
                'trade_volume_trigger': float(form_vol_trigger),
                'ex_officio_role_bonuses': {
                    k: float(v) for k, v in form_ex_bonuses.items()
                },
                'ex_officio_committee_global': bool(form_ex_global),
            }

        if save_clicked:
            try:
                save_preset(save_name, _state_payload())
                st.success(f'Preset „{save_name.strip()}" gespeichert.')
            except ValueError as exc:
                st.error(str(exc))


# --- Pre-Filter (Datum + Member + Ticker) --------------------------------

_SEARCH_FIELDS = ['name', 'asset_name', 'ticker', 'issuer_ticker', 'trade_id']


def _search_mask(df: pd.DataFrame, query: str) -> pd.Series:
    """Token-basierte, reihenfolge-unabhaengige Volltextsuche.

    Pro Zeile wird ein durchsuchbarer Text aus ``name``/``asset_name``/
    ``ticker``/``issuer_ticker``/``trade_id`` (lowercase, space-join)
    gebildet. Die
    Query wird an Whitespace in Tokens gesplittet; eine Zeile matcht, wenn
    JEDES Token als Substring vorkommt (UND-Verknuepfung). Dadurch findet
    z.B. "tina smith" auch den Namen "Smith, Tina".
    """
    tokens = query.lower().split()
    if not tokens:
        return pd.Series(True, index=df.index)
    cols = [c for c in _SEARCH_FIELDS if c in df.columns]
    haystack = pd.Series('', index=df.index)
    for c in cols:
        haystack = haystack.str.cat(
            df[c].astype('string').fillna('').str.lower(), sep=' ',
        )
    mask = pd.Series(True, index=df.index)
    for tok in tokens:
        mask &= haystack.str.contains(tok, regex=False, na=False)
    return mask


def apply_prefilter(df: pd.DataFrame,
                    apply_horizon_filter: bool = True,
                    apply_window: bool = True) -> pd.DataFrame:
    """Datum-/Such-Vorfilter + (optionaler) Horizont-Sichtbarkeitsfilter.

    ``apply_horizon_filter=False`` ueberspringt NUR den Horizont-Filter
    (Datum/Suche bleiben) — fuer die Analyse-Tabs, die offene Positionen und
    spaet geschlossene Trades brauchen.
    ``apply_window=False`` ueberspringt zusaetzlich das Sidebar-Zeitfenster
    (Datums-Cutoff) — fuer das Member-Detail-Popup, das die VOLLE Historie des
    Members zeigt (der Quartal-Slider im Popup ist dort das einzige Zeitfilter).
    Die globale Suche bleibt in beiden Faellen aktiv.
    """
    out = df

    if apply_window and window_days is not None and date_basis in out.columns:
        date_col = pd.to_datetime(out[date_basis], errors='coerce')
        max_date = date_col.max()
        if pd.notna(max_date):
            cutoff = max_date - pd.Timedelta(days=window_days)
            out = out[date_col >= cutoff]

    if search_query:
        out = out[_search_mask(out, search_query)]

    # Horizont-Sichtbarkeitsfilter (global, deaktivierbar): nur Trades, deren
    # erster Close <= primary_horizon liegt. NaN (held / nie geschlossen) faellt
    # raus. Teilverkauf <= X qualifiziert (erster Offset <= X). Toggle aus ->
    # keine Ausblendung.
    if (apply_horizon_filter and horizon_filter_active
            and 'first_close_offset_days' in out.columns):
        off = out['first_close_offset_days']
        out = out[off.notna() & (off <= primary_horizon)]

    return out


# --- Score-Recompute-Parameter (vor Min-Score, gilt fuer alle Tabs) -------
weights_override = _diff_weights(st.session_state['scoring']['weights'],
                                 DEFAULTS['weights'])
params_override = _diff_params(st.session_state['scoring'], DEFAULTS)
override_active = bool(weights_override or params_override)
needs_recompute = bool(excluded_signals) or override_active

# --- Score-Analyse-Basis (horizont-filter-frei, Recompute-konsistent) -----
# Der Score-Analyse-Tab umgeht den globalen Horizont-Sichtbarkeitsfilter
# (held-Positionen sind die relevanten Daten) und spiegelt die Sidebar-Overrides
# (Muster wie der Recent-Tab). Der horizont-gefilterte ``prefilt`` (Trades-/
# Assets-/Members-Tabs) ist eine ECHTE Teilmenge dieser Basis: identisches
# Zeitfenster + Suche, nur zusaetzlich der Horizont-Filter. Da der Recompute pro
# Zeile deterministisch ist (und ``first_close_offset_days`` unberuehrt laesst),
# wird ``prefilt`` unten per Horizont-Schritt aus ``corr_base`` ABGELEITET statt
# separat rekomputet -> nur EIN Recompute statt zwei (zeilenidentisches
# Ergebnis). min_score/primary_horizon wirken auf corr_base bewusst NICHT.
corr_base = apply_prefilter(trades, apply_horizon_filter=False)
if needs_recompute and not corr_base.empty:
    with st.spinner(f'Score neu berechnen für {len(corr_base):,} Trades …'):
        corr_base = recompute_subset(
            corr_base,
            excluded_signals,
            weights_override=weights_override or None,
            params_override=params_override or None,
        )

# --- Trades-/Assets-/Members-Basis: nur der Horizont-Schritt auf corr_base ---
# Ableitung statt separater Recompute — apply_prefilter wendet auf die bereits
# gefensterte/gesuchte (und ggf. rekomputete) Basis nur noch den Horizont-Filter
# an (apply_window=False; die Suche ist idempotent). Kein zweiter Recompute.
prefilt = apply_prefilter(corr_base, apply_window=False,
                          apply_horizon_filter=True)

# --- Trade-Tab-Subset (mit Min-Score + Sortierung) -----------------------
trade_rows = prefilt
if min_score > 0:
    trade_rows = trade_rows[trade_rows['trade_score'] >= min_score]
trade_rows = trade_rows.sort_values(
    'trade_score', ascending=(sort_dir == 'aufsteigend'), kind='mergesort',
)


# --- Column configs ------------------------------------------------------

def _dyn_height(n_rows: int) -> int:
    return min(820, max(280, n_rows * 36 + 60))


_TICKER_LINK_REGEX = r'von\+([^&]+)$'


def _ticker_google_url(ticker) -> str | None:
    """Google-Suchlink ``Aktienkurs von {Ticker}`` (None bei leerem Ticker)."""
    if ticker is None or (isinstance(ticker, float) and pd.isna(ticker)):
        return None
    t = str(ticker).strip()
    if not t:
        return None
    return ('https://www.google.com/search?q='
            + urllib.parse.quote_plus(f'Aktienkurs von {t}'))


def _trade_column_config(score_max_val: int, ticker_as_link: bool = False) -> dict:
    cc = st.column_config
    ticker_cfg = (
        cc.LinkColumn('Ticker', display_text=_TICKER_LINK_REGEX, width='small')
        if ticker_as_link
        else cc.TextColumn('Ticker', width='small')
    )
    return {
        'trade_id': cc.TextColumn('Hash-ID', width='small'),
        'transaction_date': cc.DateColumn(
            'Transaction', format='DD.MM.YYYY', width='small',
            help=help_for('transaction_date')),
        'disclosure_date': cc.DateColumn(
            'Disclosure', format='DD.MM.YYYY', width='small',
            help=help_for('disclosure_date')),
        'name': cc.TextColumn('Member', width='medium'),
        'chamber': cc.TextColumn('Chamber', width='small'),
        'Member_Party': cc.TextColumn('Party', width='small'),
        'asset_name': cc.TextColumn('Asset', width='large'),
        'ticker': ticker_cfg,
        'issuer_ticker': cc.TextColumn('Issuer', width='small',
                                       help=help_for('issuer')),
        'transaction_type': cc.TextColumn('Type', width='small'),
        'amount': cc.TextColumn('Amount Range', width='small'),
        'trade_volume_usd': cc.NumberColumn(
            'Trade Vol (USD)', format='$%d', width='medium',
            help=help_for('trade_volume')),
        'trade_amount_max_usd': cc.NumberColumn(
            'Kapital (USD)', format='$%d', width='medium',
            help=help_for('kapital')),
        'asset_tradingvolume_usd': cc.NumberColumn(
            'Asset Vol (USD)', format='$%d', width='medium',
            help=help_for('asset_volume')),
        'trade_score': cc.ProgressColumn(
            'Score',
            min_value=0,
            max_value=max(200, score_max_val),
            format='%d',
            width='small',
            help=help_for('score'),
        ),
        'committee_match': cc.TextColumn('Committee Match', width='large',
                                         help=help_for('committee_match')),
        'relevant_sectors': cc.TextColumn('Relevant Sectors', width='medium',
                                          help=help_for('relevant_sectors')),
        'signals_triggered': cc.TextColumn('Signals', width='large',
                                           help=help_for('signals')),
        'trade_score_breakdown': cc.TextColumn(
            'Score Breakdown', width='large',
            help=help_for('score_breakdown')),
        'trade_warnings': cc.TextColumn('Warnings', width='medium'),
        'post_return_30d': cc.NumberColumn(
            'Return 30d %', format='%.1f%%', width='small',
            help=help_for('post_return')),
        'post_return_60d': cc.NumberColumn(
            'Return 60d %', format='%.1f%%', width='small',
            help=help_for('post_return')),
        'post_return_90d': cc.NumberColumn(
            'Return 90d %', format='%.1f%%', width='small',
            help=help_for('post_return')),
        'post_return_120d': cc.NumberColumn(
            'Return 120d %', format='%.1f%%', width='small',
            help=help_for('post_return')),
        'post_return_180d': cc.NumberColumn(
            'Return 180d %', format='%.1f%%', width='small',
            help=help_for('post_return')),
        'effective_return_pct': cc.NumberColumn(
            'Eff. Return %', format='%.1f%%', width='small',
            help=help_for('effective_return')),
        'exit_status': cc.TextColumn('Exit', width='small',
                                     help=help_for('exit_status')),
        'exit_date': cc.DateColumn('Exit-Datum', format='DD.MM.YYYY',
                                   width='small'),
        'first_close_offset_days': cc.NumberColumn(
            'Erster Close (Tage)', format='%d', width='small',
            help=help_for('first_close')),
        'paper_return_pct': cc.NumberColumn(
            'Paper Return %', format='%.1f%%', width='small',
            help='Mark-to-Market: (aktueller Close / Einstand − 1), '
                 'richtungsbewusst. Leer = kein aktueller Preis.'),
        'price_as_of': cc.DateColumn(
            'Preis-Stand', format='DD.MM.YYYY', width='small',
            help='Datum des fuer den Paper Return verwendeten Close.'),
        'days_since_trade': cc.NumberColumn(
            'Tage seit Trade', format='%d', width='small'),
    }


def _asset_column_config(score_max_val: int) -> dict:
    cc = st.column_config
    return {
        'issuer_ticker': cc.TextColumn('Issuer', width='small',
                                       help=help_for('issuer')),
        'asset_name': cc.TextColumn('Asset', width='large'),
        'trade_count': cc.NumberColumn(
            'Trades', format='%d', width='small'),
        'member_count': cc.NumberColumn(
            'Members', format='%d', width='small'),
        'score_max': cc.ProgressColumn(
            'Score Max',
            min_value=0,
            max_value=max(200, score_max_val),
            format='%d',
            width='small',
            help=help_for('score'),
        ),
        'score_avg': cc.NumberColumn(
            'Score Avg', format='%.1f', width='small',
            help=help_for('score')),
        'score_total': cc.NumberColumn(
            'Score Σ', format='%d', width='small',
            help=help_for('score')),
        'total_volume_usd': cc.NumberColumn(
            'Volume (USD)', format='$%d', width='medium'),
        'last_date': cc.DateColumn(
            'Last', format='DD.MM.YYYY', width='small'),
        'first_date': cc.DateColumn(
            'First', format='DD.MM.YYYY', width='small'),
        'chambers': cc.TextColumn('Chambers', width='small'),
        'sectors': cc.TextColumn('Sectors', width='medium'),
        'signals': cc.TextColumn('Signals', width='large',
                                 help=help_for('signals')),
        'avg_return_primary': cc.NumberColumn(
            'Avg Return %', format='%.1f%%', width='small',
            help=help_for('avg_return')),
        'max_return_primary': cc.NumberColumn(
            'Max Return %', format='%.1f%%', width='small',
            help=help_for('max_return')),
        'pct_profitable': cc.NumberColumn(
            '% Profitable', format='%.1f%%', width='small',
            help=help_for('pct_profitable')),
    }


def _member_column_config(score_max_val: int) -> dict:
    cc = st.column_config
    return {
        'name': cc.TextColumn('Member', width='medium'),
        'chamber': cc.TextColumn('Chamber', width='small'),
        'Member_Party': cc.TextColumn('Party', width='small'),
        'trade_count': cc.NumberColumn(
            'Trades', format='%d', width='small'),
        'asset_count': cc.NumberColumn(
            'Assets', format='%d', width='small'),
        'score_max': cc.ProgressColumn(
            'Score Max',
            min_value=0,
            max_value=max(200, score_max_val),
            format='%d',
            width='small',
            help=help_for('score'),
        ),
        'score_avg': cc.NumberColumn(
            'Score Avg', format='%.1f', width='small',
            help=help_for('score')),
        'score_total': cc.NumberColumn(
            'Score Σ', format='%d', width='small',
            help=help_for('score')),
        'total_volume_usd': cc.NumberColumn(
            'Volume (USD)', format='$%d', width='medium'),
        'last_date': cc.DateColumn(
            'Last', format='DD.MM.YYYY', width='small'),
        'first_date': cc.DateColumn(
            'First', format='DD.MM.YYYY', width='small'),
        'signals': cc.TextColumn('Signals', width='large',
                                 help=help_for('signals')),
        'avg_return_primary': cc.NumberColumn(
            'Avg Return %', format='%.1f%%', width='small',
            help=help_for('avg_return')),
        'pct_profitable': cc.NumberColumn(
            '% Profitable', format='%.1f%%', width='small',
            help=help_for('pct_profitable')),
    }


def _related_return_style(v):
    """Hintergrund fuer effective_return_pct: Schwelle aus settings.

    ``+DETAIL_RELATED_RETURN_COLOR_PCT`` -> gruen,
    ``-DETAIL_RELATED_RETURN_COLOR_PCT`` -> rot (Default 10.0 %).
    """
    if pd.isna(v):
        return ''
    thr = settings.DETAIL_RELATED_RETURN_COLOR_PCT
    if v > thr:
        return 'background-color: #14532d; color: #dcfce7'
    if v < -thr:
        return 'background-color: #7f1d1d; color: #fee2e2'
    return ''


def _render_related_table(related: pd.DataFrame) -> None:
    """Simple Tabelle der weiteren Trades am gleichen Issuer."""
    st.subheader('Weitere Trades am gleichen Issuer')
    if related.empty:
        st.caption('Keine weiteren Trades im gewählten Scope.')
        return

    col_map = {
        'effective_return_pct': 'Eff. Return %',
        'trade_score': 'Score',
        'name': 'Member',
        'transaction_type': 'Type',
        'signals_triggered': 'Signals',
        'committee_match': 'Committee Match',
        'transaction_date': 'Transaction',
        'amount': 'Amount Range',
        'ticker': 'Ticker',
        'trade_id': 'Hash-ID',
    }
    src_cols = [c for c in col_map if c in related.columns]
    related_src = related.copy()
    if 'signals_triggered' in related_src.columns:
        related_src['signals_triggered'] = \
            related_src['signals_triggered'].map(_primary_only)
    tdf = related_src.sort_values(
        'transaction_date', kind='mergesort', na_position='last',
    )[src_cols].rename(columns=col_map)

    fmt = {}
    if 'Eff. Return %' in tdf.columns:
        fmt['Eff. Return %'] = (
            lambda v: f'{v:+.1f}%' if pd.notna(v) else '--'
        )
    if 'Score' in tdf.columns:
        fmt['Score'] = lambda v: f'{v:.0f}' if pd.notna(v) else '--'
    if 'Transaction' in tdf.columns:
        fmt['Transaction'] = lambda v: str(v)[:10] if pd.notna(v) else '--'

    styler = tdf.style.format(fmt, na_rep='--')
    if 'Eff. Return %' in tdf.columns:
        styler = styler.map(
            _related_return_style, subset=['Eff. Return %'],
        )
    st.dataframe(styler, hide_index=True, width='stretch')


def render_trade_detail(trade, trade_rows, key_prefix: str) -> None:
    """Detail-Ansicht eines Trades: Toggles + Kursverlauf-Chart mit Events
    + Related-Trades-Tabelle + Trade-Kontext + Post-Trade-Returns.

    Wird sowohl im Performance-Tab (key_prefix='perf') als auch im
    Trades-Tab-Popup (key_prefix='popup') verwendet; key_prefix haelt die
    Widget-Keys eindeutig.
    """
    import contextlib
    from scoring.post_trade_returns import _load_price_cache  # noqa: E402

    ticker = str(trade['ticker'])
    t_date = pd.to_datetime(trade['transaction_date'])
    window_start = t_date - pd.Timedelta(days=30)
    window_end = (
        t_date + pd.Timedelta(days=max(settings.POST_TRADE_HORIZONS_DAYS) + 30)
    )

    # Toggles: steuern Chart-Marker UND Tabelle gemeinsam.
    scope_all = st.toggle(
        _scope_label('Alle Trades (Zeitfenster ignorieren)', 'detail'),
        value=False, key=f'{key_prefix}_scope_all',
        help=_scope_help('detail', 'Weitet Chart + Related-Trades-Tabelle auf '
                         'den gesamten Trade-Zeitraum aus, statt auf das '
                         'Sidebar-Zeitfenster. Betrifft nur dieses Popup.'),
    )
    show_others = st.toggle(
        'Andere Politiker einbeziehen',
        value=True, key=f'{key_prefix}_show_others',
    )

    # Bei scope_all deckt der Chart den gesamten Trade-Zeitraum (ggf. mehrere
    # Jahre) ab -> 26-MB-Preis-Cache + breiter Kursverlauf koennen spuerbar
    # dauern. Daher klassischer Ladebildschirm waehrend des Aufbaus.
    spinner_ctx = (
        st.spinner('Lade vollständigen Kursverlauf …')
        if scope_all else contextlib.nullcontext()
    )
    with spinner_ctx:
        cache_path = str(ROOT / settings.POST_TRADE_PRICE_CACHE)
        cache = _load_price_cache(cache_path)

        # Gefilterte Menge der weiteren Trades am gleichen Issuer.
        igk = trade.get('issuer_group_key')
        related = trade_rows.iloc[0:0]
        if pd.notna(igk):
            rel = trade_rows[
                (trade_rows['issuer_group_key'] == igk)
                & (trade_rows['trade_id'] != trade['trade_id'])
            ].copy()
            rel_dates = pd.to_datetime(
                rel['transaction_date'], errors='coerce')
            if not scope_all:
                rel = rel[
                    (rel_dates >= window_start) & (rel_dates <= window_end)
                ]
            if not show_others:
                rel = rel[rel['name'] == trade['name']]
            related = rel

        # Chart-Fenster: bei scope_all auf den Bereich erweitern, der ALLE
        # angezeigten Trades abdeckt; sonst das enge Standard-Fenster.
        if scope_all:
            rel_dates_all = pd.to_datetime(
                related['transaction_date'], errors='coerce'
            ).dropna()
            chart_start = (
                min([t_date, *rel_dates_all]) - pd.Timedelta(days=30)
            )
            chart_end = (
                max([window_end, *rel_dates_all]) + pd.Timedelta(days=30)
            )
        else:
            chart_start, chart_end = window_start, window_end

        # Preisverlauf aus Cache
        series = pd.DataFrame()
        if ticker in cache.index.get_level_values(0):
            tkr_cache = cache.loc[ticker]
            tkr_cache = tkr_cache[
                tkr_cache.index != pd.Timestamp('1900-01-01')]
            series = tkr_cache[
                (tkr_cache.index >= chart_start)
                & (tkr_cache.index <= chart_end)
            ]

    if series.empty:
        st.warning(
            f'Keine Preisdaten fuer {ticker} im Cache. '
            f'(Status: {trade.get("exit_status")})'
        )
    else:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=series.index, y=series['close'],
            mode='lines', name=ticker,
            line=dict(color='#888', width=2),
        ))

        # Ausgewaehlter Trade: Richtung kanonisch bestimmen.
        sel_dir = _direction_from_transaction_type(trade['transaction_type'])
        sel_label = {'buy': 'Kauf', 'sell': 'Verkauf'}.get(
            sel_dir, 'Sonstige')
        marker_color = {'buy': '#22c55e', 'sell': '#b91c1c'}.get(
            sel_dir, 'gray')
        baseline = trade.get('post_trade_baseline_price')
        fig.add_trace(go.Scatter(
            x=[t_date], y=[baseline],
            mode='markers',
            name=f"Dieser Trade: {sel_label} · {trade['name']}",
            marker=dict(size=16, color=marker_color, symbol='star'),
            hovertemplate=(
                f"<b>Dieser Trade</b><br>{trade['name']}<br>"
                f"{sel_label} am %{{x|%Y-%m-%d}}<br>"
                "Kurs: %{y:.2f} USD<extra></extra>"
            ),
        ))

        # Horizont-Vertikalen
        for h in settings.POST_TRADE_HORIZONS_DAYS:
            x_h = t_date + pd.Timedelta(days=h)
            fig.add_vline(x=x_h, line_dash='dot', line_color='gray')
            fig.add_annotation(
                x=x_h, y=1.0, yref='paper', yanchor='bottom',
                text=f'{h}d', showarrow=False,
                font=dict(color='gray', size=10),
            )

        # Weitere Trades am gleichen Issuer (gefilterte `related`-Menge):
        # getrennt nach Buy/Sell -> separate Legenden-Einträge.
        def _add_split_series(subset, symbol, size, buy_name, sell_name):
            close = series['close'].sort_index()
            first_close = close.iloc[0] if len(close) else None
            for direction, dir_name, dir_color in [
                ('buy', buy_name, '#22c55e'),
                ('sell', sell_name, '#b91c1c'),
            ]:
                dsub = subset[subset['transaction_type'].apply(
                    lambda t: _direction_from_transaction_type(t) == direction)]
                if dsub.empty:
                    continue
                xs, ys, txt = [], [], []
                for _, row in dsub.iterrows():
                    d = pd.to_datetime(row['transaction_date'], errors='coerce')
                    if pd.isna(d):
                        continue
                    price = close.asof(d)
                    approx = False
                    if pd.isna(price):
                        if first_close is None:
                            continue
                        price = first_close
                        approx = True
                    rd_label = {'buy': 'Kauf', 'sell': 'Verkauf'}.get(
                        direction, 'Sonstige')
                    xs.append(d)
                    ys.append(price)
                    label = f"{row['name']} · {rd_label}"
                    if approx:
                        label += ' · Kurs vor Cache-Beginn (genähert)'
                    txt.append(label)
                if xs:
                    fig.add_trace(go.Scatter(
                        x=xs, y=ys, mode='markers',
                        name=dir_name, text=txt,
                        marker=dict(
                            size=size, symbol=symbol,
                            color=dir_color,
                            line=dict(width=2, color=dir_color),
                        ),
                        hovertemplate=(
                            "<b>%{text}</b><br>%{x|%Y-%m-%d}<br>"
                            "Kurs: %{y:.2f} USD<extra></extra>"
                        ),
                    ))

        own = related[related['name'] == trade['name']]
        others = related[related['name'] != trade['name']]
        _add_split_series(own, 'star-open', 11,
            f'Weitere Käufe von {trade["name"]}',
            f'Weitere Verkäufe von {trade["name"]}')
        _add_split_series(others, 'diamond-open', 7,
            'Käufe anderer Politiker',
            'Verkäufe anderer Politiker')

        # Verkaufs-Art-Marker zuletzt -> liegt im Z-Order ueber den Buy/Sell-
        # Markern. Markiert Teil-/Vollverkaeufe (transaction_type) mit eigenem
        # Symbol, unabhaengig von der Exit-Detection (s. app/chart_helpers.py).
        _close = series['close'].sort_index()
        _first_close = _close.iloc[0] if len(_close) else None

        def _price_for(d):
            dd = pd.to_datetime(d, errors='coerce')
            if pd.isna(dd):
                return None
            p = _close.asof(dd)
            return _first_close if pd.isna(p) else p

        sale_records = [{
            'date': t_date,
            'price': baseline if pd.notna(baseline) else _price_for(t_date),
            'transaction_type': trade['transaction_type'],
            'name': trade['name'],
        }]
        for _, _row in related.iterrows():
            sale_records.append({
                'date': _row['transaction_date'],
                'price': _price_for(_row['transaction_date']),
                'transaction_type': _row.get('transaction_type'),
                'name': _row['name'],
            })
        for _tr in build_sale_kind_traces(sale_records):
            fig.add_trace(_tr)

        asset_name = trade.get('asset_name', ticker)
        fig.update_layout(
            title=f'{ticker} -- {asset_name}',
            xaxis_title='Datum', yaxis_title='Kurs (USD)',
            height=500, hovermode='x unified',
        )
        st.plotly_chart(fig, width='stretch')

    # Related-Trades-Tabelle (zwischen Chart und Kontext-Tabellen)
    _render_related_table(related)

    # Zwei Tabellen nebeneinander
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader('Trade-Kontext')
        ctx_fields = [
            'transaction_date', 'name', 'chamber', 'asset_name',
            'ticker', 'transaction_type', 'amount',
            'trade_amount_max_usd', 'trade_score',
            'signals_triggered', 'committee_match',
            'trade_score_breakdown',
        ]
        ctx_available = [f for f in ctx_fields if f in trade.index]
        ctx_data: dict = {}
        for k in ctx_available:
            if k == 'signals_triggered':
                primary, hint = split_primary_hint(
                    trade[k], settings.SCORING_PRIMARY_SIGNALS)
                ctx_data['signals_triggered'] = ';'.join(primary)
                ctx_data['hints'] = ';'.join(hint) if hint else '—'
            else:
                ctx_data[k] = trade[k]
        st.dataframe(pd.Series(ctx_data, name='Wert'), width='stretch')

    with col_right:
        st.subheader('Post-Trade Returns')
        ret_fields = ['post_trade_baseline_price'] + \
            [f'post_price_{h}d' for h in settings.POST_TRADE_HORIZONS_DAYS] + \
            [f'post_return_{h}d' for h in settings.POST_TRADE_HORIZONS_DAYS] + \
            ['exit_status', 'exit_date', 'exit_price',
             'exit_amount_max_usd', 'effective_return_pct',
             'first_close_offset_days', 'opposing_trade_ids']
        ret_available = [f for f in ret_fields if f in trade.index]
        ret_data = {k: trade[k] for k in ret_available}
        st.dataframe(pd.Series(ret_data, name='Wert'), width='stretch')

        # --- Abnormal Return (marktbereinigt vs. SPY) -----------------------
        # Horizont im UI waehlbar (Scope Detail): steuert Headline-Metrik +
        # Perzentil-Rang live. Optionen = im Master vorhandene AR-Horizonte,
        # Default = AR_HEADLINE_HORIZON_DAYS (defensiver Fallback Index 0).
        _ar_present = [h for h in settings.AR_HORIZONS_DAYS
                       if f'signed_ar_{h}d' in trade.index]
        if _ar_present:
            _ar_idx = mm.ar_default_index(
                _ar_present, settings.AR_HEADLINE_HORIZON_DAYS)
            headline_h = int(st.radio(
                _scope_label('Abnormal-Return-Horizont', 'detail'),
                options=_ar_present, index=_ar_idx, horizontal=True,
                key=f'{key_prefix}_ar_horizon',
                help=_scope_help('detail', 'Waehlt den Horizont fuer die '
                                 'AR-Headline-Metrik und den zweiseitigen '
                                 'Perzentil-Rang. Der Rang vergleicht gegen '
                                 'ALLE Trades im Master (ungefiltert), nicht '
                                 'gegen die aktuelle Auswahl.'),
            ))
        else:
            headline_h = int(settings.AR_HEADLINE_HORIZON_DAYS)
        ar_head_col = f'signed_ar_{headline_h}d'
        if ar_head_col not in trade.index:
            st.caption('Abnormal Return: noch nicht im Master — Stage '
                       '`--stage_abnormal_returns` ausführen.')
        else:
            st.subheader('Abnormal Return (marktbereinigt vs. SPY)',
                         help='Signierter, marktbereinigter Abnormal Return: '
                              'rohe Rendite minus SPY ueber dasselbe Fenster, '
                              'Verkauf invertiert (positiv = besser als der Markt). '
                              'Zeigt, wie viel ueber blosses Mitschwimmen hinausgeht.')
            head_val = pd.to_numeric(
                pd.Series([trade.get(ar_head_col)]), errors='coerce').iloc[0]
            if pd.notna(head_val):
                st.metric(f'AR {headline_h}d', f'{head_val * 100:+.1f}%')
                rank = mm.ar_percentile_rank(head_val, _ar_sorted_dist(headline_h))
                st.caption(mm.format_ar_rank(rank))
            else:
                st.caption(f'AR {headline_h}d: — (noch nicht evaluierbar / '
                           'offene Position).')
            # Pro-Horizont: roher Return (bereits %) vs. AR (Bruchteil -> %).
            # Vereinigung beider Horizont-Listen, damit AR-only- oder
            # Return-only-Horizonte nicht stillschweigend fehlen, falls die
            # Listen kuenftig divergieren (heute identisch). .get()/NaN-tolerant.
            _union_h = sorted(set(settings.POST_TRADE_HORIZONS_DAYS)
                              | set(settings.AR_HORIZONS_DAYS))
            ar_rows = []
            for h in _union_h:
                raw = pd.to_numeric(
                    pd.Series([trade.get(f'post_return_{h}d')]),
                    errors='coerce').iloc[0]
                arv = pd.to_numeric(
                    pd.Series([trade.get(f'signed_ar_{h}d')]),
                    errors='coerce').iloc[0]
                ar_rows.append({
                    'Horizont': f'{h}d',
                    'Roh %': float(raw) if pd.notna(raw) else float('nan'),
                    'AR %': float(arv) * 100 if pd.notna(arv) else float('nan'),
                })
            st.dataframe(
                pd.DataFrame(ar_rows).set_index('Horizont'), width='stretch',
                column_config={
                    'Roh %': st.column_config.NumberColumn('Roh %', format='%.1f%%'),
                    'AR %': st.column_config.NumberColumn('AR %', format='%.1f%%'),
                })

    # --- Partial-Close-Zerlegung (Display-only, Lite/non-FIFO) -----------
    # Pro Schliessungs-Event (Gegen-Trade aus opposing_trade_ids) eine eigene
    # Zeile mit ihrem realisierten Return. Bewusst NICHT in den Master und NICHT
    # in Volumen-Aggregate (Volumina partitionieren global nicht sauber).
    opp_raw = trade.get('opposing_trade_ids')
    if isinstance(opp_raw, str) and opp_raw.strip():
        from scoring.post_trade_returns import _price_at_or_after  # noqa: E402
        opp_ids = [x for x in opp_raw.split(';') if x]
        # Gegen-Trades aus dem VOLLEN Datensatz nachschlagen — sie koennen aus
        # dem gefilterten/sichtbaren Subset gefallen sein.
        opp_df = trades[trades['trade_id'].astype(str).isin(opp_ids)].copy()
        opp_df['_d'] = pd.to_datetime(opp_df['transaction_date'], errors='coerce')
        opp_df = opp_df.sort_values('_d')
        events = []
        for _, orow in opp_df.iterrows():
            ed = orow['_d']
            price = (_price_at_or_after(cache, ticker, ed)
                     if pd.notna(ed) else None)
            events.append({
                'trade_id': orow['trade_id'],
                'date': ed,
                'volume': orow.get('trade_amount_max_usd'),
                'price': price,
            })
        sel_sign = -1.0 if _direction_from_transaction_type(
            trade['transaction_type']) == 'sell' else 1.0
        opening = {
            'trade_id': trade['trade_id'],
            'transaction_date': t_date,
            'baseline_price': trade.get('post_trade_baseline_price'),
            'volume': trade.get('trade_amount_max_usd'),
        }
        pc_rows = build_partial_close_rows(opening, events, sel_sign)
        if pc_rows:
            st.subheader('Partial-Close-Zerlegung (Lite · Display-only)',
                          help=help_for('partial_close'))
            st.caption('Eine Zeile pro Schliessungs-Event mit eigenem '
                       'realisiertem Return. Volumina non-FIFO -> nicht '
                       'aggregieren.')
            st.dataframe(pd.DataFrame(pc_rows), hide_index=True,
                         width='stretch')


# --- Tabs ----------------------------------------------------------------
(tab_trades, tab_assets, tab_members, tab_detail, tab_recent,
 tab_analysis) = st.tabs(
    ['Trades', 'Assets', 'Members', 'Post Trade Performance', 'Recent Trades',
     'Score-Analyse'],
    # key schreibt das Label des aktiven Tabs in session_state['main_tabs'];
    # on_change='rerun' erzwingt nach einem Tab-Klick einen Folge-Rerun, damit
    # die weiter oben gerenderte Sidebar den neuen aktiven Tab sieht (sie liest
    # den Wert aus dem vorigen Lauf). Genutzt fuer die Auto-Deaktivierung des
    # Horizont-Filters im Score-Analyse-Tab (s. Sidebar).
    key='main_tabs', on_change='rerun')

with tab_trades:
    n = len(trade_rows)
    mean_score = float(trade_rows['trade_score'].mean()) if n else 0.0
    max_score = int(trade_rows['trade_score'].max()) if n else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('Trades (gefiltert)', f'{n:,}')
    c2.metric('Mean Score', f'{mean_score:.1f}', help=help_for('score'))
    c3.metric('Max Score', f'{max_score}', help=help_for('score'))
    c4.metric('Signale ausgeschlossen', len(excluded_signals))
    c5.metric('Override aktiv', 'Ja' if override_active else 'Nein',
              help=help_for('override_active'))

    display_cols = [c for c in TRADE_DISPLAY_COLS
                    if c in trade_rows.columns and c in trade_cols_sel]
    st.caption('Tipp: Zeile anklicken öffnet die Detail-Ansicht '
               '(Chart + Kontext) als Popup. Doppelklick auf eine Zelle '
               'zeigt weiterhin den vollen Zellinhalt. Ticker = Google-Link.')
    trade_display = trade_rows[display_cols].copy()
    if 'signals_triggered' in display_cols:
        trade_display['signals_triggered'] = \
            trade_display['signals_triggered'].map(_primary_only)
    ticker_link = 'ticker' in display_cols
    if ticker_link:
        trade_display['ticker'] = trade_display['ticker'].map(_ticker_google_url)
    sel_state = st.dataframe(
        trade_display,
        width='stretch',
        hide_index=True,
        height=_dyn_height(len(trade_rows)),
        column_config=_trade_column_config(max_score, ticker_as_link=ticker_link),
        key='trades_grid',
        on_select='rerun',
        selection_mode='single-row',
    )

    @st.dialog('Trade-Detail', width='large')
    def _show_trade_dialog(idx):
        render_trade_detail(trade_rows.loc[idx], trade_rows, 'popup')

    _sel_rows = []
    if sel_state and getattr(sel_state, 'selection', None):
        _sel_rows = sel_state.selection.get('rows', [])
    if _sel_rows:
        _actual_idx = trade_rows.index[_sel_rows[0]]
        if st.session_state.get('detail_last_shown') != _actual_idx:
            st.session_state['detail_last_shown'] = _actual_idx
            _show_trade_dialog(_actual_idx)
    else:
        st.session_state['detail_last_shown'] = None

with tab_assets:
    st.caption('Aggregation aus dem gefilterten Trade-Subset (reaktiv auf'
               ' Datum, Suche, Signal-Ausschluss). Min-Score wirkt auf'
               ' `score_max` des Assets.')
    assets_df = aggregate_assets(prefilt, date_basis,
                                  primary_horizon=primary_horizon)
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
              f'{int(assets_df["score_max"].max()) if len(assets_df) else 0}',
              help=help_for('score'))
    a3.metric('Σ Volumen (USD)',
              f'{int(assets_df["total_volume_usd"].sum()) if len(assets_df) else 0:,}')

    asset_display_cols = [c for c in ASSET_DISPLAY_COLS
                          if c in assets_df.columns and c in asset_cols_sel]
    a_score_max = (int(assets_df['score_max'].max())
                   if len(assets_df) else 0)
    st.dataframe(
        assets_df[asset_display_cols] if asset_display_cols else assets_df,
        width='stretch',
        hide_index=True,
        height=_dyn_height(len(assets_df)),
        column_config=_asset_column_config(a_score_max),
    )

with tab_members:
    st.caption('Aggregation aus dem gefilterten Trade-Subset (reaktiv auf'
               ' Datum, Suche, Signal-Ausschluss). Min-Score wirkt auf'
               ' `score_max` des Members.')
    members_df = aggregate_members(prefilt, date_basis,
                                    primary_horizon=primary_horizon)
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
              f'{int(members_df["score_max"].max()) if len(members_df) else 0}',
              help=help_for('score'))
    m3.metric('Σ Volumen (USD)',
              f'{int(members_df["total_volume_usd"].sum()) if len(members_df) else 0:,}')

    member_display_cols = [c for c in MEMBER_DISPLAY_COLS
                           if c in members_df.columns
                           and c in member_cols_sel]
    m_score_max = (int(members_df['score_max'].max())
                   if len(members_df) else 0)
    st.caption('Tipp: Zeile anklicken öffnet die Member-Detail-Ansicht '
               '(Gremien, Performance-Diagramme, Verläufe) als Popup.')
    member_sel_state = st.dataframe(
        members_df[member_display_cols] if member_display_cols else members_df,
        width='stretch',
        hide_index=True,
        height=_dyn_height(len(members_df)),
        column_config=_member_column_config(m_score_max),
        key='members_grid',
        on_select='rerun',
        selection_mode='single-row',
    )

    @st.dialog('Member-Detail', width='large')
    def _show_member_dialog(m_name, m_chamber):
        # Popup-Datenpfad: VOLLE Member-Historie — weder Sidebar-Zeitfenster
        # noch Horizont-Filter wirken (der Quartal-Slider im Popup ist das
        # einzige Zeitfilter; offene + spaet geschlossene Trades fuer Verlaeufe
        # noetig). Nur die globale Suche bleibt aktiv. Score nur auf dem Member
        # rekomputiert (billig); Sidebar-Signal-/Gewichts-Filter gelten.
        from app.member_detail import render_member_detail
        base = apply_prefilter(trades, apply_horizon_filter=False,
                               apply_window=False)
        # Datenbasis transparent machen: alles, was hier noch (nicht) wirkt.
        basis_parts = ['volle Member-Historie (Sidebar-Zeitfenster und '
                       'Horizont-Filter wirken hier nicht)']
        if search_query:
            basis_parts.append(f'globale Suche „{search_query}" aktiv')
        st.caption('Datenbasis: ' + '; '.join(basis_parts) + '.')
        msel = (base['name'] == m_name) & (base['chamber'] == m_chamber)
        m_df = base[msel]
        if needs_recompute and not m_df.empty:
            m_df = recompute_subset(
                m_df, excluded_signals,
                weights_override=weights_override or None,
                params_override=params_override or None,
            )
        render_member_detail(m_df, base[~msel], m_name, m_chamber)

    _m_rows = []
    if member_sel_state and getattr(member_sel_state, 'selection', None):
        _m_rows = member_sel_state.selection.get('rows', [])
    if _m_rows and len(members_df):
        _m_row = members_df.iloc[_m_rows[0]]
        _m_key = (_m_row['name'], _m_row['chamber'])
        if st.session_state.get('member_detail_last') != _m_key:
            st.session_state['member_detail_last'] = _m_key
            _show_member_dialog(_m_row['name'], _m_row['chamber'])
    else:
        st.session_state['member_detail_last'] = None

with tab_detail:
    st.caption('Drill-Down pro Trade: Preisverlauf + Exit-Marker. '
               'Top-Trades nach Score (trade_score) aus '
               'der aktuellen Sidebar-Filterung (inkl. Suche).')

    detail_pool = trade_rows.copy()
    has_detail_data = ('effective_return_pct' in detail_pool.columns
                       and 'post_trade_baseline_price' in detail_pool.columns)

    if not has_detail_data:
        st.info(
            'Keine Post-Trade-Performance-Daten vorhanden. '
            'Bitte Pipeline-Stage `--stage_post_trade_returns` ausführen.'
        )
    elif detail_pool.empty:
        st.info('Keine Trades im aktuellen Filter.')
    else:
        detail_pool = detail_pool.sort_values(
            'trade_score', ascending=(sort_dir == 'aufsteigend'),
            kind='mergesort', na_position='last',
        )

        DEFAULT_SHOW = 25
        EXPAND_STEP = 100
        if 'detail_show_n' not in st.session_state:
            st.session_state['detail_show_n'] = DEFAULT_SHOW

        n_show = min(st.session_state['detail_show_n'], len(detail_pool))
        top_pool = detail_pool.head(n_show)

        top_columns = [
            'effective_return_pct', 'trade_score', 'trade_amount_max_usd',
            'name', 'ticker', 'transaction_date',
        ]
        if 'trade_id' in top_pool.columns:
            top_columns.append('trade_id')
        top_columns = [c for c in top_columns if c in top_pool.columns]

        _max_score = max(
            int(trade_rows['trade_score'].max()) if len(trade_rows) else 200,
            200,
        )
        st.dataframe(
            top_pool[top_columns],
            width='stretch',
            hide_index=True,
            height=_dyn_height(len(top_pool)),
            column_config=_trade_column_config(_max_score),
        )

        remaining = len(detail_pool) - n_show
        if remaining > 0:
            col_btn1, col_btn2 = st.columns([1, 3])
            with col_btn1:
                if st.button(f'{n_show} von {len(detail_pool):,} — '
                             f'{remaining:,} weitere', key='detail_expand'):
                    st.session_state['detail_show_n'] = min(
                        n_show + EXPAND_STEP, len(detail_pool),
                    )
                    st.rerun()
            with col_btn2:
                if st.button('Alle anzeigen', key='detail_show_all'):
                    st.session_state['detail_show_n'] = len(detail_pool)
                    st.rerun()

        def _format_trade_option(idx):
            r = detail_pool.loc[idx]
            ret = r.get('effective_return_pct')
            ret_str = f'{ret:+.1f}%' if pd.notna(ret) else '--'
            t_date = r.get('transaction_date')
            date_str = (
                pd.Timestamp(t_date).strftime('%Y-%m-%d')
                if pd.notna(t_date) else '--'
            )
            tid = r.get('trade_id', '')
            tid_short = str(tid)[:8] if pd.notna(tid) else '--'
            return (
                f"{date_str} | {r['name']} | {r['ticker']}"
                f" | {ret_str} | ID:{tid_short}"
            )

        selected_idx = st.selectbox(
            'Trade auswaehlen (fuer Chart-Detail)',
            options=detail_pool.index.tolist(),
            format_func=_format_trade_option,
        )
        trade = detail_pool.loc[selected_idx]

        render_trade_detail(trade, trade_rows, 'perf')


with tab_recent:
    st.caption(
        'Monitoring frischer Trades (offen UND geschlossen) der letzten X Tage '
        'nach transaction_date — unabhaengig vom globalen Horizont-Filter. '
        'Papier-Return = Mark-to-Market gegen den juengsten Close; die Spalte '
        '„Preis-Stand" zeigt, wie aktuell der Preis ist. Eigene Controls; die '
        'globale Suche wirkt mit, der globale Datums-/Horizont-/Min-Score-Filter '
        'NICHT.'
    )

    if 'paper_return_pct' not in trades.columns:
        st.info(
            'Keine Recent-Monitoring-Daten im Master (`paper_return_pct` fehlt). '
            'Bitte Pipeline-Stage `--stage_post_trade_performance` ausführen.'
        )
    else:
        window_opts = list(settings.RECENT_MONITORING_WINDOW_OPTIONS)
        rc1, _rc2 = st.columns([1, 3])
        with rc1:
            recent_window = st.selectbox(
                _scope_label('Zeitfenster (Tage, transaction_date)', 'tab'),
                options=window_opts,
                index=len(window_opts) - 1,  # Default groesstes Fenster
                key='recent_window_days',
                help=_scope_help('tab', 'Eigenes Fenster (Bezug '
                                 'transaction_date); umgeht das globale '
                                 'Sidebar-Zeitfenster, den Horizont-Filter und '
                                 'den Min-Score. Nur die globale Suche wirkt '
                                 'zusaetzlich.'),
            )

        # Basis: globale Suche anwenden (eigene Controls — KEIN Horizont-/
        # globaler Datums-/Min-Score-Filter), dann eigenes Fenster.
        recent_base = trades
        if search_query:
            recent_base = recent_base[_search_mask(recent_base, search_query)]
        recent_pool = build_recent_pool(recent_base, int(recent_window))

        if needs_recompute and not recent_pool.empty:
            with st.spinner(f'Score neu berechnen für {len(recent_pool):,} '
                            f'Recent-Trades …'):
                recent_pool = recompute_subset(
                    recent_pool,
                    excluded_signals,
                    weights_override=weights_override or None,
                    params_override=params_override or None,
                )

        if recent_pool.empty:
            st.info('Keine Trades im gewaehlten Recent-Fenster.')
        else:
            n_open = (int((recent_pool['exit_status'] == 'held').sum())
                      if 'exit_status' in recent_pool.columns else 0)
            n_priced = int(recent_pool['paper_return_pct'].notna().sum())
            m1, m2, m3 = st.columns(3)
            m1.metric('Recent Trades', f'{len(recent_pool):,}')
            m2.metric('davon offen (held)', f'{n_open:,}')
            m3.metric('mit Paper Return', f'{n_priced:,}')

            recent_pool = recent_pool.sort_values(
                'trade_score', ascending=(sort_dir == 'aufsteigend'),
                kind='mergesort', na_position='last',
            )

            DEFAULT_SHOW_R = 25
            EXPAND_STEP_R = 100
            if 'recent_show_n' not in st.session_state:
                st.session_state['recent_show_n'] = DEFAULT_SHOW_R
            n_show = min(st.session_state['recent_show_n'], len(recent_pool))
            top_pool = recent_pool.head(n_show)

            display_cols = [c for c in RECENT_DISPLAY_COLS
                            if c in top_pool.columns]
            _max_score = max(
                int(recent_pool['trade_score'].max()) if len(recent_pool) else 200,
                200,
            )
            recent_display = top_pool[display_cols].copy()
            if 'signals_triggered' in display_cols:
                recent_display['signals_triggered'] = \
                    recent_display['signals_triggered'].map(_primary_only)
            st.dataframe(
                recent_display,
                width='stretch',
                hide_index=True,
                height=_dyn_height(len(top_pool)),
                column_config=_trade_column_config(_max_score),
            )

            remaining = len(recent_pool) - n_show
            if remaining > 0:
                cb1, cb2 = st.columns([1, 3])
                with cb1:
                    if st.button(f'{n_show} von {len(recent_pool):,} — '
                                 f'{remaining:,} weitere', key='recent_expand'):
                        st.session_state['recent_show_n'] = min(
                            n_show + EXPAND_STEP_R, len(recent_pool),
                        )
                        st.rerun()
                with cb2:
                    if st.button('Alle anzeigen', key='recent_show_all'):
                        st.session_state['recent_show_n'] = len(recent_pool)
                        st.rerun()


# --- Score-Analyse: Helfer -------------------------------------------------
_HORIZON_OPTS = list(settings.POST_TRADE_HORIZONS_DAYS)
_HORIZON_DEFAULT = settings.SCORE_CORR_DEFAULT_HORIZON_DAYS
if _HORIZON_DEFAULT not in _HORIZON_OPTS:
    _HORIZON_DEFAULT = _HORIZON_OPTS[-1] if _HORIZON_OPTS else None


def _fmt_metric(value, fmt: str = '{:.3f}') -> str:
    """Metrik-Wert formatieren; NaN/None -> Gedankenstrich."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return '–'
    return fmt.format(value)


def _matured_subset(df: pd.DataFrame, ret_c: str) -> pd.DataFrame:
    """Trades mit nicht-NaN trade_score UND Objective Return des Horizonts."""
    if ret_c not in df.columns or 'trade_score' not in df.columns:
        return df.iloc[0:0]
    return df[df[ret_c].notna() & df['trade_score'].notna()]


with tab_analysis:
    import plotly.graph_objects as go

    st.caption(
        'Validiert den trade_score gegen den vorzeichenbehafteten Objective '
        'Return post_return_{X}d (exit-agnostisch, sell_sign-bereinigt -> '
        'positiv = Trade lag in seiner Richtung richtig, fuer Kauf UND '
        'Verkauf). Umgeht den globalen Horizont-Filter und spiegelt die '
        'globale Suche sowie die Sidebar-Score-Overrides; der globale Datums-/'
        'Min-Score-Filter wirkt NICHT. Kammer-/Partei-Filter wirken auf ALLE '
        'Auswertungen dieses Tabs (Kennzahlen, Scatter, Score-Buckets, '
        'Pro-Signal-Korrelation). Spearman ist die Leitkennzahl '
        '(Rangkorrelation, robust gegen Return-Ausreisser).'
    )
    st.caption(
        'Limitationen: (1) Delisting-Bias (Survivorship) — delistet ein Ticker '
        'vor Tag X, ist der Return NaN und der Trade faellt aus der Stichprobe '
        '-> Verzerrung tendenziell nach oben. (2) Reife-Beschraenkung — Trades '
        'juenger als X Tage haben per Konstruktion keinen X-Tage-Return; die '
        'Stichprobe enthaelt nur „reife" Trades (kein Bias). n wird stets '
        'angezeigt.'
    )

    chambers = sorted(c for c in corr_base['chamber'].dropna().unique()) \
        if 'chamber' in corr_base.columns else []
    parties = sorted(p for p in corr_base['Member_Party'].dropna().unique()) \
        if 'Member_Party' in corr_base.columns else []
    cc1, cc2, cc3, cc4 = st.columns(4)
    with cc1:
        an_horizon = st.selectbox(
            _scope_label('Horizont X (Tage)', 'tab'), _HORIZON_OPTS,
            index=(_HORIZON_OPTS.index(_HORIZON_DEFAULT)
                   if _HORIZON_DEFAULT in _HORIZON_OPTS else 0),
            key='an_horizon',
            help=_scope_help('tab', 'Unabhaengig vom globalen '
                             'Primaer-Horizont der Sidebar.'),
        )
    with cc2:
        n_buckets = st.selectbox(
            'Score-Buckets (gleich breit)',
            list(settings.SCORE_CORR_BUCKET_OPTIONS),
            index=list(settings.SCORE_CORR_BUCKET_OPTIONS).index(
                settings.SCORE_CORR_BUCKET_DEFAULT)
            if settings.SCORE_CORR_BUCKET_DEFAULT
            in settings.SCORE_CORR_BUCKET_OPTIONS else 0,
            key='corr_an_buckets',
        )
    with cc3:
        sel_chambers = st.multiselect('Kammer', chambers, default=chambers,
                                      key='corr_an_chamber')
    with cc4:
        sel_parties = st.multiselect('Partei', parties, default=parties,
                                     key='corr_an_party')
    st.caption('Hinweis: Beim Partei-Filter fallen Trades ohne hinterlegte '
               'Partei aus der Stichprobe.')

    an_base = corr_base
    if chambers and set(sel_chambers) != set(chambers):
        an_base = an_base[an_base['chamber'].isin(sel_chambers)]
    if parties and set(sel_parties) != set(parties):
        an_base = an_base[an_base['Member_Party'].isin(sel_parties)]

    rc = score_corr.return_col(an_horizon)
    mat = _matured_subset(an_base, rc)
    stats = score_corr.correlation_stats(mat['trade_score'], mat[rc]) \
        if not mat.empty else {'spearman': float('nan'),
                               'pearson': float('nan'), 'n': 0}
    n_held = (int((mat['exit_status'] == 'held').sum())
              if 'exit_status' in mat.columns else 0)

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric('Spearman-Rangkorrelation (ρ)', _fmt_metric(stats['spearman']),
               help=help_for('spearman'))
    mc2.metric('Pearson-Korrelation (r)', _fmt_metric(stats['pearson']),
               help=help_for('pearson'))
    mc3.metric('n (Stichprobe)', f'{stats["n"]:,}')
    mc4.metric('davon held', f'{n_held:,}')

    if stats['n'] < 2:
        st.info('Zu wenige auswertbare Trades fuer den gewaehlten Horizont.')
    else:
        max_pts = int(settings.SCORE_CORR_SCATTER_MAX_POINTS)
        plot_df = mat
        sampled = len(plot_df) > max_pts
        if sampled:
            plot_df = plot_df.sample(max_pts, random_state=0)
        # Y-Achse fuer Lesbarkeit auf das 1.–99.-Perzentil klippen (Ausreisser
        # wuerden den Bulk sonst plattdruecken). Die Kennzahlen oben rechnen
        # ueber die VOLLE Stichprobe — das Clipping ist rein visuell.
        ylo = float(mat[rc].quantile(0.01))
        yhi = float(mat[rc].quantile(0.99))
        fig = go.Figure(go.Scattergl(
            x=plot_df['trade_score'], y=plot_df[rc], mode='markers',
            marker=dict(size=4,
                        opacity=float(settings.SCORE_CORR_SCATTER_OPACITY),
                        color='#1f77b4'),
            name='Trades',
        ))
        fig.update_layout(
            xaxis_title='trade_score',
            yaxis_title=f'post_return_{an_horizon}d (%)',
            height=460, margin=dict(l=10, r=10, t=10, b=10),
            showlegend=False,
        )
        if math.isfinite(ylo) and math.isfinite(yhi) and ylo < yhi:
            fig.update_yaxes(range=[ylo, yhi])
        fig.add_hline(y=0, line_width=1, line_dash='dot',
                      line_color='#888')
        st.plotly_chart(fig, width='stretch')
        cap = (f'Scatter: {len(plot_df):,} Punkte'
               + (f' (Sample aus {len(mat):,})' if sampled else '')
               + '. Y-Achse auf 1.–99.-Perzentil geklippt (nur Darstellung).')
        st.caption(cap)

    st.subheader('Score-Buckets')
    if mat.empty:
        st.info('Keine auswertbaren Trades fuer die aktuelle Auswahl.')
    else:
        bk = score_corr.score_bucket_returns(mat, 'trade_score', rc,
                                             int(n_buckets))
        if bk.empty:
            st.info('Keine Buckets bildbar.')
        else:
            fig_b = go.Figure(go.Bar(
                x=bk['bucket'], y=bk['mean_return'],
                text=bk['n'].map(lambda v: f'n={v:,}'),
                textposition='outside',
                customdata=bk[['n', 'median_return']].to_numpy(),
                hovertemplate=('Score %{x}<br>Ø Return %{y:.2f}%<br>'
                               'Median %{customdata[1]:.2f}%<br>'
                               'n=%{customdata[0]:,}<extra></extra>'),
                marker_color='#1f77b4',
            ))
            fig_b.update_layout(
                xaxis_title='trade_score-Bucket (gleich breit)',
                yaxis_title=f'Ø post_return_{an_horizon}d (%)',
                height=400, margin=dict(l=10, r=10, t=10, b=10),
            )
            fig_b.add_hline(y=0, line_width=1, line_dash='dot',
                            line_color='#888')
            st.plotly_chart(fig_b, width='stretch')

    st.subheader('Pro-Signal-Korrelation')
    st.caption(
        'Pro Signal: binaerer „gefeuert"-Indikator gegen den signierten '
        'Return. Werte sind MARGINAL/univariat — Signale ko-okkurrieren, '
        'dies sind also KEINE isolierten Beitraege. swarm_paradox ist ein '
        'Daempfer (negative Korrelation erwartet). Sortiert nach |Spearman|.'
    )
    if mat.empty:
        st.info('Keine auswertbaren Trades fuer die aktuelle Auswahl.')
    else:
        ps = score_corr.per_signal_return_table(mat, rc)
        if ps.empty:
            st.info('Keine getriggerten Signale in der Stichprobe.')
        else:
            st.dataframe(
                ps, width='stretch', hide_index=True,
                height=_dyn_height(len(ps)),
                column_config={
                    'signal': st.column_config.TextColumn('Signal'),
                    'n_fired': st.column_config.NumberColumn(
                        'n gefeuert', format='%d'),
                    'mean_fired': st.column_config.NumberColumn(
                        'Ø Return gefeuert (%)', format='%.2f'),
                    'mean_not': st.column_config.NumberColumn(
                        'Ø Return nicht (%)', format='%.2f'),
                    'delta': st.column_config.NumberColumn(
                        'Δ (%)', format='%.2f'),
                    'spearman': st.column_config.NumberColumn(
                        'Spearman-Rangkorrelation (ρ)', format='%.3f',
                        help=help_for('spearman')),
                    'pearson': st.column_config.NumberColumn(
                        'Pearson-Korrelation (r)', format='%.3f',
                        help=help_for('pearson')),
                },
            )
