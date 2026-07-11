"""Tests fuer B5: Recent-Trades-Export ins Agent-Staging (app/staging_export.py).

Offline, ohne Netz/Streamlit. Deckt das feste §5b-Spaltenschema, die
direction-Ableitung (inkl. Erhalt vorhandener Werte), den Fehlende-Spalten-Pfad
(Schema-Stabilitaet), den Dateinamens-Zeitstempel und die Verzeichnis-Anlage ab.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.staging_export import (  # noqa: E402
    STAGING_COLUMNS,
    build_staging_frame,
    export_selection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _full_recent_pool() -> pd.DataFrame:
    """Dummy-recent_pool mit allen §5b-Quellspalten (2 Zeilen: Kauf/Verkauf)."""
    return pd.DataFrame([
        {
            'trade_id': 'trd_0001',
            'name': 'Doe, John', 'chamber': 'House', 'Member_Party': 'R',
            'ticker': 'AAPL', 'issuer_ticker': 'AAPL', 'asset_name': 'Apple Inc',
            'transaction_type': 'Purchase',
            'transaction_date': '2026-05-10', 'disclosure_date': '2026-05-20',
            'days_since_trade': 40,
            'amount': '$1,001 - $15,000', 'trade_volume_usd': 8000,
            'trade_amount_max_usd': 15000,
            'sector_yf': 'Technology', 'industry_yf': 'Consumer Electronics',
            'relevant_sectors': '[]',
            'committee_match': 'yes',
            'Member_Committees_Full': 'Financial Services',
            'exit_status': 'held',
        },
        {
            'trade_id': 'trd_0002',
            'name': 'Roe, Jane', 'chamber': 'Senate', 'Member_Party': 'D',
            'ticker': 'MSFT', 'issuer_ticker': 'MSFT',
            'asset_name': 'Microsoft Corp',
            'transaction_type': 'Sale (Full)',
            'transaction_date': '2026-05-12', 'disclosure_date': '2026-05-22',
            'days_since_trade': 38,
            'amount': '$15,001 - $50,000', 'trade_volume_usd': 30000,
            'trade_amount_max_usd': 50000,
            'sector_yf': 'Technology', 'industry_yf': 'Software',
            'relevant_sectors': '[]',
            'committee_match': '',
            'Member_Committees_Full': 'Armed Services',
            'exit_status': 'closed',
        },
    ])


# ---------------------------------------------------------------------------
# Schema + direction
# ---------------------------------------------------------------------------
def test_build_frame_exact_schema_and_order():
    """Ausgabe hat exakt STAGING_COLUMNS in der §5b-Reihenfolge."""
    frame = build_staging_frame(_full_recent_pool(), export_ts='2026-07-11T10:00:00')
    assert list(frame.columns) == STAGING_COLUMNS
    # §5b-Liste wortgetreu (Regression gegen versehentliches Umbenennen).
    assert STAGING_COLUMNS == [
        'trade_id', 'name', 'chamber', 'Member_Party',
        'ticker', 'issuer_ticker', 'asset_name',
        'transaction_type', 'direction',
        'transaction_date', 'disclosure_date', 'days_since_trade',
        'amount', 'trade_volume_usd', 'trade_amount_max_usd',
        'sector_yf', 'industry_yf', 'relevant_sectors',
        'committee_match', 'Member_Committees_Full',
        'exit_status', 'export_ts',
    ]


def test_direction_derived_from_transaction_type():
    """Purchase -> buy, Sale* -> sell (via committee_signal-Funktion)."""
    frame = build_staging_frame(_full_recent_pool(), export_ts='x')
    assert list(frame['direction']) == ['buy', 'sell']


def test_direction_existing_preserved_else_derived():
    """Vorhandene, nicht-leere direction bleibt; leere wird abgeleitet."""
    df = _full_recent_pool()
    df['direction'] = ['manuell', '']  # zweite Zelle leer -> ableiten
    frame = build_staging_frame(df, export_ts='x')
    assert list(frame['direction']) == ['manuell', 'sell']


def test_export_ts_populated():
    """export_ts wird in jede Zeile geschrieben."""
    frame = build_staging_frame(_full_recent_pool(), export_ts='2026-07-11T10:00:00')
    assert list(frame['export_ts']) == ['2026-07-11T10:00:00'] * 2


def test_default_export_ts_is_iso_string():
    """Ohne export_ts wird ein ISO-Zeitstempel gesetzt (nicht leer/NaN)."""
    frame = build_staging_frame(_full_recent_pool())
    val = frame['export_ts'].iloc[0]
    assert isinstance(val, str) and 'T' in val


# ---------------------------------------------------------------------------
# Fehlende-Spalten-Pfad (Schema-Stabilitaet)
# ---------------------------------------------------------------------------
def test_missing_source_columns_filled_empty(caplog):
    """Fehlt eine Quellspalte, wird sie leer ergaenzt + Warnung geloggt."""
    df = _full_recent_pool().drop(columns=['sector_yf', 'Member_Committees_Full'])
    import logging
    with caplog.at_level(logging.WARNING):
        frame = build_staging_frame(df, export_ts='x')
    assert list(frame.columns) == STAGING_COLUMNS
    assert frame['sector_yf'].isna().all()
    assert frame['Member_Committees_Full'].isna().all()
    assert 'sector_yf' in caplog.text
    assert 'Member_Committees_Full' in caplog.text


def test_missing_transaction_type_direction_other(caplog):
    """Ohne transaction_type faellt direction auf 'other' (mit Warnung)."""
    df = _full_recent_pool().drop(columns=['transaction_type'])
    import logging
    with caplog.at_level(logging.WARNING):
        frame = build_staging_frame(df, export_ts='x')
    assert list(frame['direction']) == ['other', 'other']
    assert 'transaction_type' in caplog.text


# ---------------------------------------------------------------------------
# export_selection: Datei, Dateiname, Verzeichnis-Anlage
# ---------------------------------------------------------------------------
def test_export_creates_dir_and_file(tmp_path):
    """Zielverzeichnis wird angelegt, Datei geschrieben, Path zurueckgegeben."""
    staging = tmp_path / 'agent_staging'  # existiert noch nicht
    path = export_selection(
        _full_recent_pool(), staging_dir=staging, timestamp='20260711-100000',
        export_ts='2026-07-11T10:00:00',
    )
    assert staging.is_dir()
    assert path == staging / 'trade_selection_20260711-100000.csv'
    assert path.is_file()


def test_export_filename_and_roundtrip(tmp_path):
    """Dateiname traegt den Timestamp; CSV liest schema-treu zurueck."""
    path = export_selection(
        _full_recent_pool(), staging_dir=tmp_path, timestamp='20260711-235959',
        export_ts='2026-07-11T23:59:59',
    )
    assert path.name == 'trade_selection_20260711-235959.csv'
    back = pd.read_csv(path)
    assert list(back.columns) == STAGING_COLUMNS
    assert len(back) == 2
    assert list(back['direction']) == ['buy', 'sell']
    assert list(back['trade_id']) == ['trd_0001', 'trd_0002']


def test_export_uses_settings_default_dir(monkeypatch, tmp_path):
    """Ohne staging_dir wird settings.AGENT_STAGING_DIR verwendet."""
    from config import settings
    from app import staging_export
    monkeypatch.setattr(settings, 'AGENT_STAGING_DIR', tmp_path / 'st_default')
    path = staging_export.export_selection(
        _full_recent_pool(), timestamp='20260711-120000',
    )
    assert path.parent == tmp_path / 'st_default'
    assert path.is_file()
