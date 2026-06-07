"""UI + Charts fuer das Member-Detail-Popup (Modul 9.x).

Streamlit-Schicht; **pure** Logik/Aggregation liegt in ``app/member_metrics.py``
(unit-getestet). Dieses Modul baut nur Figuren + Widgets und wird aus dem
``@st.dialog`` in ``streamlit_app.py`` (Members-Tab) aufgerufen.

Daten-Erwartung: ``member_df`` = Trades EINES Members (Datum/Suche-gefiltert,
OHNE Horizont-Filter, Score ggf. rekomputiert); ``bench_df`` = alle ANDEREN
Politiker (gleiche Datum/Suche-Filter, kein Recompute noetig — nur post_return).
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config.settings as settings
from app import member_metrics as mm

ROOT = Path(__file__).resolve().parent.parent
HORIZONS = settings.POST_TRADE_HORIZONS_DAYS

# Funktionale Farben (kein Polish — nur Lesbarkeit der Reihen).
_C_MEMBER = '#22c55e'
_C_BENCH = '#64748b'
_C_CF = '#f59e0b'
_C_BUY = '#22c55e'
_C_SELL = '#b91c1c'
_C_MID = '#475569'

_TICKER_LINK_REGEX = r'von\+([^&]+)$'


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

def _google_url(query: str) -> str:
    return 'https://www.google.com/search?q=' + urllib.parse.quote_plus(query)


def _ticker_google_url(ticker) -> str | None:
    if ticker is None or (isinstance(ticker, float) and pd.isna(ticker)):
        return None
    t = str(ticker).strip()
    return _google_url(f'Aktienkurs von {t}') if t else None


def _ticker_link_config(label: str = 'Ticker'):
    return st.column_config.LinkColumn(
        label, display_text=_TICKER_LINK_REGEX, width='small')


def _sel(state) -> dict:
    """Selektion robust aus Return-Objekt ODER session_state-Eintrag ziehen."""
    if state is None:
        return {}
    s = getattr(state, 'selection', None)
    if s is None and isinstance(state, dict):
        s = state.get('selection')
    return s or {}


def _first(series_like) -> object:
    s = series_like.dropna() if hasattr(series_like, 'dropna') else series_like
    return s.iloc[0] if hasattr(s, 'iloc') and len(s) else ''


@st.cache_data(show_spinner=False)
def _load_price_cache():
    from scoring.post_trade_returns import _load_price_cache
    return _load_price_cache(str(ROOT / settings.POST_TRADE_PRICE_CACHE))


@st.cache_data(show_spinner='Lade Kursverläufe …')
def _cached_trajectories(traj_input: pd.DataFrame) -> list[dict]:
    """Trajektorien gecacht. ``traj_input`` ist auf die trajektorie-relevanten
    Spalten reduziert -> Score-Aenderungen busten den Cache nicht."""
    return mm.compute_member_trajectories(
        traj_input, _load_price_cache(), HORIZONS)


_TRAJ_COLS = ['trade_id', 'ticker', 'asset_name', 'transaction_type',
              'post_trade_baseline_price', 'transaction_date',
              'exit_status', 'exit_date', 'post_return_180d']


# ---------------------------------------------------------------------------
# Header + Controls
# ---------------------------------------------------------------------------

def _render_header(member_df: pd.DataFrame, name: str, chamber: str) -> None:
    party = _first(member_df.get('Member_Party', pd.Series(dtype=object)))
    state = _first(member_df.get('Member_State', pd.Series(dtype=object)))
    district = _first(member_df.get('district', pd.Series(dtype=object)))
    ex_officio = _first(member_df.get('Member_ExOfficio', pd.Series(dtype=object)))

    meta = ' · '.join(str(x) for x in [chamber, party, state, district] if x)
    st.subheader(f'🏛️ {name}')
    if meta:
        st.caption(meta + (' · Ex Officio' if str(ex_officio).lower() in
                           ('true', '1', 'yes') else ''))

    c1, c2 = st.columns([3, 1])
    with c1:
        committees_str = _first(member_df.get(
            'Member_Committees_Full', pd.Series(dtype=object)))
        committees = mm.parse_committees_full(committees_str)
        if committees:
            lines = ['**Gremien & Rollen**']
            for c in committees:
                role = c['role'] or 'Mitglied'
                lines.append(f"- **{c['committee']}** — *{role}*")
                for s in c['subcommittees']:
                    srole = s['role'] or 'Mitglied'
                    lines.append(f"    - {s['name']} — *{srole}*")
            st.markdown('\n'.join(lines))
        else:
            st.caption('Keine Gremien-Daten.')
    with c2:
        st.link_button('🔍 Google: Name', _google_url(name),
                       width='stretch')


def _render_controls(member_df: pd.DataFrame, bench_df: pd.DataFrame):
    """Quartal-Range + 180-Toggle + Schwellwert-x. Wirken auf Member UND
    Benchmark identisch -> Vergleichbarkeit. Returns (mview, bview, threshold)."""
    quarters = mm.quarter_options(member_df)
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        if len(quarters) >= 2:
            q_from, q_to = st.select_slider(
                'Quartal-Bereich', options=quarters,
                value=(quarters[0], quarters[-1]), key='md_quarter')
        else:
            q_from = q_to = quarters[0] if quarters else None
            st.caption('Quartal: ' + (q_from or '—'))
    with c2:
        toggle_180 = st.toggle(
            'Nur ≤180d', value=False, key='md_180',
            help='Entfernt offene Positionen und Trades, die erst nach 180 '
                 'Tagen schliessen, aus allen Diagrammen.')
    with c3:
        threshold = st.slider(
            'Schwellwert x (%)', min_value=0.0, max_value=50.0,
            value=float(settings.DETAIL_RETURN_THRESHOLD_PCT), step=1.0,
            key='md_threshold')

    def _apply(df):
        df = mm.filter_by_quarter_range(df, q_from, q_to)
        df = mm.filter_long_trades_out(df, toggle_180, HORIZONS)
        return df

    return _apply(member_df), _apply(bench_df), threshold


# ---------------------------------------------------------------------------
# Aggregat-Diagramme (D1/D2/D5/D6/D7/Z3)
# ---------------------------------------------------------------------------

def _base_layout(fig, height=340, showlegend=False):
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=10, b=10),
        showlegend=showlegend)
    if showlegend:
        fig.update_layout(legend=dict(orientation='h', yanchor='bottom',
                                      y=1.0, x=0))
    return fig


def _fig_d1(mview, threshold):
    above, mid, below = [], [], []
    for h in HORIZONS:
        b = mm.horizon_bucket_shares(mview, h, threshold, HORIZONS)
        above.append((b['above'] or 0) * 100)
        mid.append((b['mid'] or 0) * 100)
        below.append((b['below'] or 0) * 100)
    fig = go.Figure()
    fig.add_bar(x=HORIZONS, y=above, name=f'>+{threshold:.0f}%',
                marker_color=_C_BUY)
    fig.add_bar(x=HORIZONS, y=mid, name=f'±{threshold:.0f}%',
                marker_color=_C_MID)
    fig.add_bar(x=HORIZONS, y=below, name=f'<−{threshold:.0f}%',
                marker_color=_C_SELL)
    fig.update_layout(barmode='stack')
    fig.update_xaxes(title='Horizont (Tage)', type='category')
    fig.update_yaxes(title='Anteil %', range=[0, 100])
    return _base_layout(fig, showlegend=True)


def _fig_d2(mview, bview, cf_view):
    mvals = mm.horizon_mean_returns(mview, HORIZONS)
    bvals = mm.horizon_mean_returns(bview, HORIZONS)
    cvals = (mm.horizon_mean_returns(cf_view, HORIZONS) if not cf_view.empty
             else {h: float('nan') for h in HORIZONS})
    fig = go.Figure()
    fig.add_bar(x=HORIZONS, y=[mvals[h] for h in HORIZONS], name='Member',
                marker_color=_C_MEMBER)
    fig.add_bar(x=HORIZONS, y=[bvals[h] for h in HORIZONS],
                name='Andere Politiker', marker_color=_C_BENCH)
    fig.add_bar(x=HORIZONS, y=[cvals[h] for h in HORIZONS],
                name='Aufsichts-Sektor', marker_color=_C_CF)
    fig.update_layout(barmode='group')
    fig.update_xaxes(title='Horizont (Tage)', type='category')
    fig.update_yaxes(title='Ø paper_return %')
    return _base_layout(fig, showlegend=True)


def _fig_d5(terminal):
    dist = mm.terminal_distribution(terminal, bin_pct=5.0)
    fig = go.Figure()
    if not dist.empty:
        colors = [_C_BUY if m > 0 else _C_SELL for m in dist['bin_mid']]
        fig.add_bar(x=dist['bin_mid'], y=dist['share'] * 100,
                    marker_color=colors)
    fig.update_xaxes(title='paper_return_terminal % (5%-Bins)')
    fig.update_yaxes(title='Anteil %')
    return _base_layout(fig)


def _fig_d6(member_df, terminal):
    sb = mm.score_bins(member_df, terminal, value='mean', bin_pts=5)
    fig = go.Figure()
    if not sb.empty:
        sb = sb.sort_values('bin_mid')
        fig.add_trace(go.Scatter(x=sb['bin_mid'], y=sb['value'],
                                 mode='lines+markers', marker_color=_C_MEMBER))
    fig.update_xaxes(title='trade_score (5-Pkt-Bins)')
    fig.update_yaxes(title='Ø terminal %')
    return _base_layout(fig)


def _fig_d7(member_df, terminal, threshold):
    sb = mm.score_bins(member_df, terminal, value='hitrate',
                       threshold=threshold, bin_pts=5)
    fig = go.Figure()
    if not sb.empty:
        sb = sb.sort_values('bin_mid')
        fig.add_bar(x=sb['bin_mid'], y=sb['value'] * 100, marker_color=_C_CF)
    fig.update_xaxes(title='trade_score (5-Pkt-Bins)')
    fig.update_yaxes(title=f'Anteil > {threshold:.0f}% (%)', range=[0, 100])
    return _base_layout(fig)


def _fig_z3(mview, oversight):
    sc = mm.sector_concentration(mview, oversight)
    fig = go.Figure()
    if not sc.empty:
        sc = sc.iloc[::-1]  # groesster oben im horizontalen Bar
        colors = [_C_CF if ov else _C_BENCH for ov in sc['is_oversight']]
        fig.add_bar(y=sc['sector'], x=sc['share'] * 100, orientation='h',
                    marker_color=colors)
    fig.update_xaxes(title='Anteil Volumen %')
    return _base_layout(fig, height=max(260, 40 * (len(sc) if not sc.empty else 4)))


# ---------------------------------------------------------------------------
# Spaghetti (D3b/D4) + bidirektionales Highlight
# ---------------------------------------------------------------------------

def _spaghetti_figure(trajs: list[dict], highlight) -> go.Figure:
    fig = go.Figure()
    for tr in trajs:
        is_hl = (highlight is not None and tr['trade_id'] == highlight)
        base_color = _C_BUY if tr['direction'] == 'buy' else _C_SELL
        fig.add_trace(go.Scatter(
            x=tr['days'], y=tr['returns'], mode='lines',
            name=f"{tr['ticker']}",
            customdata=[tr['trade_id']] * len(tr['days']),
            line=dict(width=4 if is_hl else 1.3, color=base_color),
            opacity=1.0 if (highlight is None or is_hl) else 0.18,
            showlegend=False,
            hovertemplate=(f"{tr['ticker']}<br>Tag %{{x}}<br>"
                           "%{y:.1f}%<extra></extra>"),
        ))
    for h in HORIZONS:
        fig.add_vline(x=h, line_dash='dash', line_color='gray', opacity=0.35)
    fig.add_hline(y=0, line_color='gray', opacity=0.5)
    # dragmode='select' -> Klick/Box selektiert Punkte (statt zu zoomen)
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10),
                      dragmode='select', showlegend=False)
    fig.update_xaxes(title='Tage seit Transaktion')
    fig.update_yaxes(title='paper_return %')
    return fig


def _render_spaghetti_section(trajs: list[dict], key: str, caption: str) -> None:
    st.caption(caption)
    if not trajs:
        st.info('Keine Trajektorien (keine Preisdaten im Zeitraum).')
        return

    hl_key, list_key, plot_key = f'{key}_hl', f'{key}_list', f'{key}_plot'
    list_df = pd.DataFrame([{
        'trade_id': t['trade_id'],
        'ticker_url': _ticker_google_url(t['ticker']),
        'asset_name': t['asset_name'],
        'paper_return_180': t['paper_return_180'],
    } for t in trajs])

    def _on_list():
        rows = _sel(st.session_state.get(list_key)).get('rows', [])
        if rows:
            st.session_state[hl_key] = list_df.iloc[rows[0]]['trade_id']

    def _on_plot():
        tid = mm.resolve_highlight_from_selection(
            _sel(st.session_state.get(plot_key)))
        if tid is not None:
            st.session_state[hl_key] = tid

    hl = st.session_state.get(hl_key)
    col_plot, col_list = st.columns([3, 2])
    with col_plot:
        st.plotly_chart(
            _spaghetti_figure(trajs, hl), key=plot_key, on_select=_on_plot,
            selection_mode=('points', 'box', 'lasso'), width='stretch')
    with col_list:
        st.dataframe(
            list_df, key=list_key, on_select=_on_list,
            selection_mode='single-row', hide_index=True, height=360,
            column_order=['ticker_url', 'asset_name', 'paper_return_180'],
            column_config={
                'ticker_url': _ticker_link_config(),
                'asset_name': st.column_config.TextColumn('Asset',
                                                          width='medium'),
                'paper_return_180': st.column_config.NumberColumn(
                    'Return 180d %', format='%.1f%%', width='small'),
            })
    if hl:
        st.caption(f'Hervorgehoben: **{hl}** — Klick Linie/Liste, '
                   'Doppelklick im Chart hebt auf.')


# ---------------------------------------------------------------------------
# Timeline (Z1+Z2)
# ---------------------------------------------------------------------------

def _fig_timeline(mview):
    d = mview.copy()
    d['_date'] = pd.to_datetime(d['transaction_date'], errors='coerce')
    d['_ret'] = pd.to_numeric(d.get('post_return_180d'), errors='coerce')
    d['_dir'] = d['transaction_type'].map(mm._direction)
    if 'Vote_Timing_Hit' in d.columns:
        d['_vt'] = d['Vote_Timing_Hit'].fillna(False).astype(bool)
    else:
        d['_vt'] = False
    d = d.dropna(subset=['_date'])

    fig = go.Figure()
    for dir_, color, label in [('buy', _C_BUY, 'Kauf'),
                               ('sell', _C_SELL, 'Verkauf')]:
        sub = d[d['_dir'] == dir_]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub['_date'], y=sub['_ret'], mode='markers', name=label,
            marker=dict(
                color=color,
                size=[13 if v else 7 for v in sub['_vt']],
                symbol=['star' if v else 'circle' for v in sub['_vt']],
                line=dict(width=1, color='white')),
            customdata=sub['ticker'],
            hovertemplate=('%{customdata}<br>%{x|%d.%m.%Y}<br>'
                           '180d: %{y:.1f}%<extra></extra>'),
        ))
    fig.add_hline(y=0, line_color='gray', opacity=0.5)
    fig.update_xaxes(title='Transaktionsdatum')
    fig.update_yaxes(title='paper_return 180d %')
    return _base_layout(fig, height=360, showlegend=True)


# ---------------------------------------------------------------------------
# Red-Flag-Summary
# ---------------------------------------------------------------------------

def _render_red_flags(mview, bview, terminal):
    mvals = mm.horizon_mean_returns(mview, HORIZONS)
    bvals = mm.horizon_mean_returns(bview, HORIZONS)
    s = mm.red_flag_summary(mview, mvals, bvals, terminal, HORIZONS)

    flags = 0
    if s['vote_timing_rate'] >= 0.20:
        flags += 1
    if s['committee_full_rate'] >= 0.20:
        flags += 1
    if pd.notna(s['outperformance']) and s['outperformance'] >= 5.0:
        flags += 1
    level = ['🟢 Niedrig', '🟡 Erhöht', '🟠 Hoch', '🔴 Sehr hoch'][min(flags, 3)]

    st.markdown(f'**Red-Flag-Ampel: {level}**  ({flags}/3 Indikatoren)')
    m1, m2, m3, m4 = st.columns(4)
    m1.metric('Vote-Timing-Quote', f"{s['vote_timing_rate']*100:.0f}%")
    m2.metric('Aufsichts-Sektor', f"{s['committee_full_rate']*100:.0f}%")
    m3.metric('Ø vs. Benchmark',
              f"{s['outperformance']:+.1f}pp" if pd.notna(s['outperformance'])
              else '—')
    m4.metric('Bester Trade',
              f"{s['top_terminal']:+.1f}%" if pd.notna(s['top_terminal'])
              else '—')


# ---------------------------------------------------------------------------
# Top-Trades
# ---------------------------------------------------------------------------

def _render_top_trades(mview, terminal):
    work = mview.copy()
    work['_terminal'] = terminal
    work['ticker_url'] = work['ticker'].map(_ticker_google_url)
    work = work.sort_values('_terminal', ascending=False, na_position='last')
    cols = ['ticker_url', 'asset_name', 'transaction_type', 'transaction_date',
            'trade_score', '_terminal']
    cols = [c for c in cols if c in work.columns]
    st.dataframe(
        work[cols].head(25), hide_index=True, width='stretch',
        column_config={
            'ticker_url': _ticker_link_config(),
            'asset_name': st.column_config.TextColumn('Asset', width='medium'),
            'transaction_type': st.column_config.TextColumn('Typ',
                                                            width='small'),
            'transaction_date': st.column_config.DateColumn(
                'Datum', format='DD.MM.YYYY', width='small'),
            'trade_score': st.column_config.NumberColumn('Score', format='%d',
                                                         width='small'),
            '_terminal': st.column_config.NumberColumn(
                'Terminal %', format='%.1f%%', width='small'),
        })


# ---------------------------------------------------------------------------
# Haupt-Einstieg
# ---------------------------------------------------------------------------

def render_member_detail(member_df: pd.DataFrame, bench_df: pd.DataFrame,
                         name: str, chamber: str) -> None:
    """Vollstaendige Detail-Ansicht eines Members (im Dialog ODER inline)."""
    _render_header(member_df, name, chamber)
    st.divider()
    mview, bview, threshold = _render_controls(member_df, bench_df)

    if mview.empty:
        st.info('Keine Trades im gewählten Zeitraum/Filter.')
        return

    terminal = mm.paper_return_terminal(mview, HORIZONS)
    oversight = mm.member_oversight_sectors(mview)
    cf_view = mview[mm.committee_full_mask(mview)]

    tab_over, tab_perf, tab_traj, tab_time = st.tabs(
        ['Übersicht', 'Performance', 'Verläufe', 'Timing'])

    with tab_over:
        _render_red_flags(mview, bview, terminal)
        st.divider()
        st.markdown('**Sektor-Konzentration** '
                    '(orange = Aufsichts-Sektor des Members)')
        st.plotly_chart(_fig_z3(mview, oversight), width='stretch')
        st.markdown('**Top-Trades** (nach terminalem paper_return)')
        _render_top_trades(mview, terminal)

    with tab_perf:
        ca, cb = st.columns(2)
        with ca:
            st.markdown('**D1 — Treffsicherheit je Horizont**')
            st.plotly_chart(_fig_d1(mview, threshold), width='stretch')
            st.markdown('**D5 — Verteilung terminaler Returns**')
            st.plotly_chart(_fig_d5(terminal), width='stretch')
            st.markdown('**D6 — Score → Ø Return**')
            st.plotly_chart(_fig_d6(mview, terminal), width='stretch')
        with cb:
            st.markdown('**D2 — Ø Return: Member / Andere / Aufsicht**')
            st.plotly_chart(_fig_d2(mview, bview, cf_view), width='stretch')
            st.markdown(f'**D7 — Score → Anteil > {threshold:.0f}%**')
            st.plotly_chart(_fig_d7(mview, terminal, threshold),
                            width='stretch')

    with tab_traj:
        traj_all = _cached_trajectories(
            mview[[c for c in _TRAJ_COLS if c in mview.columns]])
        st.markdown('**D4 — Alle Trade-Verläufe** (Tag 0 → Voll-Close ≤180)')
        _render_spaghetti_section(
            traj_all, 'd4',
            'Klick eine Linie oder Listenzeile zum Hervorheben.')
        st.divider()
        cf_ids = set(cf_view['trade_id']) if not cf_view.empty else set()
        traj_cf = [t for t in traj_all if t['trade_id'] in cf_ids]
        st.markdown('**D3b — Verläufe nur im Aufsichts-Sektor (committee_full)**')
        _render_spaghetti_section(
            traj_cf, 'd3b', 'Nur Trades mit committee_full-Signal.')

    with tab_time:
        st.markdown('**Z1+Z2 — Kalender-Timing** '
                    '(★ = Vote-Timing-Treffer, Farbe = Kauf/Verkauf)')
        st.plotly_chart(_fig_timeline(mview), width='stretch')
