"""
Congress Trade Tracker - Trade Import Pipeline
Importiert Trades, reichert mit Committee-Daten, Ticker-Industry und Filter-Logik an.

Modi:
  python scripts/import_trades.py                    # Full Rebuild
  python scripts/import_trades.py --delta            # Nur neue Trades
  python scripts/import_trades.py --dry_run          # Alle Stages → trades_stage.csv

Stage-Pipeline (einzelne Schritte, re-runnable):
  python scripts/import_trades.py --stage_rename          # CSV laden + Spalten umbenennen
  python scripts/import_trades.py --stage_date            # Datum normalisieren
  python scripts/import_trades.py --stage_committees      # Committee-Member-Enrichment
  python scripts/import_trades.py --stage_tickertype      # ticker_type ableiten
  python scripts/import_trades.py --stage_tickerindustry  # yfinance Lookup
  python scripts/import_trades.py --stage_filter          # Scoring + Filter 0/4
  python scripts/import_trades.py --stage_save            # trades_stage.csv → trades_master.csv

Flags kombinierbar:
  --dry_run        schreibt Output in trades_stage.csv statt trades_master.csv
  --delta          liest/schreibt trades_stage.csv als Puffer, merge bei save
  --delta --stage_rename --dry_run  → trades_stage.csv (neue Trades, kein Master-Touch)
"""

import argparse
import json
import os
import pickle
import re
import shutil
import sys
import logging
import time
from datetime import datetime
from typing import Tuple

import pandas as pd

# Pfade konfigurieren
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import settings
from utils.name_matcher import NameMatcher
from utils.openfigi_client import OpenFigiClient
from filters.vote_timing_filter import VoteTimingFilter, VOTE_TIMING_COLUMNS
from filters.asset_class_filter import AssetClassFilter, ASSET_CLASS_COLUMNS

logger = logging.getLogger(__name__)

# Pfade aus Config (bei Neuwahlen in settings.py anpassen)
PROCESSED_DIR = os.path.join(BASE_DIR, settings.DATA_PROCESSED_PATH)
RAW_DIR = os.path.join(BASE_DIR, settings.DATA_RAW_PATH)
# Input/Output Dateien (aus Config)
QUIVER_TRADES_PATH = os.path.join(RAW_DIR, settings.QUIVER_TRADES_FILE)
MASTER_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_master.csv')
UNMATCHED_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names.csv')
UNMATCHED_PREFERRED_PATH = os.path.join(PROCESSED_DIR, 'unmatched_preferred.csv')
DELISTED_TRADES_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades.csv')
CHECKPOINT_PATH = os.path.join(PROCESSED_DIR, '.enrichment_checkpoint.pkl')

CHECKPOINT_INTERVAL = 5000  # Trades zwischen Checkpoint-Speicherungen

# Stage-Pipeline Pfade
STAGE_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_stage.csv')
PIPELINE_STATE_PATH = os.path.join(PROCESSED_DIR, 'pipeline_state.json')
UNMATCHED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names_stage.csv')
DELISTED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades_stage.csv')

STAGE_DEPS = {
    'rename': {
        'input_cols': [],
        'output_cols': ['name', 'ticker', 'asset_name', 'transaction_date', 'disclosure_date',
                        'chamber', 'Member_Party', 'Member_State', 'comment', 'subholding', 'bio_guide_id'],
    },
    'date': {
        'input_cols': ['transaction_date', 'disclosure_date'],
        'output_cols': ['transaction_date', 'disclosure_date'],
    },
    'committees': {
        'input_cols': ['name', 'chamber'],
        'output_cols': ['Member_Party', 'Member_State', 'Member_Committees',
                        'Member_ExOfficio', 'Member_Chamber', 'Member_Committees_Full'],
    },
    'tickertype': {
        'input_cols': ['ticker'],
        'output_cols': ['ticker_type'],
    },
    'tickerindustry': {
        'input_cols': ['ticker'],
        'output_cols': ['Ticker_Sector', 'Ticker_Industry'],
    },
    'filter': {
        'input_cols': ['name', 'chamber', 'Member_Committees_Full', 'ticker',
                       'ticker_type', 'transaction_date'],
        'output_cols': ['MatchScore', 'MatchSource', 'Relevant_Sectors', 'Relevance_Score',
                        'Confidence_Level', 'asset_class_excluded', 'Vote_Timing_Hit',
                        'Vote_Date', 'Vote_Bill_Title', 'Vote_Policy_Area',
                        'Vote_Timing_Score', 'Days_To_Vote'],
    },
    'save': {
        'input_cols': [],
        'output_cols': ['ImportDate'],
    },
}

ARCHIVE_BACKUP_DIR = os.path.join(BASE_DIR, settings.ARCHIVE_BACKUP_PATH)

# Duplikat-Spalten fuer Delta-Modus (aus Config)
DUPLICATE_COLUMNS = getattr(
    settings,
    'DUPLICATE_CHECK_COLUMNS',
    ['name', 'ticker', 'asset_name', 'transaction_type',
     'amount', 'transaction_date', 'subholding', 'comment']
)

# Quiver → internes Spalten-Mapping
QUIVER_COLUMN_MAP = {
    'Name': 'name',
    'Ticker': 'ticker',
    'Company': 'asset_name',
    'Traded': 'transaction_date',
    'Filed': 'disclosure_date',
    'Transaction': 'transaction_type',
    'Trade_Size_USD': 'amount',
    'Party': 'Member_Party',
    'Chamber': 'chamber',
    'State': 'Member_State',
    'Comments': 'comment',
    'Subholding': 'subholding',
    'BioGuideID': 'bio_guide_id',
    'District': 'district',
    'Status': 'status',
    'Description': 'description',
    'excess_return': 'excess_return',
    'Quiver_Upload_Time': 'csv_upload_time',
    'last_modified': 'last_modified',
}


