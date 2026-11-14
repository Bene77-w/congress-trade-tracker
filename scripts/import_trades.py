"""
Congress Trade Tracker - Trade Import Pipeline (17 Stages)
Importiert Trades, reichert mit Committee-Daten, Ticker-Klassifikation,
OpenFIGI/yfinance-Sektor und Filter-Logik an.

Modi:
  python scripts/import_trades.py                    # Full Rebuild (alle 17 Stages)
  python scripts/import_trades.py --delta            # Nur neue Trades (via trade_id)
  python scripts/import_trades.py --dry_run          # Alle Stages -> trades_stage.csv

Stage-Pipeline (einzelne Schritte, re-runnable):
  --stage_rename            # CSV laden + Spalten umbenennen + trade_id
  --stage_date              # Datum normalisieren
  --stage_committees        # Committee-Member-Enrichment (Fuzzy-Match)
  --stage_tickerclassify    # Offline-Klassifikation (Regex-Entscheidungsbaum)
  --stage_tickeropenfigi    # OpenFIGI-Normalisierung (FIGI, ticker_figi)
  --stage_issuer_group      # Cross-Class Issuer Mapping (SEC EDGAR CIK, P5.6)
  --stage_tickeryfinance    # yfinance Sector/Industry-Anreicherung
  --stage_trade_volume      # trade_volume_usd aus Amount-Range (Modul 8.1)
  --stage_post_trade_performance # Post-Trade-Preise + Exit-Detection (Modul 9.x)
  --stage_asset_volume      # Cross-Class Asset-Tradingvolume rolling-window (Modul 8.2)
  --stage_gates_tags        # Pipeline-Gates + Tags (Asset-Class, Committee, Vote-Timing)
  --stage_branch1_signals   # Branch-1 Signale (committee, ex_officio, floor_action, extreme_report)
  --stage_sequence_signals  # Sequenz-Signale (contrarian, accumulative)
  --stage_swarm_signals     # Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6)
  --stage_scoring           # Trade-Score + Breakdown + Warnings (Modul 8.3)
  --stage_suspicious_export # Promote Stage-Events nach suspicious_events.csv
  --stage_save              # trades_stage.csv -> complete + curated Master

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
from utils.trade_id import compute_trade_ids, detect_new_trades
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
    TickerType,
    ApiStrategy,
    Confidence,
    TickerClassification,
)
from utils.ticker_normalizer import TickerNormalizer, NormalizedTicker
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
    trades_master_complete.csv (Voll-Run, Delta-Merge, Einzel-Stage) laeuft
    hierdurch, sodass die Master stets durchgehend datums-sortiert ist —
    statt Delta-Zeilen unsortiert anzuhaengen.
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


# Stage-Pipeline Pfade
STAGE_CSV_PATH = os.path.join(PROCESSED_DIR, 'trades_stage.csv')
PIPELINE_STATE_PATH = os.path.join(PROCESSED_DIR, 'pipeline_state.json')
UNMATCHED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'unmatched_names_stage.csv')
DELISTED_STAGE_PATH = os.path.join(PROCESSED_DIR, 'delisted_trades_stage.csv')
BRANCH1_EVENTS_STAGE_PATH = os.path.join(PROCESSED_DIR, 'branch1_events_stage.csv')
SEQUENCE_EVENTS_STAGE_PATH = os.path.join(PROCESSED_DIR, 'sequence_events_stage.csv')
SWARM_EVENTS_STAGE_PATH = os.path.join(PROCESSED_DIR, 'swarm_events_stage.csv')
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
        'input_cols': ['name', 'chamber', 'bio_guide_id', 'transaction_date'],
        'output_cols': ['Member_Congress', 'Member_Committee_Status',
                        'Member_ExOfficio', 'Member_Chamber', 'Member_Committees_Full'],
    },
    'tickerclassify': {
        'input_cols': ['ticker'],
        'output_cols': ['ticker_type', 'ticker_base', 'classification_reason',
                        'classification_confidence',
                        'exch_code', 'series_letter', 'status_suffix',
                        'manual_review_flag'],
    },
    'tickeropenfigi': {
        'input_cols': ['ticker_type', 'ticker_base'],
        'output_cols': ['figi', 'ticker_figi', 'asset_name_figi',
                        'security_type_figi', 'market_sector_figi',
                        'share_class_figi', 'issuer_ticker'],
    },
    'issuer_group': {
        # P5.6: Cross-Class Issuer Mapping via SEC EDGAR CIK.
        # ticker_type entscheidet Branch (SKIP/ETF-Foreign/CIK/Fallback);
        # issuer_ticker als Fallback fuer ETF/Foreign-Branch.
        'input_cols': ['ticker', 'ticker_base', 'ticker_type'],
        'output_cols': ['issuer_group_key'],
    },
    'tickeryfinance': {
        'input_cols': ['ticker_base', 'ticker_type'],
        'output_cols': ['sector_yf', 'industry_yf', 'quote_type_yf'],
    },
    'trade_volume': {
        # parse 'amount' (Quiver-Range-String) -> zwei Spalten:
        # - trade_volume_usd (Mittelpunkt, fuer Aggregate)
        # - trade_amount_max_usd (Obergrenze, Score-Basis fuer trade_volume-Signal)
        # MUSS vor post_trade_performance laufen (Input-Abhaengigkeit).
        'input_cols': ['amount'],
        'output_cols': ['trade_volume_usd', 'trade_amount_max_usd'],
    },
    'post_trade_performance': {
        # Post-Trade-Preise + Exit-Detection (Modul 9.x).
        # Cache: data/cache/price_history.parquet (yfinance 5y Daily).
        # trade_amount_max_usd kommt aus Stage 'trade_volume'.
        'input_cols': ['trade_id', 'name', 'ticker', 'issuer_group_key',
                       'transaction_date', 'transaction_type',
                       'trade_amount_max_usd', 'ticker_type'],
        'output_cols': ['post_trade_baseline_price',
                       'post_price_30d', 'post_price_60d',
                       'post_price_90d', 'post_price_120d', 'post_price_180d',
                       'post_return_30d', 'post_return_60d',
                       'post_return_90d', 'post_return_120d', 'post_return_180d',
                       'exit_status', 'exit_date', 'exit_price',
                       'exit_amount_max_usd', 'effective_return_pct',
                       'effective_return_horizon_days', 'opposing_trade_ids',
                       'first_close_offset_days',
                       # Recent-Monitoring (Mark-to-Market, Voll-Pass ueber
                       # RECENT_MONITORING_WINDOW_DAYS, delta-unabhaengig).
                       'paper_return_pct', 'price_as_of'],
    },
    'abnormal_returns': {
        # Signierter, marktbereinigter Abnormal Return (Konstrukt A, Anker
        # transaction_date) vs. AR_BENCHMARK_TICKER. DESKRIPTIV — kein
        # Scoring-Input. Nutzt denselben Preis-Cache wie post_trade_performance.
        # Geschrieben werden NUR die kontinuierlichen signed_ar_{H}d; Outlier-Flag
        # + Perzentil-Rang sind schwellen-/populationsabhaengig und werden in der
        # App bei der Anzeige abgeleitet (nicht in den Master gefroren).
        'input_cols': ['trade_id', 'ticker', 'transaction_date',
                       'disclosure_date', 'transaction_type'],
        'output_cols': ['signed_ar_30d', 'signed_ar_60d', 'signed_ar_90d',
                        'signed_ar_120d', 'signed_ar_180d'],
    },
    'asset_volume': {
        # Phase 8.2: Cross-Class rolling-window Sum(trade_volume_usd) ueber
        # issuer_group_key im Fenster SCORING_VOLUME_WINDOW_DAYS_Z.
        'input_cols': ['trade_volume_usd', 'issuer_group_key', 'transaction_date'],
        'output_cols': ['asset_tradingvolume_usd'],
    },
    'gates_tags': {
        'input_cols': ['name', 'chamber', 'Member_Committees_Full', 'Member_Congress',
                       'ticker', 'ticker_type', 'transaction_date'],
        'output_cols': ['MatchScore', 'MatchSource', 'Insider_Sectors', 'Relevant_Sectors',
                        'ex_officio_role_keys',
                        'asset_class_excluded',
                        'Vote_Timing_Hit', 'Vote_Timing_Hits', 'Vote_Timing_Hit_Count'],
    },
    'branch1_signals': {
        # asset_name_figi ist optional (Fallback asset_name); ticker_base ist
        # optional (Fallback ticker via resolve_issuer_ticker). Beides wird im
        # Code defensiv aufgeloest, deshalb hier nicht hart required.
        # trade_amount_max_usd: pflicht fuer TradeVolumeSignal (Stage trade_volume).
        'input_cols': ['trade_id', 'name', 'chamber', 'ticker', 'asset_name',
                       'transaction_date', 'disclosure_date',
                       'transaction_type', 'amount',
                       'Member_ExOfficio', 'Member_Committees_Full',
                       'Vote_Timing_Hit_Count',
                       'Relevant_Sectors', 'ex_officio_role_keys',
                       'trade_amount_max_usd'],
        'output_cols': ['signal_count', 'signals_triggered'],
    },
    'sequence_signals': {
        # ticker_base + asset_name_figi optional (siehe branch1_signals).
        'input_cols': ['trade_id', 'bio_guide_id', 'ticker', 'asset_name',
                       'transaction_date', 'transaction_type',
                       'signal_count', 'signals_triggered'],
        'output_cols': ['signal_count', 'signals_triggered'],
    },
    'swarm_signals': {
        # P6: Schwarm-Signale gruppieren nach issuer_group_key (Issuer-Level
        # via CIK, Cross-Class). asset_name_figi/issuer_ticker als optionale
        # Snapshot-Felder werden im Code defensiv aufgeloest.
        'input_cols': ['trade_id', 'bio_guide_id', 'ticker', 'asset_name',
                       'transaction_date', 'transaction_type',
                       'issuer_group_key',
                       'signal_count', 'signals_triggered'],
        'output_cols': ['signal_count', 'signals_triggered'],
    },
    'scoring': {
        # trade_score + breakdown + warnings. Liest signals_triggered
        # (Branch-1 + Sequenz + Schwarm), ex_officio_role_keys (Bonus v2),
        # trade_amount_max_usd.
        # trade_amount_max_usd ersetzt das frühere asset_tradingvolume_usd —
        # Volume zaehlt jetzt als eigenstaendiges Signal mit Tier-Stufen.
        'input_cols': ['signals_triggered', 'ex_officio_role_keys',
                       'trade_amount_max_usd'],
        'output_cols': ['trade_score', 'trade_score_breakdown',
                        'trade_warnings'],
    },
    'suspicious_export': {
        # Stage liest direkt branch1_events_stage.csv + sequence_events_stage.csv,
        # nicht trades_stage.csv. df wird durchgereicht, nicht mutiert.
        'input_cols': [],
        'output_cols': [],
    },
    'save': {
        'input_cols': [],
        'output_cols': ['ImportDate'],
    },
}

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
        # Modul 8.4: In-Flight-Override fuer Voll-/Delta-Pipeline. Wird vor
        # _stage_suspicious_export gesetzt, damit _load_trade_scores nicht
        # die noch-nicht-geschriebene trades_master_complete.csv lesen muss.
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
            # ACHTUNG: Logik-Kopie in scripts/diagnose_master_quiver_delta.py
            # (_load_quiver_with_ids) — bei Aenderungen dort mitziehen.
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
        Scoring-Spalten (Insider_Sectors, Relevant_Sectors etc.) setzt _stage_gates_tags.

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

    # =========================================================================
    # Ticker-Stages (Rebuild Teil 3): Classify -> OpenFIGI -> yfinance
    # =========================================================================

    def _reconstruct_classifications(self, df: pd.DataFrame) -> list:
        """Rekonstruiert TickerClassification-Liste aus DataFrame-Spalten.

        Strategie: classify() einmal pro Trade neu aufrufen (deterministisch);
        dann DataFrame-Overrides anwenden (ticker_type, manual_review_flag,
        classification_reason — gesetzt von _apply_ignore_list und manuell).
        So bleibt api_strategy + parent_ticker (Classifier-Output) verfuegbar,
        ohne im Master persistiert zu werden.

        Reihenfolge entspricht df.index (positional).
        """
        out = []
        for _, row in df.iterrows():
            raw_ticker = str(row.get('ticker') or '')
            company_name = str(row.get('asset_name') or '')
            cls = classify(raw_ticker, company_name)

            df_ticker_type = row.get('ticker_type')
            overrides = {}
            if pd.notna(df_ticker_type) and str(df_ticker_type).strip():
                tt = TickerType(df_ticker_type)
                if tt != cls.ticker_type:
                    overrides['ticker_type'] = tt
                    if tt == TickerType.IGNORED:
                        overrides['api_strategy'] = ApiStrategy.SKIP
            df_reason = row.get('classification_reason')
            if pd.notna(df_reason) and str(df_reason).strip() \
                    and str(df_reason) != cls.classification_reason:
                overrides['classification_reason'] = str(df_reason)
            df_manual = row.get('manual_review_flag')
            if pd.notna(df_manual):
                overrides['manual_review'] = bool(df_manual)

            if overrides:
                import dataclasses as _dc
                cls = _dc.replace(cls, **overrides)
            out.append(cls)
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
                asset_name_figi=_val('asset_name_figi'),
                security_type_figi=_val('security_type_figi'),
                market_sector_figi=_val('market_sector_figi'),
                share_class_figi=_val('share_class_figi'),
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

        Setzt ticker_type='ignored', manual_review_flag=False. Reconstruct leitet
        daraus api_strategy=SKIP ab (siehe _reconstruct_classifications).
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
        if not set(key_columns) <= set(old_df.columns):
            old_df = pd.DataFrame()   # Datei im frueheren Format ohne die Schluesselspalten

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
        """Drop Eintraege, fuer die die Referenzdaten jetzt eine Mitgliedschaft kennen."""
        if df is None or df.empty:
            return df
        self._init_reference()
        keep = [not self.reference.member_committees(b, c)[0]
                for b, c in zip(df['bio_guide_id'].astype(str), df['congress'].astype(str))]
        return df[keep].reset_index(drop=True)

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
             ['bio_guide_id', 'congress'], self._revalidate_unmatched_names,
             ['count'], 'Unmatched-Names'),
            (DELISTED_STAGE_PATH, DELISTED_TRADES_PATH,
             ['ticker'], self._revalidate_delisted, None, 'Delisted'),
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

        Wird in P4 (`_stage_suspicious_export`) wieder eingelesen und in
        suspicious_events.csv promoted. Bei leerer Liste wird eine
        Header-only Datei geschrieben, damit Folge-Stages die Datei
        konsistent lesen koennen.
        """
        self._ensure_directories()
        df_events = pd.DataFrame(events, columns=SIGNAL_EVENT_COLUMNS)
        df_events.to_csv(BRANCH1_EVENTS_STAGE_PATH, index=False, encoding='utf-8')

    def _apply_sequence_signals(self, df: pd.DataFrame,
                                history_df: pd.DataFrame = None) -> pd.DataFrame:
        """Wendet die Sequenz-Signale (contrarian, accumulative) an.

        Gruppiert Trades nach (bio_guide_id, issuer_ticker) und evaluiert
        jede Gruppe mit beiden Signal-Klassen. Die resultierenden Events
        werden in sequence_events_stage.csv geschrieben und ihre
        Signal-Typen in signal_count + signals_triggered pro Trade
        akkumuliert (additiv zu den Branch-1-Werten aus der vorhergehenden
        Stage).

        Delta-Kontext (``history_df`` gesetzt): Die Master-Historie (ohne die
        Delta-trade_ids) wird als Gruppierungs-KONTEXT mitverwendet, damit ein
        neuer Trade auch mit historischen Trades ein Paar bilden kann. Events
        und Aggregat-Updates bleiben strikt auf die Delta-Zeilen (``df``)
        beschraenkt; historische Events werden NICHT (re-)emittiert. Rueckgabe
        ist immer ``df`` (nur die Delta-Zeilen). ``history_df=None`` (Default)
        -> bit-identisches Full-Run-Verhalten.
        """
        from signals.committee_signal import _direction_from_transaction_type

        signals = [ContrarianSignal(), AccumulativeSignal()]

        df = df.copy()
        delta_mode = history_df is not None
        if delta_mode:
            delta_ids = set(df['trade_id'].astype(str))
            work = pd.concat([history_df, df], ignore_index=True, sort=False)
        else:
            work = df
        # Pre-compute _td (Timestamp) und _dir (buy/sell/other) einmalig
        # global auf dem Gruppierungs-Frame — pro-Gruppen-Conversion in
        # evaluate_group ist sonst der dominante Kostenpunkt (50k+ Gruppen).
        work['_td'] = pd.to_datetime(work['transaction_date'], errors='coerce')
        work['_dir'] = work['transaction_type'].map(_direction_from_transaction_type)
        # Gruppierungs-Key = issuer_ticker (share_class_figi -> ticker_base ->
        # ticker), in _stage_tickeropenfigi vektorisiert befuellt. Im
        # Defensiv-Fallback (Stage-Single-Run ohne tickeropenfigi) rekonstruieren
        # wir die Kette inline.
        if 'issuer_ticker' in work.columns:
            work['_grp_ticker'] = work['issuer_ticker'].fillna('').astype(str).str.strip()
        else:
            t_str = work['ticker'].fillna('').astype(str).str.strip()
            if 'ticker_base' in work.columns:
                tb_str = work['ticker_base'].fillna('').astype(str).str.strip()
                work['_grp_ticker'] = tb_str.where(tb_str != '', t_str)
            else:
                work['_grp_ticker'] = t_str
        work['_grp_member'] = work['bio_guide_id'].fillna('').astype(str)

        # signal_count / signals_triggered sicher initialisieren (auf den
        # Ziel-Zeilen df — nur diese bekommen Aggregate zurueckgeschrieben).
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
        mask = (work['_grp_member'] != '') & (work['_grp_ticker'] != '')
        candidate_df = work[mask]
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
                    # Delta-Kontext: nur Events fuer Delta-Trades emittieren.
                    if delta_mode and str(tid) not in delta_ids:
                        continue
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

        # Cleanup interner Gruppierungs-/Hilfsspalten. Im Delta-Modus liegen
        # die Hilfsspalten nur auf `work` (nicht auf `df`) -> nichts zu droppen.
        if not delta_mode:
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

    def _apply_swarm_signals(self, df: pd.DataFrame,
                             history_df: pd.DataFrame = None) -> pd.DataFrame:
        """Wendet die Schwarm-Signale an (swarm_buy/sell/conflict/paradox).

        Gruppiert Trades nach `settings.SWARM_GROUPING_KEY` (=issuer_group_key)
        und evaluiert jede Gruppe mit allen 4 Signal-Klassen. Conflict und
        Paradox sind mutually exclusive pro Schwarm-Auslösung (genau 1
        Gegen-Trade -> conflict; >= PARADOX_MIN_CONFLICTS -> paradox).

        Die signal_count + signals_triggered-Spalten werden additiv zu den
        Werten aus Branch-1 + Sequenz erweitert (pro Trade Dedup).
        SKIP-Buckets (issuer_group_key startet mit "SKIP:") werden im
        Signal-Code uebersprungen.

        Delta-Kontext (``history_df`` gesetzt): Die Master-Historie (ohne die
        Delta-trade_ids) wird als Gruppierungs-KONTEXT mitverwendet, damit ein
        neuer Trade auch mit historischen Trades einen Schwarm bilden kann.
        Events und Aggregat-Updates bleiben strikt auf die Delta-Zeilen
        (``df``) beschraenkt; historische Events werden NICHT (re-)emittiert.
        Rueckgabe ist immer ``df`` (nur die Delta-Zeilen). ``history_df=None``
        (Default) -> bit-identisches Full-Run-Verhalten.
        """
        from signals.committee_signal import _direction_from_transaction_type

        signals = [
            SwarmBuySignal(), SwarmSellSignal(),
            SwarmConflictSignal(), SwarmParadoxSignal(),
        ]
        grouping_key = getattr(settings, 'SWARM_GROUPING_KEY', 'issuer_group_key')

        df = df.copy()
        delta_mode = history_df is not None
        if delta_mode:
            delta_ids = set(df['trade_id'].astype(str))
            work = pd.concat([history_df, df], ignore_index=True, sort=False)
        else:
            work = df
        work['_td'] = pd.to_datetime(work['transaction_date'], errors='coerce')
        work['_dir'] = work['transaction_type'].map(_direction_from_transaction_type)

        if grouping_key not in work.columns:
            # Defensive: ohne Issuer-Group-Key keine Schwarm-Auswertung.
            if not delta_mode:
                df = df.drop(columns=['_td', '_dir'])
            self._write_swarm_events([])
            print(f"      [SKIP] Spalte '{grouping_key}' fehlt -> 0 Schwarm-Events")
            return df

        work['_grp_swarm'] = work[grouping_key].fillna('').astype(str)

        # signal_count / signals_triggered defensiv initialisieren (auf den
        # Ziel-Zeilen df — nur diese bekommen Aggregate zurueckgeschrieben).
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
        mask = (work['_grp_swarm'] != '') & (~work['_grp_swarm'].str.startswith('SKIP:'))
        candidate_df = work[mask]
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
                    # Delta-Kontext: nur Events fuer Delta-Trades emittieren.
                    if delta_mode and str(tid) not in delta_ids:
                        continue
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

        # Im Delta-Modus liegen die Hilfsspalten nur auf `work` -> nichts droppen.
        if not delta_mode:
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
        for path in (BRANCH1_EVENTS_STAGE_PATH, SEQUENCE_EVENTS_STAGE_PATH,
                     SWARM_EVENTS_STAGE_PATH):
            if not os.path.isfile(path):
                continue
            df = pd.read_csv(path, low_memory=False)
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=SIGNAL_EVENT_COLUMNS)
        return pd.concat(frames, ignore_index=True)

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

        Idempotent: Paare die schon in der Datei stehen werden uebersprungen.

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
        to_append = new_df[mask]
        if to_append.empty:
            return 0

        # Spaltenreihenfolge konsistent zum Master halten.
        to_append = to_append.reindex(columns=SIGNAL_EVENT_COLUMNS)

        write_header = not os.path.isfile(SUSPICIOUS_EVENTS_PATH)
        to_append.to_csv(
            SUSPICIOUS_EVENTS_PATH,
            mode='a',
            header=write_header,
            index=False,
            encoding='utf-8',
        )
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

        # In-Flight-Override: Voll-/Delta-Pipeline reicht das gescorete df
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
    # Stage-Pipeline: Backup, IO-Helpers, State-Management, Stage-Methoden
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
            _sort_master_by_date(df).to_csv(
                MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
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
        df['figi']               = [n.figi for n in normalized]
        df['ticker_figi']        = [n.ticker_figi for n in normalized]
        df['asset_name_figi']    = [n.asset_name_figi for n in normalized]
        df['security_type_figi'] = [n.security_type_figi for n in normalized]
        df['market_sector_figi'] = [n.market_sector_figi for n in normalized]
        df['share_class_figi']   = [n.share_class_figi for n in normalized]

        # issuer_ticker (vektorisiert): share_class_figi -> ticker_base -> ticker.
        # Garantiert nicht-leer solange mindestens einer der drei Werte gesetzt
        # ist. Gleiche Fallback-Kette wie utils.issuer_ticker.resolve_issuer_ticker
        # — die df-Spalte ist die schnelle Variante fuer Gruppierung in den
        # Signal-Stages.
        scf = df['share_class_figi'].fillna('').astype(str).str.strip()
        tb  = df['ticker_base'].fillna('').astype(str).str.strip()
        tk  = df['ticker'].fillna('').astype(str).str.strip()
        df['issuer_ticker'] = scf.where(scf != '', tb.where(tb != '', tk))

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

    def _stage_issuer_group(self, delta: bool = False, dry_run: bool = False):
        """Stage: Cross-Class Issuer Mapping via SEC EDGAR CIK (P5.6).

        Setzt issuer_group_key fuer jeden Trade. Entscheidungsbaum:
          SKIP-Types     -> SKIP:<type>
          ETF/Foreign    -> TICKER:<issuer_ticker>
          EDGAR-Treffer  -> CIK:<cik>
          Fallback       -> TICKER:<ticker_base>

        Cache: data/cache/edgar_cik_map.json (TTL 7 Tage). Bei API-Failure
        wird IssuerGroupMapperUnreachableError geworfen (strikter Abbruch).
        Doku: organisation/tool_specific_md/Ticker_Architektur.md
        """
        print(f"\n[Stage: issuer_group] Cross-Class Issuer Mapping (EDGAR CIK)...")
        df = self._read_stage_input('issuer_group', delta, dry_run)
        self._check_stage_inputs('issuer_group', df)

        mapper = IssuerGroupMapper()
        df = df.copy()
        df['issuer_group_key'] = df.apply(mapper.resolve_issuer_group_key, axis=1)

        # Coverage-Report (CIK/TICKER/SKIP-Verteilung).
        prefix = df['issuer_group_key'].str.split(':').str[0]
        counts = prefix.value_counts().to_dict()
        cik_n     = counts.get('CIK', 0)
        ticker_n  = counts.get('TICKER', 0)
        skip_n    = counts.get('SKIP', 0)
        total     = len(df)
        if total:
            print(f"      CIK:    {cik_n:>6,}  ({cik_n/total:6.1%})")
            print(f"      TICKER: {ticker_n:>6,}  ({ticker_n/total:6.1%})  (ETF + Foreign + Fallback)")
            print(f"      SKIP:   {skip_n:>6,}  ({skip_n/total:6.1%})  (Option/Bond/Cash/etc.)")

        self._write_stage_output(df, 'issuer_group', delta, dry_run)

    def _stage_tickeryfinance(self, delta: bool = False, dry_run: bool = False):
        """Stage: yfinance Sector/Industry-Anreicherung."""
        print(f"\n[Stage: tickeryfinance] yfinance Sector/Industry...")
        df = self._read_stage_input('tickeryfinance', delta, dry_run)
        self._check_stage_inputs('tickeryfinance', df)

        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)
        enricher = TickerSectorEnricher(
            cache_dir=mappings_dir,
            delisted_output_path=DELISTED_STAGE_PATH,
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

    def _compute_recent_paper_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Recent-Monitoring Papier-Return ueber den GANZEN uebergebenen Frame.

        Setzt ``paper_return_pct`` + ``price_as_of`` (Mark-to-Market gegen den
        juengsten Close) fuer Trades im RECENT_MONITORING_WINDOW_DAYS-Fenster.
        Muss IMMER ueber den vollen Master laufen (delta-unabhaengig), weil der
        aktuelle Kurs auch fuer laengst importierte offene Positionen taeglich
        wechselt.
        """
        cache_path = os.path.join(BASE_DIR, settings.POST_TRADE_PRICE_CACHE)
        return compute_paper_returns(
            df,
            cache_path=cache_path,
            recent_window_days=settings.RECENT_MONITORING_WINDOW_DAYS,
            max_price_age_bdays=settings.RECENT_PRICE_MAX_AGE_BUSINESS_DAYS,
            fetch_years=settings.POST_TRADE_PRICE_FETCH_YEARS,
            cache_flush_every=settings.POST_TRADE_CACHE_FLUSH_EVERY,
        )

    def _stage_post_trade_performance(self, delta: bool = False, dry_run: bool = False):
        """Stage: Post-Trade-Preise + Exit-Detection (Modul 9.x)."""
        print(f"\n[Stage: post_trade_performance] Post-Trade-Preise + Exit-Detection...")
        df = self._read_stage_input('post_trade_performance', delta, dry_run)
        self._check_stage_inputs('post_trade_performance', df)
        cache_path = os.path.join(BASE_DIR, settings.POST_TRADE_PRICE_CACHE)
        df = compute_post_trade_returns(
            df,
            horizons_days=settings.POST_TRADE_HORIZONS_DAYS,
            cache_path=cache_path,
            fetch_years=settings.POST_TRADE_PRICE_FETCH_YEARS,
            cache_ttl_days=settings.POST_TRADE_PRICE_CACHE_TTL_DAYS,
            refresh_window_days=settings.POST_TRADE_REFRESH_WINDOW_DAYS,
            exit_amount_tolerance=settings.POST_TRADE_EXIT_AMOUNT_TOLERANCE,
            cache_flush_every=settings.POST_TRADE_CACHE_FLUSH_EVERY,
            progress_every=settings.POST_TRADE_PROGRESS_EVERY,
            exit_window_days=settings.EXIT_DETECTION_WINDOW_DAYS,
        )
        # Recent-Monitoring Papier-Return (Voll-Pass; im Nicht-Delta-Single-Stage
        # ist df der komplette Master).
        df = self._compute_recent_paper_returns(df)
        self._write_stage_output(df, 'post_trade_performance', delta, dry_run)

    def _apply_abnormal_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Berechnet die marktbereinigten Abnormal Returns (Konstrukt A) und
        merged NUR ``signed_ar_{H}d`` in df. Outlier-Flag/Perzentil-Rang werden
        bewusst NICHT persistiert (schwellen-/populationsabhaengig -> in der App
        bei der Anzeige abgeleitet); Konstrukt B (disclosure) bleibt dem
        Standalone-Report (scripts/analyse_event_study.py) vorbehalten.

        signed_ar_{H}d ist pro Trade deterministisch (reine Cache-Lookups) ->
        delta-sicher.
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

    def _stage_abnormal_returns(self, delta: bool = False, dry_run: bool = False):
        """Stage: signierter, marktbereinigter Abnormal Return (Konstrukt A).

        DESKRIPTIV — kein Scoring-Input. Schreibt signed_ar_{H}d in den Master.
        """
        print(f"\n[Stage: abnormal_returns] Abnormal Return (marktbereinigt vs. "
              f"{settings.AR_BENCHMARK_TICKER})...")
        df = self._read_stage_input('abnormal_returns', delta, dry_run)
        self._check_stage_inputs('abnormal_returns', df)
        df = self._apply_abnormal_returns(df)
        self._write_stage_output(df, 'abnormal_returns', delta, dry_run)

    def _stage_trade_volume(self, delta: bool = False, dry_run: bool = False):
        """Stage: berechnet zwei Volumen-Spalten aus der Disclosure-Amount-Range.

        - ``trade_volume_usd``: Mittelpunkt der Range ('$1,001 - $15,000'
          -> 8000). Wird fuer ``asset_tradingvolume_usd``-Aggregation und
          fuer ``total_volume_usd`` in den View-CSVs gebraucht.
        - ``trade_amount_max_usd``: Obergrenze der Range ('$1,001 - $15,000'
          -> 15000). Score-Basis fuer das ``trade_volume``-Signal.
        """
        print(f"\n[Stage: trade_volume] Trade-Volumen aus Amount-Range...")
        df = self._read_stage_input('trade_volume', delta, dry_run)
        self._check_stage_inputs('trade_volume', df)
        df = df.copy()
        df['trade_volume_usd'] = df['amount'].map(midpoint_usd).astype('int64')
        df['trade_amount_max_usd'] = df['amount'].map(max_usd).astype('int64')

        n_zero    = int((df['trade_volume_usd'] == 0).sum())
        n_total   = len(df)
        total_usd = int(df['trade_volume_usd'].sum())
        print(f"      {n_total:,} Trades, {n_zero:,} mit volume_usd=0 "
              f"(leerer/unbekannter Amount-String)")
        print(f"      Gesamt-Volumen: ${total_usd:,}")
        self._write_stage_output(df, 'trade_volume', delta, dry_run)

    def _stage_asset_volume(self, delta: bool = False, dry_run: bool = False):
        """Stage: Cross-Class Asset-Tradingvolume retroaktiv ueber
        SCORING_VOLUME_WINDOW_DAYS_Z Tage, gruppiert nach issuer_group_key
        (Modul 8, Phase 8.2).

        Im Delta-Mode wird ``trades_master_complete.csv`` als historischer
        Kontext mitgeladen, das Volumen ueber die Union berechnet und
        anschliessend nur die Delta-Zeilen zurueckgeschrieben (Option A).
        """
        z = settings.SCORING_VOLUME_WINDOW_DAYS_Z
        print(f"\n[Stage: asset_volume] Asset-Tradingvolume "
              f"({z}d, Cross-Class via issuer_group_key)...")
        df = self._read_stage_input('asset_volume', delta, dry_run)
        self._check_stage_inputs('asset_volume', df)
        df = df.copy()

        if delta and os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            history = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
            if 'transaction_date' in history.columns:
                history['transaction_date'] = pd.to_datetime(
                    history['transaction_date'], errors='coerce')
            # Nur Spalten halten, die compute_asset_tradingvolume braucht.
            required = ['trade_id', 'trade_volume_usd', 'issuer_group_key',
                        'transaction_date']
            history = history[[c for c in required if c in history.columns]].copy()
            stage_ids = set(df.get('trade_id', pd.Series(dtype=str)).astype(str))
            if 'trade_id' in history.columns:
                history = history[~history['trade_id'].astype(str).isin(stage_ids)]
            combined = pd.concat([history, df], ignore_index=True, sort=False)
            print(f"      Delta-Mode: {len(history):,} historische + "
                  f"{len(df):,} Delta-Trades -> Union {len(combined):,}")
            combined_vol = compute_asset_tradingvolume(combined, z)
            combined['asset_tradingvolume_usd'] = combined_vol.values
            # Slice zurueck: nur die letzten len(df) Zeilen sind Delta.
            df['asset_tradingvolume_usd'] = combined_vol.iloc[len(history):].values \
                .astype('int64')
        else:
            df['asset_tradingvolume_usd'] = compute_asset_tradingvolume(df, z) \
                .astype('int64')

        n_zero = int((df['asset_tradingvolume_usd'] == 0).sum())
        p95    = int(df['asset_tradingvolume_usd'].quantile(0.95))
        p_max  = int(df['asset_tradingvolume_usd'].max())
        print(f"      {len(df):,} Trades, {n_zero:,} mit asset_vol=0 (SKIP/Edge)")
        print(f"      p95 = ${p95:,}, max = ${p_max:,}")
        self._write_stage_output(df, 'asset_volume', delta, dry_run)

    def _stage_gates_tags(self, delta: bool = False, dry_run: bool = False):
        """Stage: wendet alle Pipeline-internen Gates und Tags an
        (Asset-Class-Gate, Committee-Tag, Vote-Timing-Tag) plus Match-Scoring."""
        print(f"\n[Stage: gates_tags] Gates + Tags + Scoring-Spalten berechnen...")
        df = self._read_stage_input('gates_tags', delta, dry_run)
        self._check_stage_inputs('gates_tags', df)
        df = self._apply_gates_and_tags(df)
        self._write_stage_output(df, 'gates_tags', delta, dry_run)

    def _stage_branch1_signals(self, delta: bool = False, dry_run: bool = False):
        """Stage: wendet die Branch-1 Per-Trade-Signale an (committee,
        committee_ex_officio, floor_action, extreme_report). Schreibt
        signal_count + signals_triggered pro Trade in den Stage-CSV und
        sammelt die Auslöse-Events in branch1_events_stage.csv (fuer P4)."""
        print(f"\n[Stage: branch1_signals] Branch-1 Signale auswerten...")
        df = self._read_stage_input('branch1_signals', delta, dry_run)
        self._check_stage_inputs('branch1_signals', df)
        df = self._apply_branch1_signals(df)
        self._write_stage_output(df, 'branch1_signals', delta, dry_run)

    def _load_signal_history(self, df: pd.DataFrame):
        """Master-Historie (volle Zeilen ohne die df-trade_ids) als
        Gruppierungs-Kontext fuer die Sequenz-/Schwarm-Signale im Einzelstage-
        Delta-Modus. Gibt None zurueck, wenn kein Master existiert."""
        if not os.path.isfile(MASTER_COMPLETE_CSV_PATH):
            return None
        history = pd.read_csv(MASTER_COMPLETE_CSV_PATH, low_memory=False)
        for col in ['transaction_date', 'disclosure_date']:
            if col in history.columns:
                history[col] = pd.to_datetime(history[col], errors='coerce')
        stage_ids = set(df.get('trade_id', pd.Series(dtype=str)).astype(str))
        if 'trade_id' in history.columns:
            history = history[~history['trade_id'].astype(str).isin(stage_ids)]
        return history

    def _stage_sequence_signals(self, delta: bool = False, dry_run: bool = False):
        """Stage: wendet die Sequenz-Signale an (contrarian, accumulative).
        Aggregiert signal_count + signals_triggered additiv zu den
        Branch-1-Werten und schreibt die Events in sequence_events_stage.csv.

        Im Delta-Modus wird die Master-Historie als Gruppierungs-Kontext
        mitgeladen (analog run_delta), damit Delta-Trades mit historischen
        Trades ein Paar bilden koennen; Events/Aggregate bleiben Delta-only."""
        print(f"\n[Stage: sequence_signals] Sequenz-Signale auswerten...")
        df = self._read_stage_input('sequence_signals', delta, dry_run)
        self._check_stage_inputs('sequence_signals', df)
        history = self._load_signal_history(df) if delta else None
        df = self._apply_sequence_signals(df, history_df=history)
        self._write_stage_output(df, 'sequence_signals', delta, dry_run)

    def _stage_swarm_signals(self, delta: bool = False, dry_run: bool = False):
        """Stage: wendet die Schwarm-Signale an (swarm_buy/sell/conflict/paradox).
        Gruppiert nach issuer_group_key (Issuer-Level, CIK-basiert) und
        schreibt die Events in swarm_events_stage.csv. P6."""
        print(f"\n[Stage: swarm_signals] Schwarm-Signale auswerten...")
        df = self._read_stage_input('swarm_signals', delta, dry_run)
        self._check_stage_inputs('swarm_signals', df)
        history = self._load_signal_history(df) if delta else None
        df = self._apply_swarm_signals(df, history_df=history)
        self._write_stage_output(df, 'swarm_signals', delta, dry_run)

    def _stage_scoring(self, delta: bool = False, dry_run: bool = False):
        """Stage: berechnet trade_score, trade_score_breakdown, trade_warnings
        pro Trade auf Basis von signals_triggered, ex_officio_role_keys und
        trade_amount_max_usd. Modul 8.3."""
        print(f"\n[Stage: scoring] Trade-Score berechnen (Modul 8.3)...")
        df = self._read_stage_input('scoring', delta, dry_run)
        self._check_stage_inputs('scoring', df)
        scored = compute_trade_scores(df, settings)
        df = df.copy()
        df['trade_score'] = scored['trade_score'].values
        df['trade_score_breakdown'] = scored['trade_score_breakdown'].values
        df['trade_warnings'] = scored['trade_warnings'].values
        n_positive = int((df['trade_score'] > 0).sum())
        n_paradox = int((df['trade_warnings'] == settings.SCORING_PARADOX_WARNING_TAG).sum())
        print(f"  Trades mit trade_score > 0: {n_positive:,}")
        print(f"  Trades mit paradox-Warning: {n_paradox:,}")
        self._write_stage_output(df, 'scoring', delta, dry_run)

    def _stage_suspicious_export(self, delta: bool = False, dry_run: bool = False):
        """Stage: promotet Stage-Events nach suspicious_events.csv
        (append-only mit (signal_event_id, trade_id)-Dedup), materialisiert
        suspicious_assets.csv (Y-Tage-Verfall) und suspicious_members.csv
        (dauerhaft).

        Mutiert trades_stage.csv nicht — signal_count/signals_triggered sind
        bereits aus P2/P3 in der Stage-Datei und werden vom unveraenderten
        _stage_save uebernommen.

        dry_run=True: Counts werden gemeldet, aber Live-Dateien werden NICHT
        geschrieben (analog zur Stage-CSV-Konvention).
        """
        print(f"\n[Stage: suspicious_export] Promote Events + materialisiere Views...")

        # P6: Schema-Migration der suspicious_events.csv, falls noch im
        # alten 20-Spalten-Schema (idempotent: no-op wenn bereits aktuell).
        self._migrate_suspicious_events_schema()

        new_df = self._load_event_stages()
        if new_df.empty:
            print("      0 Events in Stage-Dateien — keine Aktion.")
            return

        if dry_run:
            # Vorschau: Anzahl neuer Events, die in Live-Lauf appended wuerden.
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
            n_new = sum(1 for k in new_keys if k not in existing_keys)
            print(f"      [dry_run] {n_new:,} neue Events wuerden nach "
                  f"{SUSPICIOUS_EVENTS_PATH} appended")
            print(f"      [dry_run] {len(new_df):,} Stage-Events gesamt; "
                  f"Live-Dateien bleiben unangetastet")
            return

        n_appended = self._append_suspicious_events(new_df)
        n_assets = self._materialize_suspicious_assets()
        n_members = self._materialize_suspicious_members()

        print(f"      {n_appended:,} neue Events -> {SUSPICIOUS_EVENTS_PATH}")
        print(f"      {n_assets:,} aktive Assets "
              f"(<= {settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS}d) "
              f"-> {SUSPICIOUS_ASSETS_PATH}")
        print(f"      {n_members:,} Members (gesamt) -> {SUSPICIOUS_MEMBERS_PATH}")

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

        # Recent-Monitoring Papier-Return: Voll-Pass ueber den ganzen Master
        # (delta-unabhaengig, aktueller Kurs wechselt taeglich).
        print("\n[Recent-Monitoring] Papier-Return aktualisieren...")
        df = self._compute_recent_paper_returns(df)

        # Master durchgehend absteigend nach transaction_date sortieren —
        # Complete- und Curated-Master erben dieselbe Reihenfolge.
        df = _sort_master_by_date(df)

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
        17-Stages-Pipeline (analog Full-Rebuild).

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Delta Import")
        print("=" * 60)

        # 1. Laden + Umbenennen + trade_id
        print("\n[1/17] Lade Quiver-Trades...")
        input_df = self.load_and_rename()
        print(f"      Gesamt: {len(input_df)} Trades in Quiver-Datei")

        # 2. Datum normalisieren
        print("\n[2/17] Normalisiere Datumsspalten...")
        input_df = self.normalize_dates(input_df)
        input_df['trade_id'] = compute_trade_ids(input_df)
        n_fallback = self.stats.get('date_fallback', 0)
        n_future = self.stats.get('date_future_fallback', 0)
        if n_fallback > 0:
            print(f"      Fallback: {n_fallback} fehlende transaction_dates gefuellt")
        if n_future > 0:
            print(f"      Fallback: {n_future} future-dates korrigiert")

        # 3. Master laden + Delta erkennen via trade_id
        print("\n[3/17] Lade Master + erkenne neue Trades...")
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
        print(f"\n[4/17] Reichere {len(new_trades):,} neue Trades an...")
        enriched_df, unmatched_df = self.enrich_member_data(new_trades)
        print(f"      {self.stats['matched']} gematcht, {self.stats['unmatched']} unmatched")

        # 5. Ticker-Klassifikation (offline)
        print("\n[5/17] Ticker-Klassifikation (offline)...")
        classifications = [
            classify(row.get('ticker', ''), row.get('asset_name', '') or '')
            for _, row in enriched_df.iterrows()
        ]
        enriched_df = enriched_df.copy()
        enriched_df['ticker_type']               = [c.ticker_type.value for c in classifications]
        enriched_df['ticker_base']               = [c.ticker_base for c in classifications]
        enriched_df['classification_reason']     = [c.classification_reason for c in classifications]
        enriched_df['classification_confidence'] = [c.confidence.value for c in classifications]
        enriched_df['exch_code']                 = [c.exch_code for c in classifications]
        enriched_df['series_letter']             = [c.series_letter for c in classifications]
        enriched_df['status_suffix']             = [c.status_suffix for c in classifications]
        enriched_df['manual_review_flag']        = [c.manual_review for c in classifications]
        enriched_df = self._apply_ignore_list(enriched_df)

        # 6. OpenFIGI-Normalisierung
        print("\n[6/17] OpenFIGI-Normalisierung...")
        cache_db = os.path.join(BASE_DIR, settings.DATA_CACHE_PATH,
                                settings.TICKER_TYPE_CACHE_DB)
        normalizer = TickerNormalizer(cache_db_path=cache_db,
                                       api_key=settings.OPENFIGI_API_KEY)
        normalized = normalizer.normalize_batch(classifications)
        enriched_df['figi']             = [n.figi for n in normalized]
        enriched_df['ticker_figi'] = [n.ticker_figi for n in normalized]
        enriched_df['asset_name_figi']   = [n.asset_name_figi for n in normalized]
        enriched_df['security_type_figi']    = [n.security_type_figi for n in normalized]
        enriched_df['market_sector_figi']    = [n.market_sector_figi for n in normalized]
        enriched_df['share_class_figi']  = [n.share_class_figi for n in normalized]
        # Type-Korrektur in-place
        indices = enriched_df.index.tolist()
        for pos, idx in enumerate(indices):
            n = normalized[pos]
            if n.classification.ticker_type.value != enriched_df.at[idx, 'ticker_type']:
                enriched_df.at[idx, 'ticker_type']           = n.classification.ticker_type.value
                enriched_df.at[idx, 'classification_reason'] = n.classification.classification_reason
        # issuer_ticker vektorisiert (P5.5): share_class_figi -> ticker_base -> ticker
        scf = enriched_df['share_class_figi'].fillna('').astype(str).str.strip()
        tb  = enriched_df['ticker_base'].fillna('').astype(str).str.strip()
        tk  = enriched_df['ticker'].fillna('').astype(str).str.strip()
        enriched_df['issuer_ticker'] = scf.where(scf != '', tb.where(tb != '', tk))

        # 7. Cross-Class Issuer Mapping via SEC EDGAR CIK (P5.6)
        print("\n[7/17] Cross-Class Issuer Mapping (EDGAR CIK)...")
        issuer_mapper = IssuerGroupMapper()
        enriched_df['issuer_group_key'] = enriched_df.apply(
            issuer_mapper.resolve_issuer_group_key, axis=1)

        # 8. yfinance Sector/Industry
        print("\n[8/17] yfinance Sector/Industry-Anreicherung...")
        mappings_dir = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH)
        enricher = TickerSectorEnricher(
            cache_dir=mappings_dir,
            delisted_output_path=DELISTED_STAGE_PATH,
        )
        transaction_dates = enriched_df['transaction_date'].tolist() if 'transaction_date' in enriched_df.columns else None
        sector_infos = enricher.enrich_batch(normalized, transaction_dates=transaction_dates)
        enriched_df['sector_yf']   = [s.sector_yf for s in sector_infos]
        enriched_df['industry_yf'] = [s.industry_yf for s in sector_infos]
        enriched_df['quote_type_yf']      = [s.quote_type_yf for s in sector_infos]

        # 9. Trade-Volume (Mittelpunkt + Max der Amount-Range)
        # MUSS vor post_trade_performance laufen, da post_trade_performance
        # trade_amount_max_usd als Input erwartet.
        print("\n[9/17] Trade-Volume aus Amount-Range berechnen...")
        enriched_df['trade_volume_usd'] = enriched_df['amount'].map(midpoint_usd).astype('int64')
        enriched_df['trade_amount_max_usd'] = enriched_df['amount'].map(max_usd).astype('int64')

        # 10. Post-Trade-Preise + Exit-Detection (Modul 9.x).
        # Union aus Master-Historie + Delta, dann Slice zurueck (Pattern: asset_volume).
        print("\n[10/17] Post-Trade-Preise + Exit-Detection...")
        ptr_history_cols = ['trade_id', 'name', 'ticker', 'issuer_group_key',
                           'transaction_date', 'transaction_type',
                           'trade_amount_max_usd', 'ticker_type']
        ptr_history = master_df[[c for c in ptr_history_cols
                                 if c in master_df.columns]].copy()
        ptr_stage_ids = set(enriched_df['trade_id'].astype(str))
        if 'trade_id' in ptr_history.columns:
            ptr_history = ptr_history[
                ~ptr_history['trade_id'].astype(str).isin(ptr_stage_ids)
            ]
        ptr_combined = pd.concat([ptr_history, enriched_df],
                                 ignore_index=True, sort=False)
        ptr_cache = os.path.join(BASE_DIR, settings.POST_TRADE_PRICE_CACHE)
        ptr_combined = compute_post_trade_returns(
            ptr_combined,
            horizons_days=settings.POST_TRADE_HORIZONS_DAYS,
            cache_path=ptr_cache,
            fetch_years=settings.POST_TRADE_PRICE_FETCH_YEARS,
            cache_ttl_days=settings.POST_TRADE_PRICE_CACHE_TTL_DAYS,
            refresh_window_days=settings.POST_TRADE_REFRESH_WINDOW_DAYS,
            exit_amount_tolerance=settings.POST_TRADE_EXIT_AMOUNT_TOLERANCE,
            cache_flush_every=settings.POST_TRADE_CACHE_FLUSH_EVERY,
            progress_every=settings.POST_TRADE_PROGRESS_EVERY,
            exit_window_days=settings.EXIT_DETECTION_WINDOW_DAYS,
        )
        # Slice zurueck: nur die Delta-Zeilen.
        ptr_out_cols = [
            'post_trade_baseline_price',
            'post_price_30d', 'post_price_60d',
            'post_price_90d', 'post_price_120d', 'post_price_180d',
            'post_return_30d', 'post_return_60d',
            'post_return_90d', 'post_return_120d', 'post_return_180d',
            'exit_status', 'exit_date', 'exit_price',
            'exit_amount_max_usd', 'effective_return_pct',
            'effective_return_horizon_days', 'opposing_trade_ids',
            'first_close_offset_days',
        ]
        for col in ptr_out_cols:
            if col in ptr_combined.columns:
                enriched_df[col] = ptr_combined[col].iloc[len(ptr_history):].values
        print(f"      {len(ptr_history):,} historische + "
              f"{len(enriched_df):,} Delta-Trades")

        # 10b. Abnormal Return fuer die Delta-Trades (per-Trade deterministisch,
        # kein historischer Kontext noetig) — deskriptiv, kein Scoring-Input.
        print("\n[10b/17] Abnormal Return (marktbereinigt vs. SPY)...")
        enriched_df = self._apply_abnormal_returns(enriched_df)

        # 11. Asset-Tradingvolume (Cross-Class rolling-window, Modul 8.2).
        # Im Delta-Mode wird Master-Historie fuer das Fenster-Backfill genutzt.
        z = settings.SCORING_VOLUME_WINDOW_DAYS_Z
        print(f"\n[11/17] Asset-Tradingvolume ({z}d, Cross-Class)...")
        history_cols = ['trade_id', 'trade_volume_usd', 'issuer_group_key',
                        'transaction_date']
        history = master_df[[c for c in history_cols if c in master_df.columns]].copy()
        stage_ids = set(enriched_df['trade_id'].astype(str))
        if 'trade_id' in history.columns:
            history = history[~history['trade_id'].astype(str).isin(stage_ids)]
        combined = pd.concat([history, enriched_df], ignore_index=True, sort=False)
        combined_vol = compute_asset_tradingvolume(combined, z)
        enriched_df['asset_tradingvolume_usd'] = \
            combined_vol.iloc[len(history):].values.astype('int64')
        print(f"      {len(history):,} historische + {len(enriched_df):,} Delta-Trades")

        # 12. Filter-Logik (Scoring + Filter 0 + Filter 4)
        print("\n[12/17] Filter-Logik (Scoring, Asset-Klasse, Vote-Timing)...")
        enriched_df = self._apply_gates_and_tags(enriched_df)

        # 13. Branch-1 Signale (committee, ex_officio, floor_action, extreme_report)
        print("\n[13/17] Branch-1 Signale auswerten...")
        enriched_df = self._apply_branch1_signals(enriched_df)

        # Master-Historie als Gruppierungs-KONTEXT fuer die Sequenz-/Schwarm-
        # Signale: ein Delta-Trade kann mit historischen Trades ein Paar/einen
        # Schwarm bilden. Volle Master-Zeilen (alle Snapshot-Spalten), ohne die
        # Delta-trade_ids. Events + Aggregate bleiben auf die Delta-Zeilen.
        sig_history = master_df.copy()
        _sig_delta_ids = set(enriched_df['trade_id'].astype(str))
        if 'trade_id' in sig_history.columns:
            sig_history = sig_history[
                ~sig_history['trade_id'].astype(str).isin(_sig_delta_ids)
            ]

        # 14. Sequenz-Signale (contrarian, accumulative)
        print("\n[14/17] Sequenz-Signale auswerten...")
        enriched_df = self._apply_sequence_signals(
            enriched_df, history_df=sig_history)

        # 15. Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6)
        print("\n[15/17] Schwarm-Signale auswerten...")
        enriched_df = self._apply_swarm_signals(
            enriched_df, history_df=sig_history)

        # 16. Scoring (trade_score + breakdown + warnings, Modul 8.3)
        print("\n[16/17] Trade-Score berechnen (Modul 8.3)...")
        scored = compute_trade_scores(enriched_df, settings)
        enriched_df = enriched_df.copy()
        enriched_df['trade_score'] = scored['trade_score'].values
        enriched_df['trade_score_breakdown'] = scored['trade_score_breakdown'].values
        enriched_df['trade_warnings'] = scored['trade_warnings'].values
        _n_pos = int((enriched_df['trade_score'] > 0).sum())
        _n_par = int((enriched_df['trade_warnings'] == settings.SCORING_PARADOX_WARNING_TAG).sum())
        print(f"  Trades mit trade_score > 0: {_n_pos:,}")
        print(f"  Trades mit paradox-Warning: {_n_par:,}")

        # Speichern (Master vor Suspicious-Export — Modul-8.4-Aggregate brauchen
        # vollstaendigen Master, da suspicious_events.csv auch alte trade_ids haelt).
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
            updated_master = preview_df  # fuer Suspicious-Export Override
        else:
            self._backup_master()
            updated_master = self._merge_into_master(enriched_df, master_df)
            # Recent-Monitoring Papier-Return: Voll-Pass ueber den GANZEN Master
            # (delta-unabhaengig — der aktuelle Kurs wechselt taeglich auch fuer
            # bereits laenger gehaltene offene Positionen).
            print("\n[Recent-Monitoring] Papier-Return aktualisieren...")
            updated_master = self._compute_recent_paper_returns(updated_master)
            # Master durchgehend absteigend nach transaction_date sortieren —
            # Complete- und Curated-Master erben dieselbe Reihenfolge.
            updated_master = _sort_master_by_date(updated_master)
            updated_master.to_csv(MASTER_COMPLETE_CSV_PATH, index=False, encoding='utf-8')
            curated = self._export_curated_master(updated_master)
            curated.to_csv(MASTER_CSV_PATH, index=False, encoding='utf-8')
            self._write_manual_review_subset(updated_master)
            logger.info(f"Master aktualisiert: {len(updated_master):,} Trades "
                        f"({len(enriched_df):,} neu hinzugefuegt)")
            self._promote_problem_outputs(delta=True)

        # 16. Suspicious-Export (promote Stage-Events nach suspicious_*.csv)
        print("\n[16/16] Suspicious-Export...")
        # Modul 8.4: updated_master in-memory durchreichen — File-Read waere
        # redundant, und im Dry-Run gibt es das File ohnehin nicht aktualisiert.
        score_cols = ['trade_id', 'trade_score', 'trade_volume_usd', 'trade_warnings']
        if all(c in updated_master.columns for c in score_cols):
            self._inflight_trade_scores = updated_master[score_cols]
        else:
            self._inflight_trade_scores = None
        self._trade_scores_cache = None
        try:
            self._stage_suspicious_export(delta=True, dry_run=dry_run)
        finally:
            self._inflight_trade_scores = None
            self._trade_scores_cache = None

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

        # Master durchgehend absteigend nach transaction_date sortieren —
        # Complete- und Curated-Master erben dieselbe Reihenfolge.
        enriched_df = _sort_master_by_date(enriched_df)

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
        Fuehrt den kompletten Import durch (alle 16 Stages im Speicher).

        Args:
            dry_run: Wenn True, schreibt Ergebnis in trades_stage.csv statt Master

        Returns:
            dict mit Statistiken
        """
        print("=" * 60)
        print("CONGRESS TRADE TRACKER - Import Pipeline (17 Stages)")
        print("=" * 60)

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
            fetch_years=settings.POST_TRADE_PRICE_FETCH_YEARS,
            cache_ttl_days=settings.POST_TRADE_PRICE_CACHE_TTL_DAYS,
            refresh_window_days=settings.POST_TRADE_REFRESH_WINDOW_DAYS,
            exit_amount_tolerance=settings.POST_TRADE_EXIT_AMOUNT_TOLERANCE,
            cache_flush_every=settings.POST_TRADE_CACHE_FLUSH_EVERY,
            progress_every=settings.POST_TRADE_PROGRESS_EVERY,
            exit_window_days=settings.EXIT_DETECTION_WINDOW_DAYS,
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

        # 12. Branch-1 Signale (committee, ex_officio, floor_action, extreme_report)
        print("\n[12/17] Branch-1 Signale auswerten...")
        df = self._apply_branch1_signals(df)

        # 13. Sequenz-Signale (contrarian, accumulative)
        print("\n[13/17] Sequenz-Signale auswerten...")
        df = self._apply_sequence_signals(df)

        # 14. Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6)
        print("\n[14/17] Schwarm-Signale auswerten...")
        df = self._apply_swarm_signals(df)

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

        # 16. Suspicious-Export (promote Stage-Events nach suspicious_*.csv)
        print("\n[16/17] Suspicious-Export...")
        # Modul 8.4: In-Memory-Scores durchreichen, da trades_master_complete
        # erst in Stage 16 geschrieben wird.
        self._inflight_trade_scores = df[
            ['trade_id', 'trade_score', 'trade_volume_usd', 'trade_warnings']
        ]
        self._trade_scores_cache = None  # Cache invalidieren
        try:
            self._stage_suspicious_export(delta=False, dry_run=dry_run)
        finally:
            self._inflight_trade_scores = None
            self._trade_scores_cache = None

        # 17. Speichern
        print("\n[17/17] Speichere Ergebnisse...")
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
    parser.add_argument('--stage_issuer_group', action='store_true',
                        help='Stage: Cross-Class Issuer Mapping (SEC EDGAR CIK, P5.6)')
    parser.add_argument('--stage_tickeryfinance', action='store_true',
                        help='Stage: yfinance Sector/Industry-Anreicherung')
    parser.add_argument('--stage_post_trade_performance', action='store_true',
                        help='Stage: Post-Trade-Preise + Exit-Detection (Modul 9.x)')
    parser.add_argument('--stage_abnormal_returns', action='store_true',
                        help='Stage: Abnormal Return (marktbereinigt vs. SPY, deskriptiv)')
    parser.add_argument('--stage_trade_volume', action='store_true',
                        help='Stage: trade_volume_usd aus Amount-Range berechnen (Modul 8)')
    parser.add_argument('--stage_asset_volume', action='store_true',
                        help='Stage: Cross-Class Asset-Tradingvolume rolling-window (Modul 8.2)')
    parser.add_argument('--stage_gates_tags', action='store_true',
                        help='Stage: Gates + Tags (Asset-Class-Gate, Committee-Tag, Vote-Timing-Tag) + Scoring')
    parser.add_argument('--stage_branch1_signals', action='store_true',
                        help='Stage: Branch-1 Per-Trade-Signale (committee, ex_officio, floor_action, extreme_report)')
    parser.add_argument('--stage_sequence_signals', action='store_true',
                        help='Stage: Sequenz-Signale (contrarian, accumulative)')
    parser.add_argument('--stage_swarm_signals', action='store_true',
                        help='Stage: Schwarm-Signale (swarm_buy/sell/conflict/paradox, P6)')
    parser.add_argument('--stage_scoring', action='store_true',
                        help='Stage: Trade-Score, Breakdown, Warnings (Modul 8.3)')
    parser.add_argument('--stage_suspicious_export', action='store_true',
                        help='Stage: Promote Stage-Events nach suspicious_events.csv + Views materialisieren')
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
        'issuer_group': args.stage_issuer_group,
        'tickeryfinance': args.stage_tickeryfinance,
        'trade_volume': args.stage_trade_volume,
        'post_trade_performance': args.stage_post_trade_performance,
        'abnormal_returns': args.stage_abnormal_returns,
        'asset_volume': args.stage_asset_volume,
        'gates_tags': args.stage_gates_tags,
        'branch1_signals': args.stage_branch1_signals,
        'sequence_signals': args.stage_sequence_signals,
        'swarm_signals': args.stage_swarm_signals,
        'scoring': args.stage_scoring,
        'suspicious_export': args.stage_suspicious_export,
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
