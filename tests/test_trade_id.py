"""Tests für utils.trade_id — Hash-basierte Trade-ID."""

import hashlib
import re

import numpy as np
import pandas as pd
import pytest

from utils.trade_id import (
    TRADE_ID_LENGTH,
    TRADE_ID_SOURCE_COLUMNS,
    compute_trade_id,
    compute_trade_ids,
)


def _row(**overrides):
    """Baut eine pd.Series mit allen TRADE_ID_SOURCE_COLUMNS + Overrides."""
    base = {
        'name': 'Pelosi',
        'ticker': 'AAPL',
        'asset_name': 'Apple Inc',
        'transaction_type': 'Purchase',
        'amount': '50000',
        'transaction_date': '2026-01-15',
        'subholding': '',
        'comment': '',
    }
    base.update(overrides)
    return pd.Series(base)


class TestComputeTradeId:
    def test_deterministic(self):
        """Gleiche Inhalte → gleiche ID."""
        row1 = _row()
        row2 = _row()
        assert compute_trade_id(row1) == compute_trade_id(row2)

    def test_different_inputs_different_ids(self):
        """Unterschiedliche Inhalte → unterschiedliche IDs."""
        row1 = _row(ticker='AAPL')
        row2 = _row(ticker='MSFT')
        assert compute_trade_id(row1) != compute_trade_id(row2)

    def test_null_handling(self):
        """None/NaN werden als leere Strings behandelt."""
        row_none = _row(name=None, subholding=None, comment=None)
        row_nan = _row(name=np.nan, subholding=np.nan, comment=np.nan)
        row_empty = _row(name='', subholding='', comment='')
        id_none = compute_trade_id(row_none)
        id_nan = compute_trade_id(row_nan)
        id_empty = compute_trade_id(row_empty)
        assert id_none == id_empty
        assert id_nan == id_empty
        assert len(id_none) == TRADE_ID_LENGTH

    def test_length(self):
        """ID hat immer 16 Hex-Zeichen aus [0-9a-f]."""
        tid = compute_trade_id(_row())
        assert len(tid) == TRADE_ID_LENGTH
        assert len(tid) == 16
        assert re.fullmatch(r'[0-9a-f]{16}', tid) is not None

    def test_order_matters(self):
        """Reihenfolge der Spalten in TRADE_ID_SOURCE_COLUMNS ist Teil der ID.

        Dokumentiert die Invariante: Wenn wir name und ticker tauschen,
        bekommen wir eine andere ID — selbst wenn die Werte dieselben sind.
        """
        # Referenz über die reale Funktion
        row = _row(name='AAA', ticker='BBB')
        ref = compute_trade_id(row)

        # Manuelle Berechnung mit vertauschter Reihenfolge — sollte abweichen
        swapped_order = list(TRADE_ID_SOURCE_COLUMNS)
        swapped_order[0], swapped_order[1] = swapped_order[1], swapped_order[0]
        parts = [str(row.get(c, '')) for c in swapped_order]
        combined = '|'.join(parts)
        swapped = hashlib.sha256(combined.encode('utf-8')).hexdigest()[:TRADE_ID_LENGTH]
        assert ref != swapped

    def test_missing_column_equals_empty(self):
        """Fehlt eine Source-Spalte komplett, wird sie wie Leerstring behandelt."""
        full = _row(comment='')
        partial = pd.Series({k: v for k, v in full.items() if k != 'comment'})
        assert compute_trade_id(full) == compute_trade_id(partial)


class TestComputeTradeIds:
    def test_vectorized_matches_rowwise(self):
        df = pd.DataFrame([
            {'name': 'A', 'ticker': 'X', 'asset_name': 'XX',
             'transaction_type': 'Purchase', 'amount': '100',
             'transaction_date': '2026-01-01', 'subholding': '', 'comment': ''},
            {'name': 'B', 'ticker': 'Y', 'asset_name': 'YY',
             'transaction_type': 'Sale', 'amount': '200',
             'transaction_date': '2026-02-02', 'subholding': '', 'comment': ''},
        ])
        ids = compute_trade_ids(df)
        assert list(ids) == [compute_trade_id(df.iloc[0]), compute_trade_id(df.iloc[1])]

    def test_empty_df(self):
        empty = pd.DataFrame(columns=TRADE_ID_SOURCE_COLUMNS)
        ids = compute_trade_ids(empty)
        assert len(ids) == 0

