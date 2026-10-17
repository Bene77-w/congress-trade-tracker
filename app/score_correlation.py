"""Score-Korrelations-Helfer fuer das Streamlit-Dashboard (Modul 9.x).

Reine pandas-Funktionen (ohne Streamlit/Plotly), damit die Analyse-Logik
testbar bleibt. Sie messen, wie gut der ``trade_score`` den vorzeichen-
behafteten Objective Return ``post_return_{X}d`` vorhersagt (exit-agnostisch,
bereits ``sell_sign``-bereinigt -> positiv = Trade lag in seiner eigenen
Richtung richtig, fuer Kauf UND Verkauf).

Vor jeder Auswertung bereinigt :func:`analysis_population` den Master
(Dubletten, Exchange). Spearman wird als Pearson auf Raengen berechnet (``a.rank().corr(b.rank())``),
weil scipy im Projekt-venv nicht installiert ist und
``pandas.Series.corr(method='spearman')`` scipy benoetigt. Das Ergebnis ist
identisch zur scipy-Spearman-Korrelation inkl. Tie-Korrektur (Average-Ranks).
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

from config import settings
from scoring.abnormal_returns import clustered_bootstrap_diff


# trade_score_breakdown-Token des Ex-Officio-Bonus: ``ex_officio:+15``.
_EX_OFFICIO_RE = re.compile(r'ex_officio:\+?(-?\d+)')


def return_col(horizon_days: int) -> str:
    """Master-Spaltenname des Objective Return fuer Horizont X (Tage)."""
    return f'post_return_{int(horizon_days)}d'


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman-Rangkorrelation ohne scipy (Pearson auf Average-Raengen)."""
    return a.rank().corr(b.rank())


def correlation_stats(scores: pd.Series, returns: pd.Series) -> dict:
    """Spearman/Pearson/n fuer Score vs. Return (paarweiser NaN-Drop).

    Liefert ``{'spearman': float, 'pearson': float, 'n': int}``. Bei n < 2
    oder konstanter Reihe sind die Koeffizienten NaN (corr liefert dann NaN).
    """
    work = pd.DataFrame({
        's': pd.to_numeric(scores, errors='coerce'),
        'r': pd.to_numeric(returns, errors='coerce'),
    }).dropna()
    n = len(work)
    if n < 2:
        return {'spearman': float('nan'), 'pearson': float('nan'), 'n': n}
    return {
        'spearman': _spearman(work['s'], work['r']),
        'pearson': work['s'].corr(work['r']),
        'n': n,
    }


def score_bucket_returns(df: pd.DataFrame, score_col: str,
                         ret_col: str) -> pd.DataFrame:
    """Mittlerer/median Return je Score-Bucket mit Sonder-Bucket ``0``.

    Score ``0`` (Hard-Gate: kein Primaersignal -> ``trade_score = 0``) bildet
    ein **eigenes** Bucket, damit unverdaechtige Trades nicht mit gescorten
    Trades vermischt werden. Alle Scores ``> 0`` werden in **feste 5er-Schritte**
    gruppiert: ``1–5``, ``6–10``, ``11–15`` usw. (Intervall ``((k-1)*5, k*5]``).
    Leere Buckets bis zum Maximum bleiben als Balken erhalten
    (``mean/median = NaN``). Liefert DataFrame mit Spalten ``[bucket, n,
    mean_return, median_return, score_min, score_max]`` in aufsteigender
    Score-Reihenfolge; ``score_min/score_max`` sind die tatsaechlichen
    Daten-Werte je Bucket (fuer den Hover).
    """
    cols = ['bucket', 'n', 'mean_return', 'median_return',
            'score_min', 'score_max']
    work = pd.DataFrame({
        'score': pd.to_numeric(df[score_col], errors='coerce'),
        'ret': pd.to_numeric(df[ret_col], errors='coerce'),
    }).dropna()
    if work.empty:
        return pd.DataFrame(columns=cols)

    def _label(s: float) -> str:
        if s <= 0:
            return '0'
        k = int(math.ceil(s / 5.0))          # 0<s<=5 -> 1, 5<s<=10 -> 2, ...
        return f'{(k - 1) * 5 + 1}–{k * 5}'

    hi = float(work['score'].max())
    kmax = int(math.ceil(hi / 5.0)) if hi > 0 else 0
    # Feste, lueckenlose Kategorienreihe -> leere Buckets bleiben als Balken.
    cats = ['0'] + [f'{(k - 1) * 5 + 1}–{k * 5}' for k in range(1, kmax + 1)]

    work['bucket'] = pd.Categorical(
        work['score'].map(_label), categories=cats, ordered=True)
    grp = work.groupby('bucket', observed=False)

    out = grp.agg(
        n=('ret', 'size'),
        mean_return=('ret', 'mean'),
        median_return=('ret', 'median'),
        score_min=('score', 'min'),
        score_max=('score', 'max'),
    ).reset_index()
    out['bucket'] = out['bucket'].astype(str)
    return out[cols]


