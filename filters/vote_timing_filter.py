"""
Congress Trade Tracker - Filter 4: Vote Timing

Erkennt Trades die kurz vor/nach einer relevanten Kongress-Abstimmung
stattfanden. Nutzt Legislative Subjects → YF-Sektor Mapping fuer
thematischen Abgleich (granularer als Policy Area Mapping).

Logik:
1. Fuer jeden Bill: legislative_subjects aus bill_subjects.json laden
2. Jedes Subject gegen legislative_subject_mapping_complete.csv matchen
3. Union aller gemappten Sektoren = Bill's Sektor-Footprint
4. Fuer jeden Trade: Sektoren aus Relevant_Sectors (Intersection
   Insider × Ticker) extrahieren
5. Sektor-Intersection pruefen + Zeitfenster
6. Alle Treffer im Fenster werden in Vote_Timing_Hits (JSON-Liste)
   gespeichert, Vote_Timing_Hit_Count = Anzahl der Treffer.
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
CONGRESS_API_DIR = os.path.join(BASE_DIR, settings.DATA_CONGRESS_API_PATH)
BILL_ACTIONS_PATH = os.path.join(CONGRESS_API_DIR, 'bill_actions.json')
BILL_SUBJECTS_PATH = os.path.join(CONGRESS_API_DIR, settings.BILL_SUBJECTS_FILE)
SUBJECT_MAPPING_PATH = os.path.join(
    BASE_DIR, settings.DATA_MAPPINGS_PATH, settings.LEGISLATIVE_SUBJECT_MAPPING_FILE
)

# Neue Spalten die dieser Filter hinzufuegt
VOTE_TIMING_COLUMNS = [
    'Vote_Timing_Hit',         # bool: mindestens ein Vote im Zeitfenster mit Sektor-Match?
    'Vote_Timing_Hits',        # str: JSON-Liste aller Treffer (Datum, Bill, Policy Area, Days_To_Vote, Konfidenz)
    'Vote_Timing_Hit_Count',   # int: Anzahl Treffer in Vote_Timing_Hits
]


class VoteTimingFilter:
    """
    Filter 4: Vote Timing - erkennt Trades nahe an Abstimmungen.

    Nutzt legislative_subject_mapping_complete.csv fuer Subject → Sektor Mapping
    statt des groben Policy Area Mappings.
    """

    def __init__(self):
        self.window_before = settings.VOTE_TIMING_WINDOW_BEFORE
        self.window_after = settings.VOTE_TIMING_WINDOW_AFTER
        self._subject_mapping = None

    def _load_subject_mapping(self) -> dict:
        """
        Laedt legislative_subject_mapping_complete.csv in ein Dict.

        Subjects koennen mehrere Sektoren haben (sector_rank 1, 2, 3).
        Alle werden gesammelt, die hoechste Konfidenz pro Subject behalten.

        Returns:
            Dict {subject_name: {'sectors': set[str], 'konfidenz': str}}
            Nur Eintraege mit nicht-leerem yf_sector.
        """
        if self._subject_mapping is not None:
            return self._subject_mapping

        if not os.path.exists(SUBJECT_MAPPING_PATH):
            logger.warning(f"Subject-Mapping nicht gefunden: {SUBJECT_MAPPING_PATH}")
            self._subject_mapping = {}
            return self._subject_mapping

        df = pd.read_csv(SUBJECT_MAPPING_PATH, encoding='utf-8')
        mapping = {}
        konfidenz_order = {'Hoch': 3, 'Mittel': 2, 'Niedrig': 1}

        for _, row in df.iterrows():
            sector = row.get('yf_sector', '')
            if pd.isna(sector) or not str(sector).strip():
                continue

            subject = str(row.get('legislative_subject', '')).strip()
            if not subject:
                continue

            konfidenz = str(row.get('konfidenz', 'Niedrig')).strip()

            if subject not in mapping:
                mapping[subject] = {'sectors': set(), 'konfidenz': konfidenz}

            mapping[subject]['sectors'].add(str(sector).strip())
            # Hoechste Konfidenz behalten
            if konfidenz_order.get(konfidenz, 0) > konfidenz_order.get(mapping[subject]['konfidenz'], 0):
                mapping[subject]['konfidenz'] = konfidenz

        self._subject_mapping = mapping
        total_sectors = sum(len(e['sectors']) for e in mapping.values())
        logger.info(f"Subject-Mapping geladen: {len(mapping)} Subjects, {total_sectors} Sektor-Zuordnungen")
        return self._subject_mapping

    def _map_subjects_to_sectors(self, legislative_subjects: list) -> tuple[set, str]:
        """
        Mappt eine Liste von legislative_subjects auf YF-Sektoren.

        Args:
            legislative_subjects: Liste von Subject-Strings aus bill_subjects.json

        Returns:
            Tuple (set von Sektor-Namen, hoechste Konfidenz als String)
        """
        mapping = self._load_subject_mapping()
        sectors = set()
        best_konfidenz = 'Niedrig'
        konfidenz_order = {'Hoch': 3, 'Mittel': 2, 'Niedrig': 1}

        for subject in legislative_subjects:
            entry = mapping.get(subject)
            if entry:
                sectors.update(entry['sectors'])
                if konfidenz_order.get(entry['konfidenz'], 0) > konfidenz_order.get(best_konfidenz, 0):
                    best_konfidenz = entry['konfidenz']

        return sectors, best_konfidenz

    def load_votes(self) -> pd.DataFrame:
        """
        Laedt Roll-Call-Votes aus bill_actions.json und verknuepft mit
        legislative_subjects aus bill_subjects.json.

        Fuer jeden Bill mit Roll-Call-Vote:
        1. legislative_subjects laden (aus bill_subjects.json, Join via bill_id)
        2. Subjects → Sektoren mappen (via legislative_subject_mapping_complete.csv)
        3. Bills ohne gemappte Subjects werden uebersprungen

        Returns:
            DataFrame mit [vote_date, mapped_sectors, bill_title, policy_area, chamber, konfidenz]
        """
        if not os.path.exists(BILL_ACTIONS_PATH):
            logger.warning(
                f"bill_actions.json nicht gefunden: {BILL_ACTIONS_PATH}\n"
                "Bitte zuerst 'python scrapers/fetch_congress_data.py' ausfuehren."
            )
            return pd.DataFrame()

        if not os.path.exists(BILL_SUBJECTS_PATH):
            logger.warning(
                f"bill_subjects.json nicht gefunden: {BILL_SUBJECTS_PATH}\n"
                "Bitte zuerst 'python scrapers/fetch_congress_data.py' ausfuehren."
            )
            return pd.DataFrame()

        # Mapping laden
        self._load_subject_mapping()

        # bill_subjects.json → Dict {bill_id: [subjects]}
        with open(BILL_SUBJECTS_PATH, encoding='utf-8') as f:
            subjects_list = json.load(f)

        subjects_by_bill = {}
        for bill in subjects_list:
            bill_id = bill.get('bill_id', '')
            subjects = bill.get('legislative_subjects', [])
            if bill_id and subjects:
                subjects_by_bill[bill_id] = subjects

        logger.info(f"Bill-Subjects geladen: {len(subjects_by_bill)} Bills mit Subjects")

        # bill_actions.json → Roll-Call-Votes extrahieren
        with open(BILL_ACTIONS_PATH, encoding='utf-8') as f:
            bills = json.load(f)

        rows = []
        seen = set()  # Dedup: (bill_id, chamber, vote_date_str)
        bills_mapped = 0
        bills_skipped = 0

        for bill in bills:
            bill_id = bill.get('bill_id', '')
            bill_title = bill.get('title', '')
            policy_area = bill.get('policy_area', '')

            # Legislative Subjects fuer diesen Bill holen
            subjects = subjects_by_bill.get(bill_id, [])
            if not subjects:
                continue

            # Subjects → Sektoren mappen
            sectors, konfidenz = self._map_subjects_to_sectors(subjects)
            if not sectors:
                bills_skipped += 1
                continue

            bills_mapped += 1
            mapped_sectors_str = ','.join(sorted(sectors))

            for action in bill.get('actions', []):
                for rc in action.get('roll_call', []):
                    chamber = rc.get('chamber', '')
                    date_str = rc.get('date', '')
                    dedup_key = (bill_id, chamber, date_str)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    rows.append({
                        'vote_date': date_str,
                        'mapped_sectors': mapped_sectors_str,
                        'bill_title': bill_title,
                        'policy_area': policy_area,
                        'chamber': chamber,
                        'konfidenz': konfidenz,
                    })

        if not rows:
            logger.info("Keine gemappten Roll-Call-Votes gefunden")
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['vote_date'] = pd.to_datetime(df['vote_date'], utc=True, errors='coerce')
        df['vote_date'] = df['vote_date'].dt.tz_localize(None)  # timezone-naive fuer Vergleiche
        df = df.dropna(subset=['vote_date'])

        logger.info(
            f"Votes geladen: {len(df)} Roll-Calls mit Subject-Mapping "
            f"({bills_mapped} Bills gemappt, {bills_skipped} ohne Mapping)"
        )
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
        Parst das kommaseparierte mapped_sectors Feld.

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
        df['Vote_Timing_Hits'] = '[]'
        df['Vote_Timing_Hit_Count'] = 0

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
            # Filter 0: Nicht-Aktien ueberspringen
            if bool(trade.get('asset_class_excluded', False)):
                continue

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

            # Sektor-Match pruefen: alle Treffer im Fenster sammeln
            hits_list = []
            for _, vote in in_window.iterrows():
                if not trade_sectors.intersection(vote['_sectors_set']):
                    continue
                hits_list.append({
                    'date': vote['vote_date'].strftime('%Y-%m-%d'),
                    'bill_title': str(vote.get('bill_title', '')),
                    'policy_area': str(vote.get('policy_area', '')),
                    'days_to_vote': int((vote['vote_date'] - trade_date).days),
                    'konfidenz': str(vote.get('konfidenz', 'Niedrig')),
                })

            if hits_list:
                df.at[idx, 'Vote_Timing_Hit'] = True
                df.at[idx, 'Vote_Timing_Hits'] = json.dumps(hits_list)
                df.at[idx, 'Vote_Timing_Hit_Count'] = len(hits_list)
                hits += 1

        # Aufraeumen: temporaere Spalte entfernen
        if '_sectors_set' in votes_mapped.columns:
            votes_mapped.drop(columns=['_sectors_set'], inplace=True)

        logger.info(f"Vote-Timing: {hits}/{total} Trades mit Timing-Hit "
                     f"({hits/total*100:.1f}%)" if total else "Keine Trades")

        return df
