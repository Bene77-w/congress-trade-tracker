"""
Congress Trade Tracker - Trade Import Pipeline
Importiert Trades, reichert mit Committee-Daten, Ticker-Klassifikation,
OpenFIGI/yfinance-Sektor und Filter-Logik an.

Modi:
  python scripts/import_trades.py            # Vollauf: Master + suspicious_*.csv
  python scripts/import_trades.py --dry_run  # Vollauf -> trades_stage.csv + Event-Stage-Dateien
  python scripts/import_trades.py --save     # uebernimmt den Dry-Run: Stage -> Master + suspicious_*.csv

Bonus-Flags (kein Master-Touch, exklusiv):
  --manual_review_only    # Regeneriert manual_review_tickers.csv aus complete
  --regenerate_curated    # Regeneriert trades_master.csv aus complete
"""

import argparse
import json
import os
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
from utils.reference_data import ReferenceData, STATUS_MATCHED, STATUS_NO_MEMBERSHIP
from utils.trade_id import compute_trade_ids
from utils.amount_parser import midpoint_usd, max_usd
from scoring.asset_volume import compute_asset_tradingvolume
from scoring.trade_score import compute_trade_scores
from scoring.post_trade_returns import (
    compute_post_trade_returns,
    compute_paper_returns,
)
from scoring.abnormal_returns import compute_abnormal_returns
from utils.ticker_classifier import (
    classify,
    Confidence,
    TickerClassification,
)
from utils.ticker_normalizer import TickerNormalizer
from utils.ticker_sector import TickerSectorEnricher
from utils.issuer_group_mapper import IssuerGroupMapper
from tags.vote_timing_tag import VoteTimingTag, VOTE_TIMING_COLUMNS
from gates.asset_class_gate import AssetClassGate, ASSET_CLASS_COLUMNS
from signals.committee_signal import CommitteeSignal
from signals.committee_full_signal import CommitteeFullSignal
from signals.committee_ex_officio_signal import CommitteeExOfficioSignal
from signals.floor_action_signal import FloorActionSignal
from signals.extreme_report_signal import ExtremeReportSignal
from signals.trade_volume_signal import TradeVolumeSignal
from signals.contrarian_signal import ContrarianSignal
from signals.accumulative_signal import AccumulativeSignal
from signals.swarm_buy_signal import SwarmBuySignal
from signals.swarm_sell_signal import SwarmSellSignal
from signals.swarm_conflict_signal import SwarmConflictSignal
from signals.swarm_paradox_signal import SwarmParadoxSignal

logger = logging.getLogger(__name__)

# Pfade aus Config (bei Neuwahlen in settings.py anpassen)
PROCESSED_DIR = os.path.join(BASE_DIR, settings.DATA_PROCESSED_PATH)
RAW_DIR = os.path.join(BASE_DIR, settings.DATA_RAW_PATH)
# Input/Output Dateien (aus Config)
QUIVER_TRADES_PATH = os.path.join(RAW_DIR, settings.QUIVER_TRADES_FILE)
MASTER_CSV_PATH = os.path.join(PROCESSED_DIR, settings.TRADES_MASTER_CSV)                    # kuratierte Excel-Sicht
MASTER_COMPLETE_CSV_PATH = os.path.join(PROCESSED_DIR, settings.TRADES_MASTER_COMPLETE_CSV)  # vollstaendig
MANUAL_REVIEW_CSV_PATH = os.path.join(PROCESSED_DIR, settings.MANUAL_REVIEW_CSV)
UNMATCHED_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names.csv')
DELISTED_TRADES_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades.csv')
IGNORED_TICKERS_PATH = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH,
                                    settings.IGNORED_TICKERS_FILE)

def _sort_master_by_date(df: pd.DataFrame) -> pd.DataFrame:
    """Sortiert den Master stabil absteigend nach transaction_date.

    Innerhalb desselben transaction_date bleibt die Eingabe-Reihenfolge
    erhalten (Quell-Reihenfolge als Tiebreak — die Quiver-Quelle liefert
    Trades bereits absteigend nach Datum). Zeilen ohne parsebares
    transaction_date wandern ans Ende. Mutiert df nicht und laesst die
    transaction_date-Spalte unveraendert (Sortierung ueber eine temporaere
    Hilfsspalte, damit die CSV-Serialisierung des Datums nicht kippt).

    Zentralisiert die Master-Sortierung: jedes Schreiben von
    trades_master_complete.csv laeuft hierdurch (save_results).
    """
    if 'transaction_date' not in df.columns or df.empty:
        return df
    td = pd.to_datetime(df['transaction_date'], errors='coerce')
    order = (
        pd.DataFrame({'_td': td.to_numpy(), '_pos': range(len(df))})
        .sort_values(['_td', '_pos'], ascending=[False, True],
                     na_position='last')
        .index.to_numpy()
    )
    return df.iloc[order]


# Stage-Pfade: Ergebnis von --dry_run, uebernommen von --save
STAGE_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_stage.csv')
UNMATCHED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names_stage.csv')
DELISTED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades_stage.csv')
BRANCH1_EVENTS_STAGE_PATH = os.path.join(PROCESSED_DIR, 'branch1_events_stage.csv')
SEQUENCE_EVENTS_STAGE_PATH = os.path.join(PROCESSED_DIR, 'sequence_events_stage.csv')
SWARM_EVENTS_STAGE_PATH = os.path.join(PROCESSED_DIR, 'swarm_events_stage.csv')


def _event_stages() -> tuple:
    """Pfade der drei Event-Stage-Dateien eines Laufs (zur Laufzeit gelesen)."""
    return (BRANCH1_EVENTS_STAGE_PATH, SEQUENCE_EVENTS_STAGE_PATH,
            SWARM_EVENTS_STAGE_PATH)


def _write_csv_atomic(df: pd.DataFrame, path: str, append: bool = False) -> None:
    """Schreibt df ueber eine temporaere Datei im selben Verzeichnis und
    ersetzt path erst danach (os.replace): ein Abbruch laesst path unveraendert.

    append=True haengt an den bisherigen Inhalt von path an (Header nur, wenn
    path noch fehlt).
    """
    temp_path = path + '.tmp'
    appending = append and os.path.isfile(path)
    try:
        if appending:
            shutil.copyfile(path, temp_path)
        df.to_csv(temp_path, mode='a' if appending else 'w',
                  header=not appending, index=False, encoding='utf-8')
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


SUSPICIOUS_EVENTS_PATH  = os.path.join(PROCESSED_DIR, settings.SUSPICIOUS_EVENTS_FILE)
SUSPICIOUS_ASSETS_PATH  = os.path.join(PROCESSED_DIR, settings.SUSPICIOUS_ASSETS_FILE)
SUSPICIOUS_MEMBERS_PATH = os.path.join(PROCESSED_DIR, settings.SUSPICIOUS_MEMBERS_FILE)

# Spalten die der Branch-1-Signal-Aggregator pro Trade in den Stage-CSV schreibt
BRANCH1_AGGREGATE_COLUMNS = ['signal_count', 'signals_triggered']

# Spalten-Schema fuer Signal-Event-Snapshots (branch1 + sequence + swarm)
# P6: +issuer_group_key (Issuer-Level, CIK-basiert) direkt nach issuer_ticker
SIGNAL_EVENT_COLUMNS = [
    'signal_event_id', 'trade_id', 'signal_type', 'triggering_date',
    'scope_key', 'member_id', 'member_name', 'chamber',
    'ticker', 'issuer_ticker', 'issuer_group_key', 'asset_name_figi',
    'transaction_date', 'disclosure_date',
    'transaction_amount', 'direction', 'is_ex_officio',
    'ex_officio_role_keys',
    'match_count', 'vote_timing_hit_count',
    'committee_subcommittee', 'relevant_sectors',
]

# View-Spalten fuer suspicious_assets.csv (P4 + Modul 8.4)
ASSETS_VIEW_COLUMNS = [
    'issuer_ticker', 'asset_name_figi', 'event_count', 'member_count',
    'signal_types', 'last_event_date', 'first_event_date',
    'directions', 'chambers', 'last_signal_type',
    'score_avg', 'score_max', 'score_total', 'total_volume_usd', 'warnings',
]

# View-Spalten fuer suspicious_members.csv (P4 + Modul 8.4)
MEMBERS_VIEW_COLUMNS = [
    'member_id', 'member_name', 'chamber', 'event_count', 'asset_count',
    'signal_types', 'last_event_date', 'first_event_date',
    'is_ex_officio_ever', 'event_trade_pairs',
    'score_avg', 'score_max', 'score_total',
]


