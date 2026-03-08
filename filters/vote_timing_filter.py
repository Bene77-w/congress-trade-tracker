"""
Congress Trade Tracker - Filter 4: Vote Timing

Erkennt Trades die kurz vor/nach einer relevanten Kongress-Abstimmung
stattfanden. Nutzt Policy Area → Sektor Mapping fuer thematischen Abgleich.

Logik:
1. Fuer jeden Trade: Sektoren aus Relevant_Sectors extrahieren
2. Votes mit uebereinstimmendem mapped_sector finden
3. Zeitfenster pruefen: trade_date ZWISCHEN (vote_date - BEFORE) und (vote_date + AFTER)
4. Bei Treffer: naechstgelegenen Vote waehlen
"""

import os
import sys
import json
import logging
from datetime import timedelta

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import settings

logger = logging.getLogger(__name__)

# Pfade
RAW_DIR = os.path.join(BASE_DIR, settings.DATA_RAW_PATH)
VOTES_CSV_PATH = os.path.join(RAW_DIR, 'congress_votes.csv')

# Neue Spalten die dieser Filter hinzufuegt
VOTE_TIMING_COLUMNS = [
    'Vote_Timing_Hit',      # bool: Trade im Zeitfenster?
    'Vote_Date',             # str: Datum der Abstimmung
    'Vote_Bill_Title',       # str: Bill-Titel
    'Vote_Policy_Area',      # str: Policy Area
    'Vote_Timing_Score',     # int: 0 oder VOTE_TIMING_SCORE
    'Days_To_Vote',          # int: Abstand (negativ = vor Abstimmung)
]


