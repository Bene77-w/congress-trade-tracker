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
# committee_ex_officio: feuert wenn committee aktiv + mindestens ein
#                   relevanter Treffer eine Fuehrungsrolle traegt
#                   (Master-Spalte ex_officio_role_keys nicht leer).
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
# Auffaelliges Meldeverhalten zwischen transaction_date und disclosure_date.
# Zwei Trigger (ODER-verknuepft):
#   - sehr SPAETE Meldung: delay_days > EXTREME_REPORT_DAYS (Pflicht-Frist 45 Tage)
#   - sehr SCHNELLE Meldung: delay_days <= EXTREME_REPORT_FAST_DAYS
# Code: signals/extreme_report_signal.py (ExtremeReportSignal)
# =============================================================================
EXTREME_REPORT_DAYS = 60                # disclosure_date - transaction_date > X (45 = Pflicht, 60 = mit Toleranz)
EXTREME_REPORT_FAST_DAYS = 5            # disclosure_date - transaction_date <= X (ungewoehnlich schnelle Meldung)

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
    # PRIMAER (steuern den Score; s. SCORING_PRIMARY_SIGNALS + Hard-Gate)
    'committee_full':   5,    # PRIMAER: Voll-Treffer (Sector + Industry)
    'swarm_buy':       20,    # PRIMAER: >=2 Politiker kaufen denselben Issuer
    # HINT (Default 0 Score-Einfluss; nur Kontext-Indikator, im Trade-Detail-
    # Popup als "Hints" ausgewiesen). Voll konfigurierbar ueber den
    # UI-Gewichts-Editor — ein Wert > 0 wirkt nur, wenn der Trade zusaetzlich
    # mindestens ein Primaersignal triggert (Hard-Gate).
    'committee':        0,    # HINT: Halb-Treffer (nur Sector)
    'floor_action':     0,    # HINT: Vote-Timing-Treffer
    'extreme_report':   0,    # HINT
    'contrarian':       0,    # HINT
    'accumulative':     0,    # HINT
    'swarm_sell':       0,    # HINT
    'swarm_conflict':   0,    # HINT: Gegenindiz (frueher -5)
    'trade_volume':    16,    # HINT: per Default via SCORING_TRADE_VOLUME_ENABLED
                              # =False deaktiviert; der Wert hier bleibt nur
                              # UI-Slider-Obergrenze / Tier-Skalierungsfaktor.
    # 'swarm_paradox':         kein additives Gewicht — Multiplikator (s.u.)
    # 'committee_ex_officio':  kein eigenes Gewicht — Bonus X (s.u.)
}

# Primaersignale (Hard-Gate, Modul 8): Nur Trades, die mindestens eines dieser
# Signale triggern, erhalten ueberhaupt einen Score > 0. Triggert ein Trade
# kein Primaersignal, gibt scoring/trade_score.compute_trade_score frueh 0
# zurueck — unabhaengig davon, wie Hint-Gewichte konfiguriert sind. Alle nicht
# hier gelisteten Signale sind "Hints" (Default 0 Score-Einfluss, reiner
# Kontext-Indikator), bleiben aber ueber den UI-Gewichts-Editor konfigurierbar.
SCORING_PRIMARY_SIGNALS = ('committee_full', 'swarm_buy')