def _counter_string(series: pd.Series) -> str:
    """Counter-String fuer Aggregat-Spalten in den View-CSVs.

    Format: 'committee:5,accumulative:2' — sortiert nach Count desc,
    Tie-Break nach Wert ascending.

    Args:
        series: pandas Series mit Strings (oder Werten, die zu Strings
            konvertierbar sind). NaN und leere Strings werden uebersprungen.

    Returns:
        Kommagetrennter Counter-String. Leerer String wenn nichts zu zaehlen.
    """
    if series is None or len(series) == 0:
        return ''
    cleaned = (
        series.dropna().astype(str).str.strip()
    )
    cleaned = cleaned[cleaned != '']
    if cleaned.empty:
        return ''
    counts = cleaned.value_counts()
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ','.join(f'{k}:{v}' for k, v in items)


def _join_distinct_warnings(series: pd.Series) -> str:
    """;-getrennte distinct-sortierte Warnings-Zusammenfuehrung (Modul 8.4).

    Akzeptiert Series mit Strings, die selbst ';'-getrennte Warnings enthalten
    koennen (z. B. zukuenftige Mehrfach-Warnings pro Trade). NaN und
    Leer-Tokens werden ausgefiltert.

    Args:
        series: pandas Series mit Warnings-Strings.

    Returns:
        ';'-getrennter String, sortiert, distinct, ohne Leerstrings.
    """
    if series is None or len(series) == 0:
        return ''
    tokens = {
        tok
        for w in series.dropna()
        for tok in str(w).split(';')
        if tok
    }
    return ';'.join(sorted(tokens))


