"""
Congress Trade Tracker - Trade Import Pipeline
Importiert Scraped Trades, erkennt Duplikate, reichert mit Committee-Daten an
"""

import os
import sys
import logging
from datetime import datetime
from typing import Tuple

import pandas as pd

# Pfade konfigurieren
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import settings
from utils.name_matcher import NameMatcher
from filters.vote_timing_filter import VoteTimingFilter, VOTE_TIMING_COLUMNS

logger = logging.getLogger(__name__)

# Pfade aus Config (bei Neuwahlen in settings.py anpassen)
PROCESSED_DIR = os.path.join(BASE_DIR, settings.DATA_PROCESSED_PATH)
RAW_DIR = os.path.join(BASE_DIR, settings.DATA_RAW_PATH)

# Input/Output Dateien (aus Config)
HOUSE_TRADES_PATH = os.path.join(RAW_DIR, settings.HOUSE_TRADES_FILE)
SENATE_TRADES_PATH = os.path.join(RAW_DIR, settings.SENATE_TRADES_FILE)
MASTER_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_master.csv')
NEW_TRADES_PATH = os.path.join(PROCESSED_DIR, 'trades_new.csv')
UNMATCHED_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names.csv')

# Duplikat-Spalten aus Config
DUPLICATE_COLUMNS = getattr(
    settings,
    'DUPLICATE_CHECK_COLUMNS',
    ['name', 'ticker', 'asset_name', 'asset_type', 'transaction_type',
     'amount', 'transaction_date', 'owner', 'subholding', 'comment', 'ptr_link']
)