class TradeImporter:
    """
    Importiert und verarbeitet Congress-Trades.

    Workflow:
    1. Lade Quiver-Trades (komplette Snapshot-Datei)
    2. Reichere mit Committee-Daten an (Fuzzy-Matching)
    3. Ticker-Industry Lookup (yfinance)
    4. Vote-Timing Filter
    5. Speichere Ergebnisse (trades_master.csv wird komplett neu geschrieben)
    """

    def __init__(self):
        """Initialisiert den TradeImporter."""
        self.matcher = None
        self.stats = {
            'total_input': 0,
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

    def load_and_rename(self) -> pd.DataFrame:
        """
        Laedt Trades aus Quiver-Datei (xlsx oder csv) und mappt Spaltennamen.
        Bei xlsx wird die Datei intern in einen DataFrame konvertiert (keine CSV-Ausgabe).

        Returns:
            DataFrame mit umbenannten Spalten (Datum noch nicht normalisiert)
        """
        if not os.path.exists(QUIVER_TRADES_PATH):
            raise FileNotFoundError(
                f"Quiver-Datei nicht gefunden: {QUIVER_TRADES_PATH}\n"
                f"Bitte congress-trading-all.xlsx (oder .csv) in data/raw/ ablegen."
            )

        ext = os.path.splitext(QUIVER_TRADES_PATH)[1].lower()
        if ext in ('.xlsx', '.xlsm', '.xls'):
            df = pd.read_excel(QUIVER_TRADES_PATH)
        else:
            df = pd.read_csv(QUIVER_TRADES_PATH, low_memory=False)

        logger.info(f"Quiver-Daten geladen: {len(df)} Trades")
        print(f"      {settings.QUIVER_TRADES_FILE}: {len(df)} Trades")

        # Spalten umbenennen (Quiver → intern)
        df = df.rename(columns=QUIVER_COLUMN_MAP)

        # chamber normalisieren (Quiver hat "House"/"Senate", passt bereits)
        if 'chamber' in df.columns:
            df['chamber'] = df['chamber'].fillna('')

        self.stats['total_input'] = len(df)
        return df

    def normalize_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Konvertiert Datumsspalten und wendet Fallback-Logik an.

        Returns:
            DataFrame mit normalisierten Datumsspalten
        """
        # Datum konvertieren
        if 'transaction_date' in df.columns:
            df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
        if 'disclosure_date' in df.columns:
            df['disclosure_date'] = pd.to_datetime(df['disclosure_date'], errors='coerce')

        # Fallback: Fehlende oder unplausible transaction_date mit disclosure_date fuellen
        if 'transaction_date' in df.columns and 'disclosure_date' in df.columns:
            # Fall 1: transaction_date fehlt komplett
            missing_mask = df['transaction_date'].isna() & df['disclosure_date'].notna()
            n_filled = missing_mask.sum()
            if n_filled > 0:
                df.loc[missing_mask, 'transaction_date'] = df.loc[missing_mask, 'disclosure_date']
                self.stats['date_fallback'] = n_filled
                logger.info(f"Fallback: {n_filled} fehlende transaction_dates mit disclosure_date gefuellt")

            # Fall 2: transaction_date liegt NACH disclosure_date (z.B. Bond-Maturity-Date)
            future_mask = (
                df['transaction_date'].notna() &
                df['disclosure_date'].notna() &
                (df['transaction_date'] > df['disclosure_date'])
            )
            n_future = future_mask.sum()
            if n_future > 0:
                df.loc[future_mask, 'transaction_date'] = df.loc[future_mask, 'disclosure_date']
                self.stats['date_future_fallback'] = n_future
                logger.info(f"Fallback: {n_future} transaction_dates nach disclosure_date mit disclosure_date ersetzt")

        logger.info(f"Gesamt geladen: {len(df)} Trades")
        return df

    def load_trade_data(self) -> pd.DataFrame:
        """Wrapper: Laedt und normalisiert Trades (Backward-Compat)."""
        df = self.load_and_rename()
        df = self.normalize_dates(df)
        return df

    def _save_checkpoint(self, df: pd.DataFrame, processed_up_to: int,
                         unmatched_counts: dict, matched_count: int = 0):
        """Speichert Enrichment-Checkpoint als Pickle."""
        checkpoint = {
            'df': df,
            'processed_up_to': processed_up_to,
            'unmatched_counts': unmatched_counts,
            'matched_count': matched_count,
        }
        self._ensure_directories()
        with open(CHECKPOINT_PATH, 'wb') as f:
            pickle.dump(checkpoint, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info(f"Checkpoint gespeichert: {processed_up_to} Trades")

    def _load_checkpoint(self) -> dict | None:
        """Laedt Checkpoint falls vorhanden."""
        if not os.path.isfile(CHECKPOINT_PATH):
            return None
        try:
            with open(CHECKPOINT_PATH, 'rb') as f:
                checkpoint = pickle.load(f)
            logger.info(f"Checkpoint geladen: {checkpoint['processed_up_to']} Trades bereits verarbeitet")
            return checkpoint
        except Exception as e:
            logger.warning(f"Checkpoint beschaedigt, starte von vorne: {e}")
            os.remove(CHECKPOINT_PATH)
            return None

    def _delete_checkpoint(self):
        """Loescht Checkpoint nach erfolgreichem Abschluss."""
        if os.path.isfile(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)
            logger.info("Checkpoint geloescht")

    def enrich_member_data(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Reichert Trades mit Committee-Member-Daten an (kein Scoring).
        Scoring-Spalten (MatchScore, Relevant_Sectors etc.) werden in _stage_filter gesetzt.
        Unterstuetzt Checkpoint-basiertes Resuming bei Abbruch.

        Args:
            df: DataFrame mit Trades

        Returns:
            Tuple (angereicherte_df, unmatched_df)
        """
        self._init_matcher()

        # Checkpoint pruefen
        checkpoint = self._load_checkpoint()
        if checkpoint is not None:
            df = checkpoint['df']
            start_pos = checkpoint['processed_up_to']
            unmatched_counts = checkpoint['unmatched_counts']
            matched_count = checkpoint.get('matched_count', 0)
            print(f"      [Checkpoint] Setze fort ab Trade {start_pos:,}/{len(df):,}")
        else:
            start_pos = 0
            unmatched_counts = {}
            matched_count = 0
            # Nur Member-Spalten initialisieren (keine Scoring-Spalten!)
            df = df.copy()
            df['Member_Party'] = ''
            df['Member_State'] = ''
            df['Member_Committees'] = ''
            df['Member_ExOfficio'] = ''
            df['Member_Chamber'] = ''
            df['Member_Committees_Full'] = ''

        total = len(df)
        logger.info(f"Reichere {total} Trades mit Committee-Member-Daten an (ab {start_pos})...")

        indices = df.index.tolist()
        for pos, idx in enumerate(indices):
            # Bereits verarbeitete Trades ueberspringen
            if pos < start_pos:
                continue

            row = df.loc[idx]
            name = row.get('name', '')
            chamber = str(row.get('chamber', '')).lower()

            # Match durchfuehren
            result = self.matcher.match(name, chamber)

            df.at[idx, 'Member_Party'] = result['party']
            df.at[idx, 'Member_State'] = result['state']
            df.at[idx, 'Member_Committees'] = result['committees']
            df.at[idx, 'Member_ExOfficio'] = result['ex_officio']
            df.at[idx, 'Member_Chamber'] = result['chamber']

            if result['matched'] and result['committees']:
                df.at[idx, 'Member_Committees_Full'] = result['committees']
                matched_count += 1

            if not result['matched']:
                if name not in unmatched_counts:
                    unmatched_counts[name] = {'count': 0, 'chamber': chamber}
                unmatched_counts[name]['count'] += 1

            # Checkpoint speichern
            processed = pos + 1
            if processed % CHECKPOINT_INTERVAL == 0:
                self._save_checkpoint(df, processed, unmatched_counts, matched_count)
                print(f"      [Checkpoint] {processed:,}/{total:,} Trades gesichert")

        # Statistiken aktualisieren
        self.stats['matched'] = matched_count
        self.stats['unmatched'] = total - matched_count

        logger.info(f"Matching: {matched_count} erfolgreich, {total - matched_count} nicht gefunden")

        # Unmatched DataFrame erstellen
        unmatched_data = []
        for name, info in unmatched_counts.items():
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

        # Checkpoint loeschen nach Erfolg
        self._delete_checkpoint()

        return df, unmatched_df

    def derive_ticker_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Leitet ticker_type via OpenFIGI API + Regex ab.

        6-Stufen-Pipeline:
        1. Leere/kaputte Ticker → "Unknown"
        2. Regex (TICKER_TYPE_PATTERNS) → Bond, T-Bill, Crypto, Warrant etc.
        3. SQLite-Cache (OpenFIGI-Ergebnisse)
        4. OpenFIGI Batch-Lookup (Cache-Misses)
        5. Whitelist (Sonderfaelle) → "Stock"
        6. Fallback → "Unknown"

        Args:
            df: DataFrame mit 'ticker'-Spalte

        Returns:
            DataFrame mit ticker_type-Spalte
        """
        # Alle unique Ticker sammeln
        all_tickers = df['ticker'].dropna().unique().tolist()
        all_tickers = [str(t).strip() for t in all_tickers if str(t).strip()]

        result_map = {}  # ticker → ticker_type

        # Stufe 1+2: Leere abfangen + Regex Pre-Klassifikation
        api_candidates = []
        for ticker in all_tickers:
            # Stufe 1: Leerer/kaputter Ticker
            if not ticker or isinstance(ticker, float):
                result_map[ticker] = 'Unknown'
                continue

            # Stufe 2: Regex-Patterns (Bonds, T-Bills, Crypto, etc.)
            matched = False
            for label, patterns in settings.TICKER_TYPE_PATTERNS.items():
                for pattern in patterns:
                    if re.match(pattern, ticker):
                        result_map[ticker] = label
                        matched = True
                        break
                if matched:
                    break

            if not matched:
                # Stufe 5 vorziehen: Whitelist (BRK-A etc.)
                if ticker in settings.TICKER_WHITELIST:
                    result_map[ticker] = 'Stock'
                else:
                    api_candidates.append(ticker)

        print(f"      Regex/Whitelist: {len(result_map)} Ticker vorklassifiziert")
        print(f"      OpenFIGI-Kandidaten: {len(api_candidates)} Ticker")

        # Stufe 3+4: OpenFIGI Cache + API-Lookup
        if api_candidates:
            cache_db_path = os.path.join(BASE_DIR, settings.DATA_CACHE_PATH,
                                         settings.TICKER_TYPE_CACHE_DB)
            client = OpenFigiClient(
                cache_db_path=cache_db_path,
                api_key=settings.OPENFIGI_API_KEY
            )
            openfigi_results = client.classify_tickers(api_candidates)

            for ticker in api_candidates:
                figi_type = openfigi_results.get(ticker, 'Unknown')
                # Stufe 6: Fallback
                result_map[ticker] = figi_type if figi_type else 'Unknown'

        # Ergebnis auf DataFrame mappen
        df['ticker_type'] = df['ticker'].map(result_map).fillna('Unknown')

        type_counts = df['ticker_type'].value_counts()
        print(f"      Ticker-Types: {len(type_counts)} Kategorien")
        for tt, cnt in type_counts.head(10).items():
            print(f"        {tt}: {cnt:,}")

        return df

    def enrich_with_ticker_industry(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Reichert Trades mit YF Sector/Industry an (via yfinance + Cache).

        Laedt bestehenden Cache, fragt nur neue Ticker bei yfinance ab,
        speichert Ergebnis in ticker_industry_cache.json.

        Args:
            df: DataFrame mit Trades (muss 'ticker'-Spalte haben)

        Returns:
            DataFrame mit neuen Spalten Ticker_Sector und Ticker_Industry
        """
        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)

        # 0. Vorzugsaktien-Mapping laden
        pref_mapping = {}  # {"EP$C": "KMI", ...}
        pref_file = os.path.join(mappings_dir, settings.PREFERRED_TICKER_MAPPING_FILE)
        if os.path.isfile(pref_file):
            pref_df = pd.read_csv(pref_file, usecols=['stammaktien_ticker', 'vorzugsaktien_ticker'])
            for _, row in pref_df.iterrows():
                pref_mapping[row['vorzugsaktien_ticker']] = row['stammaktien_ticker']
            logger.info(f"Vorzugsaktien-Mapping geladen: {len(pref_mapping)} Eintraege")

        # Preferred-Ticker in Trades erkennen und Statistiken sammeln
        pref_stats = {'total_mappings': len(pref_mapping), 'trades_mapped': 0, 'trades_unmatched': 0}
        pref_unmatched_trades = []

        all_tickers_raw = df['ticker'].dropna().unique().tolist()
        all_tickers_raw = [t.strip() for t in all_tickers_raw if t.strip()]

        # Preferred-Ticker erkennen: $ im Ticker
        preferred_tickers_in_data = [t for t in all_tickers_raw if '$' in t]

        # Mapping anwenden: preferred → stammticker fuer Cache-Lookup
        # ticker_lookup_map: original_ticker → ticker der fuer yfinance verwendet wird
        ticker_lookup_map = {}
        for pt in preferred_tickers_in_data:
            if pt in pref_mapping:
                ticker_lookup_map[pt] = pref_mapping[pt]
                trades_with_ticker = (df['ticker'].str.strip() == pt).sum()
                pref_stats['trades_mapped'] += trades_with_ticker
            else:
                ticker_lookup_map[pt] = None  # unmatched
                trades_with_ticker = (df['ticker'].str.strip() == pt).sum()
                pref_stats['trades_unmatched'] += trades_with_ticker
                # Unmatched-Details sammeln
                unmatched_rows = df[df['ticker'].str.strip() == pt]
                pref_unmatched_trades.append({
                    'ticker': pt,
                    'count': len(unmatched_rows),
                    'names': ', '.join(unmatched_rows['name'].dropna().unique()[:3]),
                    'asset_names': ', '.join(unmatched_rows['asset_name'].dropna().unique()[:3]),
                })

        # 1. Cache laden (beide Dateien: positiv + null)
        cache = self._load_ticker_caches(mappings_dir)

        # 2. Neue Ticker identifizieren (mit Preferred-Aufloesung)
        all_tickers = []
        for t in all_tickers_raw:
            if '$' in t:
                resolved = ticker_lookup_map.get(t)
                if resolved:
                    # Stammticker in die Liste, damit er bei yfinance abgefragt wird
                    all_tickers.append(resolved)
                # Unmatched preferred → nicht abfragen
            else:
                all_tickers.append(t)
        all_tickers = list(set(all_tickers))  # Deduplizieren

        new_tickers = [t for t in all_tickers if t not in cache]

        # Pre-Filter: Ticker klassifizieren (valid/whitelist/filtered)
        valid_new = []
        filtered_new = []
        for t in new_tickers:
            cls = self._classify_ticker(t)
            if cls == 'filtered':
                filtered_new.append(t)
                cache[t] = None  # direkt null cachen, keine API-Abfrage
            else:
                valid_new.append(t)  # 'valid' oder 'whitelist'

        if filtered_new:
            print(f"      Pre-Filter: {len(filtered_new)} Non-Standard-Ticker erkannt "
                  f"(Warrants, CUSIPs, Bonds etc.) -> direkt null")

        if not valid_new:
            print(f"      Cache: {len(cache)} Ticker, 0 neue Abfragen noetig")
        else:
            est_minutes = len(valid_new) * settings.TICKER_LOOKUP_PAUSE_SECONDS / 60
            print(f"      Cache: {len(cache) - len(filtered_new)} bekannt, "
                  f"{len(valid_new)} neue Ticker, "
                  f"{len(filtered_new)} gefiltert (Non-Standard)")
            print(f"      Geschaetzte Dauer: {est_minutes:.1f} Minuten")

            # Bestaetigung bei vielen neuen Tickern
            if len(valid_new) >= settings.TICKER_LOOKUP_CONFIRM_THRESHOLD:
                try:
                    answer = input(f"      {len(valid_new)} yfinance-Abfragen starten? [Enter] oder Ctrl+C: ")
                except (KeyboardInterrupt, EOFError):
                    print("\n      [SKIP] Ticker-Industry Lookup abgebrochen")
                    df['Ticker_Sector'] = ''
                    df['Ticker_Industry'] = ''
                    # Gefilterte Ticker trotzdem speichern
                    self._save_ticker_caches(cache, mappings_dir)
                    return df

            # 3. yfinance abfragen
            import yfinance as yf

            # yfinance-Logger unterdrücken (404-Spam vermeiden)
            yf_logger = logging.getLogger('yfinance')
            yf_log_level_orig = yf_logger.level
            yf_logger.setLevel(logging.CRITICAL)

            success = 0
            errors = 0
            net_errors = 0
            net_errors_consecutive = 0
            retry_later = []  # Ticker die wegen Netzwerk-Fehler nicht gecacht werden
            start_time = time.time()
            cache_interval = getattr(settings, 'TICKER_LOOKUP_CACHE_INTERVAL', 100)

            for i, ticker_sym in enumerate(valid_new):
                entry = self._fetch_ticker_info(yf, ticker_sym)

                if entry == 'NETWORK_ERROR':
                    # Netzwerk-Fehler: NICHT cachen, spaeter erneut versuchen
                    net_errors += 1
                    net_errors_consecutive += 1
                    retry_later.append(ticker_sym)

                    # Auto-Pause bei mehreren Netzwerk-Fehlern
                    pause_threshold = getattr(settings, 'TICKER_LOOKUP_NET_ERROR_PAUSE', 30)
                    abort_threshold = getattr(settings, 'TICKER_LOOKUP_NET_ERROR_ABORT', 5)

                    if net_errors_consecutive >= abort_threshold:
                        print(f"\n      [!] {abort_threshold} Netzwerk-Fehler in Folge — Abbruch")
                        print(f"          {len(retry_later)} Ticker werden beim naechsten Lauf erneut versucht")
                        break
                    elif net_errors_consecutive >= 3:
                        print(f"\n      [!] {net_errors_consecutive} Netzwerk-Fehler in Folge — {pause_threshold}s Pause...")
                        time.sleep(pause_threshold)
                elif entry is not None:
                    cache[ticker_sym] = entry
                    success += 1
                    net_errors_consecutive = 0
                else:
                    cache[ticker_sym] = None
                    errors += 1
                    net_errors_consecutive = 0

                # Fortschritt alle 100 Ticker (mit ETA)
                done = i + 1
                if done % 100 == 0 or done == len(valid_new):
                    elapsed = time.time() - start_time
                    rate = elapsed / done if done > 0 else 0
                    remaining = rate * (len(valid_new) - done)
                    eta_min = remaining / 60
                    print(f"      [{done}/{len(valid_new)}] "
                          f"{success} OK, {errors} ohne, {net_errors} Netz-Fehler"
                          f" | ETA: {eta_min:.0f}min")

                # Cache-Checkpoint (nur positive, schnell)
                if done % cache_interval == 0:
                    self._save_ticker_cache_checkpoint(cache, mappings_dir)

                time.sleep(settings.TICKER_LOOKUP_PAUSE_SECONDS)

            # yfinance-Logger wiederherstellen
            yf_logger.setLevel(yf_log_level_orig)

            print(f"      Fertig: {success} mit Industry, {errors} ohne, {net_errors} Netz-Fehler")

            # Retry-Runde fuer Netzwerk-Fehler
            if retry_later:
                print(f"      Retry: {len(retry_later)} Ticker mit Netzwerk-Fehler...")
                yf_logger.setLevel(logging.CRITICAL)
                retry_success = 0
                for ticker_sym in retry_later:
                    entry = self._fetch_ticker_info(yf, ticker_sym)
                    if entry == 'NETWORK_ERROR':
                        pass  # Immer noch Netzwerk-Fehler — nicht cachen
                    elif entry is not None:
                        cache[ticker_sym] = entry
                        retry_success += 1
                        success += 1
                    else:
                        cache[ticker_sym] = None
                        errors += 1
                    time.sleep(settings.TICKER_LOOKUP_PAUSE_SECONDS)
                yf_logger.setLevel(yf_log_level_orig)
                still_failed = len(retry_later) - retry_success - sum(
                    1 for t in retry_later if t in cache)
                print(f"      Retry: {retry_success} OK, {still_failed} weiterhin fehlgeschlagen")

        # 4. Spalten befuellen (mit Preferred-Ticker-Aufloesung)
        def _resolve_ticker(t):
            """Loest Preferred-Ticker auf Stammticker auf fuer Cache-Lookup."""
            if pd.isna(t):
                return ''
            t = str(t).strip()
            if '$' in t:
                resolved = ticker_lookup_map.get(t)
                if resolved:
                    return (cache.get(resolved) or {}).get('sector', '')
                return ''
            return (cache.get(t) or {}).get('sector', '')

        def _resolve_ticker_industry(t):
            if pd.isna(t):
                return ''
            t = str(t).strip()
            if '$' in t:
                resolved = ticker_lookup_map.get(t)
                if resolved:
                    return (cache.get(resolved) or {}).get('industry', '')
                return ''
            return (cache.get(t) or {}).get('industry', '')

        df['Ticker_Sector'] = df['ticker'].map(_resolve_ticker)
        df['Ticker_Industry'] = df['ticker'].map(_resolve_ticker_industry)

        filled = (df['Ticker_Industry'] != '').sum()
        print(f"      {filled}/{len(df)} Trades mit Ticker-Industry angereichert")

        # 6. Vorzugsaktien-Report
        if pref_mapping or preferred_tickers_in_data:
            print(f"\n      Vorzugsaktien-Mapping:")
            print(f"        {pref_stats['total_mappings']} Mappings geladen")
            total_pref_trades = pref_stats['trades_mapped'] + pref_stats['trades_unmatched']
            print(f"        {total_pref_trades} Trades mit Preferred-Ticker")
            if pref_stats['trades_mapped'] > 0:
                # Beispiel zeigen
                example = next(iter(ticker_lookup_map.items()))
                if example[1]:
                    print(f"        {pref_stats['trades_mapped']} gemappt (z.B. {example[0]} -> {example[1]})")
                else:
                    print(f"        {pref_stats['trades_mapped']} gemappt")
            if pref_stats['trades_unmatched'] > 0:
                print(f"        {pref_stats['trades_unmatched']} unmatched -> {UNMATCHED_PREFERRED_PATH}")
            else:
                print(f"        0 unmatched")

        # Unmatched Preferred speichern
        if pref_unmatched_trades:
            unmatched_pref_df = pd.DataFrame(pref_unmatched_trades)
            self._ensure_directories()
            unmatched_pref_df.to_csv(UNMATCHED_PREFERRED_PATH, index=False, encoding='utf-8')
            logger.info(f"Unmatched Preferred Ticker gespeichert: {UNMATCHED_PREFERRED_PATH}")

        # Preferred-Ticker auch im Cache speichern (mit Stammticker-Daten)
        for pt, base in ticker_lookup_map.items():
            if base and base in cache:
                cache[pt] = cache[base]
        self._save_ticker_caches(cache, mappings_dir)

        # Delisted-Analyse: Ticker ohne Industry mit transaction_date
        # Nur echte Ticker (nicht gefilterte Non-Standard-Ticker)
        null_tickers = {t for t, v in cache.items()
                        if v is None and self._classify_ticker(t) != 'filtered'}
        if null_tickers:
            delisted_mask = (
                df['ticker'].notna() &
                df['ticker'].str.strip().isin(null_tickers) &
                df['Ticker_Industry'].eq('')
            )
            delisted_df = df[delisted_mask][['ticker', 'name', 'asset_name', 'transaction_date', 'chamber']].copy()

            if not delisted_df.empty:
                # Terminal-Report: Anzahl pro Jahr
                delisted_df['year'] = pd.to_datetime(
                    delisted_df['transaction_date'], errors='coerce'
                ).dt.year
                year_counts = delisted_df['year'].value_counts().sort_index()

                print(f"\n      Delisted/Unbekannte Ticker: {len(null_tickers)} Ticker, {len(delisted_df)} Trades")
                print(f"      Verteilung nach Jahr:")
                for year, count in year_counts.items():
                    if pd.notna(year):
                        print(f"        {int(year)}: {count:,} Trades")

                # CSV speichern (immer als Stage-Datei, bei --stage_save in delisted_trades.csv uebernehmen)
                self._ensure_directories()
                delisted_df.drop(columns=['year'], inplace=True)
                try:
                    delisted_df.to_csv(DELISTED_STAGE_PATH, index=False, encoding='utf-8')
                    print(f"      -> {DELISTED_STAGE_PATH}")
                except PermissionError:
                    print(f"      [!] Konnte delisted_trades_stage.csv nicht schreiben (Datei geoeffnet?)")

        return df

    # Fehlermuster die auf Netzwerk-Probleme hindeuten (nicht cachen!)
    _NETWORK_ERROR_PATTERNS = [
        'Could not resolve host',
        'TLS connect error',
        'Connection refused',
        'Connection reset',
        'Connection timed out',
        'Network is unreachable',
    ]

    def _is_network_error(self, error_msg: str) -> bool:
        """Prüft ob ein Fehler ein Netzwerk-Problem ist (vs. 404/inhaltlich)."""
        return any(p in error_msg for p in self._NETWORK_ERROR_PATTERNS)

    def _fetch_ticker_info(self, yf, ticker_sym: str) -> dict | None | str:
        """
        Holt Sector/Industry fuer einen Ticker von yfinance mit Retry-Logik.

        Returns:
            dict mit 'sector', 'industry' bei Erfolg
            None bei 404/unbekannt/ETF/Fonds ohne Sektor (sicher cachebar)
            'NETWORK_ERROR' bei DNS/TLS/Verbindungsfehler (NICHT cachen)
        """
        delay = settings.TICKER_LOOKUP_RETRY_DELAY
        for attempt in range(settings.TICKER_LOOKUP_RETRY_ATTEMPTS):
            try:
                info = yf.Ticker(ticker_sym).info
                sector = info.get('sector', '')
                industry = info.get('industry', '')
                if sector and industry:
                    return {'sector': sector, 'industry': industry}
                return None
            except Exception as e:
                err_str = str(e)
                if self._is_network_error(err_str):
                    # Netzwerk-Fehler: Retry mit Backoff
                    if attempt < settings.TICKER_LOOKUP_RETRY_ATTEMPTS - 1:
                        logger.debug(f"Netzwerk-Retry {ticker_sym} ({attempt+1}): {e}")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.warning(f"Netzwerk-Fehler {ticker_sym}: {e}")
                        return 'NETWORK_ERROR'
                else:
                    # 404, Permission, sonstige API-Fehler: nicht retrien
                    return None

    @staticmethod
    def _load_ticker_caches(mappings_dir: str) -> dict:
        """Laedt beide Cache-Dateien und mergt sie zu einem internen Cache."""
        cache = {}
        # Positive Cache (sector/industry)
        pos_path = os.path.join(mappings_dir, settings.TICKER_INDUSTRY_CACHE_FILE)
        if os.path.isfile(pos_path):
            with open(pos_path, 'r', encoding='utf-8') as f:
                pos = json.load(f)
            cache.update(pos)
            logger.info(f"Positive Cache geladen: {len(pos)} Ticker mit Industry")

        # Null Cache (delistet/ETF/Bonds/Non-Standard)
        null_path = os.path.join(mappings_dir, settings.TICKER_0_CACHE_FILE)
        if os.path.isfile(null_path):
            with open(null_path, 'r', encoding='utf-8') as f:
                null_cache = json.load(f)
            # Alle null-Eintraege setzen
            for t in null_cache:
                if t not in cache:  # Positive Cache hat Vorrang
                    cache[t] = None
            logger.info(f"Null Cache geladen: {len(null_cache)} Ticker ohne Industry")

        return cache

    @staticmethod
    def _save_ticker_caches(cache: dict, mappings_dir: str):
        """Splittet Cache in positive + null und speichert beide Dateien."""
        os.makedirs(mappings_dir, exist_ok=True)

        pos_cache = {k: v for k, v in cache.items() if v is not None}
        null_cache = sorted(k for k, v in cache.items() if v is None)

        pos_path = os.path.join(mappings_dir, settings.TICKER_INDUSTRY_CACHE_FILE)
        with open(pos_path, 'w', encoding='utf-8') as f:
            json.dump(pos_cache, f, ensure_ascii=False, indent=2)

        null_path = os.path.join(mappings_dir, settings.TICKER_0_CACHE_FILE)
        with open(null_path, 'w', encoding='utf-8') as f:
            json.dump(null_cache, f, ensure_ascii=False, indent=2)

        logger.info(f"Cache gespeichert: {len(pos_cache)} positiv ({settings.TICKER_INDUSTRY_CACHE_FILE}), "
                     f"{len(null_cache)} null ({settings.TICKER_0_CACHE_FILE})")

    @staticmethod
    def _save_ticker_cache_checkpoint(cache: dict, mappings_dir: str):
        """Schneller Checkpoint: nur positive Cache schreiben (Absturzsicherung)."""
        pos_cache = {k: v for k, v in cache.items() if v is not None}
        pos_path = os.path.join(mappings_dir, settings.TICKER_INDUSTRY_CACHE_FILE)
        os.makedirs(mappings_dir, exist_ok=True)
        with open(pos_path, 'w', encoding='utf-8') as f:
            json.dump(pos_cache, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _classify_ticker(ticker: str) -> str:
        """
        Klassifiziert einen Ticker als 'valid', 'whitelist' oder 'filtered'.

        Returns:
            'whitelist' - Ticker in Whitelist (trotz ungewoehnlichem Format bei yfinance abfragen)
            'valid'     - Standard-Ticker-Format, bei yfinance abfragen
            'filtered'  - Non-Standard (Warrant, CUSIP, Bond etc.), direkt null cachen
        """
        if ticker in settings.TICKER_WHITELIST:
            return 'whitelist'

        for pattern in settings.TICKER_FILTER_PATTERNS:
            if re.match(pattern, ticker):
                return 'filtered'

        return 'valid'

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
            print("      [SKIP] Keine bill_actions.json vorhanden oder keine gemappten Votes")
            # Defaults setzen
            for col in VOTE_TIMING_COLUMNS:
                if col not in df.columns:
                    df[col] = False if col == 'Vote_Timing_Hit' else (0 if 'Score' in col or 'Days' in col else '')
            return df

        result = vote_filter.apply(df, votes_df)

        hits = result['Vote_Timing_Hit'].sum()
        print(f"      {hits} Trades mit Vote-Timing-Hit")

        return result


    # =========================================================================
    # Filter-Logik: Scoring + Filter 0 + Filter 4
    # =========================================================================

    def _apply_match_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Setzt MatchScore + MatchSource via NameMatcher (fuer filter-Stage)."""
        self._init_matcher()
        df = df.copy()
        df['MatchScore'] = 0
        df['MatchSource'] = ''

        for idx, row in df.iterrows():
            name = row.get('name', '')
            chamber = str(row.get('chamber', '')).lower()
            result = self.matcher.match(name, chamber)
            df.at[idx, 'MatchScore'] = result['score']
            df.at[idx, 'MatchSource'] = result['source']

        return df

    def _apply_committee_relevance(self, df: pd.DataFrame) -> pd.DataFrame:
        """Berechnet Relevant_Sectors, Relevance_Score, Confidence_Level."""
        from filters.committee_filter import CommitteeFilter
        committee_filter = CommitteeFilter()

        df = df.copy()
        df['Relevant_Sectors'] = ''
        df['Relevance_Score'] = 0
        df['Confidence_Level'] = ''

        for idx, row in df.iterrows():
            committees_full = str(row.get('Member_Committees_Full', '') or '')
            if not committees_full or committees_full == 'nan':
                continue
            chamber = str(row.get('Member_Chamber', '') or '')
            ticker = str(row.get('ticker', '') or '')
            try:
                relevance = committee_filter.calculate_relevance(
                    committees_string=committees_full,
                    chamber=chamber,
                    ticker=ticker,
                )
                df.at[idx, 'Relevant_Sectors'] = json.dumps(relevance['relevant_sectors'])
                df.at[idx, 'Relevance_Score'] = relevance['relevance_score']
                df.at[idx, 'Confidence_Level'] = relevance['confidence_level']
            except Exception as e:
                logger.warning(f"Relevance-Berechnung fehlgeschlagen fuer {row.get('name', '')}: {e}")

        return df

    def _apply_filter_logic(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wendet alle Filter an: Scoring, Filter 0 (Asset-Klasse), Filter 4 (Vote-Timing)."""
        # 1. MatchScore + MatchSource
        df = self._apply_match_scores(df)

        # 2. Committee-Relevance-Scoring
        df = self._apply_committee_relevance(df)

        # 3. Filter 0: Asset-Klasse
        df = AssetClassFilter().apply(df)

        # 4. Filter 4: Vote-Timing
        df = self.enrich_with_vote_timing(df)

        # TODO: Filter 1/2/6/7 (Volumen, Schwarm, Long/Short, Position)

        return df

    # =========================================================================
    # Stage-Pipeline: Backup, IO-Helpers, State-Management, Stage-Methoden
    # =========================================================================

    def _backup_master(self):
        """Backup von trades_master.csv vor destruktivem Schreiben."""
        if not os.path.isfile(MASTER_CSV_PATH):
            return
        os.makedirs(ARCHIVE_BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup_path = os.path.join(ARCHIVE_BACKUP_DIR, f'trades_master_{ts}.csv')
        shutil.copy2(MASTER_CSV_PATH, backup_path)
        logger.info(f"Master-Backup: {backup_path}")
        print(f"      Backup: {backup_path}")

    def _check_stage_inputs(self, stage: str, df: pd.DataFrame):
        """Prueft ob alle Eingabe-Spalten fuer eine Stage vorhanden sind."""
        required = STAGE_DEPS.get(stage, {}).get('input_cols', [])
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise RuntimeError(
                f"Stage '{stage}' braucht fehlende Spalten: {missing}\n"
                f"Bitte zuerst die zustaendigen Vorgaenger-Stages ausfuehren.")

    def _read_stage_input(self, stage: str, delta: bool, dry_run: bool) -> pd.DataFrame:
        """Liest Input-Datei basierend auf Flag-Matrix."""
        if stage == 'rename':
            return self.load_and_rename()

        if delta:
            if not os.path.isfile(STAGE_CSV_PATH):
                raise FileNotFoundError(
                    f"Delta-Stage-Modus: trades_stage.csv nicht gefunden: {STAGE_CSV_PATH}\n"
                    f"Bitte zuerst --delta --stage_rename ausfuehren.")
            df = pd.read_csv(STAGE_CSV_PATH, low_memory=False)
        else:
            if not os.path.isfile(MASTER_CSV_PATH):
                raise FileNotFoundError(
                    f"trades_master.csv nicht gefunden: {MASTER_CSV_PATH}\n"
                    f"Bitte zuerst Full Rebuild: python scripts/import_trades.py")
            df = pd.read_csv(MASTER_CSV_PATH, low_memory=False)

        for col in ['transaction_date', 'disclosure_date']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        return df

    def _write_stage_output(self, df: pd.DataFrame, stage: str, delta: bool, dry_run: bool):
        """Schreibt Output-Datei basierend auf Flag-Matrix (nicht fuer 'save')."""
        self._ensure_directories()
        if dry_run or delta:
            df.to_csv(STAGE_CSV_PATH, index=False, encoding='utf-8')
            print(f"      -> {STAGE_CSV_PATH} ({len(df):,} Trades)")
        else:
            self._backup_master()
            df.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
            print(f"      -> {MASTER_CSV_PATH} ({len(df):,} Trades)")

    def _merge_into_master(self, stage_df: pd.DataFrame,
                           master_df: pd.DataFrame = None) -> pd.DataFrame:
        """Merged stage_df in trades_master.csv (fuer Delta-Save)."""
        if master_df is None:
            if not os.path.isfile(MASTER_CSV_PATH):
                return stage_df
            master_df = pd.read_csv(MASTER_CSV_PATH, low_memory=False)
        merged = pd.concat([master_df, stage_df], ignore_index=True)
        dup_key = self._create_dup_key(merged)
        merged = merged[~dup_key.duplicated(keep='last')]
        return merged

    @staticmethod
    def _load_pipeline_state() -> dict:
        """Laedt Pipeline-State aus JSON."""
        if os.path.isfile(PIPELINE_STATE_PATH):
            with open(PIPELINE_STATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"mode": "full", "completed": [], "trades_count": 0, "started_at": ""}

    @staticmethod
    def _save_pipeline_state(state: dict):
        """Speichert Pipeline-State als JSON."""
        os.makedirs(PROCESSED_DIR, exist_ok=True)
        with open(PIPELINE_STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)

    def _stage_rename(self, delta: bool = False, dry_run: bool = False):
        """Stage: CSV laden, Spalten umbenennen, Datum normalisieren."""
        print(f"\n[Stage: rename] Lade und benenne Spalten um...")
        df = self.load_and_rename()
        df = self.normalize_dates(df)
        print(f"      {len(df):,} Trades geladen")

        if delta:
            if not os.path.exists(MASTER_CSV_PATH):
                raise FileNotFoundError(
                    f"Delta-Modus: trades_master.csv nicht gefunden: {MASTER_CSV_PATH}")
            master_df = pd.read_csv(MASTER_CSV_PATH, low_memory=False)
            if 'transaction_date' in master_df.columns:
                master_df['transaction_date'] = pd.to_datetime(
                    master_df['transaction_date'], errors='coerce')
            df = self.detect_new_trades(df, master_df)
            print(f"      Delta: {self.stats.get('new_trades', 0):,} neue, "
                  f"{self.stats.get('duplicates', 0):,} bereits vorhanden")
            if df.empty:
                print("      [OK] Keine neuen Trades.")

        self._write_stage_output(df, 'rename', delta, dry_run)

    def _stage_date(self, delta: bool = False, dry_run: bool = False):
        """Stage: Datum-Normalisierung."""
        print(f"\n[Stage: date] Normalisiere Datumsspalten...")
        df = self._read_stage_input('date', delta, dry_run)
        self._check_stage_inputs('date', df)
        df = self.normalize_dates(df)

        n_fallback = self.stats.get('date_fallback', 0)
        n_future = self.stats.get('date_future_fallback', 0)
        if n_fallback > 0:
            print(f"      Fallback: {n_fallback} fehlende transaction_dates gefuellt")
        if n_future > 0:
            print(f"      Fallback: {n_future} future-dates korrigiert")

        self._write_stage_output(df, 'date', delta, dry_run)

    def _stage_committees(self, delta: bool = False, dry_run: bool = False):
        """Stage: Committee-Member-Enrichment."""
        print(f"\n[Stage: committees] Reichere mit Committee-Member-Daten an...")
        df = self._read_stage_input('committees', delta, dry_run)
        self._check_stage_inputs('committees', df)

        df, unmatched_df = self.enrich_member_data(df)
        print(f"      {self.stats['matched']} gematcht, {self.stats['unmatched']} unmatched")

        self._write_stage_output(df, 'committees', delta, dry_run)
        if not unmatched_df.empty:
            unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')

    def _stage_tickertype(self, delta: bool = False, dry_run: bool = False):
        """Stage: ticker_type ableiten."""
        print(f"\n[Stage: tickertype] Leite ticker_type ab...")
        df = self._read_stage_input('tickertype', delta, dry_run)
        self._check_stage_inputs('tickertype', df)
        df = self.derive_ticker_types(df)
        self._write_stage_output(df, 'tickertype', delta, dry_run)

    def _stage_tickerindustry(self, delta: bool = False, dry_run: bool = False):
        """Stage: yfinance Sector/Industry Lookup."""
        print(f"\n[Stage: tickerindustry] Ticker-Industry Lookup...")
        df = self._read_stage_input('tickerindustry', delta, dry_run)
        self._check_stage_inputs('tickerindustry', df)
        df = self.enrich_with_ticker_industry(df)
        self._write_stage_output(df, 'tickerindustry', delta, dry_run)

    def _stage_filter(self, delta: bool = False, dry_run: bool = False):
        """Stage: Scoring + Filter 0 (Asset-Klasse) + Filter 4 (Vote-Timing)."""
        print(f"\n[Stage: filter] Filter + Scoring-Spalten berechnen...")
        df = self._read_stage_input('filter', delta, dry_run)
        self._check_stage_inputs('filter', df)
        df = self._apply_filter_logic(df)
        self._write_stage_output(df, 'filter', delta, dry_run)

    def _stage_save(self, delta: bool = False, dry_run: bool = False):
        """Stage: trades_stage.csv → trades_master.csv (replace oder delta-merge)."""
        print(f"\n[Stage: save] Speichere Ergebnisse...")

        if not os.path.isfile(STAGE_CSV_PATH):
            raise FileNotFoundError(
                f"trades_stage.csv nicht gefunden: {STAGE_CSV_PATH}\n"
                f"Bitte zuerst Stages ausfuehren (z.B. --dry_run oder --delta --stage_*)")

        df = pd.read_csv(STAGE_CSV_PATH, low_memory=False)
        df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        self._backup_master()
        self._ensure_directories()

        if delta and os.path.isfile(MASTER_CSV_PATH):
            df = self._merge_into_master(df)
            print(f"      Delta-Merge: {len(df):,} Trades gesamt")

        df.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        print(f"      -> {MASTER_CSV_PATH} ({len(df):,} Trades)")

        # Stage-Datei aufraaeumen
        os.remove(STAGE_CSV_PATH)

        # Unmatched-Stage → Unmatched kopieren
        if os.path.isfile(UNMATCHED_STAGE_PATH):
            shutil.copy2(UNMATCHED_STAGE_PATH, UNMATCHED_PATH)
            os.remove(UNMATCHED_STAGE_PATH)
            print(f"      Unmatched: {UNMATCHED_PATH}")

        # Delisted-Stage → Delisted kopieren
        if os.path.isfile(DELISTED_STAGE_PATH):
            shutil.copy2(DELISTED_STAGE_PATH, DELISTED_TRADES_PATH)
            os.remove(DELISTED_STAGE_PATH)
            print(f"      Delisted: {DELISTED_TRADES_PATH}")

    def run_stage(self, stage: str, delta: bool = False, dry_run: bool = False):
        """
        Fuehrt eine einzelne Pipeline-Stage aus (re-runnable).
        Flag-Matrix: siehe Modul-Docstring.
        """
        state = self._load_pipeline_state()

        method = getattr(self, f'_stage_{stage}')
        method(delta=delta, dry_run=dry_run)

        # State informell aktualisieren (kein Gate mehr)
        completed = state.get('completed', [])
        if stage not in completed:
            completed.append(stage)
        state['completed'] = completed
        state['mode'] = 'delta' if delta else 'full'
        state['dry_run'] = dry_run
        state.setdefault('started_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self._save_pipeline_state(state)

        print(f"\n[OK] Stage '{stage}' abgeschlossen.")

    @staticmethod
    def _create_dup_key(df: pd.DataFrame) -> pd.Series:
        """Erstellt einen String-Key pro Zeile fuer Duplikat-Erkennung."""
        key_parts = []
        for col in DUPLICATE_COLUMNS:
            if col in df.columns:
                key_parts.append(df[col].astype(str))
        return key_parts[0].str.cat(key_parts[1:], sep='|', na_rep='')

    def detect_new_trades(self, input_df: pd.DataFrame,
                          master_df: pd.DataFrame) -> pd.DataFrame:
        """
        Erkennt neue Trades durch Key-Vergleich (fuer Delta-Modus).

        Args:
            input_df: Komplette Quiver-Daten (neu heruntergeladen)
            master_df: Bestehende trades_master.csv

        Returns:
            DataFrame nur mit neuen Trades (nicht in Master vorhanden)
        """
        if master_df.empty:
            self.stats['new_trades'] = len(input_df)
            self.stats['duplicates'] = 0
            return input_df.copy()

        input_keys = self._create_dup_key(input_df)
        master_keys = set(self._create_dup_key(master_df))

        is_new = ~input_keys.isin(master_keys)
        new_trades = input_df[is_new].copy()

        self.stats['new_trades'] = len(new_trades)
        self.stats['duplicates'] = len(input_df) - len(new_trades)

        logger.info(f"Delta: {self.stats['new_trades']} neue, "
                     f"{self.stats['duplicates']} bereits vorhanden")
        return new_trades

    def run_delta(self, dry_run: bool = False) -> dict:
        """
        Delta-Import: Nur neue Trades anreichern und an Master mergen.

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Delta Import")
        print("=" * 60)

        # 1. Laden + Umbenennen
        print("\n[1/6] Lade Quiver-Trades...")
        input_df = self.load_and_rename()
        print(f"      Gesamt: {len(input_df)} Trades in Quiver-Datei")

        # 2. Datum normalisieren
        print("\n[2/6] Normalisiere Datumsspalten...")
        input_df = self.normalize_dates(input_df)
        n_fallback = self.stats.get('date_fallback', 0)
        n_future = self.stats.get('date_future_fallback', 0)
        if n_fallback > 0:
            print(f"      Fallback: {n_fallback} fehlende transaction_dates gefuellt")
        if n_future > 0:
            print(f"      Fallback: {n_future} future-dates korrigiert")

        # 3. Master laden + Delta erkennen
        print("\n[3/6] Lade Master + erkenne neue Trades...")
        if not os.path.exists(MASTER_CSV_PATH):
            print("      [!] Keine trades_master.csv vorhanden.")
            print("          Bitte zuerst Full Rebuild: python scripts/import_trades.py")
            return self.stats
        master_df = pd.read_csv(MASTER_CSV_PATH, low_memory=False)
        if 'transaction_date' in master_df.columns:
            master_df['transaction_date'] = pd.to_datetime(
                master_df['transaction_date'], errors='coerce')
        print(f"      {len(master_df):,} bestehende Trades")

        new_trades = self.detect_new_trades(input_df, master_df)
        print(f"      {self.stats['new_trades']:,} neue Trades")
        print(f"      {self.stats['duplicates']:,} bereits vorhanden")

        if new_trades.empty:
            print("\n[OK] Keine neuen Trades zu verarbeiten.")
            return self.stats

        # 4. Committee-Member-Enrichment
        print(f"\n[4/6] Reichere {len(new_trades):,} neue Trades an...")
        enriched_df, unmatched_df = self.enrich_member_data(new_trades)
        print(f"      {self.stats['matched']} gematcht, {self.stats['unmatched']} unmatched")

        # 5. Ticker-Type + Ticker-Industry
        print("\n[5/6] Ticker-Type + Industry Lookup...")
        enriched_df = self.derive_ticker_types(enriched_df)
        enriched_df = self.enrich_with_ticker_industry(enriched_df)

        # 6. Filter-Logik (Scoring + Filter 0 + Filter 4)
        print("\n[6/6] Filter-Logik (Scoring, Asset-Klasse, Vote-Timing)...")
        enriched_df = self._apply_filter_logic(enriched_df)

        # Speichern
        enriched_df = enriched_df.copy()
        enriched_df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._ensure_directories()

        # Unmatched immer in Stage-Datei schreiben
        if not unmatched_df.empty:
            unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')

        if dry_run:
            enriched_df.to_csv(STAGE_CSV_PATH, index=False, encoding='utf-8')
            print(f"\nDry-Run: {len(enriched_df):,} neue Trades → {STAGE_CSV_PATH}")
            print("(Zum Committen: python scripts/import_trades.py --delta --stage_save)")
        else:
            self._backup_master()
            updated_master = self._merge_into_master(enriched_df, master_df)
            updated_master.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
            logger.info(f"Master aktualisiert: {len(updated_master):,} Trades "
                        f"({len(enriched_df):,} neu hinzugefuegt)")
            if not unmatched_df.empty:
                shutil.copy2(UNMATCHED_STAGE_PATH, UNMATCHED_PATH)
            if os.path.isfile(DELISTED_STAGE_PATH):
                shutil.copy2(DELISTED_STAGE_PATH, DELISTED_TRADES_PATH)

        # Zusammenfassung
        print("\n" + "=" * 60)
        print("ZUSAMMENFASSUNG (Delta)")
        print("=" * 60)
        print(f"Quiver:     {self.stats['total_input']:,} Trades")
        print(f"Master:     {len(master_df):,} bestehend")
        print(f"Neu:        {self.stats['new_trades']:,} erkannt")
        print(f"Gematcht:   {self.stats['matched']:,} Trades")
        print(f"Unmatched:  {self.stats['unmatched']:,} Trades")
        if not dry_run:
            updated_count = len(master_df) + self.stats.get('new_trades', 0)
            print(f"Master neu: ~{updated_count:,} Trades gesamt")
        print(f"\nOutput-Dateien:")
        print(f"  - {STAGE_CSV_PATH if dry_run else MASTER_CSV_PATH}")
        if not unmatched_df.empty:
            print(f"  - {UNMATCHED_STAGE_PATH if dry_run else UNMATCHED_PATH}")
        if os.path.isfile(DELISTED_STAGE_PATH):
            print(f"  - {DELISTED_STAGE_PATH if dry_run else DELISTED_TRADES_PATH}")

        return self.stats

    def save_results(
        self,
        enriched_df: pd.DataFrame,
        unmatched_df: pd.DataFrame
    ):
        """
        Speichert alle Ergebnisse.
        trades_master.csv wird komplett neu geschrieben (kein Append).

        Args:
            enriched_df: Alle angereicherten Trades (kompletter Quiver-Snapshot)
            unmatched_df: Nicht-gematchte Namen
        """
        self._ensure_directories()

        # Import-Datum hinzufuegen
        enriched_df = enriched_df.copy()
        enriched_df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Master komplett neu schreiben (Quiver = vollstaendiger Snapshot)
        enriched_df.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        logger.info(f"Master neu geschrieben: {MASTER_CSV_PATH} ({len(enriched_df)} Trades)")

        # Unmatched Names: Stage → Final
        if not unmatched_df.empty:
            unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')
            shutil.copy2(UNMATCHED_STAGE_PATH, UNMATCHED_PATH)
            logger.info(f"Unmatched Names: {UNMATCHED_PATH} ({len(unmatched_df)} Namen)")

        # Delisted Trades: Stage → Final
        if os.path.isfile(DELISTED_STAGE_PATH):
            shutil.copy2(DELISTED_STAGE_PATH, DELISTED_TRADES_PATH)
            logger.info(f"Delisted Trades: {DELISTED_TRADES_PATH}")

    def run(self, dry_run: bool = False) -> dict:
        """
        Fuehrt den kompletten Import durch (alle Stages im Speicher).

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Import Pipeline")
        print("=" * 60)

        # 1. Laden + Umbenennen
        print("\n[1/6] Lade Quiver-Trades...")
        df = self.load_and_rename()
        print(f"      Gesamt: {len(df)} Trades geladen")

        # 2. Datum normalisieren
        print("\n[2/6] Normalisiere Datumsspalten...")
        df = self.normalize_dates(df)
        n_fallback = self.stats.get('date_fallback', 0)
        n_future = self.stats.get('date_future_fallback', 0)
        if n_fallback > 0:
            print(f"      Fallback: {n_fallback} fehlende transaction_dates gefuellt")
        if n_future > 0:
            print(f"      Fallback: {n_future} future-dates korrigiert")

        # 3. Committee-Member-Enrichment
        print("\n[3/6] Reichere mit Committee-Member-Daten an...")
        df, unmatched_df = self.enrich_member_data(df)
        print(f"      {self.stats['matched']} erfolgreich gematcht")
        print(f"      {self.stats['unmatched']} nicht gefunden")

        # 4. Ticker-Type ableiten
        print("\n[4/6] Ticker-Type Klassifikation...")
        df = self.derive_ticker_types(df)

        # 5. Ticker-Industry Lookup (yfinance)
        print("\n[5/6] Ticker-Industry Lookup...")
        df = self.enrich_with_ticker_industry(df)

        # 6. Filter-Logik (Scoring + Filter 0 + Filter 4)
        print("\n[6/6] Filter-Logik (Scoring, Asset-Klasse, Vote-Timing)...")
        df = self._apply_filter_logic(df)

        # Speichern
        print("\nSpeichere Ergebnisse...")
        if dry_run:
            self._ensure_directories()
            df_out = df.copy()
            df_out['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            df_out.to_csv(STAGE_CSV_PATH, index=False, encoding='utf-8')
            if not unmatched_df.empty:
                unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')
            print(f"\nDry-Run: {len(df_out):,} Trades → {STAGE_CSV_PATH}")
            print("(Zum Committen: python scripts/import_trades.py --stage_save)")
        else:
            self._backup_master()
            self.save_results(df, unmatched_df)

        # Zusammenfassung
        print("\n" + "=" * 60)
        print("ZUSAMMENFASSUNG")
        print("=" * 60)
        print(f"Input:      {self.stats['total_input']:,} Trades")
        print(f"Gematcht:   {self.stats['matched']:,} Trades")
        print(f"Unmatched:  {self.stats['unmatched']:,} Trades")

        print("\nOutput-Dateien:")
        print(f"  - {STAGE_CSV_PATH if dry_run else MASTER_CSV_PATH}")
        if self.stats['unmatched'] > 0:
            print(f"  - {UNMATCHED_STAGE_PATH if dry_run else UNMATCHED_PATH}")
        if os.path.isfile(DELISTED_STAGE_PATH):
            print(f"  - {DELISTED_STAGE_PATH if dry_run else DELISTED_TRADES_PATH}")

        return self.stats


def main():
    """Hauptfunktion: Fuehrt Import durch."""
    parser = argparse.ArgumentParser(description='Congress Trade Tracker - Import Pipeline')
    parser.add_argument('--delta', action='store_true',
                        help='Delta-Import: nur neue Trades anreichern und an Master mergen')
    parser.add_argument('--dry_run', action='store_true',
                        help='Schreibt Output in trades_stage.csv statt trades_master.csv')
    # Stage-Flags (einzelne Pipeline-Schritte, re-runnable)
    parser.add_argument('--stage_rename', action='store_true',
                        help='Stage: CSV laden und Spalten umbenennen')
    parser.add_argument('--stage_date', action='store_true',
                        help='Stage: Datum-Normalisierung')
    parser.add_argument('--stage_committees', action='store_true',
                        help='Stage: Committee-Member-Enrichment')
    parser.add_argument('--stage_tickertype', action='store_true',
                        help='Stage: ticker_type ableiten')
    parser.add_argument('--stage_tickerindustry', action='store_true',
                        help='Stage: yfinance Sector/Industry Lookup')
    parser.add_argument('--stage_filter', action='store_true',
                        help='Stage: Scoring + Filter 0 (Asset-Klasse) + Filter 4 (Vote-Timing)')
    parser.add_argument('--stage_save', action='store_true',
                        help='Stage: trades_stage.csv → trades_master.csv')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Stage-Flags auswerten
    stage_flags = {
        'rename': args.stage_rename,
        'date': args.stage_date,
        'committees': args.stage_committees,
        'tickertype': args.stage_tickertype,
        'tickerindustry': args.stage_tickerindustry,
        'filter': args.stage_filter,
        'save': args.stage_save,
    }
    active_stages = [name for name, flag in stage_flags.items() if flag]

    importer = TradeImporter()

    try:
        if len(active_stages) == 1:
            importer.run_stage(active_stages[0], delta=args.delta, dry_run=args.dry_run)
        elif len(active_stages) > 1:
            print("[X] Nur ein --stage_* Flag gleichzeitig erlaubt")
            sys.exit(1)
        else:
            # Kein Stage-Flag → kompletter Lauf
            if args.delta:
                importer.run_delta(dry_run=args.dry_run)
            else:
                importer.run(dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"\n[X] FEHLER: {e}")
        print(f"\nBitte stelle sicher, dass die Quiver-Datei vorhanden ist:")
        print(f"  - {QUIVER_TRADES_PATH}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n[X] Stage-Fehler: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[X] FEHLER: {e}")
        logger.exception("Import fehlgeschlagen")
        sys.exit(1)


if __name__ == "__main__":
    main()