# Ex-Officio-Bonus v2 (rollen-differenziert): pro Trade leitet der
# CommitteeTag aus den relevanten Treffern Fuehrungsrollen-Keys ab
# (Master-Spalte ex_officio_role_keys, prae-Dedup). Der Scorer addiert den
# HOECHSTEN Einzelbonus der Keys (MAX, nicht Summe), sobald mindestens ein
# Signal aus SCORING_EX_OFFICIO_TRIGGERS getriggert hat.
# committee_*-Keys wirken committee-weit (alle Sektoren des Committees) und
# zaehlen nur, wenn SCORING_EX_OFFICIO_COMMITTEE_GLOBAL=True;
# subcommittee_*-Keys gelten nur fuer Treffer des eigenen Subcommittees.
# Cochairman wird auf den Chairman-Key seiner Ebene gemappt
# (tags/committee_tag.py: COMMITTEE_ROLE_KEYS / SUBCOMMITTEE_ROLE_KEYS).
# HINT-Komponente: per Default 0 (kein Ex-Officio-Bonus mehr — frueher dominierte
# jeder Ex-Officio-Trade den Score). Keys bleiben erhalten, damit der Bonus im
# UI-Editor pro Rolle weiter konfigurierbar ist; committee_ex_officio wird wie
# die uebrigen Hints im Trade-Detail-Popup ausgewiesen.
SCORING_EX_OFFICIO_ROLE_BONUSES = {
    'committee_chairman':          0,
    'committee_vice_chairman':     0,
    'committee_ranking_member':    0,
    'committee_ex_officio':        0,
    'subcommittee_chair':          0,
    'subcommittee_vice_chair':     0,
    'subcommittee_ranking_member': 0,
    'subcommittee_ex_officio':     0,
}
SCORING_EX_OFFICIO_COMMITTEE_GLOBAL = True
SCORING_EX_OFFICIO_TRIGGERS = ['committee', 'floor_action']

# Paradox-Daempfer: Multiplikator NUR auf die Schwarm-Komponente
# (swarm_buy + swarm_sell + swarm_conflict). 0.0 = Schwarm-Anteil ausgeknockt;
# 0.5 = halbiert; 1.0 = keine Daempfung. Warning-Tag bleibt unabhaengig sichtbar
# in der trade_warnings-Spalte, damit das Gegenindiz visualisierbar ist.
# Default 1.0 (neutral), damit das Primaersignal swarm_buy sauber bleibt; im
# UI-Editor reaktivierbar (Damper < 1.0 daempft dann den swarm_buy-Beitrag).
SCORING_PARADOX_DAMPER       = 1.0
SCORING_PARADOX_WARNING_TAG  = 'paradox_swarm'

# Trade-Volume-Signal: diskrete Stufen auf trade_amount_max_usd (obere
# Quiver-Disclosure-Range). Pro Trade zaehlt nur die hoechste erreichte
# Stufe — nicht kumulativ. Tier 0 (<= 15.000 USD) triggert nicht; alles
# darueber loest ein 'trade_volume'-Signal aus und schreibt einen Event
# in suspicious_events.csv.
#
# Defaults (Vorschlag C, konservativ) — siehe Plan-Datei
# ~/.claude/plans/ich-m-chte-den-volumefilter-recursive-wand.md:
#   Tier 1: <= 50.000      USD -> +2
#   Tier 2: <= 100.000     USD -> +4
#   Tier 3: <= 250.000     USD -> +7
#   Tier 4: <= 500.000     USD -> +10
#   Tier 5: <= 1.000.000   USD -> +13
#   Tier 6: >  1.000.000   USD -> +16
#
# Format: aufsteigend sortierte Liste (max_inclusive, score). Der letzte
# Eintrag MUSS float('inf') als Obergrenze haben (Catch-all).
# HINT-Komponente: per Default deaktiviert (0 Score-Einfluss). Im UI-Editor
# reaktivierbar; trade_volume bleibt eine reine Hint-Anzeige.
SCORING_TRADE_VOLUME_ENABLED = False
SCORING_TRADE_VOLUME_TIERS   = [
    (50_000,        2),
    (100_000,       4),
    (250_000,       7),
    (500_000,      10),
    (1_000_000,    13),
    (float('inf'), 16),
]
# Untergrenze, ab der ein Trade ueberhaupt als Tier-1-Treffer gilt.
# Trades mit trade_amount_max_usd <= TRIGGER_FLOOR loesen kein Event aus
# und tragen 0 Punkte bei. Default: 15.000 (oberste Buckets ist
# "$1,001 - $15,000" mit ~73% der Trades).
SCORING_TRADE_VOLUME_TRIGGER_FLOOR = 15_000

