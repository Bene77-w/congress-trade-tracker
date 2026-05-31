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
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.score_recompute import available_signals, recompute_subset  # noqa: E402
from app.scoring_presets import (  # noqa: E402
    DEFAULT_PRESET_NAME,
    default_payload,
    delete_preset,
    load_presets,
    save_preset,
)
from config import settings  # noqa: E402


TRADES_CSV = ROOT / 'data' / 'processed' / settings.TRADES_MASTER_COMPLETE_CSV

TRADE_DISPLAY_COLS = [
    'trade_id',
    'transaction_date', 'disclosure_date', 'name', 'chamber', 'Member_Party',
    'asset_name', 'ticker', 'issuer_ticker', 'transaction_type', 'amount',
    'trade_volume_usd', 'trade_amount_max_usd', 'asset_tradingvolume_usd',
    'trade_score', 'committee_match', 'relevant_sectors',
    'signals_triggered', 'trade_score_breakdown', 'trade_warnings',
    'post_return_30d', 'post_return_60d', 'post_return_90d',
    'post_return_120d', 'effective_return_pct', 'exit_status', 'exit_date',
]

TRADE_DEFAULT_COLS = [
    'trade_id',
    'transaction_date', 'name', 'asset_name', 'ticker',
    'transaction_type', 'amount', 'asset_tradingvolume_usd',
    'trade_score', 'committee_match', 'relevant_sectors',
    'signals_triggered', 'effective_return_pct', 'exit_status',
]

ASSET_DISPLAY_COLS = [
    'issuer_ticker', 'asset_name', 'trade_count', 'member_count',
    'score_max', 'score_avg', 'score_total', 'total_volume_usd',
    'avg_return_primary', 'max_return_primary', 'pct_profitable',
    'last_date', 'first_date', 'chambers', 'sectors', 'signals',
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
    page_icon='🏛️',
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
        border-color: #334155 !important;
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

st.title('🏛️ Congress Trade Tracker')
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
              'trade_volume_trigger', 'ex_officio_bonus'):
        if current.get(k) != defaults.get(k):
            out[k] = current.get(k)
    return out


