"""Tests fuer app/member_metrics.py — pure Logik des Member-Detail-Moduls.

Deckt ab: Committee-Parser (an realen Roster-Strings gepinnt inkl. der
verlustbehafteten Grenze), Horizont-Attributierung (Mittelpunkt-Rundung),
paper_return_terminal, Linienende und Trajektorien-Aufbau. Der Kohaerenz-Test
pinnt die bewusste Divergenz Attributierung (first_close) vs. Linienende
(full_close).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.member_metrics import (  # noqa: E402
    DEFAULT_HORIZONS,
    committee_full_mask,
    committee_oversight_rows,
    compute_member_trajectories,
    downsample_trajectory,
    filter_by_quarter_range,
    filter_long_trades_out,
    horizon_agg_returns,
    horizon_bucket_shares,
    horizon_mean_returns,
    horizon_stats,
    member_oversight_sectors,
    nearest_horizon,
    paper_return_terminal,
    parse_committees_full,
    qualifies_for_horizon,
    qualifying_horizons,
    quarter_options,
    red_flag_summary,
    resolve_highlight_from_selection,
    score_bins,
    sector_concentration,
    select_top_trajectories,
    terminal_distribution,
    trajectory_end_offset,
)

H = [30, 60, 90, 120, 180]


# ---------------------------------------------------------------------------
# Committee-Parser — an realen Roster-Strings gepinnt (F2-Format:
# ';' = Committees, ':' = Header->Sub-Liste, '~' = Subcommittees, '|' = Rolle)
# ---------------------------------------------------------------------------

WALBERG = ('Education And Workforce|Chairman:Early Childhood, Elementary, And '
           'Secondary Education|Ex Officio~Health, Employment, Labor, And '
           'Pensions|Ex Officio~Higher Education And Workforce Development|Ex '
           'Officio~Workforce Protections|Ex Officio;Natural Resources|:Indian '
           'And Insular Affairs|~Water, Wildlife And Fisheries|')

AUCHINCLOSS = ('Energy And Commerce|:Energy|~Environment|~Health|')

MOORE = ('Budget|;Financial Services|:Digital Assets, Financial Technology, '
         'And Artificial Intelligence|~Financial Institutions|~Oversight And '
         'Investigations|Vice Chair')

GOTTHEIMER = ('Financial Services|:Capital Markets|~Digital Assets, Financial '
              'Technology, And Artificial Intelligence|~National Security, '
              'Illicit Finance, And International Financial Institutions|;'
              'Permanent Select Committee On Intelligence|')


def test_parse_walberg_two_committees_nested_subs():
    """Walberg: 2 Committees (NICHT 6) mit korrekt verschachtelten Subs."""
    res = parse_committees_full(WALBERG)
    assert [c['committee'] for c in res] == [
        'Education And Workforce', 'Natural Resources']

    edu = res[0]
    assert edu['role'] == 'Chairman'
    assert [s['name'] for s in edu['subcommittees']] == [
        'Early Childhood, Elementary, And Secondary Education',
        'Health, Employment, Labor, And Pensions',
        'Higher Education And Workforce Development',
        'Workforce Protections',
    ]
    assert all(s['role'] == 'Ex Officio' for s in edu['subcommittees'])

    nat = res[1]
    assert nat['role'] == ''
    assert [s['name'] for s in nat['subcommittees']] == [
        'Indian And Insular Affairs', 'Water, Wildlife And Fisheries']
    assert all(s['role'] == '' for s in nat['subcommittees'])


def test_parse_auchincloss_single_committee_three_subs():
    res = parse_committees_full(AUCHINCLOSS)
    assert len(res) == 1
    assert res[0]['committee'] == 'Energy And Commerce'
    assert res[0]['role'] == ''
    assert [s['name'] for s in res[0]['subcommittees']] == [
        'Energy', 'Environment', 'Health']


def test_parse_moore_committee_without_subs_then_with():
    """Budget (keine Subs) gefolgt von Financial Services (mit Subs + Rolle)."""
    res = parse_committees_full(MOORE)
    assert [c['committee'] for c in res] == ['Budget', 'Financial Services']
    assert res[0]['subcommittees'] == []
    fs = res[1]
    assert [s['name'] for s in fs['subcommittees']] == [
        'Digital Assets, Financial Technology, And Artificial Intelligence',
        'Financial Institutions',
        'Oversight And Investigations',
    ]
    assert fs['subcommittees'][-1]['role'] == 'Vice Chair'


def test_parse_gottheimer_intelligence_is_own_committee():
    """F2 loest die alte Format-Mehrdeutigkeit: 'Permanent Select Committee On
    Intelligence' ist jetzt eindeutig ein eigenes Committee (frueher landete es
    als Subcommittee unter Financial Services — dokumentierte Grenze des alten
    Formats)."""
    res = parse_committees_full(GOTTHEIMER)
    assert [c['committee'] for c in res] == [
        'Financial Services', 'Permanent Select Committee On Intelligence']
    assert len(res[0]['subcommittees']) == 3
    assert res[1]['subcommittees'] == []


def test_parse_empty_and_invalid():
    assert parse_committees_full('') == []
    assert parse_committees_full(None) == []
    assert parse_committees_full(np.nan) == []
    assert parse_committees_full(123) == []


# ---------------------------------------------------------------------------
# Horizont-Attributierung
# ---------------------------------------------------------------------------

def test_nearest_horizon_midpoints():
    assert nearest_horizon(20, H) == 30
    assert nearest_horizon(45, H) == 60     # exakt Mittelpunkt -> hoeher
    assert nearest_horizon(69, H) == 60
    assert nearest_horizon(75, H) == 90     # exakt Mittelpunkt -> hoeher
    assert nearest_horizon(76, H) == 90
    assert nearest_horizon(150, H) == 180
    assert nearest_horizon(400, H) == 180
    assert nearest_horizon(0, H) == 30


def test_nearest_horizon_default_horizons():
    assert DEFAULT_HORIZONS == H
    assert nearest_horizon(69) == 60


def test_qualifying_horizons_user_examples():
    assert qualifying_horizons(69, H) == [30, 60]        # B@69
    assert qualifying_horizons(76, H) == [30, 60, 90]    # ss@76
    assert qualifying_horizons(20, H) == [30]
    assert qualifying_horizons(400, H) == [30, 60, 90, 120, 180]


def test_qualifying_horizons_open_trade_all():
    assert qualifying_horizons(np.nan, H) == H
    assert qualifying_horizons(None, H) == H
    assert qualifying_horizons(pd.NA, H) == H


def test_qualifies_for_horizon_vectorized():
    df = pd.DataFrame({'first_close_offset_days': [69.0, 76.0, np.nan, 20.0]})
    # B@69 -> {30,60}; ss@76 -> {30,60,90}; offen -> alle; 20 -> {30}
    assert qualifies_for_horizon(df, 30, H).tolist() == [True, True, True, True]
    assert qualifies_for_horizon(df, 60, H).tolist() == [True, True, True, False]
    assert qualifies_for_horizon(df, 90, H).tolist() == [False, True, True, False]
    assert qualifies_for_horizon(df, 180, H).tolist() == [False, False, True, False]


# ---------------------------------------------------------------------------
# paper_return_terminal
# ---------------------------------------------------------------------------

def test_paper_return_terminal_picks_nearest_horizon_column():
    df = pd.DataFrame({
        'first_close_offset_days': [20.0, 69.0, 76.0, np.nan],
        'post_return_30d':  [1.0, 2.0, 3.0, 4.0],
        'post_return_60d':  [11.0, 12.0, 13.0, 14.0],
        'post_return_90d':  [21.0, 22.0, 23.0, 24.0],
        'post_return_120d': [31.0, 32.0, 33.0, 34.0],
        'post_return_180d': [41.0, 42.0, 43.0, 44.0],
    })
    out = paper_return_terminal(df, H)
    # 20->30d=1.0 ; 69->60d=12.0 ; 76->90d=23.0 ; offen->180d=44.0
    assert out.tolist() == [1.0, 12.0, 23.0, 44.0]


def test_paper_return_terminal_nan_when_future():
    df = pd.DataFrame({
        'first_close_offset_days': [np.nan],     # offen -> 180d
        'post_return_180d': [np.nan],            # junge Position < 180d
    })
    out = paper_return_terminal(df, H)
    assert out.isna().all()


# ---------------------------------------------------------------------------
# Linienende
# ---------------------------------------------------------------------------

def test_trajectory_end_offset_states():
    t0 = pd.Timestamp('2020-01-01')
    assert trajectory_end_offset('closed_full', t0, t0 + pd.Timedelta(days=100)) == 100
    assert trajectory_end_offset('closed_full', t0, t0 + pd.Timedelta(days=250)) == 180
    assert trajectory_end_offset('delisted', t0, t0 + pd.Timedelta(days=45)) == 45
    assert trajectory_end_offset('held', t0, pd.NaT) == 180
    assert trajectory_end_offset('closed_partial', t0, t0 + pd.Timedelta(days=30)) == 180
    assert trajectory_end_offset('not_applicable', t0, pd.NaT) == 180
    assert trajectory_end_offset('closed_full', t0, pd.NaT) == 180     # kein exit_date


def test_trajectory_end_offset_custom_cap():
    t0 = pd.Timestamp('2020-01-01')
    assert trajectory_end_offset('held', t0, pd.NaT, cap=90) == 90
    assert trajectory_end_offset('closed_full', t0, t0 + pd.Timedelta(days=200), cap=90) == 90


# ---------------------------------------------------------------------------
# Kohaerenz: Attributierung (first_close) vs. Linienende (full_close)
# ---------------------------------------------------------------------------

def test_coherence_partial_at_40_full_at_100():
    """Teil-Close 40 / Voll-Close 100:

    Attributierung (first_close=40) -> nearest 30 -> zaehlt NUR fuer Horizont 30
    (D1/D2). Linienende (closed_full, exit @100) -> Linie laeuft bis 100
    (Spaghetti). Diese Divergenz ist gewollt.
    """
    first_close = 40.0
    assert qualifying_horizons(first_close, H) == [30]

    t0 = pd.Timestamp('2020-01-01')
    assert trajectory_end_offset('closed_full', t0,
                                 t0 + pd.Timedelta(days=100)) == 100

    # Vektorisiert: zaehlt fuer 30, nicht fuer 60+.
    df = pd.DataFrame({'first_close_offset_days': [first_close]})
    assert qualifies_for_horizon(df, 30, H).iloc[0]
    assert not qualifies_for_horizon(df, 60, H).iloc[0]


# ---------------------------------------------------------------------------
# Trajektorien-Aufbau aus synthetischem Cache
# ---------------------------------------------------------------------------

def _synthetic_cache():
    idx = pd.MultiIndex.from_tuples(
        [
            ('AAA', pd.Timestamp('2020-01-01')),
            ('AAA', pd.Timestamp('2020-01-15')),
            ('AAA', pd.Timestamp('2020-02-01')),
            ('AAA', pd.Timestamp('2020-08-01')),   # 213d > cap 180 -> ausserhalb
            ('BBB', pd.Timestamp('2020-01-01')),
            ('BBB', pd.Timestamp('2020-01-15')),
        ],
        names=['ticker', 'date'])
    return pd.DataFrame(
        {'close': [100.0, 110.0, 90.0, 200.0, 50.0, 55.0]}, index=idx)


def test_compute_member_trajectories_buy_and_sell_sign():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 't1', 'ticker': 'AAA', 'asset_name': 'Alpha',
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT,
         'post_return_180d': -10.0},
        {'trade_id': 't2', 'ticker': 'BBB', 'asset_name': 'Beta',
         'transaction_type': 'Sale', 'post_trade_baseline_price': 50.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT,
         'post_return_180d': -10.0},
    ])
    traj = compute_member_trajectories(df_member, cache, H)
    assert len(traj) == 2

    t1 = next(t for t in traj if t['trade_id'] == 't1')
    # held -> cap 180; Tag 213 (2020-08-01) liegt ausserhalb -> 3 Punkte.
    assert t1['days'] == [0, 14, 31]
    # Buy: (close/100 -1)*100: 0%, +10%, -10%
    assert t1['returns'] == pytest.approx([0.0, 10.0, -10.0])
    assert t1['direction'] == 'buy'

    t2 = next(t for t in traj if t['trade_id'] == 't2')
    # Sell: Vorzeichen invertiert. close 50->55 = +10% Kurs -> -10% (schlechtes
    # Timing fuer einen Verkauf vor Anstieg).
    assert t2['days'] == [0, 14]
    assert t2['returns'] == pytest.approx([0.0, -10.0])
    assert t2['direction'] == 'sell'


def test_compute_member_trajectories_line_ends_at_full_close():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 't3', 'ticker': 'AAA', 'asset_name': 'Alpha',
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'closed_full',
         'exit_date': pd.Timestamp('2020-01-20'),   # 19 Tage -> endet @ Tag 15
         'post_return_180d': 5.0},
    ])
    traj = compute_member_trajectories(df_member, cache, H)
    assert len(traj) == 1
    # end_offset 19 -> nur Punkte bis Tag 15 (2020-01-15), Tag 31 faellt raus.
    assert traj[0]['end_offset'] == 19
    assert traj[0]['days'] == [0, 14]


def test_compute_member_trajectories_skips_missing_data():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 'x1', 'ticker': 'ZZZ', 'asset_name': 'Z',  # nicht im Cache
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT, 'post_return_180d': 1.0},
        {'trade_id': 'x2', 'ticker': 'AAA', 'asset_name': 'A',  # keine Baseline
         'transaction_type': 'Purchase', 'post_trade_baseline_price': np.nan,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT, 'post_return_180d': 1.0},
    ])
    assert compute_member_trajectories(df_member, cache, H) == []


# ---------------------------------------------------------------------------
# Downsampling + Trace-Cap (Performance-Schutz, pure)
# ---------------------------------------------------------------------------

def test_downsample_keeps_first_last_and_nth():
    days = list(range(0, 10))          # 0..9
    rets = [float(d) for d in days]
    d, r = downsample_trajectory(days, rets, sample_days=3)
    # Vielfache von 3 (0,3,6,9) + erster/letzter -> {0,3,6,9}
    assert d == [0, 3, 6, 9]
    assert r == [0.0, 3.0, 6.0, 9.0]


def test_downsample_last_point_always_kept_even_if_not_multiple():
    days = [0, 1, 2, 3, 4, 5, 7]        # 7 ist kein Vielfaches von 3
    rets = [float(x) for x in days]
    d, _ = downsample_trajectory(days, rets, sample_days=3)
    assert d[0] == 0 and d[-1] == 7    # erster + letzter immer drin
    assert 3 in d                      # Vielfaches dazwischen


def test_downsample_noop_for_small_or_one():
    days, rets = [0, 5, 9], [0.0, 5.0, 9.0]
    assert downsample_trajectory(days, rets, 1) == (days, rets)
    assert downsample_trajectory([0, 9], [0.0, 9.0], 3) == ([0, 9], [0.0, 9.0])


def test_select_top_trajectories_by_abs_return_nan_last():
    trajs = [
        {'trade_id': 'a', 'paper_return_180': 5.0},
        {'trade_id': 'b', 'paper_return_180': -200.0},
        {'trade_id': 'c', 'paper_return_180': np.nan},
        {'trade_id': 'd', 'paper_return_180': 50.0},
    ]
    top = select_top_trajectories(trajs, max_traces=2)
    assert [t['trade_id'] for t in top] == ['b', 'd']   # |−200|, |50| > |5|, NaN
    # Cap >= len -> unveraendert (Reihenfolge erhalten)
    assert select_top_trajectories(trajs, max_traces=10) == trajs
    assert select_top_trajectories(trajs, max_traces=0) == trajs


def test_compute_member_trajectories_downsample_param():
    cache = _synthetic_cache()
    df_member = pd.DataFrame([
        {'trade_id': 't1', 'ticker': 'AAA', 'asset_name': 'Alpha',
         'transaction_type': 'Purchase', 'post_trade_baseline_price': 100.0,
         'transaction_date': pd.Timestamp('2020-01-01'),
         'exit_status': 'held', 'exit_date': pd.NaT,
         'post_return_180d': -10.0},
    ])
    # Punkte bei Tag 0/14/31. sample_days=10 behaelt erster+letzter -> 0 und 31
    # (14 ist kein Vielfaches von 10) plus evtl. Vielfache; mind. Enden bleiben.
    traj = compute_member_trajectories(df_member, cache, H, sample_days=10)
    assert traj[0]['days'][0] == 0
    assert traj[0]['days'][-1] == 31


# ---------------------------------------------------------------------------
# horizon_agg_returns (Median-Default) + horizon_stats
# ---------------------------------------------------------------------------

def test_horizon_agg_returns_median_default_robust():
    df = pd.DataFrame({
        'first_close_offset_days': [np.nan] * 3,
        'post_return_30d': [1.0, 2.0, 300.0],   # Median 2, Mittel 101
    })
    med = horizon_agg_returns(df, H)             # default median
    assert med[30] == pytest.approx(2.0)
    mean = horizon_agg_returns(df, H, agg='mean')
    assert mean[30] == pytest.approx(101.0)
    # horizon_mean_returns delegiert -> identisch zum mean-Pfad
    assert horizon_mean_returns(df, H)[30] == pytest.approx(101.0)


def test_horizon_stats_n_and_aggregates():
    df = pd.DataFrame({
        'first_close_offset_days': [40.0, np.nan, np.nan],  # @40 nur fuer h=30
        'post_return_30d': [10.0, 20.0, 30.0],
        'post_return_60d': [99.0, 50.0, 70.0],
    })
    s = horizon_stats(df, H)
    assert s[30]['n'] == 3
    assert s[30]['median'] == pytest.approx(20.0)
    assert s[30]['min'] == pytest.approx(10.0)
    assert s[30]['max'] == pytest.approx(30.0)
    assert s[60]['n'] == 2                       # @40-Trade faellt raus
    assert s[60]['median'] == pytest.approx(60.0)
    # Horizont ohne Spalte -> n=0
    assert s[180]['n'] == 0


# ---------------------------------------------------------------------------
# Gremien-Tabelle (committee_oversight_rows) — Fake-Mapper
# ---------------------------------------------------------------------------

class _FakeMapper:
    """Minimal-Mapper im API-Kontrakt der echten CommitteeMapper."""

    def __init__(self, sub_map=None, comm_map=None):
        self.sub_map = sub_map or {}
        self.comm_map = comm_map or {}

    def get_sectors_for_subcommittee(self, chamber, committee, sub):
        return self.sub_map.get((committee, sub), [])

    def get_all_sectors_for_committee(self, chamber, committee):
        return self.comm_map.get(committee, [])


def test_committee_oversight_rows_committee_level_first():
    committees = parse_committees_full(AUCHINCLOSS)   # Energy And Commerce + 3 Subs
    mapper = _FakeMapper(
        sub_map={
            ('Energy And Commerce', 'Energy'): [
                {'sector': 'Energy', 'industry': 'Oil & Gas'}],
            ('Energy And Commerce', 'Health'): [
                {'sector': 'Healthcare', 'industry': 'Pharma'}],
        },
        comm_map={'Energy And Commerce': [
            {'sector': 'Utilities', 'industry': 'Electric'}]})
    out = committee_oversight_rows(committees, 'House', mapper)
    assert len(out) == 1
    rows = out[0]['rows']
    # erste Zeile = Committee-Ebene (weil comm_map gemappt)
    assert rows[0]['Subcommittee'] == '(Committee-Ebene)'
    assert rows[0]['Sektoren'] == 'Utilities'
    # danach je Subcommittee
    energy = next(r for r in rows if r['Subcommittee'] == 'Energy')
    assert energy['Sektoren'] == 'Energy'
    assert energy['Industries'] == 'Oil & Gas'
    env = next(r for r in rows if r['Subcommittee'] == 'Environment')
    assert env['Sektoren'] == ''     # ungemappt -> leer


def test_committee_oversight_rows_no_committee_level_when_unmapped():
    committees = parse_committees_full(MOORE)   # Budget (keine Subs) + Fin Serv
    mapper = _FakeMapper()    # nichts gemappt
    out = committee_oversight_rows(committees, 'House', mapper)
    budget = next(c for c in out if c['committee'] == 'Budget')
    # keine Subs, kein Committee-Mapping -> keine (Committee-Ebene)-Zeile
    assert budget['rows'] == []
    fs = next(c for c in out if c['committee'] == 'Financial Services')
    assert all(r['Subcommittee'] != '(Committee-Ebene)' for r in fs['rows'])
    assert len(fs['rows']) == 3   # 3 Subcommittees


def test_committee_oversight_rows_dedup_and_join():
    committees = parse_committees_full('Foo|:Bar|')
    mapper = _FakeMapper(sub_map={('Foo', 'Bar'): [
        {'sector': 'Tech', 'industry': 'SW'},
        {'sector': 'Tech', 'industry': 'HW'},    # Sektor-Duplikat
        {'sector': 'Health', 'industry': 'SW'},  # Industry-Duplikat
    ]})
    out = committee_oversight_rows(committees, 'House', mapper)
    row = out[0]['rows'][0]
    assert row['Sektoren'] == 'Tech, Health'     # dedupliziert, stabil
    assert row['Industries'] == 'SW, HW'


# ---------------------------------------------------------------------------
# committee_full / Aufsichts-Sektoren
# ---------------------------------------------------------------------------

def test_committee_full_mask():
    df = pd.DataFrame({'signals_triggered': [
        'trade_volume;committee_full;accumulative',
        'trade_volume;committee',          # committee != committee_full
        'committee_ex_officio;swarm_buy',  # ex_officio != committee_full
        None,
    ]})
    assert committee_full_mask(df).tolist() == [True, False, False, False]


def test_member_oversight_sectors():
    js = ('[{"sector": "Consumer Defensive", "industry": "X"}, '
          '{"sector": "Healthcare", "industry": "Y"}, '
          '{"sector": "Consumer Defensive", "industry": "Z"}]')
    df = pd.DataFrame({'Insider_Sectors': [js, js]})
    assert member_oversight_sectors(df) == {'Consumer Defensive', 'Healthcare'}
    assert member_oversight_sectors(pd.DataFrame({'x': [1]})) == set()


# ---------------------------------------------------------------------------
# D2-Gating-Konsistenz + D1-Buckets
# ---------------------------------------------------------------------------

def test_horizon_mean_returns_gating_consistency():
    """Bei Tag 40 geschlossener Trade faellt aus h>=60 (Attributierung)."""
    df = pd.DataFrame({
        'first_close_offset_days': [40.0, np.nan],   # geschlossen@40 / offen
        'post_return_30d': [10.0, 20.0],
        'post_return_60d': [100.0, 50.0],
    })
    means = horizon_mean_returns(df, H)
    assert means[30] == pytest.approx(15.0)   # beide qualifizieren bei 30
    assert means[60] == pytest.approx(50.0)   # nur der offene Trade bei 60


def test_horizon_bucket_shares():
    df = pd.DataFrame({
        'first_close_offset_days': [np.nan] * 4,
        'post_return_30d': [20.0, 3.0, -2.0, -30.0],   # >10 / mid / mid / <-10
    })
    b = horizon_bucket_shares(df, 30, threshold=10.0, horizons=H)
    assert b['n'] == 4
    assert b['above'] == pytest.approx(0.25)
    assert b['below'] == pytest.approx(0.25)
    assert b['mid'] == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# D5 / D6 / D7
# ---------------------------------------------------------------------------

def test_terminal_distribution_shares_sum_to_one():
    term = pd.Series([1.0, 2.0, 6.0, 7.0, 12.0, np.nan])
    dist = terminal_distribution(term, bin_pct=5.0)
    assert not dist.empty
    assert dist['share'].sum() == pytest.approx(1.0)
    assert int(dist['count'].sum()) == 5   # NaN ignoriert


def test_score_bins_mean_and_hitrate():
    df = pd.DataFrame({'trade_score': [1, 2, 6, 7]})
    term = pd.Series([10.0, 20.0, 0.0, 100.0])
    # Bins 5-weit: [0,5)->Scores 1,2 (term 10,20 -> mean 15); [5,10)->6,7 (0,100 -> mean 50)
    means = score_bins(df, term, value='mean', bin_pts=5).sort_values('bin_mid')
    assert means['value'].tolist() == pytest.approx([15.0, 50.0])
    hit = score_bins(df, term, value='hitrate', threshold=50.0,
                     bin_pts=5).sort_values('bin_mid')
    # Bin1: keiner >50 -> 0.0 ; Bin2: einer von zwei (100) -> 0.5
    assert hit['value'].tolist() == pytest.approx([0.0, 0.5])


# ---------------------------------------------------------------------------
# Z3 Sektor-Konzentration
# ---------------------------------------------------------------------------

def test_sector_concentration_oversight_and_shares():
    df = pd.DataFrame({
        'sector_yf': ['Technology', 'Technology', 'Healthcare', None],
        'trade_volume_usd': [100, 300, 200, 999],
    })
    res = sector_concentration(df, oversight={'Healthcare'})
    assert res['sector'].tolist() == ['Technology', 'Healthcare']  # vol desc
    assert res['volume_usd'].tolist() == [400, 200]
    assert res['share'].sum() == pytest.approx(1.0)
    assert res.set_index('sector')['is_oversight'].to_dict() == {
        'Technology': False, 'Healthcare': True}


# ---------------------------------------------------------------------------
# Quartal-Filter + 180-Toggle
# ---------------------------------------------------------------------------

def test_quarter_options_and_range_filter():
    df = pd.DataFrame({'transaction_date': pd.to_datetime([
        '2024-02-01', '2024-05-01', '2024-08-01', '2025-01-15'])})
    assert quarter_options(df) == ['2024Q1', '2024Q2', '2024Q3', '2025Q1']
    sub = filter_by_quarter_range(df, '2024Q2', '2024Q3')
    assert len(sub) == 2
    # invertierte Reihenfolge wird normalisiert
    assert len(filter_by_quarter_range(df, '2024Q3', '2024Q2')) == 2


def test_filter_long_trades_out():
    df = pd.DataFrame({'first_close_offset_days': [30.0, 200.0, np.nan]})
    assert len(filter_long_trades_out(df, enabled=False, horizons=H)) == 3
    out = filter_long_trades_out(df, enabled=True, horizons=H)
    assert out['first_close_offset_days'].tolist() == [30.0]   # >180 + offen raus


# ---------------------------------------------------------------------------
# Selektions-Aufloeser — pinnt den im Spike gefundenen customdata-Bug
# ---------------------------------------------------------------------------

def test_resolve_highlight_scalar_and_list_customdata():
    # 1D-customdata -> Skalar (der Bug-Fall aus dem Spike)
    assert resolve_highlight_from_selection(
        {'points': [{'curve_number': 0, 'customdata': 'A0'}]}) == 'A0'
    # 2D-customdata -> Liste (Streamlit-Doku-Form)
    assert resolve_highlight_from_selection(
        {'points': [{'curve_number': 2, 'customdata': [2.5]}]}) == 2.5
    # leere/fehlende Auswahl
    assert resolve_highlight_from_selection({'points': []}) is None
    assert resolve_highlight_from_selection({}) is None
    assert resolve_highlight_from_selection(None) is None


# ---------------------------------------------------------------------------
# Red-Flag-Summary
# ---------------------------------------------------------------------------

def test_red_flag_summary():
    df = pd.DataFrame({
        'Vote_Timing_Hit': [True, False, False, False],     # 25%
        'signals_triggered': ['committee_full', 'x', 'x', 'x'],  # 25%
    })
    member_means = {30: 12.0, 60: 8.0}
    bench_means = {30: 4.0, 60: 5.0}      # diff 8, 3 -> Ø 5.5
    terminal = pd.Series([5.0, 40.0, np.nan, 1.0])
    s = red_flag_summary(df, member_means, bench_means, terminal, horizons=[30, 60])
    assert s['n'] == 4
    assert s['vote_timing_rate'] == pytest.approx(0.25)
    assert s['committee_full_rate'] == pytest.approx(0.25)
    assert s['outperformance'] == pytest.approx(5.5)
    assert s['top_terminal'] == pytest.approx(40.0)