# Asset-Tradingvolume-Aggregations-Fenster (retroaktiv pro Trade).
# Σ trade_volume_usd ueber alle Trades mit gleichem issuer_group_key im Fenster
# [transaction_date - Z, transaction_date]. Cross-Class via issuer_group_key.
# SKIP-Bucket-Trades werden ausgeschlossen (Aggregat = 0).
# HINWEIS: Die Spalte asset_tradingvolume_usd wird weiterhin berechnet (fuer
# total_volume_usd in suspicious_assets.csv und den Streamlit Asset-Tab),
# fliesst aber NICHT mehr direkt in den trade_score ein. Stattdessen wirkt
# das neue TradeVolumeSignal auf trade_amount_max_usd.
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
TICKER_LOOKUP_AUTO_CONFIRM = True     # True: große Ticker-Batches ohne Enter-Bestaetigung fetchen (autonomer Run/Scheduler). False: interaktive Rueckfrage ab CONFIRM_THRESHOLD.
TICKER_LOOKUP_CACHE_INTERVAL = 100    # Cache alle N Ticker zwischenspeichern
TICKER_LOOKUP_NET_ERROR_PAUSE = 30    # Pause (Sek.) nach Pause-Threshold Netzwerk-Fehlern
TICKER_LOOKUP_NET_ERROR_PAUSE_THRESHOLD = 3  # Ab N consecutive Net-Errors -> Pause ausloesen
TICKER_LOOKUP_NET_ERROR_ABORT = 5     # Abbruch nach N Netzwerk-Fehlern in Folge
TICKER_SECTOR_CACHE_TTL_DAYS = 30     # TTL fuer ticker_industry_cache (positive Eintraege)

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

# Einheitliches Netzwerk-Retry-Schema (utils/network_retry.py)
# Abklingzeiten (Sekunden) ZWISCHEN den Versuchen bei transienten Netzwerk-
# fehlern (Timeout, ConnectionError, DNS, HTTP 5xx/429) oder leeren Antworten.
# 7 Backoffs = 8 Versuche, ~254s Gesamt-Wartezeit pro Operation. Bewusst KEINE
# Max-Gesamtzeit — die Einzelzeiten geben das Budget vor. Permanente Fehler
# (HTTP 4xx ausser 429, Parse/Schema) werden NICHT wiederholt -> sofort fail-closed.
# Greift in: ticker_sector (yfinance .info), post_trade_returns (yfinance
# download), issuer_group_mapper (SEC EDGAR), ticker_normalizer (OpenFIGI:
# ergaenzt Timeout/ConnErr; der 429-Pfad behaelt seinen eigenen Backoff).
NETWORK_RETRY_BACKOFFS = [2, 4, 8, 16, 32, 64, 128]
NETWORK_RETRY_ENABLED = True   # False: nur 1 Versuch (kein Retry) — fuer Tests/Debug

# post_trade_returns: Healthy-Network-Heuristik. Ein leeres yf.download()-
# Ergebnis ist nicht von einem echten Delisting unterscheidbar. Solange in
# diesem Lauf bereits ein echter Download gelang UND weniger als N Fehler in
# Folge auftraten, gilt das Netz als gesund -> leeres Ergebnis = echtes
# Delisting (Marker). Sonst = netzbedingt -> Retry, bei Erschoepfung ueber-
# springen (kein falscher exit_status='delisted').
# LEGACY (R1-R6): durch den Netzwerk-Canary (s.u.) abgeloest; bleibt fuer
# Backcompat/Doku, wird aber nicht mehr aktiv ausgewertet.
POST_TRADE_NETWORK_HEALTHY_TOLERANCE = 3