# --- Sidebar -------------------------------------------------------------
with st.sidebar:
    st.header('Filter')

    with st.expander('📅  Datum & Suche', expanded=True):
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
        member_query = st.text_input(
            'Member (Substring)', value='',
        ).strip()
        ticker_query = st.text_input(
            'Ticker (Substring)', value='',
        ).strip().upper()

    with st.expander('🎯  Score & Sortierung', expanded=True):
        sort_dir = st.radio(
            'Sortierrichtung (trade_score)',
            options=['absteigend', 'aufsteigend'],
            index=0,
            horizontal=True,
        )
        min_score = st.slider(
            'Min-Score',
            min_value=0,
            max_value=max(50, max_observed_score),
            value=0,
            step=1,
        )
        primary_horizon = st.selectbox(
            'Primaerer Return-Horizont',
            options=settings.POST_TRADE_HORIZONS_DAYS,
            index=2,  # Default 90d
            help='Bestimmt welcher post_return_{X}d in Asset-/Member-Aggregaten verwendet wird.',
        )

    with st.expander('📊  Spalten', expanded=False):
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

    with st.expander('🚫  Signale ausschließen', expanded=False):
        excluded_signals = st.multiselect(
            'Diese Signale tragen 0 zum Score bei:',
            options=available_signals(),
            default=[],
        )

    with st.expander('⚖️  Score-Gewichte', expanded=False):
        # Preset-Auswahl (ausserhalb der Form: sofortiger Effekt)
        presets = load_presets()
        preset_options = [DEFAULT_PRESET_NAME] + sorted(
            n for n in presets.keys() if n != DEFAULT_PRESET_NAME
        )
        selected_preset = st.selectbox(
            'Preset laden',
            options=preset_options,
            index=0,
            key='preset_select',
        )
        load_clicked = st.button(
            f'Preset „{selected_preset}" laden', width='stretch',
        )
        if load_clicked:
            payload = presets.get(selected_preset, DEFAULTS)
            merged_weights = dict(DEFAULTS['weights'])
            merged_weights.update(payload.get('weights') or {})
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
                'ex_officio_bonus': float(payload.get(
                    'ex_officio_bonus', DEFAULTS['ex_officio_bonus'])),
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

            form_weights: dict[str, float] = {}
            for sig in DEFAULTS['weights'].keys():
                form_weights[sig] = st.number_input(
                    f'{sig}',
                    value=float(state['weights'].get(
                        sig, DEFAULTS['weights'][sig])),
                    step=1.0,
                    format='%.2f',
                    key=f'w_{sig}',
                )

            with st.expander('Erweiterte Parameter'):
                form_paradox = st.slider(
                    'Paradox-Damper (Schwarm-Mult. bei swarm_paradox)',
                    min_value=0.0, max_value=1.0,
                    value=float(state['paradox_damper']),
                    step=0.05,
                    key='p_damper',
                )
                form_vol_enabled = st.checkbox(
                    'Trade-Volume-Signal aktiv',
                    value=bool(state['trade_volume_enabled']),
                    key='p_tv_enabled',
                )
                form_vol_trigger = st.number_input(
                    'Trigger-Floor (USD) — Signal triggert ab > diesem Wert',
                    value=float(state['trade_volume_trigger']),
                    min_value=0.0,
                    step=1_000.0,
                    format='%.2f',
                    key='p_tv_trigger',
                    help=(
                        'Trades mit trade_amount_max_usd <= Trigger-Floor '
                        'lösen kein trade_volume-Signal aus. Default 15000 '
                        '(oberer Quiver-Bucket "$1,001 - $15,000").'
                    ),
                )
                # Tier-Editor: (max_inclusive, score). Letzter Eintrag mit
                # max=None bedeutet Catch-all (float('inf')).
                _state_tiers = state['trade_volume_tiers'] or []
                _tier_rows = []
                for upper, score in _state_tiers:
                    _tier_rows.append({
                        'max_inclusive_usd': (None if upper is None
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
                                'Trades mit trade_amount_max_usd <= dieser '
                                'Schwelle bekommen den Tier-Score. Leer '
                                'lassen für den Catch-all (unendlich).'
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
                form_ex_bonus = st.number_input(
                    'Ex-Officio-Bonus',
                    value=float(state['ex_officio_bonus']),
                    step=1.0,
                    format='%.2f',
                    key='p_ex_bonus',
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
                'ex_officio_bonus': float(form_ex_bonus),
            }

        if save_clicked:
            try:
                save_preset(save_name, _state_payload())
                st.success(f'Preset „{save_name.strip()}" gespeichert.')
            except ValueError as exc:
                st.error(str(exc))


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
weights_override = _diff_weights(st.session_state['scoring']['weights'],
                                 DEFAULTS['weights'])
params_override = _diff_params(st.session_state['scoring'], DEFAULTS)
override_active = bool(weights_override or params_override)
needs_recompute = bool(excluded_signals) or override_active
if needs_recompute:
    with st.spinner(f'Score neu berechnen für {len(prefilt):,} Trades …'):
        prefilt = recompute_subset(
            prefilt,
            excluded_signals,
            weights_override=weights_override or None,
            params_override=params_override or None,
        )

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


def _trade_column_config(score_max_val: int) -> dict:
    cc = st.column_config
    return {
        'trade_id': cc.TextColumn('Hash-ID', width='small'),
        'transaction_date': cc.DateColumn(
            'Transaction', format='DD.MM.YYYY', width='small'),
        'disclosure_date': cc.DateColumn(
            'Disclosure', format='DD.MM.YYYY', width='small'),
        'name': cc.TextColumn('Member', width='medium'),
        'chamber': cc.TextColumn('Chamber', width='small'),
        'Member_Party': cc.TextColumn('Party', width='small'),
        'asset_name': cc.TextColumn('Asset', width='large'),
        'ticker': cc.TextColumn('Ticker', width='small'),
        'issuer_ticker': cc.TextColumn('Issuer', width='small'),
        'transaction_type': cc.TextColumn('Type', width='small'),
        'amount': cc.TextColumn('Amount Range', width='small'),
        'trade_volume_usd': cc.NumberColumn(
            'Trade Vol (USD)', format='$%d', width='medium'),
        'trade_amount_max_usd': cc.NumberColumn(
            'Kapital (USD)', format='$%d', width='medium'),
        'asset_tradingvolume_usd': cc.NumberColumn(
            'Asset Vol (USD)', format='$%d', width='medium'),
        'trade_score': cc.ProgressColumn(
            'Score',
            min_value=0,
            max_value=max(200, score_max_val),
            format='%d',
            width='small',
        ),
        'committee_match': cc.TextColumn('Committee Match', width='large'),
        'relevant_sectors': cc.TextColumn('Relevant Sectors', width='medium'),
        'signals_triggered': cc.TextColumn('Signals', width='large'),
        'trade_score_breakdown': cc.TextColumn(
            'Score Breakdown', width='large'),
        'trade_warnings': cc.TextColumn('Warnings', width='medium'),
        'post_return_30d': cc.NumberColumn(
            'Return 30d %', format='%.1f%%', width='small'),
        'post_return_60d': cc.NumberColumn(
            'Return 60d %', format='%.1f%%', width='small'),
        'post_return_90d': cc.NumberColumn(
            'Return 90d %', format='%.1f%%', width='small'),
        'post_return_120d': cc.NumberColumn(
            'Return 120d %', format='%.1f%%', width='small'),
        'effective_return_pct': cc.NumberColumn(
            'Eff. Return %', format='%.1f%%', width='small'),
        'exit_status': cc.TextColumn('Exit', width='small'),
        'exit_date': cc.DateColumn('Exit-Datum', format='DD.MM.YYYY',
                                   width='small'),
    }


def _asset_column_config(score_max_val: int) -> dict:
    cc = st.column_config
    return {
        'issuer_ticker': cc.TextColumn('Issuer', width='small'),
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
        ),
        'score_avg': cc.NumberColumn(
            'Score Avg', format='%.1f', width='small'),
        'score_total': cc.NumberColumn(
            'Score Σ', format='%d', width='small'),
        'total_volume_usd': cc.NumberColumn(
            'Volume (USD)', format='$%d', width='medium'),
        'last_date': cc.DateColumn(
            'Last', format='DD.MM.YYYY', width='small'),
        'first_date': cc.DateColumn(
            'First', format='DD.MM.YYYY', width='small'),
        'chambers': cc.TextColumn('Chambers', width='small'),
        'sectors': cc.TextColumn('Sectors', width='medium'),
        'signals': cc.TextColumn('Signals', width='large'),
        'avg_return_primary': cc.NumberColumn(
            'Avg Return %', format='%.1f%%', width='small'),
        'max_return_primary': cc.NumberColumn(
            'Max Return %', format='%.1f%%', width='small'),
        'pct_profitable': cc.NumberColumn(
            '% Profitable', format='%.1f%%', width='small'),
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
        ),
        'score_avg': cc.NumberColumn(
            'Score Avg', format='%.1f', width='small'),
        'score_total': cc.NumberColumn(
            'Score Σ', format='%d', width='small'),
        'total_volume_usd': cc.NumberColumn(
            'Volume (USD)', format='$%d', width='medium'),
        'last_date': cc.DateColumn(
            'Last', format='DD.MM.YYYY', width='small'),
        'first_date': cc.DateColumn(
            'First', format='DD.MM.YYYY', width='small'),
        'signals': cc.TextColumn('Signals', width='large'),
        'avg_return_primary': cc.NumberColumn(
            'Avg Return %', format='%.1f%%', width='small'),
        'pct_profitable': cc.NumberColumn(
            '% Profitable', format='%.1f%%', width='small'),
    }


# --- Tabs ----------------------------------------------------------------
tab_trades, tab_assets, tab_members, tab_detail = st.tabs(
    ['Trades', 'Assets', 'Members', 'Post Trade Performance'])

with tab_trades:
    n = len(trade_rows)
    mean_score = float(trade_rows['trade_score'].mean()) if n else 0.0
    max_score = int(trade_rows['trade_score'].max()) if n else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('Trades (gefiltert)', f'{n:,}')
    c2.metric('Mean Score', f'{mean_score:.1f}')
    c3.metric('Max Score', f'{max_score}')
    c4.metric('Signale ausgeschlossen', len(excluded_signals))
    c5.metric('Override aktiv', 'Ja' if override_active else 'Nein')

    display_cols = [c for c in TRADE_DISPLAY_COLS
                    if c in trade_rows.columns and c in trade_cols_sel]
    st.dataframe(
        trade_rows[display_cols],
        width='stretch',
        hide_index=True,
        height=_dyn_height(len(trade_rows)),
        column_config=_trade_column_config(max_score),
    )

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
              f'{int(assets_df["score_max"].max()) if len(assets_df) else 0}')
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
              f'{int(members_df["score_max"].max()) if len(members_df) else 0}')
    m3.metric('Σ Volumen (USD)',
              f'{int(members_df["total_volume_usd"].sum()) if len(members_df) else 0:,}')

    member_display_cols = [c for c in MEMBER_DISPLAY_COLS
                           if c in members_df.columns
                           and c in member_cols_sel]
    m_score_max = (int(members_df['score_max'].max())
                   if len(members_df) else 0)
    st.dataframe(
        members_df[member_display_cols] if member_display_cols else members_df,
        width='stretch',
        hide_index=True,
        height=_dyn_height(len(members_df)),
        column_config=_member_column_config(m_score_max),
    )

