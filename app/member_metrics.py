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


def qualifies_for_horizon(df: pd.DataFrame, h: int,
                          horizons: list[int] | None = None) -> pd.Series:
    """Boolean-Series: Trade zaehlt fuer Horizont ``h`` (Attributierungsregel).

    Vektorisierte Form von :func:`qualifying_horizons` ueber ``df``-Zeilen.
    Basis: ``first_close_offset_days``; NaN (offen) -> qualifiziert ueberall.
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    nh = df['first_close_offset_days'].apply(
        lambda d: hs[-1] if pd.isna(d) else nearest_horizon(float(d), hs))
    return h <= nh


def paper_return_terminal(df: pd.DataFrame,
                          horizons: list[int] | None = None) -> pd.Series:
    """``post_return`` am terminalen Horizont je Trade (-> D5/D6/D7).

    Terminaler Horizont = ``nearest_horizon(first_close_offset_days)``; bei
    offenen Trades (NaN) der groesste Horizont (180d). Liest die passende
    Spalte ``post_return_{h}d`` (bereits richtungsbereinigt). Fehlt die Spalte
    oder ist der Wert NaN (z. B. junge offene Position < 180d), bleibt NaN.
    """
    hs = sorted(horizons or DEFAULT_HORIZONS)
    th = df['first_close_offset_days'].apply(
        lambda d: hs[-1] if pd.isna(d) else nearest_horizon(float(d), hs))
    out = pd.Series(np.nan, index=df.index, dtype='float64')
    for h in hs:
        col = f'post_return_{h}d'
        if col in df.columns:
            mask = th == h
            out.loc[mask] = pd.to_numeric(df.loc[mask, col], errors='coerce')
    return out


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