# =============================================================================
# NETZWERK-CANARY (utils/network_canary.py)
# =============================================================================
# Ein leeres yfinance-Ergebnis ist NICHT von einem echten Delisting unterscheidbar
# (HTTP 200, keine Exception). Statt das aus dem mehrdeutigen Ergebnis selbst zu
# raten (zirkulaere Heuristik, s. LEGACY oben), probt der Canary garantiert-live
# Ticker als UNABHAENGIGES Out-of-Band-Signal: schlagen >= CANARY_MIN_POSITIVE der
# CANARY_TICKERS an, gilt das Netz als GRUEN -> ein leeres Ergebnis ist ein echtes
# Delisting; sonst ROT -> kein Marker, Fenster verwerfen, warten (fail-closed bei
# erschoepftem Budget). Greift in post_trade_returns + ticker_sector.
CANARY_TICKERS = ["SPY", "AAPL", "MSFT"]
CANARY_MIN_POSITIVE = 2           # Quorum: so viele Basket-Ticker muessen positiv sein
CANARY_VERDICT_TTL_SECONDS = 60   # Verdikt-Cache UND Fensterlaenge (s)
CANARY_PROBE_BACKOFFS = [2, 4, 8]  # kurzes Eigen-Retry-Budget je Canary-Probe (s)
CANARY_MAX_RED_ROUNDS = 30        # max. ROT/Throttle-Runden (~60s) vor fail-closed-Abbruch

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

# QUIVER-AUTO-DOWNLOAD-SCRAPER (scrapers/quiver_scraper.py)
# Headless Credential-Login + XLSX-Download; taeglich 03:00 via systemd-User-Timer.
QUIVER_LOGIN_URL = 'https://www.quiverquant.com/login/'
QUIVER_EXPORT_URL = 'https://www.quiverquant.com/export/'  # direkte Export-Seite (Premium)
QUIVER_SCRAPER_HEADLESS = True               # Default-Browser-Modus (per CLI ueberschreibbar)
QUIVER_SCRAPER_TIMEOUT_MS = 30_000           # Standard-Timeout fuer goto/wait_for_selector
QUIVER_SCRAPER_DOWNLOAD_TIMEOUT_MS = 60_000  # Timeout fuer expect_download
QUIVER_VIEWPORT_WIDTH = 1280                  # Viewport — beeinflusst, ob Hamburger-Menue erscheint
QUIVER_VIEWPORT_HEIGHT = 900
# Realistischer User-Agent: Headless-Chromium sendet sonst "HeadlessChrome",
# was Login-Seiten haeufig blocken. Bot-Schutz auf Credential-Seiten ist strenger.
QUIVER_USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
# Selektoren (per `playwright codegen` ermittelt). Playwright-`role=`-Syntax ist als
# String in fill/click/wait_for_selector nutzbar; #id-Selektoren sind CSS.
QUIVER_COOKIE_REJECT_SELECTOR = 'role=button[name="Reject All"]'  # optional, best-effort
QUIVER_EMAIL_SELECTOR = 'role=textbox[name="Username"]'    # Login-Kennung (= E-Mail) ins Username-Feld
QUIVER_PASSWORD_SELECTOR = 'role=textbox[name="Password"]'
QUIVER_SUBMIT_SELECTOR = 'role=button[name="Sign In"]'
# WICHTIG: ABO-UNABHAENGIGES Element. Hamburger-Menue existiert fuer jeden eingeloggten
# Nutzer — NICHT paywalled. Nur so bleibt die Trennung Login-Fehler (Code 2) vs.
# Download-Fehler/Abo abgelaufen (Code 3) korrekt (Login gelingt auch OHNE aktives Abo).
QUIVER_POST_LOGIN_SELECTOR = '#hamburger-icon'
QUIVER_TWOFA_SELECTOR = ''        # 2FA-Challenge-Element (leer => keine 2FA erwartet)
# Download-Trigger auf der Export-Seite: href-basiert (reihenfolge-unabhaengig, da dort
# mehrere "Download"-Links fuer verschiedene Datensaetze stehen). Fehlt der Link bei
# abgelaufenem Abo, schlaegt der Klick fehl -> korrekt als Download-Fehler (Code 3).
QUIVER_DOWNLOAD_SELECTOR = 'a[href*="congress-trading-all.xlsx"]'
# Credentials NUR aus .env (nie im Code/Doku im Klartext)
QUIVER_EMAIL = os.getenv('QUIVER_EMAIL', '')
QUIVER_PASSWORD = os.getenv('QUIVER_PASSWORD', '')