class VoteTimingFilter:
    """
    Filter 4: Vote Timing - erkennt Trades nahe an Abstimmungen.
    """

    def __init__(self):
        self.window_before = settings.VOTE_TIMING_WINDOW_BEFORE
        self.window_after = settings.VOTE_TIMING_WINDOW_AFTER
        self.timing_score = settings.VOTE_TIMING_SCORE

    def load_votes(self) -> pd.DataFrame:
        """
        Laedt congress_votes.csv.

        Returns:
            DataFrame mit Vote-Daten, oder leerer DataFrame
        """
        if not os.path.exists(VOTES_CSV_PATH):
            logger.warning(
                f"congress_votes.csv nicht gefunden: {VOTES_CSV_PATH}\n"
                "Bitte zuerst 'python scripts/fetch_congress_votes.py' ausfuehren."
            )
            return pd.DataFrame()

        df = pd.read_csv(VOTES_CSV_PATH)
        df['vote_date'] = pd.to_datetime(df['vote_date'], errors='coerce')
        df = df.dropna(subset=['vote_date'])

        logger.info(f"Votes geladen: {len(df)}")
        return df

    def _extract_trade_sectors(self, relevant_sectors_str: str) -> set[str]:
        """
        Extrahiert YF-Sektoren aus dem Relevant_Sectors JSON-String.

        Args:
            relevant_sectors_str: JSON-String aus trades_master.csv

        Returns:
            Set von Sektor-Namen
        """
        if not relevant_sectors_str or pd.isna(relevant_sectors_str):
            return set()

        try:
            sectors_list = json.loads(relevant_sectors_str)
            return {s.get('sector', '') for s in sectors_list if s.get('sector')}
        except (json.JSONDecodeError, TypeError):
            return set()

    def _parse_mapped_sectors(self, mapped_sectors_str: str) -> set[str]:
        """
        Parst das kommaseparierte mapped_sectors Feld aus congress_votes.csv.

        Args:
            mapped_sectors_str: z.B. "Industrials,Technology"

        Returns:
            Set von Sektor-Namen
        """
        if not mapped_sectors_str or pd.isna(mapped_sectors_str):
            return set()
        return {s.strip() for s in str(mapped_sectors_str).split(',') if s.strip()}

    def apply(self, trades_df: pd.DataFrame, votes_df: pd.DataFrame = None) -> pd.DataFrame:
        """
        Wendet den Vote-Timing-Filter auf alle Trades an.

        Args:
            trades_df: DataFrame mit Trades (muss transaction_date + Relevant_Sectors haben)
            votes_df: DataFrame mit Votes (optional, wird sonst geladen)

        Returns:
            trades_df mit neuen Vote_Timing_* Spalten
        """
        df = trades_df.copy()

        # Defaults setzen
        df['Vote_Timing_Hit'] = False
        df['Vote_Date'] = ''
        df['Vote_Bill_Title'] = ''
        df['Vote_Policy_Area'] = ''
        df['Vote_Timing_Score'] = 0
        df['Days_To_Vote'] = 0

        # Votes laden
        if votes_df is None:
            votes_df = self.load_votes()

        if votes_df.empty:
            logger.info("Keine Vote-Daten verfuegbar, ueberspringe Vote-Timing-Filter")
            return df

        # Nur Votes mit Sektor-Mapping beruecksichtigen
        votes_mapped = votes_df[
            votes_df['mapped_sectors'].notna() & (votes_df['mapped_sectors'] != '')
        ].copy()

        if votes_mapped.empty:
            logger.info("Keine gemappten Votes, ueberspringe Vote-Timing-Filter")
            return df

        # transaction_date sicherstellen
        if 'transaction_date' not in df.columns:
            logger.warning("Keine transaction_date Spalte, ueberspringe Vote-Timing-Filter")
            return df

        df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')

        # Zeitfenster
        window_before = timedelta(days=self.window_before)
        window_after = timedelta(days=self.window_after)

        # Vote-Sektoren vorberechnen
        votes_mapped['_sectors_set'] = votes_mapped['mapped_sectors'].apply(
            self._parse_mapped_sectors
        )

        hits = 0
        total = len(df)

        for idx, trade in df.iterrows():
            trade_date = trade['transaction_date']
            if pd.isna(trade_date):
                continue

            # Trade-Sektoren aus Committee-Mapping (Relevant_Sectors)
            trade_sectors = self._extract_trade_sectors(
                trade.get('Relevant_Sectors', '')
            )
            if not trade_sectors:
                continue

            # Zeitfenster: vote_date muss NACH trade_date liegen (innerhalb window)
            # trade_date liegt VOR der Abstimmung → verdaechtig
            window_start = trade_date - window_after   # kleine Toleranz nach hinten
            window_end = trade_date + window_before     # max X Tage vor Vote

            # Votes im Zeitfenster filtern
            in_window = votes_mapped[
                (votes_mapped['vote_date'] >= window_start) &
                (votes_mapped['vote_date'] <= window_end)
            ]

            if in_window.empty:
                continue

            # Sektor-Match pruefen
            best_vote = None
            best_days = float('inf')

            for _, vote in in_window.iterrows():
                vote_sectors = vote['_sectors_set']
                if not trade_sectors.intersection(vote_sectors):
                    continue

                # Abstand berechnen (negativ = Trade vor Vote)
                days_diff = (vote['vote_date'] - trade_date).days

                if abs(days_diff) < abs(best_days):
                    best_days = days_diff
                    best_vote = vote

            if best_vote is not None:
                df.at[idx, 'Vote_Timing_Hit'] = True
                df.at[idx, 'Vote_Date'] = best_vote['vote_date'].strftime('%Y-%m-%d')
                df.at[idx, 'Vote_Bill_Title'] = str(best_vote.get('bill_title', ''))
                df.at[idx, 'Vote_Policy_Area'] = str(best_vote.get('policy_area', ''))
                df.at[idx, 'Vote_Timing_Score'] = self.timing_score
                df.at[idx, 'Days_To_Vote'] = best_days
                hits += 1

        # Aufräumen: temporaere Spalte entfernen
        if '_sectors_set' in votes_mapped.columns:
            votes_mapped.drop(columns=['_sectors_set'], inplace=True)

        logger.info(f"Vote-Timing: {hits}/{total} Trades mit Timing-Hit "
                     f"({hits/total*100:.1f}%)" if total else "Keine Trades")

        return df
