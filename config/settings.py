"""
Congress Trade Tracker - Konfiguration
Alle Parameter für Filter, Scoring und Benachrichtigungen
"""

import os
from dotenv import load_dotenv

load_dotenv()

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
COMMITTEE_MAPPING_FILE = 'subcommittee_sector_industry_mapping.csv'

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

# Minimum relevance score to consider committee-relevant (0-100)
COMMITTEE_RELEVANCE_THRESHOLD = 30

# =============================================================================
# FILTER 4: TIMING VOR ABSTIMMUNG (rückwirkend)
# Trade X Tage vor relevanter Abstimmung
# =============================================================================
TIMING_DAYS_MIN = 60  # Mindestens X Tage vor Abstimmung
TIMING_DAYS_MAX = 90  # Maximal X Tage vor Abstimmung

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
# Filter 4 (Timing) und Filter 5 (Contrarian) ÜBERSPRUNGEN
# Gewichtung umverteilt auf Filter 2 & 3 (je +5%)
# =============================================================================
WEIGHTS = {
    'volume': 0.15,           # 15% - Handelsvolumen
    'swarm': 0.30,            # 30% - Schwarm-Verhalten (erhöht von 25%)
    'committee': 0.30,        # 30% - Relevanter Ausschuss (erhöht von 25%)
    'long_short': 0.15,       # 15% - Long/Short Wechsel
    'position_increase': 0.10  # 10% - Position vergrößert
}
# Filter 4 (timing) und Filter 5 (contrarian) nicht mehr in WEIGHTS enthalten

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
# Quiver Quantitative API (für Congress-Trades)
QUIVER_API_KEY = os.getenv('QUIVER_API_KEY', '')
QUIVER_API_BASE_URL = 'https://api.quiverquant.com/beta'

# Rate Limiting und Retry-Logik
SCRAPER_RATE_LIMIT_SECONDS = 1.0    # Pause zwischen API-Calls
SCRAPER_RETRY_ATTEMPTS = 3          # Wiederholungsversuche bei Fehler
SCRAPER_RETRY_DELAY_SECONDS = 5.0   # Wartezeit zwischen Retries

# Multi-Run Strategie (optional, für maximale Abdeckung)
SENATE_SCRAPE_RUNS = 1                # Runs pro Aufruf (1 reicht – API ist deterministisch)
SENATE_SCRAPE_RUN_PAUSE_SECONDS = 5   # Pause zwischen Runs (Sekunden)

# PDF Extractor (Anthropic Vision)
PDF_EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"  # Haiku für Kosteneffizienz
PDF_EXTRACTOR_PAGE_PAUSE_SECONDS = 0.5              # Pause zwischen Seiten-API-Calls

# PDF Extractor (Tesseract – lokales OCR, kostenlos)
PDF_EXTRACTOR_TESSERACT_PATH = os.getenv(
    'TESSERACT_PATH',
    r'C:\Program Files\Tesseract-OCR\tesseract.exe'
)
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
FUZZY_MATCH_THRESHOLD = 80

# Spalten fuer Duplikat-Erkennung
DUPLICATE_CHECK_COLUMNS = [
    'name', 'ticker', 'asset_name', 'asset_type', 'transaction_type',
    'amount', 'transaction_date', 'owner', 'subholding', 'comment', 'ptr_link',
]

# =============================================================================
# ALLGEMEINE EINSTELLUNGEN
# =============================================================================
# Politiker haben 45 Tage Zeit ihre Trades zu melden
DISCLOSURE_DELAY_DAYS = 45

# Daten-Pfade (relativ zum Projektverzeichnis)
DATA_RAW_PATH = 'data/raw'
DATA_PROCESSED_PATH = 'data/processed'
DATA_CONFIG_PATH = 'data/config'
DATA_UPLOADS_PATH = 'data/uploads'
LOGS_PATH = 'logs'

# Maximales Alter der Daten bevor neu geladen wird (in Stunden)
DATA_MAX_AGE_HOURS = 24

# =============================================================================
# DATEIPFADE (bei Neuwahlen hier anpassen!)
# =============================================================================
# Aktuelle Kongress-Session
CONGRESS_SESSION = "119th"  # 2025-2027

# Committee-Dateien (CSV nach PDF-Extraktion)
HOUSE_COMMITTEES_FILE = 'house_member&(sub)committees.csv'
SENATE_COMMITTEES_FILE = 'us_senate_member&(sub)committees.csv'

# Trade-Daten (Scraper-Ausgaben)
HOUSE_TRADES_FILE = 'house_trades.csv'
SENATE_TRADES_FILE = 'senate_trades.csv'