# Output-Dateien (Rebuild Teil 3): vollstaendige + kuratierte Master-Sicht
TRADES_MASTER_COMPLETE_CSV = 'trades_master_complete.csv'  # alle Spalten — Quelle fuer Dashboard + Workflows
TRADES_MASTER_CSV = 'trades_master.csv'                    # kuratierte Excel-Sicht fuer den User (Inspektion)
MANUAL_REVIEW_CSV = 'manual_review_tickers.csv'            # Diagnose-Output

# Kuratierte Spalten fuer trades_master.csv (Excel-Sicht fuer User-Inspektion).
# Dashboard und Workflows nutzen trades_master_complete.csv.
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
    'ex_officio_role_keys',
    'asset_class_excluded',
    'Vote_Timing_Hit',
    'Vote_Timing_Hits',
    'Vote_Timing_Hit_Count',
    'signal_count',
    'signals_triggered',
    # Modul 8: Trade-Volume + Score
    'trade_volume_usd',          # Median der Amount-Range (fuer Aggregate)
    'trade_amount_max_usd',      # Obergrenze der Amount-Range (Score-Basis)
    'asset_tradingvolume_usd',   # 90d-Cross-Class-Aggregat per Issuer
    'trade_score',
    'trade_score_breakdown',
    'trade_warnings',
]

# Coalesce-Mapping fuer den kuratierten Export: kuratierte Zielspalte ->
# Liste von Quellspalten im Master (erste nicht-leere gewinnt). Noetig, weil
# 'Member_Committees' im Master nur als 'Member_Committees_Full' existiert.
CURATED_COALESCE = {
    'Member_Committees': ['Member_Committees_Full'],
}

# Backup-Verzeichnis fuer automatische Master-Backups vor destruktiven Aenderungen
ARCHIVE_BACKUP_PATH = 'archive/trades_master_backup'


