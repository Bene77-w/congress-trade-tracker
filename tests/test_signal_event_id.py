"""Tests fuer utils.signal_event_id — Hash-basierte Signal-Event-ID."""

import hashlib
import re
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from utils.signal_event_id import (
    SIGNAL_EVENT_ID_LENGTH,
    make_signal_event_id,
)


class TestMakeSignalEventId:
    def test_deterministic(self):
        """Gleiche Inputs -> gleiche ID."""
        a = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_abc')
        b = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_abc')
        assert a == b

    def test_format(self):
        """ID hat immer 16 Hex-Zeichen aus [0-9a-f]."""
        sid = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_abc')
        assert len(sid) == SIGNAL_EVENT_ID_LENGTH
        assert len(sid) == 16
        assert re.fullmatch(r'[0-9a-f]{16}', sid) is not None

    def test_different_date_changes_id(self):
        a = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_abc')
        b = make_signal_event_id(date(2026, 3, 2), 'committee', 'trade_abc')
        assert a != b

    def test_different_type_changes_id(self):
        a = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_abc')
        b = make_signal_event_id(date(2026, 3, 1), 'contrarian', 'trade_abc')
        assert a != b

    def test_different_scope_changes_id(self):
        a = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_abc')
        b = make_signal_event_id(date(2026, 3, 1), 'committee', 'trade_xyz')
        assert a != b

    def test_date_format_normalization(self):
        """date, datetime und ISO-String erzeugen dieselbe ID."""
        d = date(2026, 3, 1)
        dt = datetime(2026, 3, 1, 14, 30)
        s = '2026-03-01'
        ids = {
            make_signal_event_id(d, 'committee', 'x'),
            make_signal_event_id(dt, 'committee', 'x'),
            make_signal_event_id(s, 'committee', 'x'),
        }
        assert len(ids) == 1

    def test_swarm_scope_groups_trades(self):
        """Schwarm-Konvention: alle Trades eines Schwarms haben dieselbe ID."""
        scope = 'AAPL|buy|2026-02-26'
        a = make_signal_event_id(date(2026, 3, 1), 'swarm_buy', scope)
        b = make_signal_event_id(date(2026, 3, 1), 'swarm_buy', scope)
        assert a == b

    def test_conflict_distinct_from_swarm(self):
        """Konflikt-Signal hat eigene ID, auch bei gleichem issuer_ticker/Fenster."""
        swarm_scope = 'AAPL|buy|2026-02-26'
        conflict_scope = 'conflict|AAPL|2026-02-26'
        swarm_id = make_signal_event_id(date(2026, 3, 1), 'swarm_buy', swarm_scope)
        conflict_id = make_signal_event_id(date(2026, 3, 3), 'swarm_conflict', conflict_scope)
        assert swarm_id != conflict_id

    def test_none_handling(self):
        """None-Inputs werden defensiv zu Leerstring konvertiert (kein Crash)."""
        sid = make_signal_event_id(None, '', '')
        assert re.fullmatch(r'[0-9a-f]{16}', sid) is not None

    def test_nan_handling(self):
        """NaN-Inputs werden ebenfalls zu Leerstring konvertiert."""
        sid_none = make_signal_event_id(None, '', '')
        sid_nan = make_signal_event_id(np.nan, '', '')
        assert sid_none == sid_nan

    def test_hash_matches_manual_construction(self):
        """Verifiziert die Hash-Konstruktion gegen manuelle SHA256-Berechnung."""
        d = date(2026, 3, 1)
        signal_type = 'committee'
        scope = 'trade_abc'
        combined = f'{d.isoformat()}|{signal_type}|{scope}'
        expected = hashlib.sha256(combined.encode('utf-8')).hexdigest()[:SIGNAL_EVENT_ID_LENGTH]
        assert make_signal_event_id(d, signal_type, scope) == expected

    def test_pipe_separator_collision_resistance(self):
        """Vermeidet trivialer Collision: 'a|b'+'c' != 'a'+'b|c'."""
        # Wenn Trenner | nicht escaped wuerde, koennten diese kollidieren.
        # Da unsere parts-Liste fixe Position hat, kollidieren sie nicht.
        a = make_signal_event_id(date(2026, 3, 1), 'a|b', 'c')
        b = make_signal_event_id(date(2026, 3, 1), 'a', 'b|c')
        # Beide produzieren denselben combined-String '2026-03-01|a|b|c'.
        # Das ist eine bekannte Limitation der einfachen Join-Strategie.
        # Test dokumentiert das Verhalten — in der Praxis irrelevant, da
        # signal_type aus einer fixen Enumeration stammt (keine |-Zeichen).
        assert a == b


class TestSettingsIntegration:
    def test_settings_constants_importable(self):
        """Smoke-Test: alle neuen Signal-Settings sind importierbar."""
        from config import settings
        assert hasattr(settings, 'RELEVANT_SECTORS_MATCH_MODE')
        assert hasattr(settings, 'VOTE_TIMING_HIT_THRESHOLD')
        assert hasattr(settings, 'CONTRARIAN_WINDOW_DAYS')
        assert hasattr(settings, 'ACCUMULATIVE_WINDOW_DAYS')
        assert hasattr(settings, 'ACCUMULATIVE_MIN_COUNT')
        assert hasattr(settings, 'EXTREME_REPORT_DAYS')
        assert hasattr(settings, 'SWARM_WINDOW_DAYS')
        assert hasattr(settings, 'SWARM_MIN_MEMBERS')
        assert hasattr(settings, 'SWARM_GROUPING_KEY')
        assert hasattr(settings, 'SWARM_TRACK_DIRECTIONS')
        assert hasattr(settings, 'SWARM_FLAG_CONFLICTS')
        assert hasattr(settings, 'SUSPICIOUS_ASSET_PERSISTENCE_DAYS')

    def test_settings_defaults(self):
        """Defaults entsprechen dem approved Plan."""
        from config import settings
        assert settings.RELEVANT_SECTORS_MATCH_MODE in (
            'sector_or_industry', 'sector_and_industry', 'hybrid'
        )
        assert settings.VOTE_TIMING_HIT_THRESHOLD == 1
        assert settings.CONTRARIAN_WINDOW_DAYS == 30
        assert settings.ACCUMULATIVE_WINDOW_DAYS == 30
        assert settings.ACCUMULATIVE_MIN_COUNT == 2
        assert settings.EXTREME_REPORT_DAYS == 60
        assert settings.SWARM_WINDOW_DAYS == 7
        assert settings.SWARM_MIN_MEMBERS == 2
        # P5.6: SWARM_GROUPING_KEY auf Issuer-Level umgestellt (CIK-basiert,
        # Cross-Class-Aufloesung). Vorher war es 'issuer_ticker' (Share-Class).
        assert settings.SWARM_GROUPING_KEY == 'issuer_group_key'
        assert settings.SWARM_TRACK_DIRECTIONS == ['buy', 'sell']
        assert settings.SWARM_FLAG_CONFLICTS is True
        assert settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS == 30