def _ex_officio_bonus(breakdown: object) -> float:
    """Liest den Ex-Officio-Bonuswert aus einem trade_score_breakdown-String.

    Format: ``...;ex_officio:+15``. Liefert 0.0, wenn nicht vorhanden/parsebar.
    """
    if breakdown is None:
        return 0.0
    try:
        if pd.isna(breakdown):
            return 0.0
    except (TypeError, ValueError):
        pass
    m = _EX_OFFICIO_RE.search(str(breakdown))
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return 0.0


def analysis_population(df: pd.DataFrame) -> pd.DataFrame:
    """Bereinigt den Master fuer jede Rendite-Auswertung.

    - ``ANALYSIS_DEDUP``: ``'trade_id'`` behaelt je ``trade_id`` die Zeile mit
      der fruehesten Offenlegung (Wiederholungen aus spaeteren Uploads und
      Amendments unterscheiden sich nur in Metadaten oder ``disclosure_date``),
      ``'full_row'`` entfernt nur vollstaendig identische Zeilen, ``'none'``
      nichts.
    - ``ANALYSIS_EXCLUDE_EXCHANGE``: Exchange-Trades haben keine Richtung; ihr
      Vorzeichen +1 wuerde sie als Kauf werten.
    """
    out = df
    mode = getattr(settings, 'ANALYSIS_DEDUP', 'trade_id')
    if mode == 'full_row':
        out = out.drop_duplicates()
    elif mode == 'trade_id' and 'trade_id' in out.columns:
        if 'disclosure_date' in out.columns:
            order = pd.to_datetime(out['disclosure_date'], errors='coerce')
            out = out.loc[order.sort_values(kind='stable').index]
        out = out.drop_duplicates(subset='trade_id').sort_index()
    if (getattr(settings, 'ANALYSIS_EXCLUDE_EXCHANGE', True)
            and 'transaction_type' in out.columns):
        tt = out['transaction_type'].astype('string').str.strip().str.lower()
        out = out[~tt.fillna('').str.startswith('exchange')]
    return out


#: Signale, die nur feuern koennen, wenn der Trade einer Branche zugeordnet
#: ist und das Mitglied Ausschuesse hat. Ihre Vergleichsgruppe ist auf solche
#: Trades beschraenkt, sonst misst die Differenz die Datenabdeckung.
COMMITTEE_SIGNALS = frozenset({'committee', 'committee_full',
                               'committee_ex_officio'})


def committee_eligible_mask(df: pd.DataFrame) -> pd.Series:
    """True, wo ein Committee-Signal ueberhaupt feuern koennte."""
    ok = pd.Series(True, index=df.index)
    for col in ('sector_yf', 'industry_yf', 'Member_Committees_Full'):
        if col in df.columns:
            v = df[col].astype('string').str.strip()
            ok &= v.notna() & (v != '')
    if 'asset_class_excluded' in df.columns:
        ex = df['asset_class_excluded']
        ok &= ~ex.map(lambda x: str(x).strip().lower() in ('true', '1', 'yes'))
    return ok


def _hit_rate(values: pd.Series) -> float:
    v = values.dropna()
    return float((v > 0).mean()) if len(v) else float('nan')