# ---------------------------------------------------------------------------
# Post-Trade Returns (Modul 9.x — Historische Insider-Detection)
# ---------------------------------------------------------------------------
# Horizont-Tage nach dem Trade, an denen Close-Preise gezogen werden.
POST_TRADE_HORIZONS_DAYS = [30, 60, 90, 120, 180]
# Exit-Detection-Fenster — ENTKOPPELT vom Return-Horizont (war faelschlich an
# max(POST_TRADE_HORIZONS_DAYS) gekoppelt). None = unbegrenzt (gesamter
# Datensatz), int = Cap in Tagen. Findet den ECHTEN Close -> realisierter
# effective_return statt willkuerlichem Kalender-Snapshot.
EXIT_DETECTION_WINDOW_DAYS = None
# yfinance-Daily-Cache (MultiIndex [ticker, date], Spalte close).
POST_TRADE_PRICE_CACHE = 'data/cache/price_history.parquet'
# Erstabruf-Fenster pro Ticker.
POST_TRADE_PRICE_FETCH_YEARS = 5
# Refresh-Schwelle pro Ticker (max(date) < today - TTL -> Refresh).
# 1 Tag: jeder orchestrierte --delta-Lauf erzwingt tagesaktuelle Preise (der
# Lauf rechnet compute_post_trade_returns ueber den vollen kombinierten Master,
# History + Delta). Trade-off: taeglicher Voll-Refetch aller faelligen Ticker.
POST_TRADE_PRICE_CACHE_TTL_DAYS = 1
# Refetch-Fenster (Tage): begrenzt den TAEGLICHEN TTL-Refresh auf Ticker mit
# mindestens einer Position, deren transaction_date >= heute - N liegt. Aeltere
# (geschlossene/abgelaufene) Positionen haben finale Horizont-Metriken -> ihre
# bereits gecachten Preise werden NICHT mehr neu geladen. Ticker, die noch GAR
# NICHT im Cache sind, werden weiterhin IMMER geladen (Full-Rebuild-Erstbefuellung
# bleibt vollstaendig). None = altes Verhalten (alle faelligen Ticker refetchen).
# 199 = max(POST_TRADE_HORIZONS_DAYS) (180) + 19 Tage Puffer: robust gegen einen
# verpassten Lauf am Horizont-Grenztag (sonst koennte post_return_180d NaN
# bleiben, bis ein Full-Rebuild heilt). Bewusst ENTKOPPELT vom Horizont — die
# Horizont-Spalten (30/60/90/120/180) bleiben davon unberuehrt.
POST_TRADE_REFRESH_WINDOW_DAYS = 199
# Toleranz fuer Exit-Schwelle (0.0 = strikt; 0.1 = 10% Unterdeckung erlaubt).
POST_TRADE_EXIT_AMOUNT_TOLERANCE = 0.0
# Inkrementelles Cache-Flushing: alle N erfolgreich gefetchte Ticker zwischenspeichern.
# Verhindert Komplettverlust beim Erstlauf (~3000 Ticker, 1-2h) falls
# yfinance/Netzwerk-Exception durchschlaegt.
POST_TRADE_CACHE_FLUSH_EVERY = 50
# last_checked-TTL fuer Delisted-Marker: ein als delisted markierter Ticker (Row
# mit close=NaN, date=Pruefdatum) wird erst nach N Tagen erneut gefetcht — nicht
# jeden Lauf. Re-Check heilt Alt-/Falsch-Marker, Re-Listings und Symbol-Reuse.
POST_TRADE_DELISTED_RECHECK_DAYS = 30
# Progress-Intervall fuer Baseline-/Horizont-/Exit-Phasen: Fortschrittszeile
# alle N verarbeiteten Trades (zusaetzlich zu jedem 10%-Schritt). Reines
# Logging — kein Einfluss auf Ergebnisse.
POST_TRADE_PROGRESS_EVERY = 20000

# Member-Detail-Popup (Modul 9.x): Default-Schwellwert x (Prozent) fuer "grosse
# Treffer" — Anteil Trades mit paper_return_terminal > x (D1/D7). Live im Popup
# per Slider ueberschreibbar; kein Pipeline-Recompute.
DETAIL_RETURN_THRESHOLD_PCT = 10.0
# Member-Detail Spaghetti-Verlaeufe (D3b/D4) — Performance-Schutz gegen
# Vielhaendler (z.B. Ro Khanna ~28k Trades): pro Trade entsteht 1 SVG-Trace.
# DETAIL_TRAJ_SAMPLE_DAYS: nur jeden n-ten Kalendertag der Trajektorie behalten
#   (erster + letzter Punkt bleiben immer). 1 = kein Downsampling.
DETAIL_TRAJ_SAMPLE_DAYS = 3
# DETAIL_TRAJ_MAX_TRACES: Hoechstzahl gezeichneter Verlaeufe (Top-N nach
#   |paper_return_180|, NaN zuletzt). Die Liste rechts zeigt weiterhin ALLE.
#   Per Popup-Toggle ("Alle Verlaeufe zeichnen") abschaltbar.
DETAIL_TRAJ_MAX_TRACES = 300


