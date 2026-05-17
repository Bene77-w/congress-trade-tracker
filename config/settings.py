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
# FILTER 0: ASSET-KLASSE
# Nicht-Aktien (ETFs, Options, Bonds, Crypto etc.) aus nachfolgenden
# Filtern und Scoring ausschliessen. Der Filter setzt nur eine Flag-Spalte;
# die Trades bleiben in trades_master.csv erhalten.
# =============================================================================
# ticker_type-Werte die als "handelbare Einzel-Aktie" gelten und weiter
# verarbeitet werden. Alle uebrigen ticker_type-Werte werden ausgeschlossen
# (asset_class_excluded=True).
# Werte sind kleinbuchstabige TickerType-Enums (utils/ticker_classifier.py).
# ADR/REIT/MLP/Common Stock sind im neuen Classifier alle unter 'stock' subsummiert.
FILTER_0_KEEP_TYPES = {
    'stock',           # TickerType.STOCK (inkl. ADR, REIT, MLP, Common)
    'foreign_stock',   # TickerType.FOREIGN_STOCK
    'shareclass',      # TickerType.SHARECLASS (BRK-B, GOOGL etc.)
}

# =============================================================================
# FILTER 1: HANDELSVOLUMEN
# Punkte basierend auf Trade-Größe
# =============================================================================
VOLUME_THRESHOLDS = {
    0: 15_000,      # < $15k = 0 Punkte
    1: 50_000,      # $15k - $50k = 1 Punkt
    2: 100_000,     # $50k - $100k = 2 Punkte
    3: 250_000,     # $100k - $250k = 3 Punkte
    4: float('inf') # > $250k = 4 Punkte
}

# =============================================================================
# FILTER 2: SCHWARM-VERHALTEN
# Mehrere Politiker kaufen dieselbe Aktie
# =============================================================================
SWARM_DAYS = 21           # Zeitraum in Tagen (nach transaction_date)
SWARM_MIN_POLITICIANS = 3  # Mindestanzahl Politiker

# =============================================================================
# FILTER 3: COMMITTEE-SECTOR MAPPING
# Politiker handelt in Sektor, der zu seinen Ausschüssen gehört
# =============================================================================
# Committee relevance scoring weights
# Sector_Rank aus subcommittee_sector_industry_mapping.csv:
# - Rank 1 = Primärer Sektor (höchste Relevanz)
# - Rank 2 = Sekundärer Sektor
# - Rank 3-4 = Tertiäre Sektoren
COMMITTEE_RANK_WEIGHTS = {
    1: 1.00,  # Rank 1 sectors: 100% weight (primär)
    2: 0.75,  # Rank 2 sectors: 75% weight (sekundär)
    3: 0.50,  # Rank 3 sectors: 50% weight (tertiär)
    4: 0.25   # Rank 4+ sectors: 25% weight (weitere)
}

COMMITTEE_CONFIDENCE_WEIGHTS = {
    'Hoch': 1.0,      # High confidence mapping
    'Mittel': 0.7,    # Medium confidence mapping
    'Niedrig': 0.4    # Low confidence mapping
}

COMMITTEE_ROLE_WEIGHTS = {
    'Chairman': 1.5,           # Ex Officio
    'Ranking Member': 1.5,     # Ex Officio
    'Vice Chairman': 1.5,      # Ex Officio (angepasst: alle Ex Officio gleich behandeln)
    'Cochairman': 1.5,         # Ex Officio
    'EXOfficio': 1.5,          # Explizit Ex Officio markiert
    '': 1.0                    # Regular member
}

# Relevance-Score-Formel (neu):
#   sector_match   = Committee-Sektor   == sector_yf
#   industry_match = Committee-Industry == industry_yf
#   raw   = (33 if sector_match) + (33 if industry_match)
#   score = round(raw * (1.5 if ex_officio else 1.0))
# Mögliche Werte: 0, 33, 50, 66, 99
COMMITTEE_SECTOR_MATCH_POINTS = 33
COMMITTEE_INDUSTRY_MATCH_POINTS = 33
COMMITTEE_EX_OFFICIO_MULTIPLIER = 1.5
COMMITTEE_EX_OFFICIO_ROLES = ['Chairman', 'Ranking Member', 'Vice Chairman', 'Cochairman', 'EXOfficio']

# Minimum relevance score to consider committee-relevant (0-100)
COMMITTEE_RELEVANCE_THRESHOLD =  1 # wird nicht im code verwebdet, da Committee-Filter als Auto-Flag (kein Score) implementiert ist

# Match-Modus fuer Relevant_Sectors (Intersection Insider × Ticker):
# 'sector_or_industry'  — Eintrag matcht, wenn sector ODER industry zum Ticker passen (Default)
# 'sector_and_industry' — Eintrag matcht nur, wenn sector UND industry passen
# 'hybrid'              — bevorzuge sector+industry-Matches, sonst Fallback auf sector_or_industry
RELEVANT_SECTORS_MATCH_MODE = 'sector_or_industry'

