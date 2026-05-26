"""
Congress Trade Tracker - Konfiguration
Alle Parameter für Filter, Scoring und Benachrichtigungen
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Projektroot (absolut, plattformunabhängig)
BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# GATE: ASSET CLASS
# Nicht-Aktien (ETFs, Options, Bonds, Crypto etc.) aus nachfolgenden
# Tags und Scoring ausschliessen. Das Gate setzt nur eine Flag-Spalte;
# die Trades bleiben in trades_master.csv erhalten.
# Code: gates/asset_class_gate.py (AssetClassGate)
# In der User-Sprache: "Filter 0".
# =============================================================================
# ticker_type-Werte die als "handelbare Einzel-Aktie" gelten und weiter
# verarbeitet werden. Alle uebrigen ticker_type-Werte werden ausgeschlossen
# (asset_class_excluded=True).
# Werte sind kleinbuchstabige TickerType-Enums (utils/ticker_classifier.py).
# ADR/REIT/MLP/Common Stock sind im neuen Classifier alle unter 'stock' subsummiert.
ASSET_CLASS_KEEP_TYPES = {
    'stock',           # TickerType.STOCK (inkl. ADR, REIT, MLP, Common)
    'foreign_stock',   # TickerType.FOREIGN_STOCK
    'shareclass',      # TickerType.SHARECLASS (BRK-B, GOOGL etc.)
}

# =============================================================================
# TAG: COMMITTEE-SECTOR MAPPING
# Politiker handelt in Sektor, der zu seinen Ausschüssen gehört
# Code: tags/committee_tag.py (CommitteeTag)
# In der User-Sprache: "Filter 3".
# =============================================================================
# CommitteeTag: produziert Relevant_Sectors (Liste von Insider-Eintraegen, die
# den Trade thematisch beruehren). Pro Eintrag gibt es ein Feld ``match_type``:
#   'full'   — Insider-Eintrag deckt sowohl Sector als auch Industry des Assets
#   'sector' — Insider-Eintrag deckt nur den Sector (Industry weicht ab)
# Industry-only existiert per Hierarchie nicht (Industry impliziert Sector).
# Welche match_types in der Liste landen, steuert RELEVANT_SECTORS_MATCH_MODE:
#   'sector_or_industry'  — full + sector (Halb- und Voll-Treffer)
#   'sector_and_industry' — nur full (strikt)
#   'hybrid'              — full bevorzugt, sector als Fallback wenn kein full
COMMITTEE_EX_OFFICIO_ROLES = ['Chairman', 'Ranking Member', 'Vice Chairman', 'Cochairman', 'EXOfficio']
RELEVANT_SECTORS_MATCH_MODE = 'sector_and_industry'

# =============================================================================
# TAG: VOTE TIMING (Congress.gov API)
# Trade kurz vor/nach relevanter Abstimmung
# Code: tags/vote_timing_tag.py (VoteTimingTag)
# In der User-Sprache: "Filter 4".
# =============================================================================
CONGRESS_API_KEY = os.getenv('CONGRESS_API_KEY', '')
VOTE_TIMING_WINDOW_BEFORE = 30     # Trade max X Tage VOR Abstimmung
VOTE_TIMING_WINDOW_AFTER  = 5      # Trade max X Tage NACH Abstimmung
VOTE_MIN_POLICY_CONFIDENCE = 0.7   # Min-Konfidenz fuer Sektor-Match
VOTE_CONGRESS_NUMBER      = 119    # Ab welchem Kongress Daten laden (119th = 2025-2027)
LEGISLATIVE_SUBJECT_MAPPING_FILE = 'legislative_subject_mapping_complete.csv'
BILL_SUBJECTS_FILE = 'bill_subjects.json'
# Auto-Refresh: bill_actions.json/bill_subjects.json werden zu Beginn der gates_tags-Stage
# per Delta-Update aktualisiert, sofern eine der Dateien aelter als TTL ist.
VOTE_DATA_TTL_HOURS       = 24     # Skip-Schwelle fuer Auto-Refresh in Stunden

# =============================================================================
# SIGNAL: COMMITTEE / COMMITTEE_FULL / COMMITTEE_EX_OFFICIO (Branch 1)
# committee:        feuert wenn Relevant_Sectors mindestens einen Eintrag hat
#                   (egal welcher match_type).
# committee_full:   feuert wenn mindestens ein Eintrag match_type='full' hat
#                   (Sector + Industry). Im Modus 'sector_and_industry' immer
#                   gleichzeitig mit committee.
# committee_ex_officio: feuert wenn committee aktiv + Member_ExOfficio truthy.
# Code: signals/committee_signal.py, committee_full_signal.py,
#       committee_ex_officio_signal.py.
# Konfiguration: RELEVANT_SECTORS_MATCH_MODE (oben), COMMITTEE_EX_OFFICIO_ROLES.
# Score-Gewichte siehe SCORING_WEIGHTS weiter unten.
# =============================================================================

# =============================================================================
# SIGNAL: FLOOR ACTION (Branch 1)
# Trade triggert wenn Vote_Timing_Hit_Count >= Schwelle (Politiker handelt
# zeitnah zu einer Abstimmung im passenden Policy-Sektor).
# Code: signals/floor_action_signal.py (FloorActionSignal)
# =============================================================================
VOTE_TIMING_HIT_THRESHOLD = 1           # Vote_Timing_Hit_Count >= X triggert

# =============================================================================
# SIGNAL: CONTRARIAN (Branch 1, Sequenz)
# Derselbe Politiker handelt entgegengerichtet (Buy <-> Sell) auf dasselbe
# Asset innerhalb des Fensters.
# Code: signals/contrarian_signal.py (ContrarianSignal)
# =============================================================================
CONTRARIAN_WINDOW_DAYS = 30

# =============================================================================
# SIGNAL: ACCUMULATIVE (Branch 1, Sequenz)
# Derselbe Politiker handelt mehrfach dasselbe Asset (gleiche Richtung) im
# Fenster.
# Code: signals/accumulative_signal.py (AccumulativeSignal)
# =============================================================================
ACCUMULATIVE_WINDOW_DAYS = 30
ACCUMULATIVE_MIN_COUNT   = 2            # Mindest-Trade-Zahl im Fenster fuer Trigger

# =============================================================================
# SIGNAL: EXTREME REPORT (Branch 1)
# Ungewoehnlich grosse Verzoegerung zwischen transaction_date und
# disclosure_date (Pflicht-Frist ist 45 Tage).
# Code: signals/extreme_report_signal.py (ExtremeReportSignal)
# =============================================================================
EXTREME_REPORT_DAYS = 60                # disclosure_date - transaction_date > X (45 = Pflicht, 60 = mit Toleranz)

# =============================================================================
# SIGNAL: SWARM (Branch 2)
# Mehrere Politiker handeln dasselbe Asset im rollenden Fenster. Buy- und
# Sell-Schwaerme werden separat getrackt; widerspruechliche Trades bekommen
# zusaetzlich ein Konflikt-Signal.
# Code: signals/swarm_buy_signal.py, swarm_sell_signal.py, swarm_conflict_signal.py
# Voraussetzung: issuer_group_key in trades_master (P5.6, CIK-basiert mit
# Cross-Class-Aufloesung GOOGL+GOOG, BRK.A+BRK.B).
# =============================================================================
SWARM_WINDOW_DAYS      = 7                       # rollendes Fenster (Tage)
SWARM_MIN_MEMBERS      = 2                       # f - Anzahl distinkter Politiker fuer Trigger
SWARM_GROUPING_KEY     = 'issuer_group_key'      # P5.6: Issuer-Level (CIK), Cross-Class
SWARM_TRACK_DIRECTIONS = ['buy', 'sell']         # welche Richtungen werden getrackt
SWARM_FLAG_CONFLICTS   = True                    # widerspruechliche Trades zusaetzlich flaggen

# Schwarm-Konflikt (P6) -- erweitertes Fenster relativ zum Schwarm
# Gegen-Trade muss innerhalb [first_swarm_trade - BEFORE, last_swarm_trade + AFTER]
# liegen, damit er als Konflikt zaehlt. Erfasst Front-Running (BEFORE) und
# Nach-Reaktion (AFTER) asymmetrisch konfigurierbar.
# Selbst-Umkehr (Member im Schwarm dreht spaeter) zaehlt gleichbehandelt wie externer Konflikt.
SWARM_CONFLICT_WINDOW_DAYS_BEFORE = 3            # Tage vor erstem Schwarm-Trade
SWARM_CONFLICT_WINDOW_DAYS_AFTER  = 3            # Tage nach letztem Schwarm-Trade
SWARM_PARADOX_MIN_CONFLICTS       = 2            # ab N Gegen-Trades: swarm_paradox statt swarm_conflict

# =============================================================================
# SEC EDGAR (P5.6) — Cross-Class Issuer Mapping
# CIK (Central Index Key) ist der einzige stabile, regulatorisch verankerte
# Issuer-Level-Identifier. EDGAR liefert die komplette Mapping-Tabelle
# (~12.000 US-Listings) in einer einzigen JSON. Cache 7 Tage TTL; bei
# API-Failure strikter Abbruch (kein silent Fallback auf veralteten Cache).
# Code: utils/issuer_group_mapper.py
# Doku: organisation/Ticker_Architektur.md
# =============================================================================
SEC_EDGAR_USER_AGENT = 'Congress Trade Tracker <kontakt@example.com>'  # Pflicht, sonst 403
SEC_EDGAR_URL        = 'https://www.sec.gov/files/company_tickers_exchange.json'
SEC_EDGAR_CACHE_FILE = 'edgar_cik_map.json'      # unter data/cache/
SEC_EDGAR_TTL_DAYS   = 7

# =============================================================================
# SCORING (Modul 8)
# Gewichtetes Combined-Scoring pro Trade. Kein Cap — Score kann beliebig
# wachsen, vereinfacht Kalibrierung der Gewichts-Verhaeltnisse.
# Setze ein Gewicht auf 0, um das Signal aus dem Scoring zu nehmen
# (Event wird trotzdem in suspicious_events.csv gespeichert).
# Code: scoring/trade_score.py (geplant in Phase 8.3)
# =============================================================================
SCORING_WEIGHTS = {
    'committee':       10,    # Halb-Treffer (Sector matcht) + Voll-Treffer
    'committee_full':   5,    # Zusatz fuer Voll-Treffer (Sector + Industry)
    'floor_action':    15,    # Vote-Timing-Treffer (VoteTimingTag → FloorActionSignal)
    'extreme_report':   5,
    'contrarian':       8,
    'accumulative':    10,
    'swarm_buy':       20,
    'swarm_sell':      20,
    'swarm_conflict':   5,
    # 'swarm_paradox':         kein additives Gewicht — Multiplikator (s.u.)
    # 'committee_ex_officio':  kein eigenes Gewicht — Bonus X (s.u.)
}

# Ex-Officio-Bonus: einmaliger Summand pro Trade, addiert wenn
# is_ex_officio=True UND mindestens ein Signal aus SCORING_EX_OFFICIO_TRIGGERS
# getriggert hat. Wird nicht pro Signal multipliziert.
SCORING_EX_OFFICIO_BONUS    = 10
SCORING_EX_OFFICIO_TRIGGERS = ['committee', 'floor_action']

# Paradox-Daempfer: Multiplikator NUR auf die Schwarm-Komponente
# (swarm_buy + swarm_sell + swarm_conflict). 0.0 = Schwarm-Anteil ausgeknockt;
# 0.5 = halbiert; 1.0 = keine Daempfung. Warning-Tag bleibt unabhaengig sichtbar
# in der trade_warnings-Spalte, damit das Gegenindiz visualisierbar ist.
SCORING_PARADOX_DAMPER       = 0.0
SCORING_PARADOX_WARNING_TAG  = 'paradox_swarm'

# Volume-Score: nichtlinear ab Schwelle U.
#   volume_part = max(0, asset_tradingvolume_usd - U) * SLOPE
# Beispiele (Default U=100k, SLOPE=0.00002):
#   - 100k$:        0 Punkte (Schwelle exakt)
#   - 1M$:          (1_000_000 - 100_000) * 0.00002 = 18 Punkte
#   - 10M$:         (10_000_000 - 100_000) * 0.00002 ≈ 198 Punkte
SCORING_VOLUME_ENABLED       = True
SCORING_VOLUME_THRESHOLD_U   = 100_000     # USD — unter U: 0 Punkte
SCORING_VOLUME_SLOPE         = 0.00002     # Punkte pro USD ueber U

# Asset-Tradingvolume-Aggregations-Fenster (retroaktiv pro Trade).
# Σ trade_volume_usd ueber alle Trades mit gleichem issuer_group_key im Fenster
# [transaction_date - Z, transaction_date]. Cross-Class via issuer_group_key.
# SKIP-Bucket-Trades werden ausgeschlossen (Aggregat = 0).
SCORING_VOLUME_WINDOW_DAYS_Z = 90

# =============================================================================
# SUSPICIOUS PERSISTENCE
# Y-Tage-Verfall fuer suspicious_assets.csv. suspicious_events.csv und
# suspicious_members.csv bleiben dauerhaft.
# =============================================================================
SUSPICIOUS_ASSET_PERSISTENCE_DAYS = 30           # Y - Asset-Verfall-Fenster (Tage)

# Dateinamen unter data/processed/. Pfade werden in scripts/import_trades.py
# aus PROCESSED_DIR zusammengesetzt.
SUSPICIOUS_EVENTS_FILE  = 'suspicious_events.csv'    # Append-only Master
SUSPICIOUS_ASSETS_FILE  = 'suspicious_assets.csv'    # View, Y-Tage-Verfall
SUSPICIOUS_MEMBERS_FILE = 'suspicious_members.csv'   # View, dauerhaft

# =============================================================================
# SCRAPER-EINSTELLUNGEN
# =============================================================================
# Rate Limiting und Retry-Logik
SCRAPER_RATE_LIMIT_SECONDS = 1.0    # Pause zwischen API-Calls
SCRAPER_RETRY_ATTEMPTS = 3          # Wiederholungsversuche bei Fehler
SCRAPER_RETRY_DELAY_SECONDS = 5.0   # Wartezeit zwischen Retries

# Multi-Run Strategie (optional, für maximale Abdeckung)
SENATE_SCRAPE_RUNS = 1                # Runs pro Aufruf (1 reicht – API ist deterministisch)
SENATE_SCRAPE_RUN_PAUSE_SECONDS = 5   # Pause zwischen Runs (Sekunden)

# PDF Extractor (Anthropic Vision)
PDF_EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"  # Haiku für Kosteneffizienz
PDF_EXTRACTOR_PAGE_PAUSE_SECONDS = 2.0              # Pause zwischen Seiten-API-Calls
PDF_EXTRACTOR_DPI = 300                              # Bildaufloesung (v2: 300, v1: 200)
PDF_EXTRACTOR_AMOUNT_PIXEL_THRESHOLD = 50            # Min. dunkle Pixel fuer "x"-Erkennung
PDF_EXTRACTOR_USE_GRID_DETECTION = True              # img2table Grid-Erkennung aktivieren
PDF_EXTRACTOR_ROW_BATCH_SIZE = 5                     # Zeilen pro Vision API Batch

# PDF Extractor (Tesseract – lokales OCR, kostenlos)
_TESSERACT_DEFAULT = (
    r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    if sys.platform == 'win32' else
    '/usr/bin/tesseract'
)
PDF_EXTRACTOR_TESSERACT_PATH = os.getenv('TESSERACT_PATH', _TESSERACT_DEFAULT)
PDF_EXTRACTOR_TESSERACT_PSM = 6       # Page Segmentation Mode: uniform text block
PDF_EXTRACTOR_TESSERACT_CONF_MIN = 30 # Wörter mit Confidence < 30 werden ignoriert

# Standard-Zeiträume für Datenabfragen
SCRAPER_DEFAULT_DAYS_BACK = 90      # Standard: Trades der letzten 90 Tage

# User-Agent für HTTP-Requests
SCRAPER_USER_AGENT = 'CongressTracker/1.0 (Python)'

# =============================================================================
# IMPORT-EINSTELLUNGEN
# =============================================================================
# Fuzzy-Matching Schwellenwert (0-100)
FUZZY_MATCH_THRESHOLD = 85

# Spalten fuer Duplikat-Erkennung (angepasst an Quiver-Daten: kein asset_type, owner, ptr_link)
DUPLICATE_CHECK_COLUMNS = [
    'name', 'ticker', 'asset_name', 'transaction_type',
    'amount', 'transaction_date', 'subholding', 'comment',
]

# Ticker-Industry Lookup (yfinance)
TICKER_INDUSTRY_CACHE_FILE = 'ticker_industry_cache.json'   # in DATA_MAPPINGS_PATH (nur positive)
TICKER_0_CACHE_FILE = 'ticker_0_cache.json'                  # in DATA_MAPPINGS_PATH (null-Eintraege)
TICKER_LOOKUP_PAUSE_SECONDS = 0.5    # Pause zwischen yfinance-Calls (Rate-Limit-Schutz)
TICKER_LOOKUP_RETRY_ATTEMPTS = 3     # Wiederholungsversuche bei Fehler
TICKER_LOOKUP_RETRY_DELAY = 5.0      # Wartezeit (Sekunden) zwischen Retries, verdoppelt sich
TICKER_LOOKUP_CONFIRM_THRESHOLD = 100 # Ab dieser Anzahl neuer Ticker: User-Bestaetigung
TICKER_LOOKUP_CACHE_INTERVAL = 100    # Cache alle N Ticker zwischenspeichern
TICKER_LOOKUP_NET_ERROR_PAUSE = 30    # Pause (Sek.) nach Pause-Threshold Netzwerk-Fehlern
TICKER_LOOKUP_NET_ERROR_PAUSE_THRESHOLD = 3  # Ab N consecutive Net-Errors -> Pause ausloesen
TICKER_LOOKUP_NET_ERROR_ABORT = 5     # Abbruch nach N Netzwerk-Fehlern in Folge
TICKER_SECTOR_CACHE_TTL_DAYS = 30     # TTL fuer ticker_industry_cache (positive Eintraege)

# Vorzugsaktien-Mapping (Preferred → Stammaktie)
PREFERRED_TICKER_MAPPING_FILE = 'Vorzugsaktien_mapping.csv'

# Ticker-Pattern-Konstanten (TICKER_TYPE_PATTERNS, TICKER_FILTER_PATTERNS,
# TICKER_WHITELIST) wurden im Rebuild Teil 3 entfernt — die Klassifikation
# liegt jetzt in utils/ticker_classifier.py (Regex-Entscheidungsbaum).

# OpenFIGI securityType -> ticker_type Mapping (primaer, wird zuerst geprueft)
# Wichtig: securityType "ETP" identifiziert ETFs korrekt, waehrend securityType2
# fuer ETFs faelschlicherweise "Mutual Fund" zurueckgibt.
OPENFIGI_SEC_TYPE_MAP = {
    "ETP": "ETF",
}

# OpenFIGI securityType2 -> ticker_type Mapping (sekundaer, Fallback)
OPENFIGI_TYPE_MAP = {
    "Common Stock": "Stock",
    "Open-End Fund": "Mutual Fund",
    "Closed-End Fund": "Closed-End Fund",
    "Preferred Stock": "Preferred Stock",
    "Warrant": "Warrant",
    "Right": "Rights",
    "Unit": "SPAC Unit",
    "REIT": "Stock",
    "ADR": "Stock",
}

# OpenFIGI Konfiguration
OPENFIGI_API_KEY = os.getenv('OPENFIGI_API_KEY', '')
TICKER_TYPE_CACHE_TTL_DAYS = 90
TICKER_TYPE_UNKNOWN_TTL_DAYS = 7
TICKER_TYPE_CACHE_DB = 'ticker_type_cache.db'
DATA_CACHE_PATH = 'data/cache'
OPENFIGI_BATCH_ABORT_THRESHOLD = 6  # Abbruch nach N fehlgeschlagenen Batches/429 in Folge
                                    # (war 3 — zu strikt; 6 erlaubt 2+4+8+16+32+64s ≈ 2 min Backoff)

# OpenFIGI HTTP / Tuning (utils/ticker_normalizer.py)
# WICHTIG: /v3/mapping und /v3/search haben UNTERSCHIEDLICHE Rate-Limits!
#   - /v3/mapping mit Key: 250 Requests / 6 Sekunden (~42/sec)
#   - /v3/search  mit Key:  20 Requests / 6 Sekunden (~3.3/sec)  → 12× strenger
#   - /v3/mapping ohne Key: 25 Requests / Minute
#   - /v3/search  ohne Key:  5 Requests / Minute                  → 5× strenger
OPENFIGI_API_URL = 'https://api.openfigi.com/v3/mapping'
OPENFIGI_SEARCH_URL = 'https://api.openfigi.com/v3/search'
OPENFIGI_REQUEST_TIMEOUT = 30          # HTTP-Timeout pro Call (Sekunden)
OPENFIGI_BATCH_SIZE_WITH_KEY = 100     # Max Ticker pro /v3/mapping-Call (mit API-Key)
OPENFIGI_BATCH_SIZE_WITHOUT_KEY = 10   # ohne API-Key
# Pausen pro Endpoint (separater Wert für Mapping vs. Search)
OPENFIGI_MAPPING_PAUSE_WITH_KEY = 0.3       # Sek. zwischen Mapping-Batches (mit Key)
OPENFIGI_MAPPING_PAUSE_WITHOUT_KEY = 3.5    # ohne Key (war 2.5 — zu knapp am 25/min-Limit)
OPENFIGI_SEARCH_PAUSE_WITH_KEY = 0.4        # ~150/min, 25% Puffer zum 200/min-Limit
OPENFIGI_SEARCH_PAUSE_WITHOUT_KEY = 13.0    # ohne Key: 5 req/min → 1 alle 12s
# Legacy-Aliase (rückwärtskompatibel; werden nicht mehr direkt genutzt)
OPENFIGI_REQUEST_PAUSE_WITH_KEY = OPENFIGI_MAPPING_PAUSE_WITH_KEY
OPENFIGI_REQUEST_PAUSE_WITHOUT_KEY = OPENFIGI_MAPPING_PAUSE_WITHOUT_KEY
OPENFIGI_RATE_LIMIT_BACKOFF_BASE = 2.0   # 429-Backoff Basis (2^n Sek.)
OPENFIGI_RATE_LIMIT_BACKOFF_MAX = 90.0   # 429-Backoff Cap (Sek.) — war 60

# =============================================================================
# TRADE-ID (utils/trade_id.py)
# WARNUNG: Aenderung von TRADE_ID_HASH_LENGTH bricht ALLE bestehenden trade_ids
# in trades_master.csv. Nur via bewusste Migration aendern.
# =============================================================================
TRADE_ID_HASH_LENGTH = 16              # Hex-Zeichen aus SHA256 (16 = 64 Bit Entropie)

# Strategie fuer trade_id-Duplikate innerhalb eines Imports / einer Migration:
#   'keep_all'  - alle Zeilen behalten, auch wenn trade_id identisch ist.
#                 trade_id ist dann KEIN eindeutiger Schluessel mehr im Master.
#   'last_wins' - bei gleicher trade_id nur die zuletzt erscheinende Zeile behalten
#                 (frueheres Standardverhalten).
# Delta-Imports zwischen Laeufen (detect_new_trades via isin) sind hiervon
# unberuehrt — bereits im Master vorhandene trade_ids werden weiterhin nicht
# erneut hinzugefuegt.
DEDUP_STRATEGY = 'keep_all'

# =============================================================================
# ALLGEMEINE EINSTELLUNGEN
# =============================================================================
# Politiker haben 45 Tage Zeit ihre Trades zu melden
DISCLOSURE_DELAY_DAYS = 45

# Daten-Pfade (relativ zum Projektverzeichnis)
DATA_RAW_PATH = 'data/raw'
DATA_PROCESSED_PATH = 'data/processed'
DATA_MAPPINGS_PATH = 'data/mappings'
DATA_UPLOADS_PATH = 'data/uploads'
DATA_CONGRESS_API_PATH = 'data/congress_api'
LOGS_PATH = 'logs'

# Maximales Alter der Daten bevor neu geladen wird (in Stunden)
DATA_MAX_AGE_HOURS = 24

# =============================================================================
# DATEIPFADE (bei Neuwahlen hier anpassen!)
# =============================================================================
# Aktuelle Kongress-Session
CONGRESS_SESSION = "119th"  # 2025-2027

# Mapping-Dateien (data/mappings/)
HOUSE_COMMITTEES_FILE = 'house_member&(sub)committees.csv'
SENATE_COMMITTEES_FILE = 'senate_member&(sub)committees.csv'
NAME_ALIASES_FILE = 'name_aliases.csv'
COMMITTEE_MAPPING_FILE = 'subcommittee_sector_industry_mapping.csv'
IGNORED_TICKERS_FILE = 'ignored_tickers.csv'

# Trade-Daten
QUIVER_TRADES_FILE = 'congress-trading-all.xlsx'  # Quiver-Daten (primaere Quelle, xlsx)
HOUSE_TRADES_FILE = 'house_trades.csv'            # Eigene House-Scraper-Daten (Ergaenzung)
SENATE_TRADES_FILE = 'senate_trades.csv'          # Eigene Senate-Scraper-Daten (Ergaenzung)

# Output-Dateien (Rebuild Teil 3): vollstaendige + kuratierte Master-Sicht
TRADES_MASTER_COMPLETE_CSV = 'trades_master_complete.csv'  # alle Spalten persistiert
MANUAL_REVIEW_CSV = 'manual_review_tickers.csv'            # Diagnose-Output

# Kuratierte Spalten fuer trades_master.csv (userrelevante Sicht).
CURATED_COLUMNS = [
    'trade_id',
    'name',
    'chamber',
    'Member_Party',
    'Member_State',
    'Member_ExOfficio',
    'ticker',
    'issuer_ticker',
    'issuer_group_key',
    'ticker_type',
    'asset_name',
    'asset_name_figi',
    'security_type_figi',
    'sector_yf',
    'industry_yf',
    'transaction_date',
    'transaction_type',
    'amount',
    'disclosure_date',
    'Member_Committees',
    'Insider_Sectors',
    'Relevant_Sectors',
    'asset_class_excluded',
    'Vote_Timing_Hit',
    'Vote_Timing_Hits',
    'Vote_Timing_Hit_Count',
    'signal_count',
    'signals_triggered',
    # Modul 8: Trade-Volume + Score (Phase 8.1 / 8.2 / 8.3)
    'trade_volume_usd',
    'asset_tradingvolume_usd',
    'trade_score',
    'trade_score_breakdown',
    'trade_warnings',
]

# Backup-Verzeichnis fuer automatische Master-Backups vor destruktiven Aenderungen
ARCHIVE_BACKUP_PATH = 'archive/trades_master_backup'
