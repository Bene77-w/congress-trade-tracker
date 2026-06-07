"""Pure Logik fuer das Member-Detail-Modul (Modul 9.x).

KEINE Streamlit-Abhaengigkeit -> voll unit-testbar. UI, Charts und Caching
liegen in ``app/member_detail.py``. Horizonte werden vom Aufrufer injiziert
(``config.settings.POST_TRADE_HORIZONS_DAYS``), damit dieses Modul keine
Settings-/Streamlit-Importkette zieht und im Test frei konfigurierbar bleibt.

Begriffe:
  - **Attributierung** (D1/D2/D5-7): basiert auf dem ERSTEN Close
    (``first_close_offset_days``). Ein Trade zaehlt fuer einen Horizont h, wenn
    h <= nearest_horizon(first_close). Mittelpunkte runden (45/75/105/150).
  - **Linienende** (Spaghetti D3b/D4): basiert auf dem VOLLEN Close
    (``exit_date`` bei ``exit_status in {closed_full, delisted}``). Divergiert
    bewusst von der Attributierung (s. Kohaerenz-Test).
  - **paper_return_{h}** = ``post_return_{h}d`` (bereits richtungsbereinigt im
    Master, ``scoring/post_trade_returns.py``); positiv = gutes Timing fuer
    Kauf UND Verkauf.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Fallback, falls der Aufrufer keine Horizonte injiziert. Die App reicht
# settings.POST_TRADE_HORIZONS_DAYS durch.
DEFAULT_HORIZONS: list[int] = [30, 60, 90, 120, 180]

# exit_status-Werte, die einen ECHTEN vollstaendigen Close markieren.
FULLY_CLOSED_STATES = {'closed_full', 'delisted'}

# Sentinel-Datum im Preis-Cache (price_history.parquet) -> herausfiltern.
_PRICE_CACHE_SENTINEL = pd.Timestamp('1900-01-01')


# ---------------------------------------------------------------------------
# Committee-Parser (Member_Committees_Full)
# ---------------------------------------------------------------------------

def _split_name_role(token: str) -> tuple[str, str]:
    """``"Name|Role"`` oder ``"Name"`` -> ``(name, role)`` (beide getrimmt)."""
    name, _, role = token.partition('|')
    return name.strip(), role.strip()


def parse_committees_full(committee_string: Any) -> list[dict]:
    """Parst ``Member_Committees_Full`` in eine verschachtelte Struktur.

    Grammatik (Quell-Roster ``data/mappings/*_member&(sub)committees.csv``,
    Spalte ``Committees`` -> 1:1 in ``Member_Committees_Full``):
      - ``;`` trennt EINTRAEGE (Committees UND Subcommittees gemischt)
      - ``:`` markiert den Beginn der Subcommittee-Liste eines Committees
      - ``|`` trennt ``Name|Rolle`` (Rolle optional)

    Ein ``;``-Eintrag startet ein NEUES Committee genau dann, wenn er ein ``:``
    enthaelt; sonst ist er Subcommittee des zuletzt geoeffneten Committees. Der
    erste Eintrag ist immer ein Committee.

    Bekannte Grenze (verlustbehaftetes Quellformat): Ein Committee OHNE
    Subcommittees, das auf ein anderes Committee folgt, ist im String nicht von
    einem Subcommittee unterscheidbar und wird als Subcommittee angehaengt
    (z. B. 'Permanent Select Committee On Intelligence' bei Josh Gottheimer).
    Der pipeline-eigene ``tags.committee_tag.CommitteeParser`` flacht dagegen
    ALLE ``;``-Subcommittees zu Top-Level-Committees ab -> fuer die Anzeige
    schlechter; daher dieser eigene, verschachtelnde Parser.

    Returns:
        ``[{'committee': str, 'role': str,
            'subcommittees': [{'name': str, 'role': str}, ...]}, ...]``
    """
    if not isinstance(committee_string, str) or not committee_string.strip():
        return []

    committees: list[dict] = []
    current: dict | None = None

    for raw in committee_string.split(';'):
        entry = raw.strip()
        if not entry:
            continue

        if ':' in entry:
            # Neues Committee + (mind.) erstes Subcommittee.
            parts = entry.split(':')
            cname, crole = _split_name_role(parts[0])
            if not cname:
                continue
            current = {'committee': cname, 'role': crole, 'subcommittees': []}
            committees.append(current)
            for sub in parts[1:]:
                sname, srole = _split_name_role(sub.strip())
                if sname:
                    current['subcommittees'].append(
                        {'name': sname, 'role': srole})
        else:
            name, role = _split_name_role(entry)
            if not name:
                continue
            if current is None:
                # Erster Eintrag ohne ':' -> Committee ohne Subcommittees.
                current = {'committee': name, 'role': role,
                           'subcommittees': []}
                committees.append(current)
            else:
                current['subcommittees'].append({'name': name, 'role': role})

    return committees


# ---------------------------------------------------------------------------
# Horizont-Attributierung (Mittelpunkt-Rundung auf first_close)
# ---------------------------------------------------------------------------

def nearest_horizon(close_day: float, horizons: list[int] | None = None) -> int:
    """Naechstgelegener Horizont per Mittelpunkt-Rundung.

    Mittelpunkte aufeinanderfolgender Horizonte sind die Grenzen; bei
    ``[30,60,90,120,180]`` ergibt das 45/75/105/150. ``close_day`` exakt auf
    einem Mittelpunkt rundet auf den hoeheren Horizont (round-half-up).
    Beispiele: 20->30, 45->60, 69->60, 75->90, 76->90, 150->180, 400->180.
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    for i in range(len(hs) - 1):
        mid = (hs[i] + hs[i + 1]) / 2.0
        if close_day < mid:
            return hs[i]
    return hs[-1]


def qualifying_horizons(close_day: Any,
                        horizons: list[int] | None = None) -> list[int]:
    """Alle Horizonte ``<= nearest_horizon(close_day)``.

    Offene/nie geschlossene Trades (``close_day`` NaN/None) qualifizieren fuer
    ALLE Horizonte. Beispiele: 69->[30,60], 76->[30,60,90], 20->[30].
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    if close_day is None or pd.isna(close_day):
        return list(hs)
    nh = nearest_horizon(float(close_day), hs)
    return [h for h in hs if h <= nh]


def _nearest_horizon_array(off: np.ndarray, hs: list[int]) -> np.ndarray:
    """Vektorisierte Mittelpunkt-Rundung (vgl. :func:`nearest_horizon`).

    NaN (offen) -> groesster Horizont. Identische Semantik wie die Skalar-Form
    (round-half-up), aber ueber ein ganzes Array — wichtig fuer den 112k-Zeilen-
    Benchmark (D2), wo ``.apply`` zu langsam waere.
    """
    off = np.asarray(off, dtype='float64')
    mids = np.array([(hs[i] + hs[i + 1]) / 2.0 for i in range(len(hs) - 1)])
    idx = np.clip(np.searchsorted(mids, off, side='right'), 0, len(hs) - 1)
    nh = np.asarray(hs)[idx]
    return np.where(np.isnan(off), hs[-1], nh)


def qualifies_for_horizon(df: pd.DataFrame, h: int,
                          horizons: list[int] | None = None) -> pd.Series:
    """Boolean-Series: Trade zaehlt fuer Horizont ``h`` (Attributierungsregel).

    Vektorisierte Form von :func:`qualifying_horizons` ueber ``df``-Zeilen.
    Basis: ``first_close_offset_days``; NaN (offen) -> qualifiziert ueberall.
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    off = pd.to_numeric(df['first_close_offset_days'], errors='coerce').to_numpy()
    nh = _nearest_horizon_array(off, hs)
    return pd.Series(h <= nh, index=df.index)


def paper_return_terminal(df: pd.DataFrame,
                          horizons: list[int] | None = None) -> pd.Series:
    """``post_return`` am terminalen Horizont je Trade (-> D5/D6/D7).

    Terminaler Horizont = ``nearest_horizon(first_close_offset_days)``; bei
    offenen Trades (NaN) der groesste Horizont (180d). Liest die passende
    Spalte ``post_return_{h}d`` (bereits richtungsbereinigt). Fehlt die Spalte
    oder ist der Wert NaN (z. B. junge offene Position < 180d), bleibt NaN.
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    off = pd.to_numeric(df['first_close_offset_days'], errors='coerce').to_numpy()
    th = _nearest_horizon_array(off, hs)
    out = np.full(len(df), np.nan)
    for h in hs:
        col = f'post_return_{h}d'
        if col in df.columns:
            m = th == h
            out[m] = pd.to_numeric(df[col], errors='coerce').to_numpy()[m]
    return pd.Series(out, index=df.index)


# ---------------------------------------------------------------------------
# Spaghetti-Trajektorien (Linienende auf vollen Close)
# ---------------------------------------------------------------------------

def trajectory_end_offset(exit_status: Any, transaction_date: Any,
                          exit_date: Any, cap: int = 180) -> int:
    """Tage-Offset, bis zu dem eine Spaghetti-Linie laeuft.

    ``closed_full``/``delisted`` mit gueltigem ``exit_date`` -> ``min(cap,
    voller-Close-Offset)``. Alles andere (``held``/``closed_partial``/
    ``not_applicable``/kein ``exit_date``) laeuft bis ``cap`` (= 180), weil die
    Position bis dahin nicht VOLL geschlossen ist.
    """
    if (exit_status in FULLY_CLOSED_STATES
            and pd.notna(exit_date) and pd.notna(transaction_date)):
        off = (pd.Timestamp(exit_date) - pd.Timestamp(transaction_date)).days
        if off >= 0:
            return min(cap, off)
    return cap


def _direction(transaction_type: Any) -> str:
    """Kanonische Richtung (reuse der Pipeline-Logik, kein Streamlit)."""
    from signals.committee_signal import _direction_from_transaction_type
    return _direction_from_transaction_type(transaction_type)


def compute_member_trajectories(df_member: pd.DataFrame, cache: pd.DataFrame,
                                horizons: list[int] | None = None,
                                cap: int | None = None) -> list[dict]:
    """Pro Trade die taegliche paper-Return-Trajektorie Tag 0 -> Linienende.

    Reine Logik (kein Streamlit, kein Caching) -> der UI-Wrapper in
    ``member_detail.py`` legt ein ``@st.cache_data`` darum und laedt den Cache
    intern (nicht als gehashtes Argument).

    Args:
        df_member: Trade-Subset eines Members (Spalten: ``trade_id``,
            ``ticker``, ``asset_name``, ``transaction_type``,
            ``post_trade_baseline_price``, ``transaction_date``,
            ``exit_status``, ``exit_date``, ``post_return_180d``).
        cache: Preis-Cache, MultiIndex ``(ticker, date)`` -> Spalte ``close``
            (``data/cache/price_history.parquet``).
        horizons: Return-Horizonte (Default 30/60/90/120/180).
        cap: Max-Offset in Tagen (Default = groesster Horizont).

    Returns:
        Liste von Dicts je Trade mit Trajektorie:
        ``{'trade_id','ticker','asset_name','direction','paper_return_180',
           'end_offset','days':[int,...],'returns':[float,...]}``.
        Trades ohne Preisdaten/Baseline werden uebersprungen.
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    cap = cap if cap is not None else hs[-1]

    out: list[dict] = []
    if len(cache.index) == 0:
        return out
    tickers_set = set(cache.index.get_level_values(0).unique())

    for _, row in df_member.iterrows():
        ticker = str(row.get('ticker'))
        baseline = pd.to_numeric(row.get('post_trade_baseline_price'),
                                 errors='coerce')
        t_date = pd.to_datetime(row.get('transaction_date'), errors='coerce')
        if (ticker not in tickers_set or pd.isna(t_date)
                or pd.isna(baseline) or baseline <= 0):
            continue

        end_off = trajectory_end_offset(
            row.get('exit_status'), t_date, row.get('exit_date'), cap)
        end_date = t_date + pd.Timedelta(days=end_off)

        sign = -1.0 if _direction(row.get('transaction_type')) == 'sell' else 1.0

        tkr = cache.loc[ticker]
        tkr = tkr[tkr.index != _PRICE_CACHE_SENTINEL]
        seg = tkr[(tkr.index >= t_date) & (tkr.index <= end_date)]
        if seg.empty:
            continue

        days = [int(d) for d in (seg.index - t_date).days]
        rets = ((seg['close'].to_numpy(dtype='float64') / float(baseline) - 1.0)
                * 100.0 * sign)

        out.append({
            'trade_id': row.get('trade_id'),
            'ticker': ticker,
            'asset_name': row.get('asset_name'),
            'direction': _direction(row.get('transaction_type')),
            'paper_return_180': pd.to_numeric(
                row.get('post_return_180d'), errors='coerce'),
            'end_offset': end_off,
            'days': days,
            'returns': [float(r) for r in rets],
        })

    return out


# ---------------------------------------------------------------------------
# Sektoren / committee_full (Quellen-Entscheidung: Aufsicht aus Insider_Sectors,
# committee_full-Gruppe strukturell aus signals_triggered)
# ---------------------------------------------------------------------------

def parse_insider_sectors(value: Any) -> list[dict]:
    """``Insider_Sectors``-Zelle (JSON-Liste von Dicts) defensiv parsen."""
    from signals.committee_signal import parse_relevant_sectors
    return parse_relevant_sectors(value)


def member_oversight_sectors(df_member: pd.DataFrame) -> set[str]:
    """Menge der Aufsichts-Sektoren des Members (aus ``Insider_Sectors``).

    Insider_Sectors ist pro Member konstant; ``unique()`` ist nur defensiv.
    """
    sectors: set[str] = set()
    if 'Insider_Sectors' not in df_member.columns:
        return sectors
    for val in df_member['Insider_Sectors'].dropna().unique():
        for d in parse_insider_sectors(val):
            s = d.get('sector')
            if s:
                sectors.add(str(s))
    return sectors


def committee_full_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean-Series: Trade hat das ``committee_full``-Signal gefeuert.

    Strukturell aus ``signals_triggered`` (``;``-separiert) — bewusst NICHT aus
    dem live-rekomputierten Score, damit die Gruppe nicht verschwindet, wenn der
    User das Signal vom Score ausschliesst.
    """
    if 'signals_triggered' not in df.columns:
        return pd.Series(False, index=df.index)
    return (df['signals_triggered'].astype('string').fillna('')
            .str.contains('committee_full', regex=False))


# ---------------------------------------------------------------------------
# Horizont-Aggregate (D1/D2) — gated per Attributierung (qualifies_for_horizon)
# ---------------------------------------------------------------------------

def horizon_mean_returns(df: pd.DataFrame,
                         horizons: list[int] | None = None) -> dict[int, float]:
    """Ø ``post_return`` je Horizont, gated per :func:`qualifies_for_horizon`.

    Identische Gating-Logik fuer Member, Benchmark UND committee_full-Gruppe ->
    die D2-Balken sind vergleichbar (ein bei Tag 40 geschlossener Trade faellt
    bei h>=60 aus ALLEN drei Reihen gleich heraus).
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    out: dict[int, float] = {}
    for h in hs:
        col = f'post_return_{h}d'
        if col not in df.columns:
            out[h] = float('nan')
            continue
        mask = qualifies_for_horizon(df, h, hs)
        vals = pd.to_numeric(df.loc[mask, col], errors='coerce').dropna()
        out[h] = float(vals.mean()) if len(vals) else float('nan')
    return out


def horizon_bucket_shares(df: pd.DataFrame, h: int, threshold: float,
                          horizons: list[int] | None = None) -> dict:
    """Anteile in 3 Buckets bei Horizont h (>+x / -x..+x / <-x), gated.

    Returns ``{'above','mid','below','n'}`` (Anteile 0..1, ``n`` = Trades im
    Horizont). Schwellwert ``threshold`` = x (Prozent).
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    col = f'post_return_{h}d'
    if col not in df.columns:
        return {'above': float('nan'), 'mid': float('nan'),
                'below': float('nan'), 'n': 0}
    mask = qualifies_for_horizon(df, h, hs)
    vals = pd.to_numeric(df.loc[mask, col], errors='coerce').dropna()
    n = len(vals)
    if n == 0:
        return {'above': 0.0, 'mid': 0.0, 'below': 0.0, 'n': 0}
    above = float((vals > threshold).mean())
    below = float((vals < -threshold).mean())
    return {'above': above, 'mid': 1.0 - above - below, 'below': below, 'n': n}


# ---------------------------------------------------------------------------
# Verteilungen / Score-Beziehungen (D5/D6/D7)
# ---------------------------------------------------------------------------

def _bin_edges(vals: pd.Series, width: float) -> np.ndarray:
    lo = np.floor(float(vals.min()) / width) * width
    hi = np.ceil(float(vals.max()) / width) * width
    if hi <= lo:
        hi = lo + width
    return np.arange(lo, hi + width, width)


def terminal_distribution(terminal: pd.Series,
                          bin_pct: float = 5.0) -> pd.DataFrame:
    """Verteilung von ``paper_return_terminal`` in ``bin_pct``-Bins (D5).

    Returns df mit Spalten ``bin_mid``, ``count``, ``share`` (Summe share = 1).
    """
    vals = pd.to_numeric(terminal, errors='coerce').dropna()
    if vals.empty:
        return pd.DataFrame(columns=['bin_mid', 'count', 'share'])
    edges = _bin_edges(vals, bin_pct)
    cats = pd.cut(vals, bins=edges, include_lowest=True)
    counts = cats.value_counts().sort_index()
    total = int(counts.sum())
    return pd.DataFrame({
        'bin_mid': [iv.mid for iv in counts.index],
        'count': counts.to_numpy(),
        'share': counts.to_numpy() / total if total else counts.to_numpy(),
    })


def score_bins(df: pd.DataFrame, terminal: pd.Series, value: str = 'mean',
               threshold: float | None = None,
               bin_pts: int = 5) -> pd.DataFrame:
    """Gruppiert nach ``trade_score``-Bins (``bin_pts``-weit).

    ``value='mean'``  -> Ø ``terminal`` je Bin (D6).
    ``value='hitrate'`` -> Anteil ``terminal > threshold`` je Bin (D7).
    Returns df mit ``bin_mid``, ``value``, ``n``.
    """
    if 'trade_score' not in df.columns or df.empty:
        return pd.DataFrame(columns=['bin_mid', 'value', 'n'])
    work = pd.DataFrame({
        'score': pd.to_numeric(df['trade_score'], errors='coerce'),
        'term': pd.to_numeric(terminal, errors='coerce'),
    }).dropna(subset=['score'])
    if work.empty:
        return pd.DataFrame(columns=['bin_mid', 'value', 'n'])
    edges = _bin_edges(work['score'], bin_pts)
    work['bin'] = pd.cut(work['score'], bins=edges, include_lowest=True)
    rows = []
    for b, g in work.groupby('bin', observed=True):
        t = g['term'].dropna()
        if value == 'hitrate':
            v = float((t > (threshold or 0.0)).mean()) if len(t) else float('nan')
        else:
            v = float(t.mean()) if len(t) else float('nan')
        rows.append({'bin_mid': float(b.mid), 'value': v, 'n': int(len(g))})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sektor-Konzentration (Z3)
# ---------------------------------------------------------------------------

def sector_concentration(df: pd.DataFrame,
                         oversight: set[str] | None = None) -> pd.DataFrame:
    """Volumen/Anteil je ``sector_yf``; ``oversight`` markiert Aufsichts-Sektoren.

    Returns df ``sector``, ``volume_usd``, ``share``, ``is_oversight`` (Volumen
    absteigend). ``share`` summiert sich zu 1.
    """
    cols = ['sector', 'volume_usd', 'share', 'is_oversight']
    if 'sector_yf' not in df.columns or df.empty:
        return pd.DataFrame(columns=cols)
    vol = (pd.to_numeric(df['trade_volume_usd'], errors='coerce').fillna(0.0)
           if 'trade_volume_usd' in df.columns
           else pd.Series(1.0, index=df.index))
    work = pd.DataFrame({'sector': df['sector_yf'].astype('string'), 'vol': vol})
    work = work[work['sector'].notna() & (work['sector'].str.len() > 0)]
    if work.empty:
        return pd.DataFrame(columns=cols)
    g = work.groupby('sector')['vol'].sum().sort_values(ascending=False)
    total = float(g.sum())
    ov = oversight or set()
    return pd.DataFrame({
        'sector': list(g.index),
        'volume_usd': g.to_numpy(),
        'share': g.to_numpy() / total if total else g.to_numpy(),
        'is_oversight': [str(s) in ov for s in g.index],
    })


# ---------------------------------------------------------------------------
# Quartal-Filter + 180-Toggle (Popup-Controls)
# ---------------------------------------------------------------------------

def quarter_options(df: pd.DataFrame,
                    date_col: str = 'transaction_date') -> list[str]:
    """Aufsteigende Liste vorkommender Quartale als ``'YYYYQn'``."""
    d = pd.to_datetime(df.get(date_col), errors='coerce').dropna()
    if d.empty:
        return []
    periods = sorted(pd.PeriodIndex(d, freq='Q').unique())
    return [f'{p.year}Q{p.quarter}' for p in periods]


def filter_by_quarter_range(df: pd.DataFrame, q_from: str | None,
                            q_to: str | None,
                            date_col: str = 'transaction_date') -> pd.DataFrame:
    """Filtert auf das (inklusive) Quartalsintervall ``[q_from, q_to]``."""
    if not q_from or not q_to or date_col not in df.columns:
        return df
    p = pd.PeriodIndex(pd.to_datetime(df[date_col], errors='coerce'), freq='Q')
    pf, pt = pd.Period(q_from, 'Q'), pd.Period(q_to, 'Q')
    if pf > pt:
        pf, pt = pt, pf
    mask = np.asarray((p >= pf) & (p <= pt))
    return df[mask]


def filter_long_trades_out(df: pd.DataFrame, enabled: bool,
                           horizons: list[int] | None = None) -> pd.DataFrame:
    """180-Toggle: bei ``enabled`` nur Trades mit erstem Close <= max-Horizont.

    Entfernt offene Positionen (NaN) und solche, die erst nach 180d schliessen.
    """
    if not enabled or 'first_close_offset_days' not in df.columns:
        return df
    cap = sorted(horizons or DEFAULT_HORIZONS)[-1]
    off = df['first_close_offset_days']
    return df[off.notna() & (off <= cap)]


# ---------------------------------------------------------------------------
# Selektions-Aufloesung (PURE — pinnt den im Spike gefundenen customdata-Bug)
# ---------------------------------------------------------------------------

def resolve_highlight_from_selection(selection: Any) -> Any:
    """trade_id (customdata) des ersten selektierten Punkts einer plotly-Auswahl.

    ``customdata`` ist bei 1D-Eingabe ein Skalar, bei 2D eine Liste -> beides
    behandeln. Returns ``None`` ohne Auswahl. Schema s. Streamlit-Doku
    (``selection.points[i].customdata``).
    """
    if not isinstance(selection, dict):
        return None
    pts = selection.get('points') or []
    if not pts:
        return None
    cd = pts[0].get('customdata')
    if isinstance(cd, (list, tuple)):
        return cd[0] if cd else None
    return cd


# ---------------------------------------------------------------------------
# Red-Flag-Summary (Phase 4)
# ---------------------------------------------------------------------------

def red_flag_summary(df_member: pd.DataFrame, member_means: dict[int, float],
                     bench_means: dict[int, float], terminal: pd.Series,
                     horizons: list[int] | None = None) -> dict:
    """Aggregierte Kennzahlen fuer die Red-Flag-Ampel.

    Keys: ``n``, ``vote_timing_rate`` (Anteil Vote_Timing_Hit),
    ``committee_full_rate`` (Anteil Aufsichts-Sektor-Trades),
    ``outperformance`` (Ø member-bench ueber Horizonte), ``top_terminal``.
    """
    n = int(len(df_member))
    vt = 0.0
    if n and 'Vote_Timing_Hit' in df_member.columns:
        vt = float(df_member['Vote_Timing_Hit'].fillna(False).astype(bool).mean())
    cf = float(committee_full_mask(df_member).mean()) if n else 0.0
    hs = sorted(horizons or DEFAULT_HORIZONS)
    diffs = [member_means[h] - bench_means[h] for h in hs
             if h in member_means and h in bench_means
             and pd.notna(member_means.get(h)) and pd.notna(bench_means.get(h))]
    outperf = float(np.mean(diffs)) if diffs else float('nan')
    term = pd.to_numeric(terminal, errors='coerce').dropna()
    top = float(term.max()) if len(term) else float('nan')
    return {'n': n, 'vote_timing_rate': vt, 'committee_full_rate': cf,
            'outperformance': outperf, 'top_terminal': top}
