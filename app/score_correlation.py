"""Score-Korrelations-Helfer fuer das Streamlit-Dashboard (Modul 9.x).

Reine pandas-Funktionen (ohne Streamlit/Plotly), damit die Analyse-Logik
testbar bleibt. Sie messen, wie gut der ``trade_score`` den vorzeichen-
behafteten Objective Return ``post_return_{X}d`` vorhersagt (exit-agnostisch,
bereits ``sell_sign``-bereinigt -> positiv = Trade lag in seiner eigenen
Richtung richtig, fuer Kauf UND Verkauf).

Spearman wird als Pearson auf Raengen berechnet (``a.rank().corr(b.rank())``),
weil scipy im Projekt-venv nicht installiert ist und
``pandas.Series.corr(method='spearman')`` scipy benoetigt. Das Ergebnis ist
identisch zur scipy-Spearman-Korrelation inkl. Tie-Korrektur (Average-Ranks).
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd


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


def score_bucket_returns(df: pd.DataFrame, score_col: str, ret_col: str,
                         n_buckets: int) -> pd.DataFrame:
    """Mittlerer/median Return je gleich breitem Score-Intervall.

    Teilt die Score-Spanne ``[min, max]`` in ``n_buckets`` **gleich breite,
    ganzzahlige** Intervalle (``pd.cut`` — bewusst KEINE Quantile/``qcut``):
    die Bucket-Anzahl entspricht damit immer der Auswahl und die Intervalle
    sind gleich breit, unabhaengig davon, wie viele Trades je Bucket fallen
    (Datenanteil irrelevant). Leere Buckets bleiben als Balken erhalten
    (``mean/median = NaN``). Liefert DataFrame mit Spalten ``[bucket, n,
    mean_return, median_return, score_min, score_max]`` in aufsteigender
    Score-Reihenfolge; ``bucket`` ist ein disjunktes Integer-Label aus den
    Bin-Grenzen, ``score_min/score_max`` sind die tatsaechlichen Daten-Werte
    je Bucket (fuer den Hover).

    Bei kollabierter Spanne (alle Scores gleich) entsteht ein Sammel-Bucket.
    Ist die Score-Spanne kleiner als ``n_buckets`` Integer-Schritte, fallen
    Bin-Grenzen zusammen (``np.unique``) -> dann weniger Buckets als gewuenscht
    (bei ganzzahligen Grenzen unvermeidbar).
    """
    cols = ['bucket', 'n', 'mean_return', 'median_return',
            'score_min', 'score_max']
    work = pd.DataFrame({
        'score': pd.to_numeric(df[score_col], errors='coerce'),
        'ret': pd.to_numeric(df[ret_col], errors='coerce'),
    }).dropna()
    if work.empty:
        return pd.DataFrame(columns=cols)

    lo = int(math.floor(work['score'].min()))
    hi = int(math.ceil(work['score'].max()))
    n = max(1, int(n_buckets))
    # Ganzzahlige, gleich breite Bin-Grenzen. np.unique faengt zu kleine
    # Spannen ab (dann < n_buckets Buckets) und garantiert streng steigende
    # Grenzen.
    edges = np.unique(np.round(np.linspace(lo, hi, n + 1)).astype(int))

    if edges.size < 2:
        # Score-Spanne kollabiert (alle Werte gleich) -> ein Sammel-Bucket.
        work['bucket'] = 'all'
        grp = work.groupby('bucket', observed=True)
    else:
        # observed=False -> leere Buckets bleiben als Balken erhalten.
        work['bucket'] = pd.cut(work['score'], bins=edges, include_lowest=True)
        grp = work.groupby('bucket', observed=False)

    out = grp.agg(
        n=('ret', 'size'),
        mean_return=('ret', 'mean'),
        median_return=('ret', 'median'),
        score_min=('score', 'min'),
        score_max=('score', 'max'),
    ).reset_index(drop=True)

    if edges.size < 2:
        out['bucket'] = f'{lo}'
    else:
        # Disjunkte, lueckenlose Integer-Labels aus den Bin-Grenzen. Bucket i
        # umfasst die Integer (edges[i]+1 .. edges[i+1]]; das erste Intervall
        # ist wegen include_lowest unten geschlossen (edges[0] .. edges[1]).
        labels = []
        for i in range(edges.size - 1):
            left = int(edges[i]) if i == 0 else int(edges[i]) + 1
            right = int(edges[i + 1])
            labels.append(f'{left}' if left == right else f'{left}–{right}')
        out['bucket'] = labels
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


def per_signal_return_table(df: pd.DataFrame, ret_col: str,
                            signals_col: str = 'signals_triggered',
                            breakdown_col: str = 'trade_score_breakdown'
                            ) -> pd.DataFrame:
    """Pro-Signal-Korrelation: wie haengt jedes Signal mit dem Return zusammen.

    Marginal/univariat — Signale ko-okkurrieren, dies sind also KEINE isolierten
    Beitraege. Signal-Universum datengetrieben aus den ``signals_triggered``-
    Tokens (alle real gefeuerten Signale) plus Pseudo-Signal
    ``committee_ex_officio``, das nicht in ``signals_triggered`` steht, sondern
    aus dem Breakdown-Token ``ex_officio:+N`` (N>0) abgeleitet wird.
    ``swarm_paradox`` ist ein Daempfer (negative Korrelation erwartet — kein
    Bug). Pro Signal binaerer Indikator (1=gefeuert) gegen den signierten
    Return. Liefert DataFrame ``[signal, n_fired, mean_fired, mean_not, delta,
    spearman, pearson]`` absteigend nach |spearman|.
    """
    cols = ['signal', 'n_fired', 'mean_fired', 'mean_not', 'delta',
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

    # Pseudo-Signal committee_ex_officio aus dem Breakdown ableiten.
    if breakdown_col in df.columns:
        ex_bonus = df[breakdown_col].map(_ex_officio_bonus)
        if (ex_bonus > 0).any():
            indicators['committee_ex_officio'] = (ex_bonus > 0).astype(int)

    rows = []
    for sig, ind in indicators.items():
        pair = pd.DataFrame({'ind': ind.astype(float), 'ret': ret}).dropna()
        fired = pair.loc[pair['ind'] == 1, 'ret']
        not_fired = pair.loc[pair['ind'] == 0, 'ret']
        mean_fired = fired.mean() if len(fired) else float('nan')
        mean_not = not_fired.mean() if len(not_fired) else float('nan')
        # Korrelation nur sinnvoll, wenn beide Gruppen (gefeuert/nicht) vorkommen.
        if pair['ind'].nunique() > 1 and len(pair) >= 2:
            spearman = _spearman(pair['ind'], pair['ret'])
            pearson = pair['ind'].corr(pair['ret'])
        else:
            spearman = float('nan')
            pearson = float('nan')
        rows.append({
            'signal': sig,
            'n_fired': int(len(fired)),
            'mean_fired': mean_fired,
            'mean_not': mean_not,
            'delta': mean_fired - mean_not,
            'spearman': spearman,
            'pearson': pearson,
        })

    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out
    return out.sort_values(
        'spearman', key=lambda s: s.abs(),
        ascending=False, na_position='last',
    ).reset_index(drop=True)