# ---------------------------------------------------------------------------
# Event-Study / Abnormal Return (Insider-Aufdeckung — Discovery-Modul)
# ---------------------------------------------------------------------------
# Marktbereinigter, richtungsbereinigter Abnormal Return (signed BHAR) pro Trade
# als schaerferes Lineal gegenueber dem rohen Kalender-Return. Zweck: aussergewoehn-
# liche Trades (Ausreisser) entdecken UND pruefen, ob die Verdachts-Signale beim AR
# einen Edge zeigen (Diskriminator: Signale-sind-Rauschen vs. falsches-Lineal).
# Genutzt von der Pipeline-Stage 'abnormal_returns' (schreibt signed_ar_{H}d in den
# Master — deskriptiv, KEIN Scoring-Input) UND vom eigenstaendigen Analyse-Skript
# scripts/analyse_event_study.py. Settings-Aenderung = Stage bzw. Skript neu laufen
# lassen (nur Cache-Lookups, kein yfinance-Download).
#
# Markt-Benchmark fuer die Marktbereinigung. Muss im Preis-Cache liegen (SPY ist da).
AR_BENCHMARK_TICKER = 'SPY'
# Konstrukt A (Insider, primaer): Event-Fenster ab transaction_date (Kalendertage).
AR_HORIZONS_DAYS = [30, 60, 90, 120, 180]
# Primaerhorizont: Outlier-Flag, Score-Diskriminator UND App-Anzeige (Trade-/Member-
# Detail-Headline + Perzentil-Rang). Muss kein Element von AR_HORIZONS_DAYS sein
# (wird sonst automatisch ergaenzt).
AR_HEADLINE_HORIZON_DAYS = 120
# Konstrukt B (Copycat/Follower, sekundaer): Event-Fenster ab disclosure_date. Misst,
# ob ein Aussenstehender durch Nachhandeln nach der Meldung profitieren koennte —
# KEIN Informationsbeweis (skaliert mit Sichtbarkeit/Trade-Groesse). Staerker
# right-censored als A (Melde-Latenz, median ~20d).
AR_DISCLOSURE_HORIZONS_DAYS = [30, 90]
# Ausreisser-Definition auf signed_ar_{headline}d:
#   'absolute'   -> feste Bandgrenzen AR_OUTLIER_UPPER/LOWER (BRUCHTEIL, 0.03 = 3%)
#   'percentile' -> obere/untere Quantile AR_OUTLIER_PCT_UPPER/LOWER (robuster gegen
#                   schwere Tails / Horizont-Skalierung)
AR_OUTLIER_MODE = 'absolute'
AR_OUTLIER_UPPER = 0.03      # signed_AR >= X -> positiver Ausreisser (Insider-verdaechtig)
AR_OUTLIER_LOWER = -0.03     # signed_AR <= X -> negativer Ausreisser
AR_OUTLIER_PCT_UPPER = 0.95  # nur MODE='percentile'
AR_OUTLIER_PCT_LOWER = 0.05  # nur MODE='percentile'
# Member-geclusterter Block-Bootstrap (resamplet ganze Member ueber bio_guide_id,
# nicht Einzeltrades — Swarm = Clustering per Definition; naives Row-Resampling
# erzeugt Schein-Signifikanz). Anzahl Wiederholungen + Seed (Reproduzierbarkeit).
AR_BOOTSTRAP_ITERS = 2000
AR_BOOTSTRAP_SEED = 0


# ---------------------------------------------------------------------------
# Recent-Trade-Monitoring (Modul 9.x — Live-Beobachtung frischer Trades)
# ---------------------------------------------------------------------------
# Eigener Use-Case neben der historischen Analyse: zeigt OFFENE und frisch
# geschlossene Trades samt Papier-Return (Mark-to-Market gegen den juengsten
# Close), statt sie wie der Horizont-Filter auszublenden.
# Maximales Fenster (Tage, Bezug transaction_date) fuer das der Papier-Return
# in der Pipeline ueberhaupt berechnet wird. Aeltere Trades -> NaN.
RECENT_MONITORING_WINDOW_DAYS = 180
# Im Dashboard waehlbare Fenstergroessen (Selectbox). <= RECENT_MONITORING_WINDOW_DAYS.
RECENT_MONITORING_WINDOW_OPTIONS = [7, 30, 60, 90, 120, 180]
# Frische-Anforderung an den Papier-Preis: refetch, wenn der juengste Cache-Close
# aelter ist als der letzte abgeschlossene Handelstag (BDay). 1 = "nicht aelter
# als ein Handelstag" (erfuellt "<=24h werktags" beim taeglichen 03:00-Lauf).
RECENT_PRICE_MAX_AGE_BUSINESS_DAYS = 1