with tab_detail:
    st.caption('Drill-Down pro Trade: Preisverlauf + Exit-Marker. '
               'Top-Trades nach Kapital (trade_amount_max_usd) aus '
               'der aktuellen Sidebar-Filterung.')

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
            'trade_amount_max_usd', ascending=False, na_position='last',
        )

        search_query = st.text_input(
            'Suche (Name / Ticker / Hash-ID)',
            value='',
            key='detail_search',
        ).strip()
        if search_query:
            name_mask = detail_pool['name'].astype(str).str.contains(
                search_query, case=False, na=False,
            )
            ticker_mask = detail_pool['ticker'].astype(str).str.contains(
                search_query, case=False, na=False,
            )
            trade_id_mask = pd.Series(False, index=detail_pool.index)
            if 'trade_id' in detail_pool.columns:
                trade_id_mask = detail_pool['trade_id'].astype(str).str.contains(
                    search_query, case=False, na=False,
                )
            detail_pool = detail_pool[name_mask | ticker_mask | trade_id_mask]
            if detail_pool.empty:
                st.info(f'Keine Trades gefunden fuer "{search_query}".')
                st.stop()

        DEFAULT_SHOW = 25
        EXPAND_STEP = 100
        if 'detail_show_n' not in st.session_state:
            st.session_state['detail_show_n'] = DEFAULT_SHOW

        n_show = min(st.session_state['detail_show_n'], len(detail_pool))
        top_pool = detail_pool.head(n_show)

        top_columns = [
            'trade_amount_max_usd', 'name', 'ticker',
            'transaction_date', 'effective_return_pct',
        ]
        if 'trade_id' in top_pool.columns:
            top_columns.insert(0, 'trade_id')
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

        # Chart: Preisverlauf aus Cache
        from scoring.post_trade_returns import _load_price_cache  # noqa: E402
        cache_path = str(ROOT / settings.POST_TRADE_PRICE_CACHE)
        cache = _load_price_cache(cache_path)

        ticker = str(trade['ticker'])
        t_date = pd.to_datetime(trade['transaction_date'])
        window_start = t_date - pd.Timedelta(days=30)
        window_end = (
            t_date
            + pd.Timedelta(days=max(settings.POST_TRADE_HORIZONS_DAYS) + 30)
        )

        series = pd.DataFrame()
        if ticker in cache.index.get_level_values(0):
            tkr_cache = cache.loc[ticker]
            tkr_cache = tkr_cache[
                tkr_cache.index != pd.Timestamp('1900-01-01')
            ]
            series = tkr_cache[
                (tkr_cache.index >= window_start)
                & (tkr_cache.index <= window_end)
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

            # Buy/Sell-Marker
            marker_color = (
                'green' if trade['transaction_type'] == 'Purchase'
                else 'red'
            )
            baseline = trade.get('post_trade_baseline_price')
            fig.add_trace(go.Scatter(
                x=[t_date], y=[baseline],
                mode='markers',
                name=f"Original {trade['transaction_type']}",
                marker=dict(size=15, color=marker_color, symbol='star'),
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

            # Exit-Marker
            exit_date_val = trade.get('exit_date')
            if pd.notna(exit_date_val):
                fig.add_trace(go.Scatter(
                    x=[pd.to_datetime(exit_date_val)],
                    y=[trade.get('exit_price')],
                    mode='markers',
                    name=f"Exit ({trade['exit_status']})",
                    marker=dict(size=15, color='orange', symbol='x'),
                ))

            # Andere Politiker (gleicher issuer_group_key)
            igk = trade.get('issuer_group_key')
            if pd.notna(igk):
                other_trades = trade_rows[
                    (trade_rows['issuer_group_key'] == igk)
                    & (trade_rows['trade_id'] != trade['trade_id'])
                    & (
                        pd.to_datetime(
                            trade_rows['transaction_date'], errors='coerce'
                        ) >= window_start
                    )
                    & (
                        pd.to_datetime(
                            trade_rows['transaction_date'], errors='coerce'
                        ) <= window_end
                    )
                ]
                if not other_trades.empty:
                    hover_text = (
                        other_trades['name']
                        + ' ('
                        + other_trades['transaction_type']
                        + ')'
                    )
                    fig.add_trace(go.Scatter(
                        x=pd.to_datetime(
                            other_trades['transaction_date']
                        ),
                        y=[baseline] * len(other_trades),
                        mode='markers',
                        name='Andere Politiker (gleiches Issuer)',
                        text=hover_text,
                        marker=dict(size=8, color='blue', symbol='diamond'),
                    ))

            asset_name = trade.get('asset_name', ticker)
            fig.update_layout(
                title=f'{ticker} -- {asset_name}',
                xaxis_title='Datum', yaxis_title='Kurs (USD)',
                height=500, hovermode='x unified',
            )
            st.plotly_chart(fig, width='stretch')

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
            ctx_data = {k: trade[k] for k in ctx_available}
            st.dataframe(
                pd.Series(ctx_data, name='Wert'),
                width='stretch',
            )

        with col_right:
            st.subheader('Post-Trade Returns')
            ret_fields = ['post_trade_baseline_price'] + \
                         [f'post_price_{h}d'
                          for h in settings.POST_TRADE_HORIZONS_DAYS] + \
                         [f'post_return_{h}d'
                          for h in settings.POST_TRADE_HORIZONS_DAYS] + \
                         ['exit_status', 'exit_date', 'exit_price',
                          'exit_amount_max_usd', 'effective_return_pct',
                          'opposing_trade_ids']
            ret_available = [f for f in ret_fields if f in trade.index]
            ret_data = {k: trade[k] for k in ret_available}
            st.dataframe(
                pd.Series(ret_data, name='Wert'),
                width='stretch',
            )
