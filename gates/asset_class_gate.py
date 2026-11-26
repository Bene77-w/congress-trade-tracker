"""
Congress Trade Tracker - Gate: Asset Class

Markiert Trades die keine handelbare Einzel-Aktie sind (ETFs, Options,
Bonds, Crypto, Warrants, Mutual Funds etc.) als asset_class_excluded=True.
Diese Trades bleiben im Master erhalten, bekommen aber keine Signale und
keinen Score und sind kein Input fuer Sequenz- und Schwarmsignale anderer
Trades (Maske vor den Signal-Schritten in TradeImporter.run()). Der
Vote-Timing-Tag prueft das Flag selbst.

Grundlage: Die via OpenFIGI befuellte ticker_type-Spalte.
Konfiguration: settings.ASSET_CLASS_KEEP_TYPES.

In der User-Sprache entspricht dieses Gate dem historischen "Filter 0".
"""

import logging

import pandas as pd

from config import settings

logger = logging.getLogger(__name__)

ASSET_CLASS_COLUMNS = ['asset_class_excluded']


class AssetClassGate:
    """
    Gate: Schliesst Nicht-Aktien von weiterer Verarbeitung aus.

    Input:  DataFrame mit Spalte 'ticker_type' (erzeugt von derive_ticker_types).
    Output: DataFrame mit zusaetzlicher bool-Spalte 'asset_class_excluded'.
    """

    def __init__(self):
        self.keep_types = set(settings.ASSET_CLASS_KEEP_TYPES)

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if 'ticker_type' not in df.columns:
            raise ValueError(
                "AssetClassGate.apply() benoetigt die Spalte 'ticker_type'. "
                "derive_ticker_types() muss vorher laufen."
            )

        df = df.copy()
        df['asset_class_excluded'] = ~df['ticker_type'].isin(self.keep_types)

        excluded = int(df['asset_class_excluded'].sum())
        kept = len(df) - excluded
        logger.info(
            f"Gate Asset-Klasse: {kept:,} behalten, {excluded:,} ausgefiltert "
            f"({excluded / len(df) * 100:.2f}%)"
        )

        # Top-5 ausgeschlossene Types zur Diagnose
        excluded_counts = df.loc[df['asset_class_excluded'], 'ticker_type'].value_counts()
        if not excluded_counts.empty:
            top = ', '.join(f"{t}={c}" for t, c in excluded_counts.head(5).items())
            logger.info(f"  Top ausgeschlossen: {top}")

        return df