# ---------------------------------------------------------------------------
# Score-Korrelation (Modul 9.x — Score-Validierung)
# ---------------------------------------------------------------------------
# Dashboard-Tabs "Score-Korrelation" / "Score-Analyse": messen, wie gut der
# trade_score den vorzeichenbehafteten Objective Return post_return_{X}d
# vorhersagt (exit-agnostisch, sell_sign-bereinigt -> positiv = Trade lag in
# seiner Richtung richtig, fuer Kauf UND Verkauf). Beide Tabs umgehen den
# globalen Horizont-Sichtbarkeitsfilter (held-Positionen sind die relevanten
# Daten) und nutzen die Horizonte aus POST_TRADE_HORIZONS_DAYS.
# Default-Horizont X (Tage); muss in POST_TRADE_HORIZONS_DAYS enthalten sein.
SCORE_CORR_DEFAULT_HORIZON_DAYS = 180
# Leitkennzahl der Korrelation. Spearman = Rangkorrelation (robust gegen
# Return-Ausreisser); Pearson wird zusaetzlich angezeigt. Spearman wird ohne
# scipy als Pearson auf Raengen berechnet (a.rank().corr(b.rank())).
SCORE_CORR_DEFAULT_METHOD = 'spearman'
# Im "Score-Analyse"-Tab waehlbare Bucket-Anzahl: Anzahl gleich breiter
# Score-Intervalle (pd.cut ueber die trade_score-Spanne), NICHT Quantile.
SCORE_CORR_BUCKET_OPTIONS = [3, 4, 5, 10]
SCORE_CORR_BUCKET_DEFAULT = 5
# Scatter-Performance: Hoechstzahl gezeichneter Punkte (Sample-Cap, da der
# Master ~90k auswertbare Trades hat) und Punkt-Opazitaet. Die Kennzahlen
# (Spearman/Pearson/n) werden IMMER ueber die volle Stichprobe gerechnet —
# das Cap betrifft nur die Visualisierung.
SCORE_CORR_SCATTER_MAX_POINTS = 20000
SCORE_CORR_SCATTER_OPACITY = 0.3


# ---------------------------------------------------------------------------
# UI-Scope-Kennzeichnung der Zeitfenster-/Horizont-Widgets (Modul 9.x)
# ---------------------------------------------------------------------------
# Jedes Zeit-/Horizont-Widget traegt ein Scope-Praefix im Label + einen
# Scope-Grundtext im Tooltip, damit von aussen erkennbar ist, wie weit eine
# Einstellung reicht (die verschiedenen Zeitfenster beschraenkten sich frueher
# unsichtbar gegenseitig). Bedeutung der drei Stufen:
#   global  -> wirkt auf Trades-/Assets-/Members-/Post-Trade-Performance-Tab
#   tab     -> wirkt nur im jeweiligen Tab (umgeht die globalen Einstellungen)
#   detail  -> wirkt nur im geoeffneten Detail-Popup
UI_SCOPE_PREFIX = {
    'global': '[Global]',
    'tab': '[Tab]',
    'detail': '[Detail]',
}
UI_SCOPE_HELP = {
    'global': 'Scope Global: wirkt auf Trades-, Assets-, Members- und '
              'Post-Trade-Performance-Tab.',
    'tab': 'Scope Tab: wirkt nur in diesem Tab und ist unabhaengig von den '
           'globalen Horizont-Einstellungen der Sidebar.',
    'detail': 'Scope Detail: wirkt nur in diesem Detail-Popup.',
}