ARCHIVE_BACKUP_DIR = os.path.join(BASE_DIR, settings.ARCHIVE_BACKUP_PATH)

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
        self.reference = None
        self.stats = {
            'total_input': 0,
            'matched': 0,
            'unmatched': 0
        }
        # Modul 8.4: Lazy-Cache fuer trade_id -> Score-Lookup
        self._trade_scores_cache = None
        # Modul 8.4: In-Flight-Scores des laufenden Laufs. _export_suspicious
        # setzt sie, damit _load_trade_scores nicht den alten bzw. noch nicht
        # geschriebenen trades_master_complete.csv liest.
        self._inflight_trade_scores = None

    def _ensure_directories(self):
        """Stellt sicher, dass alle Verzeichnisse existieren."""
        os.makedirs(PROCESSED_DIR, exist_ok=True)

    def _init_reference(self):
        """Laedt die Referenztabellen (data/reference/) einmal."""
        if self.reference is None:
            self.reference = ReferenceData()

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
            # Bewusste Entscheidung: In diesem Fall wird transaction_date durch
            # disclosure_date ersetzt. Typischer Ausloeser ist ein versehentlich als
            # Handelsdatum erfasstes Anleihe-Faelligkeitsdatum; das Meldedatum ist
            # hier die verlaesslichere Naeherung an das tatsaechliche Handelsdatum.
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

    def enrich_member_data(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Ordnet jedem Trade die Ausschuesse zu, die im Kongress des
        Transaktionsdatums galten - ueber bio_guide_id und data/reference/
        (utils/reference_data.py). Partei und Bundesstaat bleiben die der Quelle.
        Scoring-Spalten (Insider_Sectors, Relevant_Sectors etc.) setzt _apply_gates_and_tags.

        Returns:
            Tuple (angereicherte_df, unmatched_df). unmatched_df listet Personen,
            deren Trades in einem abgedeckten Kongress liegen, fuer die die
            Referenzdaten aber keine Mitgliedschaft kennen.
        """
        self._init_reference()
        df = self.reference.assign(df)

        status = df['Member_Committee_Status']
        self.stats['matched'] = int((status == STATUS_MATCHED).sum())
        self.stats['unmatched'] = len(df) - self.stats['matched']
        logger.info("Ausschuss-Zuordnung: %s", status.value_counts().to_dict())

        open_rows = df[status == STATUS_NO_MEMBERSHIP]
        unmatched_df = (
            open_rows.groupby(['bio_guide_id', 'Member_Congress'], as_index=False, dropna=False)
            .agg(trade_name=('name', 'first'), chamber=('chamber', 'first'),
                 count=('name', 'size'))
            .rename(columns={'Member_Congress': 'congress'})
            .sort_values('count', ascending=False))
        return df, unmatched_df

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

        Setzt ticker_type='ignored', manual_review_flag=False.
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
        if 'manual_review_flag' in df.columns:
            df.loc[mask, 'manual_review_flag'] = False
        if 'classification_reason' in df.columns:
            df.loc[mask, 'classification_reason'] = 'IGNORED_LIST'
        print(f"      {n_hit} Trades via ignored_tickers.csv ausgeschlossen")
        return df

    def _promote_problem_outputs(self) -> None:
        """Promotet Stage-Dateien zu Live-Dateien fuer alle Problem-Outputs.

        Overwrite Live mit Stage; falls Stage fehlt oder leer, wird Live
        geloescht (Auto-Purge). Die Stage-Dateien raeumt der Aufrufer nach dem
        ganzen Save auf (_clear_run_stages), damit ein Abbruch sie nicht
        einzeln verliert.
        """
        for stage_path, live_path, label in [
            (UNMATCHED_STAGE_PATH, UNMATCHED_PATH, 'Unmatched-Names'),
            (DELISTED_STAGE_PATH, DELISTED_TRADES_PATH, 'Delisted'),
        ]:
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
            elif os.path.isfile(live_path):
                os.remove(live_path)
                print(f"      {label}: {live_path} entfernt (keine offenen Probleme)")

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
        vote_tag = VoteTimingTag()
        votes_df = vote_tag.load_votes()

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

        result = vote_tag.apply(df, votes_df)

        hits = result['Vote_Timing_Hit'].sum()
        print(f"      {hits} Trades mit Vote-Timing-Hit")

        return result


    # =========================================================================
    # Filter-Logik: Scoring + Filter 0 + Filter 4
    # =========================================================================

    def _apply_committee_relevance(self, df: pd.DataFrame) -> pd.DataFrame:
        """Berechnet Insider_Sectors (alle Committee-Sektoren),
        Relevant_Sectors (Intersection mit sector_yf/industry_yf, jeder
        Eintrag mit match_type='full' oder 'sector') und
        ex_officio_role_keys (';'-getrennte Fuehrungsrollen-Keys der
        relevanten Treffer, prae-Dedup — Basis des Ex-Officio-Bonus v2)."""
        from tags.committee_tag import CommitteeTag
        # Ausschuss -> Industries je Kongress des Trades (committee_industries.csv)
        self._init_reference()
        tags: dict = {}

        df = df.copy()
        df['Insider_Sectors'] = ''
        df['Relevant_Sectors'] = ''
        df['ex_officio_role_keys'] = ''

        for idx, row in df.iterrows():
            committees_full = str(row.get('Member_Committees_Full', '') or '')
            if not committees_full or committees_full == 'nan':
                continue
            mapper = self.reference.mapper(row.get('Member_Congress'))
            if mapper.congress not in tags:
                tags[mapper.congress] = CommitteeTag(mapper=mapper)
            committee_tag = tags[mapper.congress]
            chamber = str(row.get('Member_Chamber', '') or '')
            ticker = str(row.get('ticker', '') or '')
            ticker_sector = row.get('sector_yf', '') or ''
            ticker_industry = row.get('industry_yf', '') or ''
            try:
                relevance = committee_tag.calculate_relevance(
                    committees_string=committees_full,
                    chamber=chamber,
                    ticker=ticker,
                    ticker_sector=str(ticker_sector),
                    ticker_industry=str(ticker_industry),
                )
                df.at[idx, 'Insider_Sectors'] = json.dumps(relevance['insider_sectors'])
                df.at[idx, 'Relevant_Sectors'] = json.dumps(relevance['relevant_sectors'])
                df.at[idx, 'ex_officio_role_keys'] = \
                    relevance.get('ex_officio_role_keys', '')
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

    def _apply_gates_and_tags(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wendet alle Pipeline-internen Gates und Tags an.

        Reihenfolge:
          1. Match-Scoring (interner Diagnose-Score)
          2. Committee-Tag    (Filter 3 in der User-Sprache)
          3. Asset-Class-Gate (Filter 0 in der User-Sprache)
          4. Vote-Timing-Tag  (Filter 4 in der User-Sprache)

        Signals (User-Filter 1/2/6/7: Volumen, Schwarm, Long/Short, Position+)
        werden hier spaeter angedockt, sobald implementiert.
        """
        # Pre-Hook: Vote-Daten frisch halten (fuer Vote-Timing-Tag)
        self._ensure_fresh_vote_data()

        # 2. Committee-Tag
        df = self._apply_committee_relevance(df)

        # 3. Asset-Class-Gate
        df = AssetClassGate().apply(df)

        # 4. Vote-Timing-Tag
        df = self.enrich_with_vote_timing(df)

        # TODO: Signals (Volumen, Schwarm, Long/Short, Position+) anbinden, sobald implementiert.

        return df

    def _apply_branch1_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wendet die vier Branch-1 Per-Trade-Signale an.

        Signale: committee, committee_ex_officio, floor_action, extreme_report.

        Schreibt pro Trade zwei aggregierte Spalten in den DataFrame:
          - signal_count        : Anzahl ausgeloester Signale (0-4)
          - signals_triggered   : Semikolon-getrennte Liste der Signal-Typen

        Die einzelnen Auslöse-Events werden in eine CSV-Datei geschrieben
        (BRANCH1_EVENTS_STAGE_PATH), damit die spaetere Suspicious-Export-Stage
        (P4) sie einsammeln und in suspicious_events.csv promoten kann.
        """
        signals = [
            CommitteeSignal(),
            CommitteeFullSignal(),
            CommitteeExOfficioSignal(),
            FloorActionSignal(),
            ExtremeReportSignal(),
            TradeVolumeSignal(),
        ]

        df = df.copy()
        signal_count = []
        signals_triggered = []
        events: list = []

        for _, row in df.iterrows():
            row_events = [s.evaluate_row(row) for s in signals]
            row_events = [e for e in row_events if e is not None]
            signal_count.append(len(row_events))
            signals_triggered.append(';'.join(e['signal_type'] for e in row_events))
            events.extend(row_events)

        df['signal_count'] = signal_count
        df['signals_triggered'] = signals_triggered

        self._write_branch1_events(events)

        n_triggered = sum(1 for c in signal_count if c > 0)
        print(f"      {n_triggered:,} / {len(df):,} Trades mit mindestens einem Signal")
        print(f"      {len(events):,} Branch-1-Events insgesamt -> {BRANCH1_EVENTS_STAGE_PATH}")
        return df

    def _write_branch1_events(self, events: list):
        """Schreibt die gesammelten Branch-1-Events in eine CSV-Stage-Datei.

        Wird von `_export_suspicious` wieder eingelesen und in
        suspicious_events.csv promoted. Bei leerer Liste wird eine
        Header-only Datei geschrieben, damit Folge-Stages die Datei
        konsistent lesen koennen.
        """
        self._ensure_directories()
        df_events = pd.DataFrame(events, columns=SIGNAL_EVENT_COLUMNS)
        df_events.to_csv(BRANCH1_EVENTS_STAGE_PATH, index=False, encoding='utf-8')

    def _apply_sequence_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wendet die Sequenz-Signale (contrarian, accumulative) an.

        Gruppiert Trades nach (bio_guide_id, issuer_ticker) und evaluiert
        jede Gruppe mit beiden Signal-Klassen. Die resultierenden Events
        werden in sequence_events_stage.csv geschrieben und ihre
        Signal-Typen in signal_count + signals_triggered pro Trade
        akkumuliert (additiv zu den Branch-1-Werten aus der vorhergehenden
        Stage).
        """
        from signals.committee_signal import _direction_from_transaction_type

        signals = [ContrarianSignal(), AccumulativeSignal()]

        df = df.copy()
        # Pre-compute _td (Timestamp) und _dir (buy/sell/other) einmalig
        # global auf dem Gruppierungs-Frame — pro-Gruppen-Conversion in
        # evaluate_group ist sonst der dominante Kostenpunkt (50k+ Gruppen).
        df['_td'] = pd.to_datetime(df['transaction_date'], errors='coerce')
        df['_dir'] = df['transaction_type'].map(_direction_from_transaction_type)
        # Gruppierungs-Key = issuer_ticker (share_class_figi -> ticker_base ->
        # ticker), in run() nach der OpenFIGI-Normalisierung vektorisiert
        # befuellt. Fehlt die Spalte, wird die Kette inline rekonstruiert.
        if 'issuer_ticker' in df.columns:
            df['_grp_ticker'] = df['issuer_ticker'].fillna('').astype(str).str.strip()
        else:
            t_str = df['ticker'].fillna('').astype(str).str.strip()
            if 'ticker_base' in df.columns:
                tb_str = df['ticker_base'].fillna('').astype(str).str.strip()
                df['_grp_ticker'] = tb_str.where(tb_str != '', t_str)
            else:
                df['_grp_ticker'] = t_str
        df['_grp_member'] = df['bio_guide_id'].fillna('').astype(str)

        # signal_count / signals_triggered sicher initialisieren.
        if 'signal_count' not in df.columns:
            df['signal_count'] = 0
        else:
            df['signal_count'] = pd.to_numeric(
                df['signal_count'], errors='coerce'
            ).fillna(0).astype(int)
        if 'signals_triggered' not in df.columns:
            df['signals_triggered'] = ''
        else:
            df['signals_triggered'] = df['signals_triggered'].fillna('').astype(str)

        events: list = []
        # Mapping trade_id -> Liste neuer Signal-Typen
        per_trade_new: dict = {}
        # Nur Gruppen mit nicht-leerer member_id UND ticker — Trades ohne
        # diese Felder koennen keinen Sequenz-Bezug haben.
        mask = (df['_grp_member'] != '') & (df['_grp_ticker'] != '')
        candidate_df = df[mask]
        groups = candidate_df.groupby(['_grp_member', '_grp_ticker'],
                                       sort=False, dropna=False)
        n_groups = groups.ngroups
        n_qualifying = 0
        for (mid, tkr), group_df in groups:
            if len(group_df) < 2:
                continue
            n_qualifying += 1
            for sig in signals:
                sig_events = sig.evaluate_group(group_df)
                for ev in sig_events:
                    tid = ev.get('trade_id', '')
                    events.append(ev)
                    if tid == '' or tid is None:
                        continue
                    per_trade_new.setdefault(tid, []).append(ev['signal_type'])
        print(f"      {n_groups:,} Member×Asset-Gruppen, {n_qualifying:,} mit >=2 Trades evaluiert")

        # Aggregat-Spalten pro Trade aktualisieren — dedupliziert pro Trade,
        # damit zwei Sequenz-Auslösungen desselben Typs nur einmal gezaehlt
        # werden. Direktes Index-Update statt df.apply (~100x schneller fuer
        # ~100k-Zeilen-Master).
        if per_trade_new:
            tid_to_unique = {tid: sorted(set(types))
                             for tid, types in per_trade_new.items()}
            # Index-Lookup: trade_id -> list of DataFrame-Indices
            tid_to_indices: dict = {}
            for idx, tid in df['trade_id'].items():
                if tid in tid_to_unique:
                    tid_to_indices.setdefault(tid, []).append(idx)

            for tid, new_types in tid_to_unique.items():
                indices = tid_to_indices.get(tid, [])
                for idx in indices:
                    existing = [t for t in
                                str(df.at[idx, 'signals_triggered']).split(';')
                                if t]
                    merged = existing + [t for t in new_types if t not in existing]
                    df.at[idx, 'signals_triggered'] = ';'.join(merged)
                    df.at[idx, 'signal_count'] = len(merged)

        # Cleanup interner Gruppierungs-/Hilfsspalten.
        df = df.drop(columns=['_grp_ticker', '_grp_member', '_td', '_dir'])

        self._write_sequence_events(events)

        n_events = len(events)
        n_trades_touched = len(per_trade_new)
        print(f"      {n_events:,} Sequenz-Events, "
              f"{n_trades_touched:,} Trades davon betroffen "
              f"-> {SEQUENCE_EVENTS_STAGE_PATH}")
        return df

    def _write_sequence_events(self, events: list):
        """Schreibt Sequenz-Signal-Events in eine CSV-Stage-Datei.

        Wird in P4 zusammen mit branch1_events_stage.csv in
        suspicious_events.csv promoted.
        """
        self._ensure_directories()
        df_events = pd.DataFrame(events, columns=SIGNAL_EVENT_COLUMNS)
        df_events.to_csv(SEQUENCE_EVENTS_STAGE_PATH, index=False, encoding='utf-8')

    # =========================================================================
    # P6 — Schwarm-Signale (Branch 2): asset-zentrierte Gruppierung nach
    # issuer_group_key (Issuer-Level, CIK-basiert, Cross-Class).
    # =========================================================================

    def _apply_swarm_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wendet die Schwarm-Signale an (swarm_buy/sell/conflict/paradox).

        Gruppiert Trades nach `settings.SWARM_GROUPING_KEY` (=issuer_group_key)
        und evaluiert jede Gruppe mit allen 4 Signal-Klassen. Conflict und
        Paradox sind mutually exclusive pro Schwarm-Auslösung (genau 1
        Gegen-Trade -> conflict; >= PARADOX_MIN_CONFLICTS -> paradox).

        Die signal_count + signals_triggered-Spalten werden additiv zu den
        Werten aus Branch-1 + Sequenz erweitert (pro Trade Dedup).
        SKIP-Buckets (issuer_group_key startet mit "SKIP:") werden im
        Signal-Code uebersprungen.
        """
        from signals.committee_signal import _direction_from_transaction_type

        signals = [
            SwarmBuySignal(), SwarmSellSignal(),
            SwarmConflictSignal(), SwarmParadoxSignal(),
        ]
        grouping_key = getattr(settings, 'SWARM_GROUPING_KEY', 'issuer_group_key')

        df = df.copy()
        df['_td'] = pd.to_datetime(df['transaction_date'], errors='coerce')
        df['_dir'] = df['transaction_type'].map(_direction_from_transaction_type)

        if grouping_key not in df.columns:
            # Defensive: ohne Issuer-Group-Key keine Schwarm-Auswertung.
            df = df.drop(columns=['_td', '_dir'])
            self._write_swarm_events([])
            print(f"      [SKIP] Spalte '{grouping_key}' fehlt -> 0 Schwarm-Events")
            return df

        df['_grp_swarm'] = df[grouping_key].fillna('').astype(str)

        # signal_count / signals_triggered defensiv initialisieren.
        if 'signal_count' not in df.columns:
            df['signal_count'] = 0
        else:
            df['signal_count'] = pd.to_numeric(
                df['signal_count'], errors='coerce'
            ).fillna(0).astype(int)
        if 'signals_triggered' not in df.columns:
            df['signals_triggered'] = ''
        else:
            df['signals_triggered'] = df['signals_triggered'].fillna('').astype(str)

        events: list = []
        per_trade_new: dict = {}

        # Nur Gruppen mit nicht-leerem grouping_key (SKIP-Buckets werden im
        # Signal-Code zusaetzlich aussortiert -- hier sparen wir uns das
        # Iterieren).
        mask = (df['_grp_swarm'] != '') & (~df['_grp_swarm'].str.startswith('SKIP:'))
        candidate_df = df[mask]
        groups = candidate_df.groupby('_grp_swarm', sort=False, dropna=False)
        n_groups = groups.ngroups
        n_qualifying = 0
        for grp_key, group_df in groups:
            if len(group_df) < 2:
                continue
            n_qualifying += 1
            for sig in signals:
                sig_events = sig.evaluate_group(group_df)
                for ev in sig_events:
                    tid = ev.get('trade_id', '')
                    events.append(ev)
                    if tid == '' or tid is None:
                        continue
                    per_trade_new.setdefault(tid, []).append(ev['signal_type'])
        print(f"      {n_groups:,} Issuer-Group-Buckets, "
              f"{n_qualifying:,} mit >=2 Trades evaluiert")

        # Aggregat-Spalten pro Trade aktualisieren (dedupliziert pro Trade)
        if per_trade_new:
            tid_to_unique = {tid: sorted(set(types))
                             for tid, types in per_trade_new.items()}
            tid_to_indices: dict = {}
            for idx, tid in df['trade_id'].items():
                if tid in tid_to_unique:
                    tid_to_indices.setdefault(tid, []).append(idx)

            for tid, new_types in tid_to_unique.items():
                indices = tid_to_indices.get(tid, [])
                for idx in indices:
                    existing = [t for t in
                                str(df.at[idx, 'signals_triggered']).split(';')
                                if t]
                    merged = existing + [t for t in new_types if t not in existing]
                    df.at[idx, 'signals_triggered'] = ';'.join(merged)
                    df.at[idx, 'signal_count'] = len(merged)

        df = df.drop(columns=['_grp_swarm', '_td', '_dir'])

        self._write_swarm_events(events)

        n_events = len(events)
        n_trades_touched = len(per_trade_new)
        print(f"      {n_events:,} Schwarm-Events, "
              f"{n_trades_touched:,} Trades davon betroffen "
              f"-> {SWARM_EVENTS_STAGE_PATH}")
        return df

    def _write_swarm_events(self, events: list):
        """Schreibt Schwarm-Signal-Events in swarm_events_stage.csv.

        Wird in P4 zusammen mit branch1_events_stage.csv und
        sequence_events_stage.csv in suspicious_events.csv promoted.
        """
        self._ensure_directories()
        df_events = pd.DataFrame(events, columns=SIGNAL_EVENT_COLUMNS)
        df_events.to_csv(SWARM_EVENTS_STAGE_PATH, index=False, encoding='utf-8')

    # =========================================================================
    # P4 — Suspicious-Export: promotet Stage-Events nach suspicious_events.csv
    # und materialisiert assets/members-Views.
    # =========================================================================

    def _load_event_stages(self) -> pd.DataFrame:
        """Konkateniert branch1_events_stage.csv + sequence_events_stage.csv.

        Returns:
            DataFrame mit SIGNAL_EVENT_COLUMNS-Schema. Leer (0 Zeilen) wenn
            keine Stage-Datei existiert.
        """
        frames = []
        for path in _event_stages():
            if not os.path.isfile(path):
                continue
            df = pd.read_csv(path, low_memory=False)
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=SIGNAL_EVENT_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def _export_suspicious(self, trades: pd.DataFrame) -> None:
        """Uebernimmt die Event-Stage-Dateien nach suspicious_events.csv
        (append-only mit (signal_event_id, trade_id)-Dedup) und materialisiert
        suspicious_assets.csv (Y-Tage-Verfall) und suspicious_members.csv
        (dauerhaft) immer neu, auch ohne neue Events: die Scores des Laufs
        gelten auch fuer die Event-Historie.

        Aufgerufen aus run() (ohne --dry_run) und aus save(), jeweils bevor
        der Master geschrieben wird. Die Scores der Views stammen aus
        ``trades`` (In-Flight), nicht aus dem alten trades_master_complete.csv.
        Die Event-Stage-Dateien raeumt der Aufrufer nach dem Master-Schreiben
        auf (_clear_run_stages).

        Args:
            trades: Trades des laufenden Laufs mit trade_id, trade_score,
                trade_volume_usd, trade_warnings.
        """
        # P6: Schema-Migration der suspicious_events.csv, falls noch im
        # alten 20-Spalten-Schema (idempotent: no-op wenn bereits aktuell).
        self._migrate_suspicious_events_schema()

        self._inflight_trade_scores = trades
        self._trade_scores_cache = None
        try:
            n_appended = self._append_suspicious_events(self._load_event_stages())
            n_assets = self._materialize_suspicious_assets()
            n_members = self._materialize_suspicious_members()
        finally:
            self._inflight_trade_scores = None
            self._trade_scores_cache = None
        print(f"      {n_appended:,} neue Events -> {SUSPICIOUS_EVENTS_PATH}")
        print(f"      {n_assets:,} aktive Assets "
              f"(<= {settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS}d) "
              f"-> {SUSPICIOUS_ASSETS_PATH}")
        print(f"      {n_members:,} Members (gesamt) -> {SUSPICIOUS_MEMBERS_PATH}")

    def _clear_run_stages(self) -> None:
        """Entfernt alle Stage-Dateien eines Laufs (Trades, Unmatched,
        Delisted, Events). Zu Beginn jedes Laufs, damit ein abgebrochener Lauf
        keine Mischung mit Dateien eines frueheren Laufs hinterlaesst, und nach
        der erfolgreichen Uebernahme, damit sie nur einmal uebernommen werden.
        """
        for path in (STAGE_CSV_PATH, UNMATCHED_STAGE_PATH, DELISTED_STAGE_PATH,
                     *_event_stages()):
            if os.path.isfile(path):
                os.remove(path)

    def _migrate_suspicious_events_schema(self) -> bool:
        """Migriert suspicious_events.csv auf das aktuelle SIGNAL_EVENT_COLUMNS-
        Schema. Insbesondere P6: fuegt fehlendes `issuer_group_key` ein.

        Robust gegenueber gemischten Zeilenlaengen (falls die Datei bereits
        durch fehlgeschlagene Appends inkonsistent ist): alte Zeilen werden
        auf das neue Schema gemappt, bereits 21-spaltige Zeilen unveraendert
        uebernommen, malformed Zeilen verworfen.

        Returns: True wenn migriert (Datei neu geschrieben), False wenn nichts
        zu tun (Schema bereits aktuell oder Datei fehlt).
        """
        import csv
        if not os.path.isfile(SUSPICIOUS_EVENTS_PATH):
            return False
        with open(SUSPICIOUS_EVENTS_PATH, 'r', encoding='utf-8', newline='') as f:
            old_header = next(csv.reader(f), None)
        if old_header is None:
            return False
        if old_header == SIGNAL_EVENT_COLUMNS:
            return False

        expected_old_len = len(old_header)
        expected_new_len = len(SIGNAL_EVENT_COLUMNS)
        print(f"      Schema-Migration: suspicious_events.csv "
              f"({expected_old_len} -> {expected_new_len} Spalten)...")

        # Positions-Mapping alte Spaltennamen -> neuer Spaltenindex
        new_index_by_name = {c: i for i, c in enumerate(SIGNAL_EVENT_COLUMNS)}
        unknown_old_cols = [c for c in old_header if c not in new_index_by_name]
        if unknown_old_cols:
            print(f"        WARN: alte Spalten ohne Mapping (werden verworfen): "
                  f"{unknown_old_cols}")

        temp_path = SUSPICIOUS_EVENTS_PATH + '.migrating'
        n_old, n_new, n_bad = 0, 0, 0
        with open(SUSPICIOUS_EVENTS_PATH, 'r', encoding='utf-8', newline='') as fin, \
             open(temp_path, 'w', encoding='utf-8', newline='') as fout:
            reader = csv.reader(fin)
            writer = csv.writer(fout)
            next(reader)  # alten Header skippen
            writer.writerow(SIGNAL_EVENT_COLUMNS)
            for row in reader:
                if len(row) == expected_old_len:
                    # Map alte Spalten an neue Positionen, fehlende Spalten leer
                    new_row = [''] * expected_new_len
                    for i, val in enumerate(row):
                        col_name = old_header[i]
                        new_idx = new_index_by_name.get(col_name)
                        if new_idx is not None:
                            new_row[new_idx] = val
                    writer.writerow(new_row)
                    n_old += 1
                elif len(row) == expected_new_len:
                    # Bereits im neuen Schema (aus fehlgeschlagenem Append)
                    writer.writerow(row)
                    n_new += 1
                else:
                    n_bad += 1
        os.replace(temp_path, SUSPICIOUS_EVENTS_PATH)
        print(f"        {n_old:,} alte Zeilen migriert, "
              f"{n_new:,} bereits {expected_new_len}-spaltig, "
              f"{n_bad:,} malformed verworfen "
              f"-> {SUSPICIOUS_EVENTS_PATH}")
        return True

    def _append_suspicious_events(self, new_df: pd.DataFrame) -> int:
        """Hängt neue (signal_event_id, trade_id)-Paare an suspicious_events.csv.

        Idempotent: Paare die schon in der Datei stehen oder in new_df doppelt
        vorkommen, werden nur einmal geschrieben.

        Returns:
            Anzahl neu angehaengter Zeilen.
        """
        if new_df.empty:
            return 0

        self._ensure_directories()
        # P6: Schema-Migration falls die alte Datei noch das 20-Spalten-Schema hat
        self._migrate_suspicious_events_schema()

        if os.path.isfile(SUSPICIOUS_EVENTS_PATH):
            existing = pd.read_csv(
                SUSPICIOUS_EVENTS_PATH,
                usecols=['signal_event_id', 'trade_id'],
                low_memory=False,
            )
            existing_keys = set(
                zip(existing['signal_event_id'].astype(str),
                    existing['trade_id'].astype(str))
            )
        else:
            existing_keys = set()

        new_keys = list(zip(new_df['signal_event_id'].astype(str),
                            new_df['trade_id'].astype(str)))
        mask = [k not in existing_keys for k in new_keys]
        to_append = new_df[mask].drop_duplicates(
            subset=['signal_event_id', 'trade_id']
        )
        if to_append.empty:
            return 0

        # Spaltenreihenfolge konsistent zum Master halten.
        to_append = to_append.reindex(columns=SIGNAL_EVENT_COLUMNS)

        _write_csv_atomic(to_append, SUSPICIOUS_EVENTS_PATH, append=True)
        return len(to_append)

    def _load_trade_scores(self) -> pd.DataFrame:
        """Laedt trade_id -> (trade_score, trade_volume_usd, trade_warnings)
        aus trades_master_complete.csv (Modul 8.4).

        Lazy-Cache: nur einmal pro Importer-Instanz gelesen. Defensiv gegen
        fehlende Datei oder Spalten — gibt in dem Fall ein leeres DataFrame
        mit den vier erwarteten Spalten zurueck.

        Returns:
            DataFrame mit Spalten ['trade_id', 'trade_score',
            'trade_volume_usd', 'trade_warnings'].
        """
        if self._trade_scores_cache is not None:
            return self._trade_scores_cache

        cols = ['trade_id', 'trade_score', 'trade_volume_usd', 'trade_warnings']
        empty = pd.DataFrame({
            'trade_id': pd.Series(dtype=str),
            'trade_score': pd.Series(dtype=int),
            'trade_volume_usd': pd.Series(dtype=int),
            'trade_warnings': pd.Series(dtype=str),
        })

        # In-Flight-Override: _export_suspicious reicht das gescorete df
        # direkt durch, bevor es persistiert ist.
        if self._inflight_trade_scores is not None:
            df = self._inflight_trade_scores
            missing = [c for c in cols if c not in df.columns]
            if missing:
                print(f"      [8.4] In-Flight-Scores fehlen Spalten {missing} — "
                      f"Aggregate auf 0.")
                self._trade_scores_cache = empty
                return empty
            df = df[cols].copy().fillna({
                'trade_score': 0,
                'trade_volume_usd': 0,
                'trade_warnings': '',
            })
            df['trade_score'] = df['trade_score'].astype(int)
            df['trade_volume_usd'] = df['trade_volume_usd'].astype(int)
            df['trade_warnings'] = df['trade_warnings'].astype(str)
            df['trade_id'] = df['trade_id'].astype(str)
            self._trade_scores_cache = df
            return df

        if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            print(f"      [8.4] {MASTER_COMPLETE_CSV_PATH} nicht gefunden — "
                  f"Score-Aggregate fallen auf 0 zurueck.")
            self._trade_scores_cache = empty
            return empty

        try:
            df = pd.read_csv(
                MASTER_COMPLETE_CSV_PATH,
                usecols=cols,
                low_memory=False,
            )
        except ValueError as e:
            # usecols schlaegt fehl, wenn Spalten fehlen (Phase 8.3 nicht durchgelaufen).
            print(f"      [8.4] trades_master_complete fehlen Score-Spalten "
                  f"({e}) — Aggregate auf 0.")
            self._trade_scores_cache = empty
            return empty

        df = df.fillna({
            'trade_score': 0,
            'trade_volume_usd': 0,
            'trade_warnings': '',
        })
        df['trade_score'] = df['trade_score'].astype(int)
        df['trade_volume_usd'] = df['trade_volume_usd'].astype(int)
        df['trade_warnings'] = df['trade_warnings'].astype(str)
        df['trade_id'] = df['trade_id'].astype(str)

        self._trade_scores_cache = df
        return df

    def _materialize_suspicious_assets(self) -> int:
        """Aggregiert suspicious_events.csv pro issuer_ticker mit Y-Tage-Verfall.

        Returns:
            Anzahl Zeilen in suspicious_assets.csv.
        """
        if not os.path.isfile(SUSPICIOUS_EVENTS_PATH):
            return 0

        events = pd.read_csv(SUSPICIOUS_EVENTS_PATH, low_memory=False)
        if events.empty:
            # Datei evtl. nur mit Header — leere View schreiben und gehen.
            pd.DataFrame(columns=ASSETS_VIEW_COLUMNS).to_csv(
                SUSPICIOUS_ASSETS_PATH, index=False, encoding='utf-8')
            return 0

        events['_td'] = pd.to_datetime(events['triggering_date'],
                                       errors='coerce').dt.date
        cutoff = (datetime.now().date()
                  - timedelta(days=settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS))
        active = events[events['_td'].notna() & (events['_td'] >= cutoff)]
        if active.empty:
            pd.DataFrame(columns=ASSETS_VIEW_COLUMNS).to_csv(
                SUSPICIOUS_ASSETS_PATH, index=False, encoding='utf-8')
            return 0

        active = active.sort_values('_td')  # _td ascending → 'last' = jüngstes

        # Modul 8.4: Score-Aggregat pro issuer_ticker auf distinct trade_id.
        scores = self._load_trade_scores()
        distinct = active[['issuer_ticker', 'trade_id']].copy()
        distinct['trade_id'] = distinct['trade_id'].astype(str)
        distinct = distinct.drop_duplicates()
        joined = distinct.merge(scores, on='trade_id', how='left').fillna({
            'trade_score': 0,
            'trade_volume_usd': 0,
            'trade_warnings': '',
        })
        if not joined.empty:
            score_agg = joined.groupby(
                'issuer_ticker', sort=False, dropna=False
            ).agg(
                score_avg=('trade_score', lambda s: round(float(s.mean()), 1)),
                score_max=('trade_score', 'max'),
                score_total=('trade_score', 'sum'),
                total_volume_usd=('trade_volume_usd', 'sum'),
                warnings=('trade_warnings', _join_distinct_warnings),
            )
        else:
            score_agg = pd.DataFrame(columns=[
                'score_avg', 'score_max', 'score_total',
                'total_volume_usd', 'warnings',
            ])

        grp = active.groupby('issuer_ticker', sort=False, dropna=False)

        rows = []
        for issuer_ticker, g in grp:
            last_row = g.iloc[-1]
            if issuer_ticker in score_agg.index:
                sa = score_agg.loc[issuer_ticker]
                score_avg = float(sa['score_avg'])
                score_max = int(sa['score_max'])
                score_total = int(sa['score_total'])
                total_volume_usd = int(sa['total_volume_usd'])
                warnings_str = str(sa['warnings'])
            else:
                score_avg, score_max, score_total = 0.0, 0, 0
                total_volume_usd, warnings_str = 0, ''

            rows.append({
                'issuer_ticker':     issuer_ticker,
                'asset_name_figi':   last_row.get('asset_name_figi', ''),
                'event_count':       len(g),
                'member_count':      g['member_id'].nunique(),
                'signal_types':      _counter_string(g['signal_type']),
                'last_event_date':   g['_td'].max().isoformat(),
                'first_event_date':  g['_td'].min().isoformat(),
                'directions':        _counter_string(g['direction']),
                'chambers':          _counter_string(g['chamber']),
                'last_signal_type':  last_row.get('signal_type', ''),
                'score_avg':         score_avg,
                'score_max':         score_max,
                'score_total':       score_total,
                'total_volume_usd':  total_volume_usd,
                'warnings':          warnings_str,
            })

        result = pd.DataFrame(rows, columns=ASSETS_VIEW_COLUMNS)
        result.to_csv(SUSPICIOUS_ASSETS_PATH, index=False, encoding='utf-8')
        return len(result)

    def _materialize_suspicious_members(self) -> int:
        """Aggregiert suspicious_events.csv pro member_id (kein Verfall).

        Returns:
            Anzahl Zeilen in suspicious_members.csv.
        """
        if not os.path.isfile(SUSPICIOUS_EVENTS_PATH):
            return 0

        events = pd.read_csv(SUSPICIOUS_EVENTS_PATH, low_memory=False)
        if events.empty:
            pd.DataFrame(columns=MEMBERS_VIEW_COLUMNS).to_csv(
                SUSPICIOUS_MEMBERS_PATH, index=False, encoding='utf-8')
            return 0

        events['_td'] = pd.to_datetime(events['triggering_date'],
                                       errors='coerce').dt.date
        events = events.sort_values('_td')  # ascending → iloc[-1] = jüngstes

        # Modul 8.4: Score-Aggregat pro member_id auf distinct trade_id.
        scores = self._load_trade_scores()
        distinct = events[['member_id', 'trade_id']].copy()
        distinct['trade_id'] = distinct['trade_id'].astype(str)
        distinct = distinct.drop_duplicates()
        joined = distinct.merge(scores, on='trade_id', how='left').fillna({
            'trade_score': 0,
        })
        if not joined.empty:
            score_agg = joined.groupby(
                'member_id', sort=False, dropna=False
            ).agg(
                score_avg=('trade_score', lambda s: round(float(s.mean()), 1)),
                score_max=('trade_score', 'max'),
                score_total=('trade_score', 'sum'),
            )
        else:
            score_agg = pd.DataFrame(columns=[
                'score_avg', 'score_max', 'score_total',
            ])

        grp = events.groupby('member_id', sort=False, dropna=False)

        rows = []
        for member_id, g in grp:
            last_row = g.iloc[-1]
            pairs = list(zip(g['signal_event_id'].astype(str),
                             g['trade_id'].astype(str)))
            if member_id in score_agg.index:
                sa = score_agg.loc[member_id]
                score_avg = float(sa['score_avg'])
                score_max = int(sa['score_max'])
                score_total = int(sa['score_total'])
            else:
                score_avg, score_max, score_total = 0.0, 0, 0

            rows.append({
                'member_id':         member_id,
                'member_name':       last_row.get('member_name', ''),
                'chamber':           last_row.get('chamber', ''),
                'event_count':       len(g),
                'asset_count':       g['issuer_ticker'].nunique(),
                'signal_types':      _counter_string(g['signal_type']),
                'last_event_date':   g['_td'].max().isoformat(),
                'first_event_date':  g['_td'].min().isoformat(),
                'is_ex_officio_ever': bool(
                    g['is_ex_officio'].astype(str).str.lower().isin(
                        ['true', '1', 'yes']).any()
                ),
                'event_trade_pairs': json.dumps(
                    [[s, t] for s, t in pairs], ensure_ascii=False
                ),
                'score_avg':         score_avg,
                'score_max':         score_max,
                'score_total':       score_total,
            })

        result = pd.DataFrame(rows, columns=MEMBERS_VIEW_COLUMNS)
        result.to_csv(SUSPICIOUS_MEMBERS_PATH, index=False, encoding='utf-8')
        return len(result)

    # =========================================================================
    # Backup, Kurs- und Rendite-Helfer
    # =========================================================================

    def _prune_backups(self, prefix: str):
        """Loescht alte Backups eines Praefix, behaelt die neuesten N.

        N kommt aus settings.ARCHIVE_BACKUP_RETENTION (0/None = Rotation aus).
        Gematcht wird strikt auf '<prefix>_<YYYYMMDD-HHMMSS>.csv', damit
        'trades_master' nicht die 'trades_master_complete'-Reihe mitloescht und
        manuell abgelegte Snapshots (z.B. '..._pre_migration_...') unangetastet
        bleiben. Sortierung erfolgt ueber den Timestamp im Dateinamen, nicht
        ueber mtime -- Kopier- und Sync-Vorgaenge verschieben mtime.
        """
        keep = getattr(settings, 'ARCHIVE_BACKUP_RETENTION', 0)
        if not keep or keep <= 0:
            return

        pattern = re.compile(r'^' + re.escape(prefix) + r'_\d{8}-\d{6}\.csv$')
        try:
            matches = sorted(f for f in os.listdir(ARCHIVE_BACKUP_DIR)
                             if pattern.match(f))
        except OSError as exc:
            logger.warning(f"Backup-Rotation uebersprungen ({prefix}): {exc}")
            return

        for name in matches[:-keep]:
            stale = os.path.join(ARCHIVE_BACKUP_DIR, name)
            try:
                os.remove(stale)
                logger.info(f"Backup-Rotation: {name} geloescht")
                print(f"      Rotation: {name} geloescht")
            except OSError as exc:
                logger.warning(f"Backup-Rotation: {name} nicht loeschbar: {exc}")

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
                self._prune_backups(prefix)

    def _compute_recent_paper_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Recent-Monitoring Papier-Return ueber den GANZEN uebergebenen Frame.

        Setzt ``paper_return_pct`` + ``price_as_of`` (Mark-to-Market gegen den
        juengsten Close) fuer Trades im RECENT_MONITORING_WINDOW_DAYS-Fenster.
        Muss IMMER ueber den vollen Master laufen, weil der aktuelle Kurs auch
        fuer laengst importierte offene Positionen taeglich wechselt.
        """
        cache_path = os.path.join(BASE_DIR, settings.POST_TRADE_PRICE_CACHE)
        return compute_paper_returns(
            df,
            cache_path=cache_path,
            recent_window_days=settings.RECENT_MONITORING_WINDOW_DAYS,
            max_price_age_bdays=settings.RECENT_PRICE_MAX_AGE_BUSINESS_DAYS,
            fetch_period=settings.POST_TRADE_PRICE_FETCH_PERIOD,
            cache_flush_every=settings.POST_TRADE_CACHE_FLUSH_EVERY,
        )

    def _apply_abnormal_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Berechnet die marktbereinigten Abnormal Returns (Konstrukt A) und
        merged NUR ``signed_ar_{H}d`` in df. Outlier-Flag/Perzentil-Rang werden
        bewusst NICHT persistiert (schwellen-/populationsabhaengig -> in der App
        bei der Anzeige abgeleitet); Konstrukt B (disclosure) bleibt dem
        Standalone-Report (scripts/analyse_event_study.py) vorbehalten.

        signed_ar_{H}d ist pro Trade deterministisch (reine Cache-Lookups).
        """
        cache_path = os.path.join(BASE_DIR, settings.POST_TRADE_PRICE_CACHE)
        ar = compute_abnormal_returns(
            df,
            cache_path=cache_path,
            benchmark_ticker=settings.AR_BENCHMARK_TICKER,
            horizons_days=settings.AR_HORIZONS_DAYS,
            headline_horizon_days=settings.AR_HEADLINE_HORIZON_DAYS,
            disclosure_horizons_days=settings.AR_DISCLOSURE_HORIZONS_DAYS,
            outlier_mode=settings.AR_OUTLIER_MODE,
            outlier_upper=settings.AR_OUTLIER_UPPER,
            outlier_lower=settings.AR_OUTLIER_LOWER,
            outlier_pct_upper=settings.AR_OUTLIER_PCT_UPPER,
            outlier_pct_lower=settings.AR_OUTLIER_PCT_LOWER,
        )
        ar_cols = [f'signed_ar_{int(h)}d' for h in settings.AR_HORIZONS_DAYS]
        df = df.copy()
        for col in ar_cols:
            if col in ar.columns:
                df[col] = ar[col].values
        head_col = f'signed_ar_{int(settings.AR_HEADLINE_HORIZON_DAYS)}d'
        n_eval = int(ar[head_col].notna().sum()) if head_col in ar.columns else 0
        print(f"      Abnormal Return (vs. {settings.AR_BENCHMARK_TICKER}): "
              f"{len(ar_cols)} Horizonte, {n_eval:,} evaluierbar @ "
              f"{settings.AR_HEADLINE_HORIZON_DAYS}d")
        return df

    def save_results(self, df: pd.DataFrame):
        """
        Schreibt complete + curated Master (vorher Backup) plus
        manual_review_tickers.csv und promotet die Problem-Outputs. Beide
        Master-Dateien werden komplett neu geschrieben (kein Append).
        """
        self._ensure_directories()
        self._backup_master()

        # Import-Datum hinzufuegen
        df = df.copy()
        df['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Master durchgehend absteigend nach transaction_date sortieren —
        # Complete- und Curated-Master erben dieselbe Reihenfolge.
        df = _sort_master_by_date(df)

        # 1. Complete-Master (alle Spalten)
        df.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
        logger.info(f"Complete-Master: {MASTER_COMPLETE_CSV_PATH} ({len(df)} Trades)")

        # 2. Curated-Master (Spalten gemaess settings.CURATED_COLUMNS)
        curated = self._export_curated_master(df)
        curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        logger.info(f"Curated-Master: {MASTER_CSV_PATH} "
                    f"({len(curated)} Trades, {len(curated.columns)} Spalten)")

        # 3. Manual-Review (falls Tickers mit manual_review_flag)
        self._write_manual_review_subset(df)

        # 4. Problem-Outputs promoten (Overwrite, Auto-Purge)
        self._promote_problem_outputs()

    def save(self):
        """
        --save: uebernimmt das Ergebnis von --dry_run. Reihenfolge: Stage
        lesen -> Papier-Return (Kurs-Refresh, kann abbrechen) ->
        Suspicious-Export mit den Scores aus trades_stage.csv -> Master
        schreiben -> Stage-Dateien aufraeumen. Bricht ein Schritt ab, bleibt
        der ganze Satz liegen und ein erneutes --save uebernimmt ihn.
        """
        print("\n[Save] Uebernehme Dry-Run-Ergebnis...")
        missing = [p for p in (STAGE_CSV_PATH, *_event_stages())
                   if not os.path.isfile(p)]
        if missing:
            raise FileNotFoundError(
                f"Dry-Run-Ergebnis fehlt oder ist unvollstaendig: {missing}\n"
                f"Bitte zuerst ausfuehren: python scripts/import_trades.py --dry_run")
        df = pd.read_csv(STAGE_CSV_PATH, low_memory=False)

        # Recent-Monitoring Papier-Return: aktueller Kurs zum Speicherzeitpunkt.
        print("\n[Recent-Monitoring] Papier-Return aktualisieren...")
        df = self._compute_recent_paper_returns(df)

        print("\n[Suspicious-Export] Promote Events + materialisiere Views...")
        self._export_suspicious(df)

        self.save_results(df)
        self._clear_run_stages()
        print(f"\n[OK] {len(df):,} Trades -> {MASTER_COMPLETE_CSV_PATH}")

    def run(self, dry_run: bool = False) -> dict:
        """
        Fuehrt den kompletten Import durch (alle 17 Schritte im Speicher).

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master;
                Suspicious-Export und Master-Schreiben uebernimmt dann save().

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Import Pipeline (17 Schritte)")
        print("=" * 60)
        # Stage-Dateien frueherer Laeufe entfernen: ein abgebrochener Lauf
        # hinterlaesst so keinen Satz, den --save uebernehmen koennte.
        self._clear_run_stages()

        # 1. Laden + Umbenennen + trade_id
        print("\n[1/17] Lade Quiver-Trades...")
        df = self.load_and_rename()
        print(f"      Gesamt: {len(df)} Trades geladen")

        # 2. Datum normalisieren + trade_id
        print("\n[2/17] Normalisiere Datumsspalten + trade_id...")
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
        print("\n[3/17] Reichere mit Committee-Member-Daten an...")
        df, unmatched_df = self.enrich_member_data(df)
        print(f"      {self.stats['matched']} erfolgreich gematcht")
        print(f"      {self.stats['unmatched']} nicht gefunden")

        # 4. Ticker-Klassifikation (offline)
        print("\n[4/17] Ticker-Klassifikation (offline Regex)...")
        classifications = [
            classify(row.get('ticker', ''), row.get('asset_name', '') or '')
            for _, row in df.iterrows()
        ]
        df = df.copy()
        df['ticker_type']               = [c.ticker_type.value for c in classifications]
        df['ticker_base']               = [c.ticker_base for c in classifications]
        df['classification_reason']     = [c.classification_reason for c in classifications]
        df['classification_confidence'] = [c.confidence.value for c in classifications]
        df['exch_code']                 = [c.exch_code for c in classifications]
        df['series_letter']             = [c.series_letter for c in classifications]
        df['status_suffix']             = [c.status_suffix for c in classifications]
        df['manual_review_flag']        = [c.manual_review for c in classifications]
        df = self._apply_ignore_list(df)
        n_review = int(df['manual_review_flag'].sum())
        print(f"      {len(df):,} klassifiziert, {n_review} fuer manual_review markiert")

        # 5. OpenFIGI-Normalisierung
        print("\n[5/17] OpenFIGI-Normalisierung...")
        cache_db = os.path.join(BASE_DIR, settings.DATA_CACHE_PATH,
                                settings.TICKER_TYPE_CACHE_DB)
        normalizer = TickerNormalizer(
            cache_db_path=cache_db,
            api_key=settings.OPENFIGI_API_KEY,
        )
        normalized = normalizer.normalize_batch(classifications)
        df['figi']             = [n.figi for n in normalized]
        df['ticker_figi'] = [n.ticker_figi for n in normalized]
        df['asset_name_figi']   = [n.asset_name_figi for n in normalized]
        df['security_type_figi']    = [n.security_type_figi for n in normalized]
        df['market_sector_figi']    = [n.market_sector_figi for n in normalized]
        df['share_class_figi']  = [n.share_class_figi for n in normalized]
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
        # issuer_ticker vektorisiert (P5.5): share_class_figi -> ticker_base -> ticker
        scf = df['share_class_figi'].fillna('').astype(str).str.strip()
        tb  = df['ticker_base'].fillna('').astype(str).str.strip()
        tk  = df['ticker'].fillna('').astype(str).str.strip()
        df['issuer_ticker'] = scf.where(scf != '', tb.where(tb != '', tk))

        # 6. Cross-Class Issuer Mapping via SEC EDGAR CIK (P5.6)
        print("\n[6/17] Cross-Class Issuer Mapping (EDGAR CIK)...")
        issuer_mapper = IssuerGroupMapper()
        df['issuer_group_key'] = df.apply(issuer_mapper.resolve_issuer_group_key, axis=1)

        # 7. yfinance Sector/Industry-Anreicherung
        print("\n[7/17] yfinance Sector/Industry-Anreicherung...")
        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)
        enricher = TickerSectorEnricher(
            cache_dir=mappings_dir,
            delisted_output_path=DELISTED_STAGE_PATH,
        )
        transaction_dates = df['transaction_date'].tolist() if 'transaction_date' in df.columns else None
        sector_infos = enricher.enrich_batch(normalized, transaction_dates=transaction_dates)
        df['sector_yf']   = [s.sector_yf for s in sector_infos]
        df['industry_yf'] = [s.industry_yf for s in sector_infos]
        df['quote_type_yf']      = [s.quote_type_yf for s in sector_infos]

        # 8. Trade-Volume (Mittelpunkt + Max der Amount-Range)
        # MUSS vor post_trade_performance laufen, da post_trade_performance
        # trade_amount_max_usd als Input erwartet.
        print("\n[8/17] Trade-Volume aus Amount-Range berechnen...")
        df['trade_volume_usd'] = df['amount'].map(midpoint_usd).astype('int64')
        df['trade_amount_max_usd'] = df['amount'].map(max_usd).astype('int64')

        # 9. Post-Trade-Preise + Exit-Detection (Modul 9.x)
        print("\n[9/17] Post-Trade-Preise + Exit-Detection...")
        post_trade_cache = os.path.join(BASE_DIR, settings.POST_TRADE_PRICE_CACHE)
        df = compute_post_trade_returns(
            df,
            horizons_days=settings.POST_TRADE_HORIZONS_DAYS,
            cache_path=post_trade_cache,
            fetch_period=settings.POST_TRADE_PRICE_FETCH_PERIOD,
            cache_ttl_days=settings.POST_TRADE_PRICE_CACHE_TTL_DAYS,
            refresh_window_days=settings.POST_TRADE_REFRESH_WINDOW_DAYS,
            exit_amount_tolerance=settings.POST_TRADE_EXIT_AMOUNT_TOLERANCE,
            cache_flush_every=settings.POST_TRADE_CACHE_FLUSH_EVERY,
            progress_every=settings.POST_TRADE_PROGRESS_EVERY,
            exit_window_days=settings.EXIT_DETECTION_WINDOW_DAYS,
            benchmark_ticker=settings.AR_BENCHMARK_TICKER,
        )
        # Recent-Monitoring Papier-Return (Voll-Pass ueber den ganzen Master).
        df = self._compute_recent_paper_returns(df)

        # 9b. Abnormal Return (marktbereinigt vs. SPY) — deskriptiv, kein Scoring-Input.
        print("\n[9b/17] Abnormal Return (marktbereinigt vs. SPY)...")
        df = self._apply_abnormal_returns(df)

        # 10. Asset-Tradingvolume (Cross-Class rolling-window, Modul 8.2)
        z = settings.SCORING_VOLUME_WINDOW_DAYS_Z
        print(f"\n[10/17] Asset-Tradingvolume ({z}d, Cross-Class)...")
        df['asset_tradingvolume_usd'] = \
            compute_asset_tradingvolume(df, z).astype('int64')

        # 11. Filter-Logik (Scoring + Filter 0 + Filter 4)
        print("\n[11/17] Filter-Logik (Scoring, Asset-Klasse, Vote-Timing)...")
        df = self._apply_gates_and_tags(df)

        # 12.-14. Signale nur fuer Trades, die das Asset-Gate durchlassen (H2):
        # Ausgeschlossene Zeilen bleiben im Master, bekommen kein Signal (und
        # damit keinen Score) und sind kein Input fuer Sequenz- und
        # Schwarmsignale anderer Trades.
        excluded = df['asset_class_excluded'].astype(bool)
        signaled = df[~excluded]
        print(f"\n      {int(excluded.sum()):,} Trades vom Asset-Gate ausgeschlossen, ohne Signale")

        # 12. Branch-1 Signale (committee, ex_officio, floor_action, extreme_report)
        print("\n[12/17] Branch-1 Signale auswerten...")
        signaled = self._apply_branch1_signals(signaled)

        # 13. Sequenz-Signale (contrarian, accumulative)
        print("\n[13/17] Sequenz-Signale auswerten...")
        signaled = self._apply_sequence_signals(signaled)

        # 14. Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6)
        print("\n[14/17] Schwarm-Signale auswerten...")
        signaled = self._apply_swarm_signals(signaled)

        df = pd.concat([
            signaled,
            df[excluded].assign(signal_count=0, signals_triggered=''),
        ]).reindex(df.index)

        # 15. Scoring (trade_score + breakdown + warnings, Modul 8.3)
        print("\n[15/17] Trade-Score berechnen (Modul 8.3)...")
        scored = compute_trade_scores(df, settings)
        df = df.copy()
        df['trade_score'] = scored['trade_score'].values
        df['trade_score_breakdown'] = scored['trade_score_breakdown'].values
        df['trade_warnings'] = scored['trade_warnings'].values
        _n_pos = int((df['trade_score'] > 0).sum())
        _n_par = int((df['trade_warnings'] == settings.SCORING_PARADOX_WARNING_TAG).sum())
        print(f"  Trades mit trade_score > 0: {_n_pos:,}")
        print(f"  Trades mit paradox-Warning: {_n_par:,}")

        # Stage immer schreiben (auch leer) -> Promotion entscheidet
        self._ensure_directories()
        unmatched_df.to_csv(UNMATCHED_STAGE_PATH, index=False, encoding='utf-8')

        if dry_run:
            # 16./17. Dry-Run: Trades + Event-Stage-Dateien bleiben liegen,
            # --save uebernimmt beide gemeinsam.
            print("\n[16-17/17] Dry-Run: schreibe trades_stage.csv...")
            df_out = df.copy()
            df_out['ImportDate'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self._write_manual_review_subset(df_out)
            # Zuletzt und atomar: erst eine vollstaendige trades_stage.csv
            # weist den Dry-Run als abgeschlossen aus (siehe save()).
            _write_csv_atomic(df_out, STAGE_CSV_PATH)
            print(f"\nDry-Run: {len(df_out):,} Trades -> {STAGE_CSV_PATH}")
            print("(Zum Speichern: python scripts/import_trades.py --save)")
        else:
            # 16. Suspicious-Export (Event-Stage-Dateien -> suspicious_*.csv)
            print("\n[16/17] Suspicious-Export...")
            self._export_suspicious(df)
            # 17. Speichern
            print("\n[17/17] Speichere Ergebnisse...")
            self.save_results(df)
            self._clear_run_stages()

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
                f"Bitte Full Rebuild ausfuehren: python scripts/import_trades.py")
        df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        self._write_manual_review_subset(df)
        print(f"\n[OK] Manual-Review aus {len(df):,} Trades regeneriert.")

    def run_regenerate_curated(self):
        """Regeneriert trades_master.csv (kuratiert) aus complete, ohne Datenverlust."""
        if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            raise FileNotFoundError(
                f"trades_master_complete.csv nicht vorhanden: {MASTER_COMPLETE_CSV_PATH}\n"
                f"Bitte Full Rebuild ausfuehren: python scripts/import_trades.py")
        df = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        self._backup_master()
        curated = self._export_curated_master(df)
        curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
        print(f"\n[OK] Curated regeneriert: {MASTER_CSV_PATH} "
              f"({len(curated):,} Trades, {len(curated.columns)} Spalten)")


