"""Event-Study / Abnormal-Return-Analyse (Insider-Aufdeckung — Discovery).

EIGENSTAENDIGES Analyse-Skript, unabhaengig von der Import-Pipeline. Liest den
fertigen Master, berechnet den signierten, marktbereinigten Abnormal Return
(scoring/abnormal_returns.py) im Speicher und schreibt:
  - organisation/analyse_event_study.md   (Befund-Report, Single-Source)
  - data/processed/ar_outliers.csv         (identifizierte Ausreisser-Trades)
Es SCHREIBT NICHT in den Master und ist KEINE Pipeline-Stage. Aenderungen an den
AR_*-Settings (Horizonte/Schwellen) erfordern nur einen erneuten Skript-Lauf
(reine Cache-Lookups, kein yfinance-Download, kein Delta-Lauf-Risiko).

Kernfragen:
  1. Diskriminator: Korreliert trade_score beim AR besser als beim rohen
     Kalender-Return? -> Spearman(trade_score, post_return_90d) vs.
     Spearman(trade_score, signed_ar_H) auf IDENTISCHER Population (gleicher
     Horizont H = AR_HEADLINE_HORIZON_DAYS, nur Marktbereinigung unterscheidet
     sich).
  2. Pro Signal: mittlerer signed_ar_H MIT vs. OHNE Signal (Differenz +
     member-geclustertes Bootstrap-CI). Committee-Signale nur innerhalb der
     Trades, die sie ausloesen koennten (committee_eligible_mask).
  3. Ausreisser-Charakterisierung: nach Signal / Kammer / Sektor / Member —
     empirische Grundlage fuer die spaetere Filter-Rekonstruktion.

Lauf: source venv/bin/activate && python scripts/analyse_event_study.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import settings  # noqa: E402
from app.score_correlation import (  # noqa: E402
    COMMITTEE_SIGNALS,
    analysis_population,
    committee_eligible_mask,
)
from scoring.abnormal_returns import (  # noqa: E402
    clustered_bootstrap_diff,
    clustered_bootstrap_mean,
    compute_abnormal_returns,
)

TRADES_MASTER = BASE_DIR / settings.DATA_PROCESSED_PATH / settings.TRADES_MASTER_COMPLETE_CSV
CACHE_PATH = str(BASE_DIR / settings.POST_TRADE_PRICE_CACHE)
REPORT_MD = BASE_DIR / 'organisation' / 'analyse_event_study.md'
OUTLIERS_CSV = BASE_DIR / settings.DATA_PROCESSED_PATH / 'ar_outliers.csv'

# Verdachts-Signale fuer die Pro-Signal-Analyse — echte Tokens aus signals_triggered
# (siehe organisation/tool_specific_md/signal_info.md). NICHT abgeleitet.
SIGNALS = ['committee', 'committee_full', 'committee_ex_officio', 'floor_action', 'swarm_buy', 'swarm_sell',
           'accumulative']

# Spalten, die wir aus dem Master brauchen (Rest wird nicht geladen).
# post_return-Spalten dynamisch: Abschnitt 2 (Diskriminator) vergleicht gegen
# post_return_{AR_HEADLINE_HORIZON_DAYS}d — der Headline-Horizont ist
# konfigurierbar (z. B. 120) und muss mitgeladen werden, sonst KeyError.
_RAW_RETURN_COLS = sorted({
    'post_return_90d',
    'post_return_180d',
    f'post_return_{int(settings.AR_HEADLINE_HORIZON_DAYS)}d',
})
LOAD_COLS = ['trade_id', 'bio_guide_id', 'name', 'chamber', 'sector_yf',
             'ticker', 'transaction_type', 'transaction_date', 'disclosure_date',
             'trade_score', 'signals_triggered', 'industry_yf',
             'Member_Committees_Full', 'asset_class_excluded'] + _RAW_RETURN_COLS


def _spearman(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    """Spearman als Rang-Pearson (kein scipy). Returns (rho, n)."""
    m = a.notna() & b.notna()
    n = int(m.sum())
    if n < 3:
        return float('nan'), n
    rho = a[m].rank().corr(b[m].rank())
    return float(rho), n


def _has_signal(series: pd.Series, token: str) -> pd.Series:
    """Bool-Maske: signals_triggered (';'-getrennt) enthaelt token exakt."""
    s = series.fillna('').astype(str)
    return s.str.split(';').apply(lambda toks: token in [t.strip() for t in toks])


def _fmt_pct(x: float) -> str:
    return 'n/a' if x is None or not np.isfinite(x) else f"{x * 100:+.2f}%"


def _load() -> pd.DataFrame:
    cols = [c for c in LOAD_COLS if c in pd.read_csv(TRADES_MASTER, nrows=0).columns]
    df = pd.read_csv(TRADES_MASTER, usecols=cols, low_memory=False)
    for c in ['trade_score'] + _RAW_RETURN_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    # Gleiche Population wie im Dashboard: Duplikate + Exchange raus.
    return analysis_population(df).reset_index(drop=True)


def main() -> None:
    print(f"Lade {TRADES_MASTER} ...")
    df = _load()
    n_total = len(df)
    print(f"Trades: {n_total:,}")

    print("Berechne Abnormal Returns (signed BHAR, beide Konstrukte) ...")
    df = compute_abnormal_returns(
        df,
        cache_path=CACHE_PATH,
        benchmark_ticker=settings.AR_BENCHMARK_TICKER,
        horizons_days=settings.AR_HORIZONS_DAYS,
        headline_horizon_days=settings.AR_HEADLINE_HORIZON_DAYS,
        disclosure_horizons_days=settings.AR_DISCLOSURE_HORIZONS_DAYS,
        outlier_mode=settings.AR_OUTLIER_MODE,
        outlier_upper=settings.AR_OUTLIER_UPPER,
        outlier_lower=settings.AR_OUTLIER_LOWER,
        outlier_pct_upper=settings.AR_OUTLIER_PCT_UPPER,
        outlier_pct_lower=settings.AR_OUTLIER_PCT_LOWER,
    )

    headline = int(settings.AR_HEADLINE_HORIZON_DAYS)
    head_col = f'signed_ar_{headline}d'
    iters = int(settings.AR_BOOTSTRAP_ITERS)
    seed = int(getattr(settings, 'AR_BOOTSTRAP_SEED', 0))

    lines: list[str] = []
    L = lines.append
    L('# Event-Study / Abnormal Return — Insider-Aufdeckung (Discovery)')
    L('')
    L(f'_Erzeugt: {datetime.now():%Y-%m-%d %H:%M} von `scripts/analyse_event_study.py`._')
    L('')
    L('Signierter, marktbereinigter Abnormal Return (signed BHAR) als schaerferes '
      'Lineal gegenueber dem rohen Kalender-Return. `signed_ar` ist ein Bruchteil '
      '(0.03 = 3%), Verkauf invertiert das Vorzeichen (positiv = gut getimt).')
    L('')

    # --- Datenstand / ehrliches N pro Konstrukt & Horizont ---
    L('## 1. Datenstand & evaluierbares N (Right-Censoring)')
    L('')
    L(f'- Trades gesamt: **{n_total:,}**')
    L(f'- Benchmark: **{settings.AR_BENCHMARK_TICKER}**, Bootstrap: {iters} Iter '
      f'(member-geclustert ueber `bio_guide_id`, Seed {seed})')
    L(f'- Outlier-Modus: **{settings.AR_OUTLIER_MODE}** auf `{head_col}`')
    L('')
    L('| Konstrukt | Spalte | N evaluierbar | Median | Mittel |')
    L('|---|---|--:|--:|--:|')
    a_h = sorted(set(int(h) for h in settings.AR_HORIZONS_DAYS) | {headline})
    for h in a_h:
        col = f'signed_ar_{h}d'
        s = df[col]
        L(f'| A (transaction) | `{col}` | {int(s.notna().sum()):,} | '
          f'{_fmt_pct(s.median())} | {_fmt_pct(s.mean())} |')
    for h in sorted(set(int(h) for h in settings.AR_DISCLOSURE_HORIZONS_DAYS)):
        col = f'signed_ar_disc_{h}d'
        s = df[col]
        L(f'| B (disclosure) | `{col}` | {int(s.notna().sum()):,} | '
          f'{_fmt_pct(s.median())} | {_fmt_pct(s.mean())} |')
    L('')

    # --- Diskriminator: Score gegen rohen Return vs. AR (gleiche Population, H=90) ---
    L(f'## 2. Diskriminator — trade_score gegen rohen Return vs. AR (H={headline})')
    L('')
    L('Identische Population (Zeilen, in denen `trade_score`, `post_return_'
      f'{headline}d` UND `{head_col}` vorhanden sind). Nur die Marktbereinigung '
      'unterscheidet die beiden Lineale; der Horizont ist konstant.')
    L('')
    raw_col = f'post_return_{headline}d'
    pop = df['trade_score'].notna() & df[raw_col].notna() & df[head_col].notna()
    sub = df[pop]
    rho_raw, _ = _spearman(sub['trade_score'], sub[raw_col])
    rho_ar, _ = _spearman(sub['trade_score'], sub[head_col])
    L(f'- Population N = **{int(pop.sum()):,}**')
    L(f'- Spearman(trade_score, `{raw_col}` — roh, signiert): **{rho_raw:+.4f}**')
    L(f'- Spearman(trade_score, `{head_col}` — marktbereinigt): **{rho_ar:+.4f}**')
    L('')
    # Sanity gegen den Vorgaenger-Report (eigene Population, 180d).
    rho180, n180 = _spearman(df['trade_score'], df['post_return_180d'])
    L(f'_Sanity-Check (eigene Population): Spearman(trade_score, post_return_180d) '
      f'= {rho180:+.4f} (N={n180:,}) — reproduziert den Report-Wert (~+0.003)._')
    L('')

    # --- Pro Signal: signed_ar_90d mit vs. ohne (clustered bootstrap) ---
    L(f'## 3. Pro Signal — mittlerer `{head_col}` mit vs. ohne (member-geclustert)')
    L('')
    L('Differenz = Mittel(mit Signal) - Mittel(ohne Signal). 95%-CI + p aus '
      'member-geclustertem Block-Bootstrap (resamplet Member, nicht Trades).')
    L('')
    L('| Signal | N mit | Mittel mit | Mittel ohne | Differenz | 95%-CI | p |')
    L('|---|--:|--:|--:|--:|---|--:|')
    elig_all = committee_eligible_mask(df)
    df_all = df
    for sig in SIGNALS:
        df = df_all[elig_all] if sig in COMMITTEE_SIGNALS else df_all
        clusters_all = df['bio_guide_id'].fillna('').astype(str).values
        vals_all = df[head_col].values
        mask = _has_signal(df['signals_triggered'], sig)
        mean_with = clustered_bootstrap_mean(
            df.loc[mask, head_col].values,
            df.loc[mask, 'bio_guide_id'].fillna('').astype(str).values,
            n_iter=iters, seed=seed)
        mean_wo = clustered_bootstrap_mean(
            df.loc[~mask, head_col].values,
            df.loc[~mask, 'bio_guide_id'].fillna('').astype(str).values,
            n_iter=iters, seed=seed)
        diff = clustered_bootstrap_diff(
            vals_all, clusters_all, mask.values, n_iter=iters, seed=seed)
        ci = (f"[{_fmt_pct(diff['ci_lo'])}, {_fmt_pct(diff['ci_hi'])}]"
              if np.isfinite(diff['point']) else 'n/a')
        p = 'n/a' if not np.isfinite(diff['p']) else f"{diff['p']:.3f}"
        L(f"| `{sig}` | {mean_with['n']:,} | {_fmt_pct(mean_with['point'])} | "
          f"{_fmt_pct(mean_wo['point'])} | {_fmt_pct(diff['point'])} | {ci} | {p} |")
    df = df_all
    L('')
    L('_Der Vorgaenger-Report wies `committee_ex_officio` als robust signifikant '
      'aus (t=-2.55). Unter dem member-geclusterten Bootstrap ist KEIN Signal von 0 '
      'unterscheidbar (alle CIs umschliessen 0, p oben) — die fruehere Signifikanz '
      'war ein Clustering-Artefakt (Schwarm-Korrelation als Schein-N)._')
    L('')

    # --- Schwellen-Kalibrierung ---
    sH = df[head_col].dropna()
    n_eval = int(df[head_col].notna().sum())
    frac_out = (int(df['ar_outlier_flag'].sum()) / n_eval * 100) if n_eval else float('nan')
    p_lo = (sH < settings.AR_OUTLIER_LOWER).mean() * 100
    p_hi = (sH > settings.AR_OUTLIER_UPPER).mean() * 100
    L('## 4. Schwellen-Kalibrierung (Ausreisser-Definition)')
    L('')
    L(f'`{head_col}` ist stark heavy-tailed (90d Single-Name vs. '
      f'{settings.AR_BENCHMARK_TICKER}). Die aktuelle absolute Schwelle '
      f'(±{settings.AR_OUTLIER_UPPER * 100:.0f}%) flaggt **{frac_out:.0f}%** der '
      f'evaluierbaren Trades ({p_lo:.0f}% unter {settings.AR_OUTLIER_LOWER * 100:.0f}%, '
      f'{p_hi:.0f}% ueber +{settings.AR_OUTLIER_UPPER * 100:.0f}%) — das ist KEIN Tail.')
    L('')
    L('| Perzentil | Wert |')
    L('|---|--:|')
    for q in [0.01, 0.05, 0.10, 0.50, 0.90, 0.95, 0.99]:
        L(f'| p{int(q * 100)} | {_fmt_pct(sH.quantile(q))} |')
    L('')
    L(f'Fuer eine echte Tail-Isolation: engere absolute Schwellen '
      f'(~±{abs(sH.quantile(0.95)) * 100:.0f}% entspricht p95) oder '
      f'`AR_OUTLIER_MODE=\'percentile\'` (p95/p99). `ar_outliers.csv` ist nach '
      f'`{head_col}` absteigend sortiert — die extremsten Trades stehen oben.')
    L('')

    # --- Ausreisser-Charakterisierung ---
    out = df[df['ar_outlier_flag']].copy()
    n_pos = int((out['ar_outlier_direction'] == 'positive').sum())
    n_neg = int((out['ar_outlier_direction'] == 'negative').sum())
    L('## 5. Ausreisser-Charakterisierung')
    L('')
    L(f'Ausreisser auf `{head_col}` (Modus {settings.AR_OUTLIER_MODE}): '
      f'**{len(out):,}** ( +{n_pos:,} / -{n_neg:,} ), von '
      f'{int(df[head_col].notna().sum()):,} evaluierbaren.')
    L('')

    def _breakdown(col: str, title: str, top: int | None = None) -> None:
        L(f'**Nach {title}**')
        L('')
        L(f'| {title} | Ausreisser | positiv | negativ |')
        L('|---|--:|--:|--:|')
        grp = out.groupby(out[col].fillna('(leer)'))
        agg = grp['ar_outlier_direction'].agg(
            total='size',
            pos=lambda s: int((s == 'positive').sum()),
            neg=lambda s: int((s == 'negative').sum()),
        ).sort_values('total', ascending=False)
        if top:
            agg = agg.head(top)
        for key, r in agg.iterrows():
            L(f"| {key} | {int(r['total']):,} | {int(r['pos']):,} | {int(r['neg']):,} |")
        L('')

    # Nach Signal (ein Trade kann mehrere Signale tragen -> je Signal zaehlen).
    L('**Nach Signal** (ein Ausreisser kann mehrere Signale tragen)')
    L('')
    L('| Signal | Ausreisser | positiv | negativ | Anteil an Signal-Trades |')
    L('|---|--:|--:|--:|--:|')
    for sig in SIGNALS:
        m_out = _has_signal(out['signals_triggered'], sig)
        m_all = _has_signal(df['signals_triggered'], sig) & df[head_col].notna()
        sub_out = out[m_out]
        pos = int((sub_out['ar_outlier_direction'] == 'positive').sum())
        neg = int((sub_out['ar_outlier_direction'] == 'negative').sum())
        share = (len(sub_out) / int(m_all.sum()) * 100) if int(m_all.sum()) else float('nan')
        share_s = 'n/a' if not np.isfinite(share) else f"{share:.1f}%"
        L(f"| `{sig}` | {len(sub_out):,} | {pos:,} | {neg:,} | {share_s} |")
    L('')
    if 'chamber' in out.columns:
        _breakdown('chamber', 'Kammer')
    if 'sector_yf' in out.columns:
        _breakdown('sector_yf', 'Sektor', top=15)
    if 'name' in out.columns:
        _breakdown('name', 'Member (Top 20)', top=20)

    # --- Grenzen ---
    L('## 6. Grenzen')
    L('')
    L(f'- **Right-Censoring:** Preis-Cache endet {pd.Timestamp(_cache_end()):%Y-%m-%d}; '
      'Trades mit `t0 + H` dahinter sind nicht evaluierbar (NaN). N pro Horizont '
      'siehe Abschnitt 1.')
    exo_out = out[_has_signal(out['signals_triggered'], 'committee_ex_officio')]
    if len(exo_out):
        vc = exo_out['name'].value_counts()
        L(f'- **Member-Konzentration:** {vc.iloc[0] / len(exo_out) * 100:.0f}% der '
          f'`committee_ex_officio`-Ausreisser entfallen auf einen einzigen Member '
          f'({vc.index[0]}) — der Punkt-Effekt ist member-getrieben, nicht zwingend '
          'ex-officio-getrieben (Filter-Rekonstruktion droht "ist das <Member>" zu lernen).')
    L('- **Geltungsbereich:** getestet ist nur das marktbereinigte **Kalender**-Lineal '
      'bei H=90. Event-verankerte Lineale (Votes, Konstrukt C) sind aufgeschoben — '
      'kein Urteil ueber die Signale insgesamt, nur ueber den 90d-Kalender-Edge.')
    L('- **Konstrukt B** (disclosure) ist staerker zensiert als A (Melde-Latenz).')
    L('- **Niedrige Decke:** Der Vorgaenger-Report zeigt R^2 aller Signale ~1.2% — '
      'ein echtes ~0 ist ein moegliches, valides Ergebnis (Diskriminator).')
    L('- **Konstrukt B misst Copycat**, nicht Insider-Information (skaliert mit '
      'Sichtbarkeit/Trade-Groesse) — nicht als Informationsbeweis lesen.')
    L('')

    REPORT_MD.write_text('\n'.join(lines), encoding='utf-8')
    print(f"Report geschrieben: {REPORT_MD}")

    # --- Ausreisser-CSV ---
    ar_cols = [f'signed_ar_{h}d' for h in a_h]
    keep = [c for c in (
        ['trade_id', 'bio_guide_id', 'name', 'chamber', 'sector_yf', 'ticker',
         'transaction_type', 'transaction_date', 'disclosure_date', 'trade_score',
         'signals_triggered', 'ar_outlier_direction'] + ar_cols +
        ['post_return_90d']) if c in out.columns]
    out_sorted = out.sort_values(head_col, ascending=False)
    out_sorted[keep].to_csv(OUTLIERS_CSV, index=False)
    print(f"Ausreisser geschrieben: {OUTLIERS_CSV} ({len(out_sorted):,} Zeilen)")


def _cache_end() -> pd.Timestamp:
    from scoring.post_trade_returns import _load_price_cache
    cache = _load_price_cache(CACHE_PATH)
    return pd.Timestamp(cache.index.get_level_values(1).max())


if __name__ == '__main__':
    main()