# =============================================================================
# FILTER 4: VOTE TIMING (Congress.gov API)
# Trade kurz vor/nach relevanter Abstimmung
# =============================================================================
CONGRESS_API_KEY = os.getenv('CONGRESS_API_KEY', '')
VOTE_TIMING_WINDOW_BEFORE = 30     # Trade max X Tage VOR Abstimmung
VOTE_TIMING_WINDOW_AFTER  = 5      # Trade max X Tage NACH Abstimmung
VOTE_MIN_POLICY_CONFIDENCE = 0.7   # Min-Konfidenz fuer Sektor-Match
VOTE_CONGRESS_NUMBER      = 119    # Ab welchem Kongress Daten laden (119th = 2025-2027)
LEGISLATIVE_SUBJECT_MAPPING_FILE = 'legislative_subject_mapping_complete.csv'
BILL_SUBJECTS_FILE = 'bill_subjects.json'
# Auto-Refresh: bill_actions.json/bill_subjects.json werden zu Beginn der filter-Stage
# per Delta-Update aktualisiert, sofern eine der Dateien aelter als TTL ist.
VOTE_DATA_TTL_HOURS       = 24     # Skip-Schwelle fuer Auto-Refresh in Stunden

# =============================================================================
# FILTER 5: CONTRARIAN
# Kauf bei gefallener / Verkauf bei gestiegener Aktie
# =============================================================================
CONTRARIAN_BUY_THRESHOLD = -0.15   # -15% in 12 Monaten = Kauf-Signal
CONTRARIAN_SELL_THRESHOLD = 0.30   # +30% in 12 Monaten = Verkauf-Signal
CONTRARIAN_LOOKBACK_MONTHS = 12    # Betrachtungszeitraum

# =============================================================================
# FILTER 6: LONG/SHORT WECHSEL
# Richtungswechsel innerhalb kurzer Zeit
# =============================================================================
LONG_SHORT_MONTHS = 6  # Zeitraum in Monaten

# =============================================================================
# FILTER 7: POSITION VERGRÖSSERT
# Wiederholtes Nachkaufen derselben Aktie
# =============================================================================
POSITION_INCREASE_MONTHS = 4  # Zeitraum in Monaten
POSITION_INCREASE_MIN_BUYS = 2  # Mindestanzahl Käufe

# =============================================================================
# GEWICHTUNGEN (Summe = 100%)
# Filter 3 (Committee) = Auto-Flag (kein Gewicht)
# Filter 5 (Contrarian) übersprungen
# =============================================================================
WEIGHTS = {
    'volume': 0.075,           # 7.5% - Handelsvolumen
    'swarm': 0.35,             # 35% - Schwarm-Verhalten
    'timing': 0.35,            # 35% - Timing vor Abstimmung
    'long_short': 0.075,       # 7.5% - Long/Short Wechsel
    'position_increase': 0.15  # 15% - Position vergrößert
}
# Filter 3 (Committee) = Auto-Flag: kein Scoring-Gewicht, Trade automatisch verdächtig
# Filter 5 (Contrarian) übersprungen

# =============================================================================
# SCORE-SCHWELLEN
# =============================================================================
SCORE_THRESHOLDS = {
    'strong': 75,      # 75-100: Starke Empfehlung
    'interesting': 50,  # 50-74: Interessant
    'watch': 30,       # 30-49: Beobachten
    'none': 0          # 0-29: Kein Signal
}

SCORE_LABELS = {
    'strong': '⭐⭐⭐ Starke Empfehlung',
    'interesting': '⭐⭐ Interessant',
    'watch': '⭐ Beobachten',
    'none': 'Kein Signal'
}

# =============================================================================
# EMAIL-BENACHRICHTIGUNG
# =============================================================================
EMAIL_ALERT_THRESHOLD = 50  # Email senden ab diesem Score

# SMTP-Konfiguration aus Umgebungsvariablen
SMTP_SERVER = os.getenv('SMTP_SERVER', '')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
EMAIL_USER = os.getenv('EMAIL_USER', '')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')
EMAIL_RECIPIENT = os.getenv('EMAIL_RECIPIENT', '')

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
# Spalten in CURATED_COALESCE werden per Fallback-Logik befuellt (erstes nicht-leeres gewinnt).
CURATED_COLUMNS = [
    'trade_id',
    'name',
    'chamber',
    'Member_Party',
    'Member_State',
    'Member_ExOfficio',
    'ticker',
    'asset_name',          # Coalesce: name_figi || asset_name
    'security_type_figi',   # Coalesce: security_type_figi || ticker_type
    'sector_yf',
    'industry_yf',
    'transaction_date',
    'transaction_type',
    'amount',
    'disclosure_date',
    'Member_Committees',
    'Insider_Sectors',
    'Relevant_Sectors',
    'Relevance_Score',
    'asset_class_excluded',
    'Vote_Timing_Hit',
    'Vote_Timing_Hits',
    'Vote_Timing_Hit_Count',
]

# Coalesce-Logik: erstes nicht-leeres Element der Liste gewinnt.
# Key = Spaltenname in trades_master.csv, Value = geordnete Quellspalten.
CURATED_COALESCE = {
    'asset_name':         ['name_figi',          'asset_name'],
    'security_type_figi': ['security_type_figi', 'ticker_type'],
}

# Backup-Verzeichnis fuer automatische Master-Backups vor destruktiven Aenderungen
ARCHIVE_BACKUP_PATH = 'archive/trades_master_backup'
