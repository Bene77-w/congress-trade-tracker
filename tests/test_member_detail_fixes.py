"""Tests fuer die 5 Fixes in app/member_detail.py (B1/Z1a/B2/B3/R3).

Rein logische Helfer (keine Streamlit-Runtime). Deckt ab:
- B1: RED_FLAG_LEVELS emoji-frei
- Z1a: _count_red_flags respektiert die Settings-Schwellen (monkeypatch)
- B2: _resolve_highlight validiert den Highlight-Key gegen Trajektorien
- R3: _resolve_ar_outlier_thresholds (absolute/percentile + Fallback)
"""
import re

import pandas as pd
import pytest

import config.settings as settings
from app import member_detail as md


# ---------------------------------------------------------------------------
# B1 — Red-Flag-Level emoji-frei
# ---------------------------------------------------------------------------

# Grobe Emoji-Range (BMP-Symbole + Supplementary). Deckt die frueher genutzten
# Ampel-Emojis 🟢🟡🟠🔴 ab.
_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF]')


def test_red_flag_levels_no_emoji():
    assert len(md.RED_FLAG_LEVELS) == 4
    for label in md.RED_FLAG_LEVELS:
        assert not _EMOJI_RE.search(label), f'Emoji in {label!r}'


def test_red_flag_levels_use_color_syntax():
    # Streamlit-Farbsyntax muss erhalten bleiben (Rendering via st.markdown).
    joined = ' '.join(md.RED_FLAG_LEVELS)
    assert ':green[' in joined and ':orange[' in joined and ':red[' in joined


# ---------------------------------------------------------------------------
# Z1a — Red-Flag-Zaehlung respektiert Settings-Schwellen
# ---------------------------------------------------------------------------

def _summary(vt=0.0, cf=0.0, outp=0.0):
    return {'vote_timing_rate': vt, 'committee_full_rate': cf,
            'outperformance': outp}


def test_count_red_flags_all_below():
    assert md._count_red_flags(_summary(0.1, 0.1, 1.0)) == 0


def test_count_red_flags_all_above():
    assert md._count_red_flags(_summary(0.5, 0.5, 20.0)) == 3


def test_count_red_flags_nan_outperformance_ignored():
    # NaN outperformance darf nicht als Flag zaehlen.
    s = _summary(0.0, 0.0, float('nan'))
    assert md._count_red_flags(s) == 0


def test_count_red_flags_respects_settings_thresholds(monkeypatch):
    # Bei 0.10 gesetzt und Default-Schwelle 0.20 -> kein Flag.
    s = _summary(vt=0.10)
    assert md._count_red_flags(s) == 0
    # Schwelle runtersetzen -> jetzt zaehlt es.
    monkeypatch.setattr(settings, 'DETAIL_RED_FLAG_VOTE_TIMING_RATE', 0.05)
    assert md._count_red_flags(s) == 1


def test_count_red_flags_outperformance_threshold(monkeypatch):
    s = _summary(outp=3.0)
    assert md._count_red_flags(s) == 0  # Default 5.0
    monkeypatch.setattr(settings, 'DETAIL_RED_FLAG_OUTPERFORMANCE_PP', 2.0)
    assert md._count_red_flags(s) == 1


# ---------------------------------------------------------------------------
# B2 — Highlight-Validierung
# ---------------------------------------------------------------------------

def _trajs(*ids):
    return [{'trade_id': i} for i in ids]


def test_resolve_highlight_none():
    assert md._resolve_highlight(None, _trajs('a', 'b')) is None


def test_resolve_highlight_valid():
    assert md._resolve_highlight('b', _trajs('a', 'b')) == 'b'


def test_resolve_highlight_orphan_returns_none():
    # Highlight zeigt auf Trade eines anderen Members -> None.
    assert md._resolve_highlight('x', _trajs('a', 'b')) is None


def test_resolve_highlight_empty_trajs():
    assert md._resolve_highlight('a', []) is None


# ---------------------------------------------------------------------------
# R3 — AR-Ausreisser-Schwellen-Resolver
# ---------------------------------------------------------------------------

def test_ar_thresholds_absolute_mode(monkeypatch):
    monkeypatch.setattr(settings, 'AR_OUTLIER_MODE', 'absolute')
    monkeypatch.setattr(settings, 'AR_OUTLIER_UPPER', 0.03)
    monkeypatch.setattr(settings, 'AR_OUTLIER_LOWER', -0.03)
    up, lo, fb = md._resolve_ar_outlier_thresholds(pd.Series([0.1, -0.2, 0.5]))
    assert (up, lo, fb) == (0.03, -0.03, False)


def test_ar_thresholds_percentile_mode(monkeypatch):
    monkeypatch.setattr(settings, 'AR_OUTLIER_MODE', 'percentile')
    monkeypatch.setattr(settings, 'AR_OUTLIER_PCT_UPPER', 0.9)
    monkeypatch.setattr(settings, 'AR_OUTLIER_PCT_LOWER', 0.1)
    ref = pd.Series([float(i) / 100 for i in range(101)])  # 0.00 .. 1.00
    up, lo, fb = md._resolve_ar_outlier_thresholds(ref)
    assert fb is False
    assert up == pytest.approx(0.90, abs=1e-9)
    assert lo == pytest.approx(0.10, abs=1e-9)


def test_ar_thresholds_percentile_fallback_on_thin_ref(monkeypatch):
    monkeypatch.setattr(settings, 'AR_OUTLIER_MODE', 'percentile')
    monkeypatch.setattr(settings, 'AR_OUTLIER_UPPER', 0.03)
    monkeypatch.setattr(settings, 'AR_OUTLIER_LOWER', -0.03)
    # < 2 verwertbare Werte -> Fallback auf absolute Schwellen.
    up, lo, fb = md._resolve_ar_outlier_thresholds(pd.Series([float('nan')]))
    assert (up, lo, fb) == (0.03, -0.03, True)


def test_ar_thresholds_percentile_drops_nan(monkeypatch):
    monkeypatch.setattr(settings, 'AR_OUTLIER_MODE', 'percentile')
    monkeypatch.setattr(settings, 'AR_OUTLIER_PCT_UPPER', 1.0)
    monkeypatch.setattr(settings, 'AR_OUTLIER_PCT_LOWER', 0.0)
    ref = pd.Series([0.0, float('nan'), 1.0])
    up, lo, fb = md._resolve_ar_outlier_thresholds(ref)
    assert fb is False
    assert up == pytest.approx(1.0) and lo == pytest.approx(0.0)
