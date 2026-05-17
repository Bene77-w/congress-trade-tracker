"""
Congress Trade Tracker - Trade Import Pipeline (8 Stages, Rebuild Teil 3)
Importiert Trades, reichert mit Committee-Daten, Ticker-Klassifikation,
OpenFIGI/yfinance-Sektor und Filter-Logik an.

Modi:
  python scripts/import_trades.py                    # Full Rebuild (alle 8 Stages)
  python scripts/import_trades.py --delta            # Nur neue Trades (via trade_id)
  python scripts/import_trades.py --dry_run          # Alle Stages -> trades_stage.csv

Stage-Pipeline (einzelne Schritte, re-runnable):
  --stage_rename          # CSV laden + Spalten umbenennen + trade_id
  --stage_date            # Datum normalisieren
  --stage_committees      # Committee-Member-Enrichment (Fuzzy-Match)
  --stage_tickerclassify  # Offline-Klassifikation (Regex-Entscheidungsbaum)
  --stage_tickeropenfigi  # OpenFIGI-Normalisierung (FIGI, ticker_figi)
  --stage_tickeryfinance  # yfinance Sector/Industry-Anreicherung
  --stage_filter          # Scoring + Filter 0/4
  --stage_save            # trades_stage.csv -> complete + curated Master

Bonus-Flags (kein Master-Touch, exklusiv):
  --manual_review_only    # Regeneriert manual_review_tickers.csv aus complete
  --regenerate_curated    # Regeneriert trades_master.csv aus complete

Flag-Matrix:
  --dry_run        schreibt Output in trades_stage.csv statt complete+curated
  --delta          Delta-Detect via trade_id, merge bei save
  --delta --stage_rename --dry_run  -> Stage-CSV (neue Trades, kein Master-Touch)
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
from datetime import datetime, timedelta
from typing import Tuple

import pandas as pd

# Pfade konfigurieren
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import settings
from utils.name_matcher import NameMatcher
from utils.openfigi_client import OpenFigiClient  # noqa: F401  (Legacy, parallel verfuegbar)
from utils.trade_id import compute_trade_ids, detect_new_trades
from utils.ticker_classifier import (
    classify,
    TickerType,
    ApiStrategy,
    Confidence,
    TickerClassification,
)
from utils.ticker_normalizer import TickerNormalizer, NormalizedTicker
from utils.ticker_sector import TickerSectorEnricher
from filters.vote_timing_filter import VoteTimingFilter, VOTE_TIMING_COLUMNS
from filters.asset_class_filter import AssetClassFilter, ASSET_CLASS_COLUMNS

logger = logging.getLogger(__name__)

# Pfade aus Config (bei Neuwahlen in settings.py anpassen)
PROCESSED_DIR = os.path.join(BASE_DIR, settings.DATA_PROCESSED_PATH)
RAW_DIR = os.path.join(BASE_DIR, settings.DATA_RAW_PATH)
# Input/Output Dateien (aus Config)
QUIVER_TRADES_PATH = os.path.join(RAW_DIR, settings.QUIVER_TRADES_FILE)
MASTER_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_master.csv')                          # kuratierte Sicht
MASTER_COMPLETE_CSV_PATH = os.path.join(PROCESSED_DIR, settings.TRADES_MASTER_COMPLETE_CSV)  # vollstaendig
MANUAL_REVIEW_CSV_PATH = os.path.join(PROCESSED_DIR, settings.MANUAL_REVIEW_CSV)
UNMATCHED_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names.csv')
UNMATCHED_PREFERRED_PATH = os.path.join(PROCESSED_DIR, 'unmatched_preferred.csv')
DELISTED_TRADES_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades.csv')
IGNORED_TICKERS_PATH = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH,
                                    settings.IGNORED_TICKERS_FILE)
CHECKPOINT_PATH = os.path.join(PROCESSED_DIR, '.enrichment_checkpoint.pkl')

CHECKPOINT_INTERVAL = 5000  # Trades zwischen Checkpoint-Speicherungen

# Stage-Pipeline Pfade
STAGE_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_stage.csv')
PIPELINE_STATE_PATH = os.path.join(PROCESSED_DIR, 'pipeline_state.json')
UNMATCHED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names_stage.csv')
DELISTED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades_stage.csv')
UNMATCHED_PREFERRED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'unmatched_preferred_stage.csv')

STAGE_DEPS = {
    'rename': {
        'input_cols': [],
        'output_cols': ['trade_id', 'name', 'ticker', 'asset_name',
                        'transaction_date', 'disclosure_date',
                        'chamber', 'Member_Party', 'Member_State',
                        'comment', 'subholding', 'bio_guide_id'],
    },
    'date': {
        'input_cols': ['transaction_date', 'disclosure_date'],
        'output_cols': ['transaction_date', 'disclosure_date'],
    },
    'committees': {
        'input_cols': ['name', 'chamber'],
        'output_cols': ['Member_Party', 'Member_State',
                        'Member_ExOfficio', 'Member_Chamber', 'Member_Committees_Full'],
    },
    'tickerclassify': {
        'input_cols': ['ticker'],
        'output_cols': ['ticker_type', 'ticker_base', 'classification_reason',
                        'classification_confidence', 'api_strategy',
                        'exch_code', 'series_letter', 'status_suffix',
                        'manual_review_flag'],
    },
    'tickeropenfigi': {
        'input_cols': ['ticker_type', 'ticker_base', 'api_strategy'],
        'output_cols': ['figi', 'ticker_figi', 'name_figi',
                        'security_type_figi', 'market_sector_figi'],
    },
    'tickeryfinance': {
        'input_cols': ['ticker_base', 'ticker_type'],
        'output_cols': ['sector_yf', 'industry_yf', 'quote_type_yf'],
    },
    'filter': {
        'input_cols': ['name', 'chamber', 'Member_Committees_Full', 'ticker',
                       'ticker_type', 'transaction_date'],
        'output_cols': ['MatchScore', 'MatchSource', 'Insider_Sectors', 'Relevant_Sectors',
                        'Relevance_Score', 'asset_class_excluded',
                        'Vote_Timing_Hit', 'Vote_Timing_Hits', 'Vote_Timing_Hit_Count'],
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
    'TickerType': 'tickertype_org',
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
        Scoring-Spalten (MatchScore, Insider_Sectors, Relevant_Sectors etc.) werden in _stage_filter gesetzt.
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

    # =========================================================================
    # Ticker-Stages (Rebuild Teil 3): Classify -> OpenFIGI -> yfinance
    # =========================================================================

    def _reconstruct_classifications(self, df: pd.DataFrame) -> list:
        """Rekonstruiert TickerClassification-Liste aus DataFrame-Spalten.

        Erwartet Spalten aus _stage_tickerclassify (siehe STAGE_DEPS).
        Reihenfolge entspricht df.index (positional).
        """
        out = []
        for _, row in df.iterrows():
            exch = row.get('exch_code')
            series = row.get('series_letter')
            status = row.get('status_suffix')
            out.append(TickerClassification(
                raw_ticker=str(row.get('ticker') or ''),
                company_name=str(row.get('asset_name') or ''),
                ticker_base=str(row.get('ticker_base') or ''),
                ticker_type=TickerType(row['ticker_type']),
                api_strategy=ApiStrategy(row['api_strategy']),
                exch_code=exch if pd.notna(exch) and exch != '' else None,
                confidence=Confidence(row.get('classification_confidence') or 'low'),
                classification_reason=str(row.get('classification_reason') or ''),
                manual_review=bool(row.get('manual_review_flag', False)),
                series_letter=series if pd.notna(series) and series != '' else None,
                status_suffix=status if pd.notna(status) and status != '' else None,
            ))
        return out

    def _reconstruct_normalized(self, df: pd.DataFrame) -> list:
        """Rekonstruiert NormalizedTicker-Liste aus DataFrame-Spalten."""
        classifications = self._reconstruct_classifications(df)
        out = []
        for cls, (_, row) in zip(classifications, df.iterrows()):
            def _val(col):
                v = row.get(col)
                return v if pd.notna(v) and v != '' else None
            out.append(NormalizedTicker(
                classification=cls,
                figi=_val('figi'),
                ticker_figi=_val('ticker_figi'),
                name_figi=_val('name_figi'),
                security_type_figi=_val('security_type_figi'),
                market_sector_figi=_val('market_sector_figi'),
                normalization_errors=[],
            ))
        return out

    def _load_ignored_tickers(self) -> set:
        """Lädt Ignore-Liste (raw_ticker → set). Cached pro Instanz."""
        if hasattr(self, '_ignored_tickers_cache'):
            return self._ignored_tickers_cache
        ignored: set = set()
        if os.path.isfile(IGNORED_TICKERS_PATH):
            try:
                ign_df = pd.read_csv(IGNORED_TICKERS_PATH, dtype=str).fillna('')
                ignored = set(ign_df['raw_ticker'].astype(str).str.strip())
            except Exception as exc:
                logger.warning(f"Ignore-Liste {IGNORED_TICKERS_PATH} konnte nicht gelesen werden: {exc}")
        self._ignored_tickers_cache = ignored
        return ignored

    def _apply_ignore_list(self, df: pd.DataFrame) -> pd.DataFrame:
        """Markiert Trades, deren raw_ticker in ignored_tickers.csv steht.

        Setzt ticker_type='ignored', api_strategy='skip', manual_review_flag=False.
        Verhindert, dass private/nicht-oeffentliche Assets in manual_review-Aggregat
        oder API-Lookups landen.
        """
        ignored = self._load_ignored_tickers()
        if not ignored or 'ticker' not in df.columns:
            return df
        mask = df['ticker'].astype(str).str.strip().isin(ignored)
        n_hit = int(mask.sum())
        if n_hit == 0:
            return df
        df.loc[mask, 'ticker_type'] = 'ignored'
        if 'api_strategy' in df.columns:
            df.loc[mask, 'api_strategy'] = 'skip'
        if 'manual_review_flag' in df.columns:
            df.loc[mask, 'manual_review_flag'] = False
        if 'classification_reason' in df.columns:
            df.loc[mask, 'classification_reason'] = 'IGNORED_LIST'
        print(f"      {n_hit} Trades via ignored_tickers.csv ausgeschlossen")
        return df

    # ------------------------------------------------------------------
    # Kumulative Problem-Outputs (Delta-Modus): Merge + Re-Validate
    # ------------------------------------------------------------------
    def _merge_cumulative_csv(
        self,
        target_path: str,
        new_df: pd.DataFrame,
        key_columns: list,
        revalidate_fn,
        sum_columns=None,
    ) -> None:
        """Merged neue Eintraege in bestehende Live-Datei und re-validiert.

        Verhalten:
        - Liest target_path (falls vorhanden) als old_df.
        - Kombiniert old_df + new_df via key_columns.
        - sum_columns werden bei Konflikten aufsummiert; andere Spalten:
          letzter Wert gewinnt.
        - revalidate_fn filtert Eintraege heraus, die nicht mehr aktuell sind.
        - Schreibt Ergebnis nach target_path. Leeres Ergebnis -> Datei loeschen.
        """
        if os.path.isfile(target_path):
            try:
                old_df = pd.read_csv(target_path, dtype=str).fillna('')
            except Exception as exc:
                logger.warning(
                    f"Kumulative Datei {target_path} konnte nicht gelesen werden: {exc}")
                old_df = pd.DataFrame()
        else:
            old_df = pd.DataFrame()

        if old_df.empty and (new_df is None or new_df.empty):
            if os.path.isfile(target_path):
                os.remove(target_path)
            return

        if new_df is None or new_df.empty:
            union_df = old_df.copy()
        elif old_df.empty:
            union_df = new_df.copy()
        else:
            union_df = pd.concat([old_df, new_df], ignore_index=True, sort=False)

        if sum_columns:
            for col in sum_columns:
                if col in union_df.columns:
                    union_df[col] = (pd.to_numeric(union_df[col], errors='coerce')
                                     .fillna(0).astype(int))
            agg = {c: 'sum' for c in sum_columns if c in union_df.columns}
            other_cols = [c for c in union_df.columns
                          if c not in key_columns and c not in (sum_columns or [])]
            for c in other_cols:
                agg[c] = 'last'
            union_df = union_df.groupby(key_columns, as_index=False).agg(agg)
        else:
            union_df = union_df.drop_duplicates(subset=key_columns, keep='last')

        final_df = revalidate_fn(union_df)

        if final_df is None or final_df.empty:
            if os.path.isfile(target_path):
                os.remove(target_path)
            return
        final_df.to_csv(target_path, index=False, encoding='utf-8')

    def _revalidate_unmatched_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """Drop Eintraege, fuer die NameMatcher.match jetzt einen Match liefert."""
        if df is None or df.empty:
            return df
        self._init_matcher()
        keep_idx = []
        for idx, row in df.iterrows():
            result = self.matcher.match(
                row.get('trade_name', ''),
                row.get('chamber', ''),
            )
            if not result.get('matched', False):
                keep_idx.append(idx)
        if not keep_idx:
            return df.iloc[0:0]
        return df.loc[keep_idx].reset_index(drop=True)

    def _revalidate_unmatched_preferred(self, df: pd.DataFrame) -> pd.DataFrame:
        """Drop preferred_ticker, die jetzt in Vorzugsaktien_mapping.csv oder
        ignored_tickers.csv stehen.

        Liest das Vorzugsaktien-Mapping mit csv.DictReader (gleiche Logik wie
        utils/ticker_sector.py), um robust gegen Kommas in Firmennamen zu sein.
        """
        if df is None or df.empty:
            return df
        import csv as _csv
        preferred_path = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH,
                                      settings.PREFERRED_TICKER_MAPPING_FILE)
        mapped_set: set = set()
        if os.path.isfile(preferred_path):
            try:
                with open(preferred_path, 'r', encoding='utf-8-sig') as f:
                    reader = _csv.DictReader(f)
                    for row in reader:
                        pref = (row.get('vorzugsaktien_ticker') or '').strip()
                        if pref:
                            mapped_set.add(pref)
            except Exception as exc:
                logger.warning(
                    f"Vorzugsaktien-Mapping {preferred_path} konnte nicht gelesen werden: {exc}")
        ignored = self._load_ignored_tickers()
        excluded = mapped_set | ignored
        if not excluded or 'preferred_ticker' not in df.columns:
            return df
        mask = ~df['preferred_ticker'].astype(str).str.strip().isin(excluded)
        return df[mask].reset_index(drop=True)

    def _revalidate_delisted(self, df: pd.DataFrame) -> pd.DataFrame:
        """Drop Ticker, die jetzt im positiven ticker_industry_cache.json stehen.

        Kein API-Call - rein cache-basiert.
        """
        if df is None or df.empty:
            return df
        cache_path = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH,
                                  settings.TICKER_INDUSTRY_CACHE_FILE)
        if not os.path.isfile(cache_path):
            return df
        try:
            with open(cache_path, encoding='utf-8') as f:
                positive_cache = json.load(f)
        except Exception as exc:
            logger.warning(
                f"Positiv-Cache {cache_path} konnte nicht gelesen werden: {exc}")
            return df
        positive_tickers = set(positive_cache.keys()) if isinstance(positive_cache, dict) else set()
        if not positive_tickers or 'ticker' not in df.columns:
            return df
        mask = ~df['ticker'].astype(str).str.strip().isin(positive_tickers)
        return df[mask].reset_index(drop=True)

    def _promote_problem_outputs(self, delta: bool) -> None:
        """Promotet Stage-Dateien zu Live-Dateien fuer alle Problem-Outputs.

        delta=False (Full-Run): Overwrite Live mit Stage; falls Stage fehlt
          oder leer, wird Live geloescht (Auto-Purge).
        delta=True (Delta-Run): Merge alt + neu via Schluessel, Re-Validation,
          dann schreiben. Gefixte Eintraege verschwinden automatisch.

        Stage-Dateien werden in beiden Modi am Ende entfernt.
        """
        promotions = [
            # (stage_path, live_path, key_columns, revalidate_fn, sum_columns, label)
            (UNMATCHED_STAGE_PATH, UNMATCHED_PATH,
             ['trade_name', 'chamber'], self._revalidate_unmatched_names,
             ['count'], 'Unmatched-Names'),
            (DELISTED_STAGE_PATH, DELISTED_TRADES_PATH,
             ['ticker'], self._revalidate_delisted, None, 'Delisted'),
            (UNMATCHED_PREFERRED_STAGE_PATH, UNMATCHED_PREFERRED_PATH,
             ['preferred_ticker'], self._revalidate_unmatched_preferred,
             None, 'Unmatched-Preferred'),
        ]
        for stage_path, live_path, keys, revalidate, sum_cols, label in promotions:
            if delta:
                if os.path.isfile(stage_path):
                    try:
                        new_df = pd.read_csv(stage_path, dtype=str).fillna('')
                    except Exception as exc:
                        logger.warning(
                            f"Stage-Datei {stage_path} konnte nicht gelesen werden: {exc}")
                        new_df = pd.DataFrame()
                else:
                    new_df = pd.DataFrame()
                self._merge_cumulative_csv(
                    target_path=live_path,
                    new_df=new_df,
                    key_columns=keys,
                    revalidate_fn=revalidate,
                    sum_columns=sum_cols,
                )
                if os.path.isfile(live_path):
                    print(f"      {label}: {live_path} (kumulativ + re-validated)")
            else:
                # Full-Run: Stage -> Live overwrite. Stage fehlt/leer -> Live loeschen.
                stage_has_rows = False
                if os.path.isfile(stage_path):
                    try:
                        stage_df = pd.read_csv(stage_path, dtype=str)
                        stage_has_rows = not stage_df.empty
                    except Exception:
                        stage_has_rows = False
                if stage_has_rows:
                    shutil.copy2(stage_path, live_path)
                    print(f"      {label}: {live_path}")
                else:
                    if os.path.isfile(live_path):
                        os.remove(live_path)
                        print(f"      {label}: {live_path} entfernt (keine offenen Probleme)")
            if os.path.isfile(stage_path):
                os.remove(stage_path)

    def _build_manual_review_preview(self, delta_enriched: pd.DataFrame) -> pd.DataFrame:
        """Erzeugt Vorschau-DataFrame fuer manual_review_tickers.csv im Delta-Dry-Run.

        Kombiniert delta_enriched mit dem bestehenden Master (Lese-only).
        Der Aufrufer schreibt das Aggregat via _write_manual_review_subset.
        """
        if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            return delta_enriched
        try:
            master_df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        except Exception as exc:
            logger.warning(f"Master-Complete fuer Preview nicht lesbar: {exc}")
            return delta_enriched
        # Zusammenfuehren; Spalten harmonisieren ueber Union
        combined = pd.concat([master_df, delta_enriched], ignore_index=True, sort=False)
        # Bei doppeltem trade_id den neueren Eintrag (delta) bevorzugen
        if 'trade_id' in combined.columns:
            combined = combined.drop_duplicates(subset=['trade_id'], keep='last')
        return combined

    def _write_manual_review_subset(self, df: pd.DataFrame):
        """Aggregiert manual_review-Tickers pro raw_ticker und schreibt CSV.

        Bei jedem Lauf neu geschrieben (kein Append). Wenn keine Reviews
        anstehen, wird eine evtl. vorhandene Datei entfernt.
        Eintraege mit ticker_type='ignored' werden ausgeklammert.
        """
        if 'manual_review_flag' not in df.columns:
            return
        review_df = df[df['manual_review_flag'] == True].copy()
        if 'ticker_type' in review_df.columns:
            review_df = review_df[review_df['ticker_type'] != 'ignored']
        ignored = self._load_ignored_tickers()
        if ignored and 'ticker' in review_df.columns:
            review_df = review_df[~review_df['ticker'].astype(str).str.strip().isin(ignored)]
        if review_df.empty:
            if os.path.isfile(MANUAL_REVIEW_CSV_PATH):
                os.remove(MANUAL_REVIEW_CSV_PATH)
            return

        agg = review_df.groupby('ticker').agg(
            ticker_base=('ticker_base', 'first'),
            ticker_type=('ticker_type', 'first'),
            classification_reason=('classification_reason', 'first'),
            count_of_trades=('trade_id', 'count'),
            first_trade_date=('transaction_date', 'min'),
            last_trade_date=('transaction_date', 'max'),
            example_member=('name', 'first'),
        ).reset_index().rename(columns={'ticker': 'raw_ticker'})
        agg = agg.sort_values('count_of_trades', ascending=False)
        agg.to_csv(MANUAL_REVIEW_CSV_PATH, index=False, encoding='utf-8')
        print(f"      -> {MANUAL_REVIEW_CSV_PATH} ({len(agg)} Tickers)")

    def _export_curated_master(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extrahiert kuratierte Spalten aus complete-DataFrame.

        Spalten in CURATED_COALESCE: erstes nicht-leeres Element der Quellspalten-Liste gewinnt.
        adj_-Prefix-Regel (legacy): 'adj_X' liest Quellspalte 'X', schreibt als 'adj_X'.
        """
        cols = list(settings.CURATED_COLUMNS)
        coalesce = getattr(settings, 'CURATED_COALESCE', {})
        out_data = {}
        for col in cols:
            if col in coalesce:
                series = pd.Series([''] * len(df), index=df.index, dtype=object)
                for src in coalesce[col]:
                    if src in df.columns:
                        filled = df[src].fillna('').astype(str).str.strip()
                        series = series.where(series != '', filled)
                out_data[col] = series
            elif col.startswith('adj_'):
                src = col[len('adj_'):]
                out_data[col] = df[src] if src in df.columns else ''
            else:
                out_data[col] = df[col] if col in df.columns else ''
        return pd.DataFrame(out_data, index=df.index)

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
            defaults = {
                'Vote_Timing_Hit': False,
                'Vote_Timing_Hits': '[]',
                'Vote_Timing_Hit_Count': 0,
            }
            for col in VOTE_TIMING_COLUMNS:
                if col not in df.columns:
                    df[col] = defaults.get(col, '')
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
        """Berechnet Insider_Sectors (alle Committee-Sektoren), Relevant_Sectors
        (Intersection mit sector_yf/industry_yf) und Relevance_Score."""
        from filters.committee_filter import CommitteeFilter
        committee_filter = CommitteeFilter()

        df = df.copy()
        df['Insider_Sectors'] = ''
        df['Relevant_Sectors'] = ''
        df['Relevance_Score'] = 0

        for idx, row in df.iterrows():
            committees_full = str(row.get('Member_Committees_Full', '') or '')
            if not committees_full or committees_full == 'nan':
                continue
            chamber = str(row.get('Member_Chamber', '') or '')
            ticker = str(row.get('ticker', '') or '')
            ticker_sector = row.get('sector_yf', '') or ''
            ticker_industry = row.get('industry_yf', '') or ''
            try:
                relevance = committee_filter.calculate_relevance(
                    committees_string=committees_full,
                    chamber=chamber,
                    ticker=ticker,
                    ticker_sector=str(ticker_sector),
                    ticker_industry=str(ticker_industry),
                )
                df.at[idx, 'Insider_Sectors'] = json.dumps(relevance['insider_sectors'])
                df.at[idx, 'Relevant_Sectors'] = json.dumps(relevance['relevant_sectors'])
                df.at[idx, 'Relevance_Score'] = relevance['relevance_score']
            except Exception as e:
                logger.warning(f"Relevance-Berechnung fehlgeschlagen fuer {row.get('name', '')}: {e}")

        return df

    def _ensure_fresh_vote_data(self):
        """
        Pre-Stage Hook fuer Filter 4: Stoesst Delta-Update von bill_actions.json
        + bill_subjects.json an, wenn eine der Dateien aelter als
        settings.VOTE_DATA_TTL_HOURS ist. Schlaegt der Fetch fehl (kein API-Key,
        Netzwerk, sys.exit aus run_update), laeuft die Pipeline mit dem letzten
        bekannten Stand weiter (Warning).
        """
        actions_path = os.path.join(BASE_DIR, 'data/congress_api/bill_actions.json')
        subjects_path = os.path.join(BASE_DIR, 'data/congress_api/bill_subjects.json')
        ttl = timedelta(hours=settings.VOTE_DATA_TTL_HOURS)
        now = datetime.now()

        def _age(p):
            if not os.path.exists(p):
                return None
            return now - datetime.fromtimestamp(os.path.getmtime(p))

        age_actions = _age(actions_path)
        age_subjects = _age(subjects_path)

        if (age_actions is not None and age_subjects is not None
                and age_actions < ttl and age_subjects < ttl):
            print(f"      [Vote-Data] frisch (Alter: actions={age_actions}, "
                  f"subjects={age_subjects}) - skip Fetch")
            return

        # Lock-Datei pruefen: anderer Fetcher-Prozess (manueller --force / --update) laeuft
        lock_path = os.path.join(BASE_DIR, 'data/congress_api/.force_in_progress')
        if os.path.exists(lock_path):
            try:
                with open(lock_path, 'r', encoding='utf-8') as f:
                    lock_info = json.load(f)
                lock_msg = (f"PID {lock_info.get('pid', '?')}, gestartet "
                            f"{lock_info.get('started_at', '?')}, Modus "
                            f"{lock_info.get('mode', '?')}")
            except (OSError, json.JSONDecodeError):
                lock_msg = "Lock-Datei vorhanden, aber unleserlich"
            print(f"      [Vote-Data] WARN: anderer fetch_congress_data-Prozess laeuft "
                  f"({lock_msg}) - skip Auto-Refresh, nutze vorhandene JSON")
            return

        try:
            from scrapers.fetch_congress_data import CongressFetcher
            api_key = os.getenv('CONGRESS_API_KEY')
            if not api_key:
                print("      [Vote-Data] WARN: CONGRESS_API_KEY fehlt - skip Fetch "
                      "(nutze vorhandene JSON)")
                return

            print(f"      [Vote-Data] Delta-Update startet "
                  f"(TTL ueberschritten: actions={age_actions}, subjects={age_subjects})")
            fetcher = CongressFetcher(api_key=api_key)
            fetcher.run_update()
            print("      [Vote-Data] Delta-Update fertig")

        except SystemExit as e:
            print(f"      [Vote-Data] WARN: Fetch abgebrochen ({e}) - "
                  f"nutze vorhandene JSON")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"      [Vote-Data] WARN: Fetch fehlgeschlagen "
                  f"({type(e).__name__}: {e}) - nutze vorhandene JSON")

    def _apply_filter_logic(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wendet alle Filter an: Scoring, Filter 0 (Asset-Klasse), Filter 4 (Vote-Timing)."""
        # Pre-Hook: Vote-Daten frisch halten (Filter 4)
        self._ensure_fresh_vote_data()

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
        """Backup beider Master-Dateien (trades_master.csv und complete) vor destruktivem Schreiben."""
        os.makedirs(ARCHIVE_BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        for src, prefix in [
            (MASTER_CSV_PATH, 'trades_master'),
            (MASTER_COMPLETE_CSV_PATH, 'trades_master_complete'),
        ]:
            if os.path.isfile(src):
                backup_path = os.path.join(ARCHIVE_BACKUP_DIR, f'{prefix}_{ts}.csv')
                shutil.copy2(src, backup_path)
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
            # Single-Stage liest aus complete-Master (vollstaendige Spalten).
            # Curated-Master traegt nur eine Teilmenge und wuerde fehlende
            # Inputs liefern.
            if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
                raise FileNotFoundError(
                    f"trades_master_complete.csv nicht gefunden: {MASTER_COMPLETE_CSV_PATH}\n"
                    f"Bitte zuerst Migration ausfuehren: "
                    f"python scripts/migrate_add_trade_ids.py\n"
                    f"oder Full Rebuild: python scripts/import_trades.py")
            df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)

        for col in ['transaction_date', 'disclosure_date']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        return df

    def _write_stage_output(self, df: pd.DataFrame, stage: str, delta: bool, dry_run: bool):
        """Schreibt Output-Datei basierend auf Flag-Matrix (nicht fuer 'save').

        Single-Stage ohne dry_run/delta schreibt in den complete-Master,
        damit alle Spalten erhalten bleiben.
        """
        self._ensure_directories()
        if dry_run or delta:
            df.to_csv(STAGE_CSV_PATH, index=False, encoding='utf-8')
            print(f"      -> {STAGE_CSV_PATH} ({len(df):,} Trades)")
        else:
            self._backup_master()
            df.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
            print(f"      -> {MASTER_COMPLETE_CSV_PATH} ({len(df):,} Trades)")

    def _merge_into_master(self, stage_df: pd.DataFrame,
                           master_df: pd.DataFrame = None) -> pd.DataFrame:
        """Merged stage_df in trades_master_complete.csv.

        Duplikat-Behandlung gemaess settings.DEDUP_STRATEGY:
          - 'last_wins': bei gleicher trade_id nur die zuletzt erscheinende Zeile
          - 'keep_all':  alle Zeilen behalten (trade_id nicht mehr eindeutig)
        """
        if master_df is None:
            if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
                return stage_df.reset_index(drop=True)
            master_df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        merged = pd.concat([master_df, stage_df], ignore_index=True)
        if 'trade_id' in merged.columns and settings.DEDUP_STRATEGY == 'last_wins':
            merged = merged[~merged['trade_id'].duplicated(keep='last')]
        return merged.reset_index(drop=True)

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
        """Stage: CSV laden, Spalten umbenennen, Datum normalisieren, trade_id vergeben."""
        print(f"\n[Stage: rename] Lade und benenne Spalten um...")
        df = self.load_and_rename()
        df = self.normalize_dates(df)
        # trade_id stabil aus Trade-Inhalt (sha256-Hash) berechnen.
        df['trade_id'] = compute_trade_ids(df)
        print(f"      {len(df):,} Trades geladen, trade_ids vergeben")

        if delta:
            if not os.path.exists(MASTER_COMPLETE_CSV_PATH):
                raise FileNotFoundError(
                    f"Delta-Modus: {MASTER_COMPLETE_CSV_PATH} nicht gefunden.\n"
                    f"Bitte zuerst Migration: python scripts/migrate_add_trade_ids.py")
            master_df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
            new_df = detect_new_trades(df, master_df)
            self.stats['new_trades'] = len(new_df)
            self.stats['duplicates'] = len(df) - len(new_df)
            df = new_df
            print(f"      Delta: {self.stats['new_trades']:,} neue, "
                  f"{self.stats['duplicates']:,} bereits vorhanden")
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
        # Stage immer schreiben (auch leer) -> Promotion kann zwischen
        # "kein Run" und "leerer Run" unterscheiden (Auto-Purge bei Full-Run).
        unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')

    def _stage_tickerclassify(self, delta: bool = False, dry_run: bool = False):
        """Stage: Offline-Klassifikation via Regex-Entscheidungsbaum."""
        print(f"\n[Stage: tickerclassify] Offline-Klassifikation...")
        df = self._read_stage_input('tickerclassify', delta, dry_run)
        self._check_stage_inputs('tickerclassify', df)

        classifications = [
            classify(row.get('ticker', ''), row.get('asset_name', '') or '')
            for _, row in df.iterrows()
        ]
        df = df.copy()
        df['ticker_type']               = [c.ticker_type.value for c in classifications]
        df['ticker_base']               = [c.ticker_base for c in classifications]
        df['classification_reason']     = [c.classification_reason for c in classifications]
        df['classification_confidence'] = [c.confidence.value for c in classifications]
        df['api_strategy']              = [c.api_strategy.value for c in classifications]
        df['exch_code']                 = [c.exch_code for c in classifications]
        df['series_letter']             = [c.series_letter for c in classifications]
        df['status_suffix']             = [c.status_suffix for c in classifications]
        df['manual_review_flag']        = [c.manual_review for c in classifications]

        df = self._apply_ignore_list(df)
        n_review = int(df['manual_review_flag'].sum())
        print(f"      {len(df):,} klassifiziert, {n_review} fuer manual_review markiert")
        self._write_manual_review_subset(df)
        self._write_stage_output(df, 'tickerclassify', delta, dry_run)

    def _stage_tickeropenfigi(self, delta: bool = False, dry_run: bool = False):
        """Stage: OpenFIGI-Normalisierung (FIGI, ticker_figi, security_type_figi)."""
        print(f"\n[Stage: tickeropenfigi] OpenFIGI-Normalisierung...")
        df = self._read_stage_input('tickeropenfigi', delta, dry_run)
        self._check_stage_inputs('tickeropenfigi', df)

        cache_db = os.path.join(BASE_DIR, settings.DATA_CACHE_PATH,
                                settings.TICKER_TYPE_CACHE_DB)
        normalizer = TickerNormalizer(
            cache_db_path=cache_db,
            api_key=settings.OPENFIGI_API_KEY,
        )

        classifications = self._reconstruct_classifications(df)
        normalized = normalizer.normalize_batch(classifications)

        df = df.copy()
        df['figi']             = [n.figi for n in normalized]
        df['ticker_figi'] = [n.ticker_figi for n in normalized]
        df['name_figi']   = [n.name_figi for n in normalized]
        df['security_type_figi']    = [n.security_type_figi for n in normalized]
        df['market_sector_figi']    = [n.market_sector_figi for n in normalized]

        # Type-Korrektur (z.B. STOCK -> ETF) zurueckspielen
        n_corrected = 0
        indices = df.index.tolist()
        for pos, idx in enumerate(indices):
            n = normalized[pos]
            if n.classification.ticker_type.value != df.at[idx, 'ticker_type']:
                df.at[idx, 'ticker_type']           = n.classification.ticker_type.value
                df.at[idx, 'classification_reason'] = n.classification.classification_reason
                n_corrected += 1
        if n_corrected:
            print(f"      Type-Korrektur: {n_corrected} (z.B. STOCK -> ETF)")

        self._write_stage_output(df, 'tickeropenfigi', delta, dry_run)

    def _stage_tickeryfinance(self, delta: bool = False, dry_run: bool = False):
        """Stage: yfinance Sector/Industry-Anreicherung."""
        print(f"\n[Stage: tickeryfinance] yfinance Sector/Industry...")
        df = self._read_stage_input('tickeryfinance', delta, dry_run)
        self._check_stage_inputs('tickeryfinance', df)

        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)
        enricher = TickerSectorEnricher(
            cache_dir=mappings_dir,
            preferred_mapping_path=os.path.join(
                mappings_dir, settings.PREFERRED_TICKER_MAPPING_FILE),
            delisted_output_path=DELISTED_STAGE_PATH,
            unmatched_preferred_path=UNMATCHED_PREFERRED_STAGE_PATH,
        )

        normalized = self._reconstruct_normalized(df)
        transaction_dates = df['transaction_date'].tolist() if 'transaction_date' in df.columns else None
        sector_infos = enricher.enrich_batch(normalized, transaction_dates=transaction_dates)

        df = df.copy()
        df['sector_yf']   = [s.sector_yf for s in sector_infos]
        df['industry_yf'] = [s.industry_yf for s in sector_infos]
        df['quote_type_yf']      = [s.quote_type_yf for s in sector_infos]

        n_sector = int((df['sector_yf'].notna() & (df['sector_yf'] != '')).sum())
        print(f"      {n_sector:,}/{len(df):,} mit Sektor angereichert")
        self._write_stage_output(df, 'tickeryfinance', delta, dry_run)

    def _stage_filter(self, delta: bool = False, dry_run: bool = False):
        """Stage: Scoring + Filter 0 (Asset-Klasse) + Filter 4 (Vote-Timing)."""
        print(f"\n[Stage: filter] Filter + Scoring-Spalten berechnen...")
        df = self._read_stage_input('filter', delta, dry_run)
        self._check_stage_inputs('filter', df)
        df = self._apply_filter_logic(df)
        self._write_stage_output(df, 'filter', delta, dry_run)

    def _stage_save(self, delta: bool = False, dry_run: bool = False):
        """Stage: trades_stage.csv -> complete + curated Master (replace oder delta-merge)."""
        print(f"\n[Stage: save] Speichere Ergebnisse...")

        if not os.path.isfile(STAGE_CSV_PATH):
            raise FileNotFoundError(
                f"trades_stage.csv nicht gefunden: {STAGE_CSV_PATH}\n"
                f"Bitte zuerst Stages ausfuehren (z.B. --dry_run oder --delta --stage_*)")

        df = pd.read_csv(STAGE_CSV_PATH, low_memory=False)
        df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        self._backup_master()
        self._ensure_directories()

        if delta and os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            df = self._merge_into_master(df)
            print(f"      Delta-Merge: {len(df):,} Trades gesamt")

        # 1. Complete-Master (alle Spalten)
        df.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
        print(f"      -> {MASTER_COMPLETE_CSV_PATH} ({len(df):,} Trades)")

        # 2. Curated-Master
        curated = self._export_curated_master(df)
        curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        print(f"      -> {MASTER_CSV_PATH} ({len(curated):,} Trades, "
              f"{len(curated.columns)} Spalten)")

        # 3. Manual-Review (falls Tickers mit manual_review_flag)
        self._write_manual_review_subset(df)

        # Stage-Datei aufraeumen
        os.remove(STAGE_CSV_PATH)

        # Problem-Outputs promoten: Delta=akkumulieren+revalidieren, Full=overwrite
        self._promote_problem_outputs(delta=delta)

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

    def run_delta(self, dry_run: bool = False) -> dict:
        """
        Delta-Import: Nur neue Trades anreichern und an Master mergen.
        8-Stages-Pipeline (Rebuild Teil 3).

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Delta Import")
        print("=" * 60)

        # 1. Laden + Umbenennen + trade_id
        print("\n[1/8] Lade Quiver-Trades...")
        input_df = self.load_and_rename()
        print(f"      Gesamt: {len(input_df)} Trades in Quiver-Datei")

        # 2. Datum normalisieren
        print("\n[2/8] Normalisiere Datumsspalten...")
        input_df = self.normalize_dates(input_df)
        input_df['trade_id'] = compute_trade_ids(input_df)
        n_fallback = self.stats.get('date_fallback', 0)
        n_future = self.stats.get('date_future_fallback', 0)
        if n_fallback > 0:
            print(f"      Fallback: {n_fallback} fehlende transaction_dates gefuellt")
        if n_future > 0:
            print(f"      Fallback: {n_future} future-dates korrigiert")

        # 3. Master laden + Delta erkennen via trade_id
        print("\n[3/8] Lade Master + erkenne neue Trades...")
        if not os.path.exists(MASTER_COMPLETE_CSV_PATH):
            print(f"      [!] {MASTER_COMPLETE_CSV_PATH} nicht vorhanden.")
            print("          Bitte zuerst Migration: python scripts/migrate_add_trade_ids.py")
            print("          oder Full Rebuild: python scripts/import_trades.py")
            return self.stats
        master_df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        if 'transaction_date' in master_df.columns:
            master_df['transaction_date'] = pd.to_datetime(
                master_df['transaction_date'], errors='coerce')
        print(f"      {len(master_df):,} bestehende Trades")

        new_trades = detect_new_trades(input_df, master_df)
        self.stats['new_trades'] = len(new_trades)
        self.stats['duplicates'] = len(input_df) - len(new_trades)
        print(f"      {self.stats['new_trades']:,} neue Trades")
        print(f"      {self.stats['duplicates']:,} bereits vorhanden")

        if new_trades.empty:
            print("\n[OK] Keine neuen Trades zu verarbeiten.")
            return self.stats

        # 4. Committee-Member-Enrichment
        print(f"\n[4/8] Reichere {len(new_trades):,} neue Trades an...")
        enriched_df, unmatched_df = self.enrich_member_data(new_trades)
        print(f"      {self.stats['matched']} gematcht, {self.stats['unmatched']} unmatched")

        # 5. Ticker-Klassifikation (offline)
        print("\n[5/8] Ticker-Klassifikation (offline)...")
        classifications = [
            classify(row.get('ticker', ''), row.get('asset_name', '') or '')
            for _, row in enriched_df.iterrows()
        ]
        enriched_df = enriched_df.copy()
        enriched_df['ticker_type']               = [c.ticker_type.value for c in classifications]
        enriched_df['ticker_base']               = [c.ticker_base for c in classifications]
        enriched_df['classification_reason']     = [c.classification_reason for c in classifications]
        enriched_df['classification_confidence'] = [c.confidence.value for c in classifications]
        enriched_df['api_strategy']              = [c.api_strategy.value for c in classifications]
        enriched_df['exch_code']                 = [c.exch_code for c in classifications]
        enriched_df['series_letter']             = [c.series_letter for c in classifications]
        enriched_df['status_suffix']             = [c.status_suffix for c in classifications]
        enriched_df['manual_review_flag']        = [c.manual_review for c in classifications]
        enriched_df = self._apply_ignore_list(enriched_df)

        # 6. OpenFIGI-Normalisierung
        print("\n[6/8] OpenFIGI-Normalisierung...")
        cache_db = os.path.join(BASE_DIR, settings.DATA_CACHE_PATH,
                                settings.TICKER_TYPE_CACHE_DB)
        normalizer = TickerNormalizer(cache_db_path=cache_db,
                                       api_key=settings.OPENFIGI_API_KEY)
        normalized = normalizer.normalize_batch(classifications)
        enriched_df['figi']             = [n.figi for n in normalized]
        enriched_df['ticker_figi'] = [n.ticker_figi for n in normalized]
        enriched_df['name_figi']   = [n.name_figi for n in normalized]
        enriched_df['security_type_figi']    = [n.security_type_figi for n in normalized]
        enriched_df['market_sector_figi']    = [n.market_sector_figi for n in normalized]
        # Type-Korrektur in-place
        indices = enriched_df.index.tolist()
        for pos, idx in enumerate(indices):
            n = normalized[pos]
            if n.classification.ticker_type.value != enriched_df.at[idx, 'ticker_type']:
                enriched_df.at[idx, 'ticker_type']           = n.classification.ticker_type.value
                enriched_df.at[idx, 'classification_reason'] = n.classification.classification_reason

        # 7. yfinance Sector/Industry
        print("\n[7/8] yfinance Sector/Industry-Anreicherung...")
        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)
        enricher = TickerSectorEnricher(
            cache_dir=mappings_dir,
            preferred_mapping_path=os.path.join(
                mappings_dir, settings.PREFERRED_TICKER_MAPPING_FILE),
            delisted_output_path=DELISTED_STAGE_PATH,
            unmatched_preferred_path=UNMATCHED_PREFERRED_STAGE_PATH,
        )
        transaction_dates = enriched_df['transaction_date'].tolist() if 'transaction_date' in enriched_df.columns else None
        sector_infos = enricher.enrich_batch(normalized, transaction_dates=transaction_dates)
        enriched_df['sector_yf']   = [s.sector_yf for s in sector_infos]
        enriched_df['industry_yf'] = [s.industry_yf for s in sector_infos]
        enriched_df['quote_type_yf']      = [s.quote_type_yf for s in sector_infos]

        # 8. Filter-Logik (Scoring + Filter 0 + Filter 4)
        print("\n[8/8] Filter-Logik (Scoring, Asset-Klasse, Vote-Timing)...")
        enriched_df = self._apply_filter_logic(enriched_df)

        # Speichern
        enriched_df = enriched_df.copy()
        enriched_df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._ensure_directories()

        # Stage immer schreiben (auch leer) -> Promotion entscheidet
        unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')

        if dry_run:
            enriched_df.to_csv(STAGE_CSV_PATH, index=False, encoding='utf-8')
            # Vorschau Endzustand: existing-master ∪ delta_enriched fuer manual_review
            preview_df = self._build_manual_review_preview(enriched_df)
            self._write_manual_review_subset(preview_df)
            print(f"\nDry-Run: {len(enriched_df):,} neue Trades -> {STAGE_CSV_PATH}")
            print("(Zum Committen: python scripts/import_trades.py --delta --stage_save)")
        else:
            self._backup_master()
            updated_master = self._merge_into_master(enriched_df, master_df)
            updated_master.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
            curated = self._export_curated_master(updated_master)
            curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
            self._write_manual_review_subset(updated_master)
            logger.info(f"Master aktualisiert: {len(updated_master):,} Trades "
                        f"({len(enriched_df):,} neu hinzugefuegt)")
            self._promote_problem_outputs(delta=True)

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
        print(f"  - {STAGE_CSV_PATH if dry_run else MASTER_COMPLETE_CSV_PATH}")
        if not dry_run:
            print(f"  - {MASTER_CSV_PATH} (kuratiert)")
        if not unmatched_df.empty:
            print(f"  - {UNMATCHED_STAGE_PATH if dry_run else UNMATCHED_PATH}")
        if os.path.isfile(DELISTED_STAGE_PATH):
            print(f"  - {DELISTED_STAGE_PATH if dry_run else DELISTED_TRADES_PATH}")
        if os.path.isfile(UNMATCHED_PREFERRED_PATH):
            print(f"  - {UNMATCHED_PREFERRED_PATH}")
        if os.path.isfile(MANUAL_REVIEW_CSV_PATH):
            print(f"  - {MANUAL_REVIEW_CSV_PATH}")

        return self.stats

    def save_results(
        self,
        enriched_df: pd.DataFrame,
        unmatched_df: pd.DataFrame
    ):
        """
        Speichert alle Ergebnisse als complete + curated Master plus
        manual_review_tickers.csv. Beide Master-Dateien werden komplett
        neu geschrieben (kein Append).
        """
        self._ensure_directories()

        # Import-Datum hinzufuegen
        enriched_df = enriched_df.copy()
        enriched_df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 1. Complete-Master (alle Spalten)
        enriched_df.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
        logger.info(f"Complete-Master: {MASTER_COMPLETE_CSV_PATH} ({len(enriched_df)} Trades)")

        # 2. Curated-Master (Spalten gemaess settings.CURATED_COLUMNS)
        curated = self._export_curated_master(enriched_df)
        curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        logger.info(f"Curated-Master: {MASTER_CSV_PATH} "
                    f"({len(curated)} Trades, {len(curated.columns)} Spalten)")

        # 3. Manual-Review (falls Tickers mit manual_review_flag)
        self._write_manual_review_subset(enriched_df)

        # 4. Stage immer schreiben (auch leer) -> Promotion entscheidet
        unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')

        # 5. Problem-Outputs promoten: Full-Run = Overwrite (Auto-Purge)
        self._promote_problem_outputs(delta=False)

    def run(self, dry_run: bool = False) -> dict:
        """
        Fuehrt den kompletten Import durch (alle 8 Stages im Speicher).

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Import Pipeline (8 Stages)")
        print("=" * 60)

        # 1. Laden + Umbenennen + trade_id
        print("\n[1/8] Lade Quiver-Trades...")
        df = self.load_and_rename()
        print(f"      Gesamt: {len(df)} Trades geladen")

        # 2. Datum normalisieren + trade_id
        print("\n[2/8] Normalisiere Datumsspalten + trade_id...")
        df = self.normalize_dates(df)
        df['trade_id'] = compute_trade_ids(df)
        n_fallback = self.stats.get('date_fallback', 0)
        n_future = self.stats.get('date_future_fallback', 0)
        if n_fallback > 0:
            print(f"      Fallback: {n_fallback} fehlende transaction_dates gefuellt")
        if n_future > 0:
            print(f"      Fallback: {n_future} future-dates korrigiert")
        print(f"      {len(df):,} trade_ids vergeben")

        # 3. Committee-Member-Enrichment
        print("\n[3/8] Reichere mit Committee-Member-Daten an...")
        df, unmatched_df = self.enrich_member_data(df)
        print(f"      {self.stats['matched']} erfolgreich gematcht")
        print(f"      {self.stats['unmatched']} nicht gefunden")

        # 4. Ticker-Klassifikation (offline)
        print("\n[4/8] Ticker-Klassifikation (offline Regex)...")
        classifications = [
            classify(row.get('ticker', ''), row.get('asset_name', '') or '')
            for _, row in df.iterrows()
        ]
        df = df.copy()
        df['ticker_type']               = [c.ticker_type.value for c in classifications]
        df['ticker_base']               = [c.ticker_base for c in classifications]
        df['classification_reason']     = [c.classification_reason for c in classifications]
        df['classification_confidence'] = [c.confidence.value for c in classifications]
        df['api_strategy']              = [c.api_strategy.value for c in classifications]
        df['exch_code']                 = [c.exch_code for c in classifications]
        df['series_letter']             = [c.series_letter for c in classifications]
        df['status_suffix']             = [c.status_suffix for c in classifications]
        df['manual_review_flag']        = [c.manual_review for c in classifications]
        df = self._apply_ignore_list(df)
        n_review = int(df['manual_review_flag'].sum())
        print(f"      {len(df):,} klassifiziert, {n_review} fuer manual_review markiert")

        # 5. OpenFIGI-Normalisierung
        print("\n[5/8] OpenFIGI-Normalisierung...")
        cache_db = os.path.join(BASE_DIR, settings.DATA_CACHE_PATH,
                                settings.TICKER_TYPE_CACHE_DB)
        normalizer = TickerNormalizer(
            cache_db_path=cache_db,
            api_key=settings.OPENFIGI_API_KEY,
        )
        normalized = normalizer.normalize_batch(classifications)
        df['figi']             = [n.figi for n in normalized]
        df['ticker_figi'] = [n.ticker_figi for n in normalized]
        df['name_figi']   = [n.name_figi for n in normalized]
        df['security_type_figi']    = [n.security_type_figi for n in normalized]
        df['market_sector_figi']    = [n.market_sector_figi for n in normalized]
        # Type-Korrektur (z.B. STOCK -> ETF)
        n_corrected = 0
        indices = df.index.tolist()
        for pos, idx in enumerate(indices):
            n = normalized[pos]
            if n.classification.ticker_type.value != df.at[idx, 'ticker_type']:
                df.at[idx, 'ticker_type']           = n.classification.ticker_type.value
                df.at[idx, 'classification_reason'] = n.classification.classification_reason
                n_corrected += 1
        if n_corrected:
            print(f"      Type-Korrektur: {n_corrected} (z.B. STOCK -> ETF)")

        # 6. yfinance Sector/Industry-Anreicherung
        print("\n[6/8] yfinance Sector/Industry-Anreicherung...")
        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)
        enricher = TickerSectorEnricher(
            cache_dir=mappings_dir,
            preferred_mapping_path=os.path.join(
                mappings_dir, settings.PREFERRED_TICKER_MAPPING_FILE),
            delisted_output_path=DELISTED_STAGE_PATH,
            unmatched_preferred_path=UNMATCHED_PREFERRED_STAGE_PATH,
        )
        transaction_dates = df['transaction_date'].tolist() if 'transaction_date' in df.columns else None
        sector_infos = enricher.enrich_batch(normalized, transaction_dates=transaction_dates)
        df['sector_yf']   = [s.sector_yf for s in sector_infos]
        df['industry_yf'] = [s.industry_yf for s in sector_infos]
        df['quote_type_yf']      = [s.quote_type_yf for s in sector_infos]

        # 7. Filter-Logik (Scoring + Filter 0 + Filter 4)
        print("\n[7/8] Filter-Logik (Scoring, Asset-Klasse, Vote-Timing)...")
        df = self._apply_filter_logic(df)

        # 8. Speichern
        print("\n[8/8] Speichere Ergebnisse...")
        if dry_run:
            self._ensure_directories()
            df_out = df.copy()
            df_out['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            df_out.to_csv(STAGE_CSV_PATH, index=False, encoding='utf-8')
            if not unmatched_df.empty:
                unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')
            self._write_manual_review_subset(df_out)
            print(f"\nDry-Run: {len(df_out):,} Trades -> {STAGE_CSV_PATH}")
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
        if dry_run:
            print(f"  - {STAGE_CSV_PATH}")
        else:
            print(f"  - {MASTER_COMPLETE_CSV_PATH}")
            print(f"  - {MASTER_CSV_PATH} (kuratiert)")
        if self.stats['unmatched'] > 0:
            print(f"  - {UNMATCHED_STAGE_PATH if dry_run else UNMATCHED_PATH}")
        if os.path.isfile(DELISTED_STAGE_PATH):
            print(f"  - {DELISTED_STAGE_PATH if dry_run else DELISTED_TRADES_PATH}")
        if os.path.isfile(UNMATCHED_PREFERRED_PATH):
            print(f"  - {UNMATCHED_PREFERRED_PATH}")
        if os.path.isfile(MANUAL_REVIEW_CSV_PATH):
            print(f"  - {MANUAL_REVIEW_CSV_PATH}")

        return self.stats

    # =========================================================================
    # Bonus-Flag-Handler (Rebuild Teil 3)
    # =========================================================================

    def run_manual_review_only(self):
        """Regeneriert manual_review_tickers.csv aus complete-Master, ohne Master-Touch."""
        if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            raise FileNotFoundError(
                f"trades_master_complete.csv nicht vorhanden: {MASTER_COMPLETE_CSV_PATH}\n"
                f"Bitte zuerst Migration: python scripts/migrate_add_trade_ids.py")
        df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        self._write_manual_review_subset(df)
        print(f"\n[OK] Manual-Review aus {len(df):,} Trades regeneriert.")

    def run_regenerate_curated(self):
        """Regeneriert trades_master.csv (kuratiert) aus complete, ohne Datenverlust."""
        if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            raise FileNotFoundError(
                f"trades_master_complete.csv nicht vorhanden: {MASTER_COMPLETE_CSV_PATH}\n"
                f"Bitte zuerst Migration: python scripts/migrate_add_trade_ids.py")
        df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        self._backup_master()
        curated = self._export_curated_master(df)
        curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        print(f"\n[OK] Curated regeneriert: {MASTER_CSV_PATH} "
              f"({len(curated):,} Trades, {len(curated.columns)} Spalten)")


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
    parser.add_argument('--stage_tickerclassify', action='store_true',
                        help='Stage: Offline-Klassifikation via Regex-Entscheidungsbaum')
    parser.add_argument('--stage_tickeropenfigi', action='store_true',
                        help='Stage: OpenFIGI-Normalisierung (FIGI, ticker_figi)')
    parser.add_argument('--stage_tickeryfinance', action='store_true',
                        help='Stage: yfinance Sector/Industry-Anreicherung')
    parser.add_argument('--stage_filter', action='store_true',
                        help='Stage: Scoring + Filter 0 (Asset-Klasse) + Filter 4 (Vote-Timing)')
    parser.add_argument('--stage_save', action='store_true',
                        help='Stage: trades_stage.csv -> complete + curated Master')

    # Bonus-Flags (Rebuild Teil 3): Regenerieren ohne Master-Touch
    parser.add_argument('--manual_review_only', action='store_true',
                        help='Regeneriert manual_review_tickers.csv aus complete-Master')
    parser.add_argument('--regenerate_curated', action='store_true',
                        help='Regeneriert trades_master.csv (kuratiert) aus complete')
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
        'tickerclassify': args.stage_tickerclassify,
        'tickeropenfigi': args.stage_tickeropenfigi,
        'tickeryfinance': args.stage_tickeryfinance,
        'filter': args.stage_filter,
        'save': args.stage_save,
    }
    active_stages = [name for name, flag in stage_flags.items() if flag]

    importer = TradeImporter()

    try:
        # Bonus-Flag-Dispatch: nicht kombinierbar mit anderen Flags
        if args.manual_review_only:
            if active_stages or args.delta or args.dry_run:
                print("[X] --manual_review_only nicht kombinierbar mit "
                      "--stage_*, --delta oder --dry_run")
                sys.exit(1)
            importer.run_manual_review_only()
            return

        if args.regenerate_curated:
            if active_stages or args.delta or args.dry_run:
                print("[X] --regenerate_curated nicht kombinierbar mit "
                      "--stage_*, --delta oder --dry_run")
                sys.exit(1)
            importer.run_regenerate_curated()
            return

        if len(active_stages) == 1:
            importer.run_stage(active_stages[0], delta=args.delta, dry_run=args.dry_run)
        elif len(active_stages) > 1:
            print("[X] Nur ein --stage_* Flag gleichzeitig erlaubt")
            sys.exit(1)
        else:
            # Kein Stage-Flag -> kompletter Lauf
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