def main():
    """Hauptfunktion: Fuehrt Import durch."""
    parser = argparse.ArgumentParser(description='Congress Trade Tracker - Import Pipeline')
    # Ohne Flag: Vollauf. Die Modi schliessen sich gegenseitig aus.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry_run', action='store_true',
                      help='Vollauf, schreibt trades_stage.csv + Event-Stage-Dateien '
                           'statt Master und suspicious_*.csv')
    mode.add_argument('--save', action='store_true',
                      help='Uebernimmt den Dry-Run: Event-Stage-Dateien -> suspicious_*.csv, '
                           'trades_stage.csv -> complete + curated Master (mit Backup)')
    # Bonus-Flags (Rebuild Teil 3): Regenerieren ohne Master-Touch
    mode.add_argument('--manual_review_only', action='store_true',
                      help='Regeneriert manual_review_tickers.csv aus complete-Master')
    mode.add_argument('--regenerate_curated', action='store_true',
                      help='Regeneriert trades_master.csv (kuratiert) aus complete')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    importer = TradeImporter()

    try:
        if args.manual_review_only:
            importer.run_manual_review_only()
        elif args.regenerate_curated:
            importer.run_regenerate_curated()
        elif args.save:
            importer.save()
        else:
            importer.run(dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"\n[X] FEHLER: {e}")
        print(f"\nBitte stelle sicher, dass die Quiver-Datei vorhanden ist:")
        print(f"  - {QUIVER_TRADES_PATH}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n[X] Abbruch: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[X] FEHLER: {e}")
        logger.exception("Import fehlgeschlagen")
        sys.exit(1)


if __name__ == "__main__":
    main()