class TradeImporter:
    """
    Importiert und verarbeitet Congress-Trades.

    Workflow:
    1. Lade Scraped Trades (senate_trades.csv + house_trades.csv)
    2. Erkenne neue Trades (Duplikat-Check)
    3. Reichere mit Committee-Daten an (Fuzzy-Matching)
    4. Speichere Ergebnisse
    """

    def __init__(self):
        """Initialisiert den TradeImporter."""
        self.matcher = None
        self.stats = {
            'total_input': 0,
            'new_trades': 0,
            'duplicates': 0,
            'matched': 0,
            'unmatched': 0
        }

    def _ensure_directories(self):
        """Stellt sicher, dass alle Verzeichnisse existieren."""
        os.makedirs(PROCESSED_DIR, exist_ok=True)

    def _init_matcher(self):
        """Initialisiert den NameMatcher."""
        if self.matcher is None:
            self.matcher = NameMatcher()

    def load_trade_data(self) -> pd.DataFrame:
        """
        Laedt Scraped Trades aus senate_trades.csv und house_trades.csv.

        Returns:
            Kombinierter DataFrame mit allen Trades (inkl. chamber-Spalte)
        """
        frames = []

        # Senate-Daten laden
        if os.path.exists(SENATE_TRADES_PATH):
            senate_df = pd.read_csv(SENATE_TRADES_PATH)
            senate_df['chamber'] = 'Senate'
            logger.info(f"senate_trades.csv: {len(senate_df)} Trades")
            print(f"      senate_trades.csv: {len(senate_df)} Trades")
            frames.append(senate_df)
        else:
            logger.warning(f"senate_trades.csv nicht gefunden: {SENATE_TRADES_PATH}")
            print(f"      [WARN] senate_trades.csv nicht gefunden")

        # House-Daten laden
        if os.path.exists(HOUSE_TRADES_PATH):
            house_df = pd.read_csv(HOUSE_TRADES_PATH)
            house_df['chamber'] = 'House'
            logger.info(f"house_trades.csv: {len(house_df)} Trades")
            print(f"      house_trades.csv: {len(house_df)} Trades")
            frames.append(house_df)
        else:
            logger.warning(f"house_trades.csv nicht gefunden: {HOUSE_TRADES_PATH}")
            print(f"      [WARN] house_trades.csv nicht gefunden")

        if not frames:
            raise FileNotFoundError(
                f"Keine Scraper-Dateien gefunden!\n"
                f"Erwartet: {SENATE_TRADES_PATH}\n"
                f"Oder:     {HOUSE_TRADES_PATH}"
            )

        df = pd.concat(frames, ignore_index=True)

        # Datum konvertieren
        if 'transaction_date' in df.columns:
            df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
        if 'disclosure_date' in df.columns:
            df['disclosure_date'] = pd.to_datetime(df['disclosure_date'], errors='coerce')

        self.stats['total_input'] = len(df)
        logger.info(f"Gesamt geladen: {len(df)} Trades")

        return df

    def load_master_data(self) -> pd.DataFrame:
        """
        Laedt bestehende Master-Daten.

        Returns:
            DataFrame mit bestehenden Trades, oder leerer DataFrame
        """
        if os.path.exists(MASTER_CSV_PATH):
            df = pd.read_csv(MASTER_CSV_PATH)
            # Datum konvertieren
            if 'transaction_date' in df.columns:
                df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
            logger.info(f"Master-Daten geladen: {len(df)} bestehende Trades")
            return df
        else:
            logger.info("Keine Master-Daten vorhanden (erster Import)")
            return pd.DataFrame()

    def detect_new_trades(self, input_df: pd.DataFrame, master_df: pd.DataFrame) -> pd.DataFrame:
        """
        Erkennt neue Trades durch Duplikat-Check.

        Args:
            input_df: Neue Scraped-Daten
            master_df: Bestehende Master-Daten

        Returns:
            DataFrame nur mit neuen Trades
        """
        if master_df.empty:
            # Erster Import: Alle sind neu
            self.stats['new_trades'] = len(input_df)
            self.stats['duplicates'] = 0
            logger.info("Erster Import: Alle Trades sind neu")
            return input_df.copy()

        # Erstelle Duplikat-Key fuer beide DataFrames
        def create_dup_key(df: pd.DataFrame) -> pd.Series:
            key_parts = []
            for col in DUPLICATE_COLUMNS:
                if col in df.columns:
                    # String-Konvertierung fuer konsistenten Vergleich
                    key_parts.append(df[col].astype(str))
            return key_parts[0].str.cat(key_parts[1:], sep='|')

        input_keys = create_dup_key(input_df)
        master_keys = set(create_dup_key(master_df))

        # Finde neue Trades
        is_new = ~input_keys.isin(master_keys)
        new_trades = input_df[is_new].copy()

        self.stats['new_trades'] = len(new_trades)
        self.stats['duplicates'] = len(input_df) - len(new_trades)

        logger.info(f"Duplikat-Check: {self.stats['new_trades']} neue, {self.stats['duplicates']} Duplikate")

        return new_trades

    def enrich_with_committees(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Reichert Trades mit Committee-Daten UND Sektor-Relevanz an.

        Args:
            df: DataFrame mit Trades

        Returns:
            Tuple (angereicherte_df, unmatched_df)
        """
        self._init_matcher()

        # Initialize committee filter
        from filters.committee_filter import CommitteeFilter
        committee_filter = CommitteeFilter()

        # Neue Spalten
        df = df.copy()
        df['Member_Party'] = ''
        df['Member_State'] = ''
        df['Member_Committees'] = ''
        df['Member_ExOfficio'] = ''
        df['Member_Chamber'] = ''
        df['MatchScore'] = 0
        df['MatchSource'] = ''

        # NEW: Committee relevance columns
        df['Member_Committees_Full'] = ''
        df['Relevant_Sectors'] = ''
        df['Relevance_Score'] = 0
        df['Confidence_Level'] = ''

        # Unmatched sammeln
        unmatched_counts = {}

        logger.info(f"Reichere {len(df)} Trades mit Committee-Daten an...")

        for idx, row in df.iterrows():
            name = row.get('name', '')
            chamber = str(row.get('chamber', '')).lower()
            ticker = row.get('ticker', '')

            # Match durchfuehren
            result = self.matcher.match(name, chamber)

            df.at[idx, 'Member_Party'] = result['party']
            df.at[idx, 'Member_State'] = result['state']
            df.at[idx, 'Member_Committees'] = result['committees']
            df.at[idx, 'Member_ExOfficio'] = result['ex_officio']
            df.at[idx, 'Member_Chamber'] = result['chamber']
            df.at[idx, 'MatchScore'] = result['score']
            df.at[idx, 'MatchSource'] = result['source']

            # NEW: Committee relevance calculation
            if result['matched'] and result['committees']:
                committees_full = result['committees']
                df.at[idx, 'Member_Committees_Full'] = committees_full

                try:
                    # Calculate relevance
                    relevance = committee_filter.calculate_relevance(
                        committees_string=committees_full,
                        chamber=result['chamber'],
                        ticker=ticker
                    )

                    # Store results
                    import json
                    df.at[idx, 'Relevant_Sectors'] = json.dumps(relevance['relevant_sectors'])
                    df.at[idx, 'Relevance_Score'] = relevance['relevance_score']
                    df.at[idx, 'Confidence_Level'] = relevance['confidence_level']

                except Exception as e:
                    logger.warning(f"Failed to calculate committee relevance for {name}: {e}")
                    # Keep defaults (empty/zero)

            if not result['matched']:
                # Unmatched zaehlen
                if name not in unmatched_counts:
                    unmatched_counts[name] = {
                        'count': 0,
                        'chamber': chamber
                    }
                unmatched_counts[name]['count'] += 1

        # Statistiken aktualisieren
        matched = len(df[df['MatchSource'] != 'none'])
        unmatched = len(df[df['MatchSource'] == 'none'])
        self.stats['matched'] = matched
        self.stats['unmatched'] = unmatched

        logger.info(f"Matching: {matched} erfolgreich, {unmatched} nicht gefunden")

        # Unmatched DataFrame erstellen
        unmatched_data = []
        for name, info in unmatched_counts.items():
            # Besten Fuzzy-Match finden (auch unter Threshold)
            best = self.matcher.get_best_fuzzy_match(name, info['chamber'])
            unmatched_data.append({
                'trade_name': name,
                'count': info['count'],
                'chamber': info['chamber'],
                'best_match': best['best_match'],
                'best_match_score': best['score']
            })

        unmatched_df = pd.DataFrame(unmatched_data)
        if not unmatched_df.empty:
            unmatched_df = unmatched_df.sort_values('count', ascending=False)

        return df, unmatched_df

    def enrich_with_vote_timing(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Reichert Trades mit Vote-Timing-Daten an (Filter 4).

        Args:
            df: DataFrame mit Trades (nach Committee-Enrichment)

        Returns:
            DataFrame mit Vote_Timing_* Spalten
        """
        vote_filter = VoteTimingFilter()
        votes_df = vote_filter.load_votes()

        if votes_df.empty:
            logger.info("Keine Vote-Daten, ueberspringe Vote-Timing")
            print("      [SKIP] Keine congress_votes.csv vorhanden")
            print("      Tipp: python scripts/fetch_congress_votes.py ausfuehren")
            # Defaults setzen
            for col in VOTE_TIMING_COLUMNS:
                if col not in df.columns:
                    df[col] = False if col == 'Vote_Timing_Hit' else (0 if 'Score' in col or 'Days' in col else '')
            return df

        result = vote_filter.apply(df, votes_df)

        hits = result['Vote_Timing_Hit'].sum()
        print(f"      {hits} Trades mit Vote-Timing-Hit")

        return result

    def _backfill_vote_timing(self, master_df: pd.DataFrame) -> pd.DataFrame:
        """
        Backfill: Wendet Vote-Timing auf bestehende Master-Daten an,
        die noch keine Vote_Timing_* Spalten haben.

        Args:
            master_df: Bestehende Master-Daten

        Returns:
            Master-DataFrame mit Vote-Timing-Spalten (oder unveraendert)
        """
        if master_df.empty:
            return master_df

        # Pruefen ob Backfill noetig
        if 'Vote_Timing_Hit' in master_df.columns:
            logger.info("Vote-Timing Spalten bereits vorhanden, kein Backfill noetig")
            return master_df

        print("\n      [BACKFILL] Vote-Timing auf bestehende Trades anwenden...")
        result = self.enrich_with_vote_timing(master_df)
        hits = result['Vote_Timing_Hit'].sum()
        print(f"      [BACKFILL] {hits}/{len(result)} bestehende Trades mit Hit")

        return result

    def save_results(
        self,
        enriched_df: pd.DataFrame,
        master_df: pd.DataFrame,
        unmatched_df: pd.DataFrame
    ):
        """
        Speichert alle Ergebnisse.

        Args:
            enriched_df: Angereicherte neue Trades
            master_df: Bestehende Master-Daten
            unmatched_df: Nicht-gematchte Namen
        """
        self._ensure_directories()

        # Import-Datum hinzufuegen
        enriched_df = enriched_df.copy()
        enriched_df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 1. Neue Trades speichern
        enriched_df.to_csv(NEW_TRADES_PATH, index=False, encoding='utf-8')
        logger.info(f"Neue Trades gespeichert: {NEW_TRADES_PATH}")

        # 2. Master aktualisieren (alte + neue)
        if master_df.empty:
            updated_master = enriched_df
        else:
            updated_master = pd.concat([master_df, enriched_df], ignore_index=True)

        updated_master.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        logger.info(f"Master aktualisiert: {MASTER_CSV_PATH} ({len(updated_master)} Trades)")

        # 3. Unmatched Names speichern
        if not unmatched_df.empty:
            unmatched_df.to_csv(UNMATCHED_PATH, index=False, encoding='utf-8')
            logger.info(f"Unmatched Names: {UNMATCHED_PATH} ({len(unmatched_df)} Namen)")

    def run(self) -> dict:
        """
        Fuehrt den kompletten Import durch.

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Import Pipeline")
        print("=" * 60)

        # 1. Scraped Trades laden
        print("\n[1/5] Lade Scraped Trades...")
        input_df = self.load_trade_data()
        print(f"      Gesamt: {len(input_df)} Trades geladen")

        # 2. Master-Daten laden
        print("\n[2/5] Lade bestehende Master-Daten...")
        master_df = self.load_master_data()
        if not master_df.empty:
            print(f"      {len(master_df)} bestehende Trades")
        else:
            print("      Keine bestehenden Daten (erster Import)")

        # 2a. Backfill: Vote-Timing auf bestehende Master-Daten (einmalig)
        master_df = self._backfill_vote_timing(master_df)

        # 3. Neue Trades erkennen
        print("\n[3/5] Erkenne neue Trades...")
        new_trades = self.detect_new_trades(input_df, master_df)
        print(f"      {self.stats['new_trades']} neue Trades")
        print(f"      {self.stats['duplicates']} Duplikate uebersprungen")

        if new_trades.empty:
            # Auch bei 0 neuen Trades: Backfill speichern falls Master geaendert
            if 'Vote_Timing_Hit' in master_df.columns and not master_df.empty:
                self._ensure_directories()
                master_df.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
            print("\n[OK] Keine neuen Trades zu verarbeiten.")
            return self.stats

        # 4. Mit Committee-Daten anreichern
        print("\n[4/5] Reichere mit Committee-Daten an...")
        enriched_df, unmatched_df = self.enrich_with_committees(new_trades)
        print(f"      {self.stats['matched']} erfolgreich gematcht")
        print(f"      {self.stats['unmatched']} nicht gefunden")

        # 5. Vote-Timing anreichern (Filter 4)
        print("\n[5/5] Vote-Timing Filter...")
        enriched_df = self.enrich_with_vote_timing(enriched_df)

        # 6. Ergebnisse speichern
        print("\n[6/6] Speichere Ergebnisse...")
        self.save_results(enriched_df, master_df, unmatched_df)

        # Zusammenfassung
        print("\n" + "=" * 60)
        print("ZUSAMMENFASSUNG")
        print("=" * 60)
        print(f"Input:      {self.stats['total_input']:,} Trades")
        print(f"Neu:        {self.stats['new_trades']:,} Trades")
        print(f"Duplikate:  {self.stats['duplicates']:,} uebersprungen")
        print(f"Gematcht:   {self.stats['matched']:,} Trades")
        print(f"Unmatched:  {self.stats['unmatched']:,} Trades")

        print("\nOutput-Dateien:")
        print(f"  - {MASTER_CSV_PATH}")
        print(f"  - {NEW_TRADES_PATH}")
        if self.stats['unmatched'] > 0:
            print(f"  - {UNMATCHED_PATH}")

        return self.stats


def main():
    """Hauptfunktion: Fuehrt Import durch."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    importer = TradeImporter()

    try:
        importer.run()
    except FileNotFoundError as e:
        print(f"\n[X] FEHLER: {e}")
        print("\nBitte stelle sicher, dass die Scraper-Dateien vorhanden sind:")
        print(f"  - {SENATE_TRADES_PATH}")
        print(f"  - {HOUSE_TRADES_PATH}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[X] FEHLER: {e}")
        logger.exception("Import fehlgeschlagen")
        sys.exit(1)


if __name__ == "__main__":
    main()
