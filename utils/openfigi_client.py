"""
OpenFIGI Client - Ticker-Typ-Klassifikation via Bloomberg Open Symbology API.

Batch-fähiger Client mit SQLite-Cache. Unterscheidet zuverlässig
Stock, ETF, Mutual Fund, Preferred Stock, Warrant etc.

API-Docs: https://www.openfigi.com/api
"""

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)


class OpenFigiClient:
    """
    Klassifiziert Ticker via OpenFIGI API mit SQLite-Cache.

    Ablauf:
    1. Cache-Lookup (SQLite, beachtet TTL)
    2. Cache-Misses → Batch-API-Call (100 Ticker/Request)
    3. Ergebnisse in Cache schreiben
    """

    API_URL = "https://api.openfigi.com/v3/mapping"
    BATCH_SIZE_WITH_KEY = 100
    BATCH_SIZE_WITHOUT_KEY = 10
    REQUEST_PAUSE_WITH_KEY = 0.3    # Sekunden zwischen Requests (mit Key: 250 req/min)
    REQUEST_PAUSE_WITHOUT_KEY = 2.5  # Sekunden zwischen Requests (ohne Key: 25 req/min)

    def __init__(self, cache_db_path: str, api_key: str = None):
        self.cache_db_path = cache_db_path
        self.api_key = api_key or ''
        self.batch_size = self.BATCH_SIZE_WITH_KEY if self.api_key else self.BATCH_SIZE_WITHOUT_KEY
        self.request_pause = self.REQUEST_PAUSE_WITH_KEY if self.api_key else self.REQUEST_PAUSE_WITHOUT_KEY
        self._init_db()

    def _init_db(self):
        """SQLite-Tabelle anlegen falls nicht vorhanden."""
        os.makedirs(os.path.dirname(self.cache_db_path), exist_ok=True)
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ticker_types (
                    ticker TEXT PRIMARY KEY,
                    security_type TEXT NOT NULL,
                    openfigi_raw_type TEXT,
                    openfigi_raw_type2 TEXT,
                    openfigi_market_sector TEXT,
                    source TEXT NOT NULL,
                    checked_date TEXT NOT NULL,
                    figi TEXT
                )
            """)

    def classify_tickers(self, tickers: list[str]) -> dict[str, str]:
        """
        Klassifiziert eine Liste von Tickern via Cache + API.

        Args:
            tickers: Liste von Ticker-Strings (z.B. ["SPY", "AAPL", "QQQ"])

        Returns:
            Dict {ticker: security_type} z.B. {"SPY": "ETF", "AAPL": "Stock"}
        """
        if not tickers:
            return {}

        # 1. Cache-Lookup
        cached, misses = self._get_cached(tickers)
        logger.info(f"OpenFIGI Cache: {len(cached)} hits, {len(misses)} misses")

        if not misses:
            return cached

        # 2. API-Lookup fuer Cache-Misses
        print(f"      OpenFIGI: {len(misses)} Ticker abfragen...")
        api_results = self._query_api_batch(misses)

        # 3. Ergebnisse in Cache schreiben
        self._save_to_cache(api_results)

        # 4. Merge: Cache + API-Ergebnisse
        for ticker, info in api_results.items():
            cached[ticker] = info['security_type']

        return cached

    def _get_cached(self, tickers: list[str]) -> tuple[dict[str, str], list[str]]:
        """
        Liest Cache, gibt (gefundene, cache_misses) zurueck.
        Beachtet TTL: normale Eintraege 90 Tage, Unknown 7 Tage.
        Bei Offline-Modus (API nicht erreichbar) werden alle Eintraege verwendet.
        """
        found = {}
        misses = []
        now = datetime.now()
        ttl_normal = timedelta(days=settings.TICKER_TYPE_CACHE_TTL_DAYS)
        ttl_unknown = timedelta(days=settings.TICKER_TYPE_UNKNOWN_TTL_DAYS)

        # Chunk-Queries wegen SQLite-Variable-Limit (999 vor v3.32.0)
        CHUNK_SIZE = 900
        cached_map = {}
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.row_factory = sqlite3.Row
            for i in range(0, len(tickers), CHUNK_SIZE):
                chunk = tickers[i:i + CHUNK_SIZE]
                placeholders = ','.join('?' for _ in chunk)
                rows = conn.execute(
                    f"SELECT ticker, security_type, checked_date FROM ticker_types "
                    f"WHERE ticker IN ({placeholders})",
                    chunk
                ).fetchall()
                for row in rows:
                    cached_map[row['ticker']] = row

        for ticker in tickers:
            row = cached_map.get(ticker)
            if row is None:
                misses.append(ticker)
                continue

            checked = datetime.fromisoformat(row['checked_date'])
            ttl = ttl_unknown if row['security_type'] == 'Unknown' else ttl_normal

            if now - checked <= ttl:
                found[ticker] = row['security_type']
            else:
                misses.append(ticker)

        return found, misses

    def _query_api_batch(self, tickers: list[str]) -> dict[str, dict]:
        """
        Schickt Ticker in Batches an OpenFIGI.
        Rate-Limiting + exponentieller Backoff bei 429/5xx.

        Returns:
            Dict {ticker: {security_type, raw_type, raw_type2, market_sector, figi, source}}
        """
        results = {}
        total_batches = (len(tickers) + self.batch_size - 1) // self.batch_size
        consecutive_failures = 0

        for batch_idx in range(total_batches):
            start = batch_idx * self.batch_size
            batch = tickers[start:start + self.batch_size]

            # Request-Body bauen
            jobs = []
            for ticker in batch:
                normalized = self.normalize_for_openfigi(ticker)
                jobs.append({
                    "idType": "TICKER",
                    "idValue": normalized,
                    "exchCode": "US"
                })

            # API-Call mit Retry
            response_data = self._api_call_with_retry(jobs)

            if response_data is None:
                # Netzwerk-Fehler: Ticker dieses Batches als Unknown markieren
                for ticker in batch:
                    results[ticker] = {
                        'security_type': 'Unknown',
                        'raw_type': None,
                        'raw_type2': None,
                        'market_sector': None,
                        'figi': None,
                        'source': 'error'
                    }
                consecutive_failures += 1
                if consecutive_failures >= settings.OPENFIGI_BATCH_ABORT_THRESHOLD:
                    remaining = tickers[start + self.batch_size:]
                    logger.error(
                        f"OpenFIGI: {consecutive_failures} Batches in Folge fehlgeschlagen — "
                        f"Abbruch. {len(remaining)} verbleibende Ticker werden als Unknown markiert."
                    )
                    for ticker in remaining:
                        results[ticker] = {
                            'security_type': 'Unknown',
                            'raw_type': None,
                            'raw_type2': None,
                            'market_sector': None,
                            'figi': None,
                            'source': 'error'
                        }
                    break
                continue

            consecutive_failures = 0

            # Response parsen (gleiche Reihenfolge wie Request)
            for ticker, resp_item in zip(batch, response_data):
                results[ticker] = self._parse_response_item(resp_item)

            done = min(start + self.batch_size, len(tickers))
            if total_batches > 1:
                print(f"        [{done}/{len(tickers)}] Ticker abgefragt")

            # Rate-Limiting (ausser beim letzten Batch)
            if batch_idx < total_batches - 1:
                time.sleep(self.request_pause)

        return results

    def _api_call_with_retry(self, jobs: list[dict], max_retries: int = 3) -> Optional[list]:
        """Einzelner API-Call mit exponentiellem Backoff."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key

        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(
                    self.API_URL,
                    json=jobs,
                    headers=headers,
                    timeout=30
                )

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < max_retries:
                        logger.warning(f"OpenFIGI HTTP {resp.status_code}, "
                                       f"Retry {attempt + 1}/{max_retries} in {delay}s")
                        time.sleep(delay)
                        delay *= 2
                        continue
                    logger.error(f"OpenFIGI HTTP {resp.status_code} nach {max_retries} Retries")
                    return None

                logger.error(f"OpenFIGI HTTP {resp.status_code}: {resp.text[:200]}")
                return None

            except requests.RequestException as e:
                if attempt < max_retries:
                    logger.warning(f"OpenFIGI Netzwerk-Fehler: {e}, "
                                   f"Retry {attempt + 1}/{max_retries} in {delay}s")
                    time.sleep(delay)
                    delay *= 2
                    continue
                logger.error(f"OpenFIGI Netzwerk-Fehler nach {max_retries} Retries: {e}")
                return None

        return None

    def _parse_response_item(self, resp_item: dict) -> dict:
        """Parst ein einzelnes Response-Item (ein Ticker)."""
        if 'warning' in resp_item or 'data' not in resp_item:
            return {
                'security_type': 'Unknown',
                'raw_type': None,
                'raw_type2': None,
                'market_sector': None,
                'figi': None,
                'source': 'openfigi'
            }

        data = resp_item['data'][0]  # Erstes Ergebnis nehmen
        raw_type = data.get('securityType', '')
        raw_type2 = data.get('securityType2', '')
        market_sector = data.get('marketSector', '')
        figi = data.get('figi', '')

        # Typ mappen (securityType primaer, securityType2 sekundaer)
        security_type = self.map_openfigi_type(raw_type, raw_type2, market_sector)

        return {
            'security_type': security_type,
            'raw_type': raw_type,
            'raw_type2': raw_type2,
            'market_sector': market_sector,
            'figi': figi,
            'source': 'openfigi'
        }

    def _save_to_cache(self, results: dict[str, dict]):
        """Schreibt API-Ergebnisse in SQLite (UPSERT)."""
        now_iso = datetime.now().isoformat()

        with sqlite3.connect(self.cache_db_path) as conn:
            for ticker, info in results.items():
                conn.execute("""
                    INSERT INTO ticker_types
                        (ticker, security_type, openfigi_raw_type, openfigi_raw_type2,
                         openfigi_market_sector, source, checked_date, figi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        security_type = excluded.security_type,
                        openfigi_raw_type = excluded.openfigi_raw_type,
                        openfigi_raw_type2 = excluded.openfigi_raw_type2,
                        openfigi_market_sector = excluded.openfigi_market_sector,
                        source = excluded.source,
                        checked_date = excluded.checked_date,
                        figi = excluded.figi
                """, (
                    ticker,
                    info['security_type'],
                    info.get('raw_type'),
                    info.get('raw_type2'),
                    info.get('market_sector'),
                    info['source'],
                    now_iso,
                    info.get('figi')
                ))

    @staticmethod
    def normalize_for_openfigi(ticker: str) -> str:
        """
        Konvertiert Quiver-Format in OpenFIGI-Format.
        BRK-B → BRK.B (Class Shares).
        Preferred (-P[A-Z]) und Warrants (-W[SD]) werden NICHT konvertiert,
        da sie in Stufe 2 (Regex) abgefangen werden und hier nicht ankommen sollten.
        """
        parts = ticker.split("-")
        if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
            return f"{parts[0]}.{parts[1]}"
        return ticker

    @staticmethod
    def map_openfigi_type(security_type: str, security_type2: str,
                          market_sector: str = '') -> str:
        """
        Mappt OpenFIGI Response → interner ticker_type.

        Prueft zuerst securityType (z.B. "ETP" → ETF), dann securityType2.
        Sonderfall: marketSector Corp/Govt/Muni → Bond.
        """
        # Primaer: securityType (erkennt ETFs korrekt als "ETP")
        mapped = settings.OPENFIGI_SEC_TYPE_MAP.get(security_type)
        if mapped:
            return mapped

        # Sekundaer: securityType2
        mapped = settings.OPENFIGI_TYPE_MAP.get(security_type2)
        if mapped:
            return mapped

        # Bond-Erkennung via marketSector
        if market_sector in ('Corp', 'Govt', 'Muni'):
            return 'Bond'

        if security_type2:
            return security_type2  # Originalwert beibehalten

        return 'Unknown'

    def get_cache_stats(self) -> dict:
        """Gibt Statistiken ueber den Cache zurueck."""
        with sqlite3.connect(self.cache_db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM ticker_types").fetchone()[0]
            by_type = conn.execute(
                "SELECT security_type, COUNT(*) as cnt FROM ticker_types "
                "GROUP BY security_type ORDER BY cnt DESC"
            ).fetchall()
            by_source = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM ticker_types "
                "GROUP BY source ORDER BY cnt DESC"
            ).fetchall()

        return {
            'total': total,
            'by_type': {row[0]: row[1] for row in by_type},
            'by_source': {row[0]: row[1] for row in by_source},
        }