def per_signal_return_table(df: pd.DataFrame, ret_col: str,
                            signals_col: str = 'signals_triggered',
                            breakdown_col: str = 'trade_score_breakdown',
                            cluster_col: str | None = None,
                            n_iter: int | None = None,
                            ) -> pd.DataFrame:
    """Pro Signal: Return mit gegen ohne Signal.

    Marginal/univariat — Signale ko-okkurrieren, dies sind also KEINE isolierten
    Beitraege. Signal-Universum datengetrieben aus den ``signals_triggered``-
    Tokens. ``committee_ex_officio`` steht dort seit dem Ex-Officio-Signal
    selbst; nur aeltere Master ohne dieses Token leiten es aus dem
    Breakdown-Token ``ex_officio:+N`` (N>0) ab. ``swarm_paradox`` ist ein
    Daempfer (negative Korrelation erwartet — kein Bug).

    Fuer ``COMMITTEE_SIGNALS`` zaehlt als Vergleichsgruppe nur, wer das Signal
    haette ausloesen koennen (:func:`committee_eligible_mask`).

    Leitkennzahl ist ``delta`` (Ø gefeuert − Ø nicht, in Prozentpunkten) mit
    Trefferquote (Anteil Return > 0). Mit ``cluster_col`` (z. B.
    ``bio_guide_id``) kommen 95 %-CI und p aus einem member-geclusterten
    Bootstrap dazu — ohne Clustering waere die Unsicherheit stark
    unterschaetzt, weil einzelne Mitglieder tausende Trades stellen.
    Spearman/Pearson auf dem 0/1-Indikator bleiben zur Rueckverfolgung.

    Liefert DataFrame ``[signal, n_fired, n_compare, mean_fired, mean_not,
    delta, ci_lo, ci_hi, p, hit_fired, hit_not, spearman, pearson]``,
    absteigend nach |delta|.
    """
    cols = ['signal', 'n_fired', 'n_compare', 'mean_fired', 'mean_not',
            'delta', 'ci_lo', 'ci_hi', 'p', 'hit_fired', 'hit_not',
            'spearman', 'pearson']
    if df.empty or signals_col not in df.columns:
        return pd.DataFrame(columns=cols)

    ret = pd.to_numeric(df[ret_col], errors='coerce')

    # Indikatoren aus signals_triggered (datengetrieben, one-hot 0/1).
    indicators: dict[str, pd.Series] = {}
    dummies = df[signals_col].fillna('').astype(str).str.get_dummies(sep=';')
    for token in dummies.columns:
        if token:
            indicators[token] = dummies[token]

    # Rueckfall fuer aeltere Master: Pseudo-Signal aus dem Breakdown.
    if ('committee_ex_officio' not in indicators
            and breakdown_col in df.columns):
        ex_bonus = df[breakdown_col].map(_ex_officio_bonus)
        if (ex_bonus > 0).any():
            indicators['committee_ex_officio'] = (ex_bonus > 0).astype(int)

    eligible = committee_eligible_mask(df)
    iters = int(n_iter if n_iter is not None
                else getattr(settings, 'SCORE_CORR_BOOTSTRAP_ITERS', 2000))
    seed = int(getattr(settings, 'AR_BOOTSTRAP_SEED', 0))
    nan = float('nan')

    rows = []
    for sig, ind in indicators.items():
        # Gefeuerte Trades gehoeren immer dazu, auch wenn die Eignungsmaske
        # sie (z. B. per asset_class_excluded) ausschliessen wuerde.
        scope = (eligible | (ind == 1)) if sig in COMMITTEE_SIGNALS else \
            pd.Series(True, index=df.index)
        pair = pd.DataFrame({'ind': ind.astype(float), 'ret': ret})[scope]
        pair = pair.dropna()
        fired = pair.loc[pair['ind'] == 1, 'ret']
        not_fired = pair.loc[pair['ind'] == 0, 'ret']
        mean_fired = fired.mean() if len(fired) else nan
        mean_not = not_fired.mean() if len(not_fired) else nan
        # Korrelation nur sinnvoll, wenn beide Gruppen (gefeuert/nicht) vorkommen.
        both = pair['ind'].nunique() > 1 and len(pair) >= 2
        spearman = _spearman(pair['ind'], pair['ret']) if both else nan
        pearson = pair['ind'].corr(pair['ret']) if both else nan
        ci_lo = ci_hi = p = nan
        if both and cluster_col and cluster_col in df.columns:
            cl = df.loc[pair.index, cluster_col].fillna('').astype(str).values
            d = clustered_bootstrap_diff(pair['ret'].values, cl,
                                         (pair['ind'] == 1).values,
                                         n_iter=iters, seed=seed)
            ci_lo, ci_hi, p = d['ci_lo'], d['ci_hi'], d['p']
        rows.append({
            'signal': sig,
            'n_fired': int(len(fired)),
            'n_compare': int(len(not_fired)),
            'mean_fired': mean_fired,
            'mean_not': mean_not,
            'delta': mean_fired - mean_not,
            'ci_lo': ci_lo, 'ci_hi': ci_hi, 'p': p,
            'hit_fired': _hit_rate(fired),
            'hit_not': _hit_rate(not_fired),
            'spearman': spearman,
            'pearson': pearson,
        })

    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out
    return out.sort_values(
        'delta', key=lambda s: s.abs(),
        ascending=False, na_position='last',
    ).reset_index(drop=True)
