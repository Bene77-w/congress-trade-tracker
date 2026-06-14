"""
Cross-Class Issuer Mapping via SEC EDGAR CIK (P5.6).

Liefert den Issuer-Level-Key fuer jeden Trade. Cross-Class-Aufloesung
(GOOGL+GOOG, BRK.A+BRK.B) erfolgt ueber den SEC EDGAR Central Index Key:
jede SEC-registrierte Entitaet hat genau einen CIK unabhaengig von der
Anzahl Aktienklassen.

Entscheidungsbaum:
    1. ticker_type in {option, bond, treasury, crypto, when_issued,
       invalid, misc, warrant, unit, ignored}? -> "SKIP:<type>"
    2. ticker_type in {etf, foreign_stock}?    -> "TICKER:<issuer_ticker>"
       (ETF-Sponsoren NICHT gruppieren, Foreign Stocks meist Single-Class)
    3. EDGAR-Lookup (Ticker normalisiert)?     -> "CIK:<cik>"
    4. Fallback                                -> "TICKER:<ticker_base>"

Cache: data/cache/edgar_cik_map.json, TTL 7 Tage. Bei abgelaufener TTL
wird die SEC-JSON neu geladen. Bei API-Failure (egal ob Bootstrap oder
TTL-Refresh) wird IssuerGroupMapperUnreachableError geworfen — bewusste
Entscheidung gegen Resilienz-Toleranz, um zu garantieren dass keine
veralteten CIKs in den Master einfliessen.

Doku: organisation/Ticker_Architektur.md
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Union

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from config import settings
from utils.network_retry import retry_call


# Ticker-Typen ohne sinnvolle Issuer-Gruppierung (Optionen, Bonds, Cash etc.).
# Werden mit SKIP:-Praefix gekennzeichnet und vom Schwarm ausgeschlossen.
_SKIP_TICKER_TYPES = frozenset({
    'option', 'bond', 'treasury', 'crypto', 'when_issued',
    'invalid', 'misc', 'warrant', 'unit', 'ignored',
})

# Ticker-Typen die den EDGAR-Lookup bypassen. ETFs (CIK = Sponsor) und
# Foreign Stocks (oft kein SEC-Filing) bekommen TICKER:<issuer_ticker>.
_BYPASS_TICKER_TYPES = frozenset({'etf', 'foreign_stock'})


class IssuerGroupMapperUnreachableError(RuntimeError):
    """SEC EDGAR ist nicht erreichbar und Cache kann nicht geladen werden."""


@dataclass
class _CacheState:
    loaded_at: datetime
    ticker_to_cik: dict


class IssuerGroupMapper:
    """EDGAR-Lookup-Wrapper mit lokalem Cache.

    Public API: resolve_issuer_group_key(row) -> str.
    Die Mapping-Tabelle wird beim ersten Lookup geladen (lazy bootstrap).
    """

    def __init__(self,
                 cache_dir: Optional[str] = None,
                 url: Optional[str] = None,
                 user_agent: Optional[str] = None,
                 ttl_days: Optional[int] = None):
        # Pfade aus settings ableitbar, fuer Tests injizierbar.
        if cache_dir is None:
            base_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), '..'))
            cache_dir = os.path.join(base_dir, settings.DATA_CACHE_PATH)
        self._cache_path = os.path.join(cache_dir, settings.SEC_EDGAR_CACHE_FILE)
        self._url        = url or settings.SEC_EDGAR_URL
        self._user_agent = user_agent or settings.SEC_EDGAR_USER_AGENT
        self._ttl_days   = ttl_days if ttl_days is not None else settings.SEC_EDGAR_TTL_DAYS
        self._cache: Optional[_CacheState] = None

    # ------------------------------------------------------------------
    # Public Lookup
    # ------------------------------------------------------------------

    def resolve_issuer_group_key(self, row: Union[dict, pd.Series]) -> str:
        ticker_type = _get_str(row, 'ticker_type').lower()
        if ticker_type in _SKIP_TICKER_TYPES:
            return f"SKIP:{ticker_type}"

        if ticker_type in _BYPASS_TICKER_TYPES:
            issuer_ticker = _get_str(row, 'issuer_ticker') or _get_str(row, 'ticker_base') or _get_str(row, 'ticker')
            return f"TICKER:{issuer_ticker}" if issuer_ticker else "TICKER:"

        ticker = _get_str(row, 'ticker')
        cik = self._lookup_cik(ticker) if ticker else None
        if cik:
            return f"CIK:{cik}"

        ticker_base = _get_str(row, 'ticker_base') or ticker
        return f"TICKER:{ticker_base}" if ticker_base else "TICKER:"

    # ------------------------------------------------------------------
    # CIK Lookup + Cache
    # ------------------------------------------------------------------

    def _lookup_cik(self, ticker: str) -> Optional[str]:
        normalized = normalize_ticker_for_edgar(ticker)
        if not normalized:
            return None
        cache = self._ensure_cache()
        return cache.ticker_to_cik.get(normalized)

    def _ensure_cache(self) -> _CacheState:
        if self._cache is not None:
            return self._cache
        cache_on_disk = self._load_disk_cache()
        if cache_on_disk and self._is_fresh(cache_on_disk):
            self._cache = cache_on_disk
            return self._cache
        # TTL abgelaufen oder kein Cache -> Refresh-Versuch.
        try:
            fresh = self._fetch_from_sec()
        except IssuerGroupMapperUnreachableError:
            # Strikter Abbruch: kein silent Fallback auf veralteten Cache.
            raise
        self._write_disk_cache(fresh)
        self._cache = fresh
        return self._cache

    def _load_disk_cache(self) -> Optional[_CacheState]:
        if not os.path.exists(self._cache_path):
            return None
        try:
            with open(self._cache_path, 'r', encoding='utf-8') as fh:
                raw = json.load(fh)
            loaded_at = datetime.fromisoformat(raw['loaded_at'])
            ticker_to_cik = raw['ticker_to_cik']
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None
        return _CacheState(loaded_at=loaded_at, ticker_to_cik=ticker_to_cik)

    def _write_disk_cache(self, state: _CacheState) -> None:
        os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        payload = {
            'loaded_at': state.loaded_at.isoformat(timespec='seconds'),
            'ticker_to_cik': state.ticker_to_cik,
        }
        with open(self._cache_path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)

    def _is_fresh(self, state: _CacheState) -> bool:
        age = datetime.now() - state.loaded_at
        return age <= timedelta(days=self._ttl_days)

    def _fetch_from_sec(self) -> _CacheState:
        if requests is None:
            raise IssuerGroupMapperUnreachableError(
                "SEC nicht erreichbar — Python-'requests' nicht installiert.\n"
                "Um mit altem TTL weiterzuarbeiten, ist eine Code-Aenderung noetig.")
        headers = {'User-Agent': self._user_agent}

        def _get_payload():
            resp = requests.get(self._url, headers=headers, timeout=30)
            resp.raise_for_status()  # 5xx/429 -> transient (Retry); 4xx -> permanent
            return resp.json()       # unparsebar (200) -> permanent, kein Retry

        # Transiente SEC-Stoerungen (Timeout, ConnectionError, 5xx, 429) werden
        # mit Standard-Backoff wiederholt. Bei erschoepftem Budget ODER einem
        # permanenten Fehler (4xx, unparsebar) bleibt der strikte fail-closed-
        # Vertrag: IssuerGroupMapperUnreachableError -> kein Fallback auf alten Cache.
        try:
            payload = retry_call(_get_payload, label="SEC EDGAR company_tickers")
        except Exception as exc:  # noqa: BLE001 — NetworkRetryExhausted ODER permanent
            raise IssuerGroupMapperUnreachableError(
                f"SEC nicht erreichbar ({exc}).\n"
                "Um mit altem TTL weiterzuarbeiten, ist eine Code-Aenderung noetig "
                "(siehe utils/issuer_group_mapper.py).") from exc
        ticker_to_cik = _parse_sec_payload(payload)
        return _CacheState(loaded_at=datetime.now(), ticker_to_cik=ticker_to_cik)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_ticker_for_edgar(ticker: str) -> str:
    """Normalisiert Ticker-Symbole auf EDGAR-Schreibweise (Bindestriche).

    EDGAR nutzt "BRK-B", waehrend Quiver/House/Senate "BRK.B" / "BRK/B" /
    "BRK B" liefern koennen. Hier wird auf Bindestriche vereinheitlicht und
    auf Grossbuchstaben gehoben.
    """
    if ticker is None:
        return ''
    s = str(ticker).strip().upper()
    if not s:
        return ''
    # Punkt, Slash und Whitespace -> Bindestrich.
    for ch in ('.', '/', ' '):
        s = s.replace(ch, '-')
    # Mehrfach-Bindestriche kollabieren.
    while '--' in s:
        s = s.replace('--', '-')
    return s.strip('-')


def _parse_sec_payload(payload: dict) -> dict:
    """SEC company_tickers_exchange.json -> {ticker_upper: cik_string}."""
    fields = payload.get('fields') or []
    data   = payload.get('data') or []
    try:
        idx_cik    = fields.index('cik')
        idx_ticker = fields.index('ticker')
    except ValueError as exc:
        raise IssuerGroupMapperUnreachableError(
            f"SEC-Payload-Schema unerwartet (Felder fehlen: {exc})") from exc
    out: dict[str, str] = {}
    for row in data:
        if len(row) <= max(idx_cik, idx_ticker):
            continue
        ticker = row[idx_ticker]
        cik    = row[idx_cik]
        if not ticker or cik is None:
            continue
        out[normalize_ticker_for_edgar(str(ticker))] = str(cik)
    return out


def _get_str(row: Any, key: str) -> str:
    """Robuster Getter (dict/Series) der einen sauberen str-Wert zurueckgibt."""
    if hasattr(row, 'get'):
        val = row.get(key)
    else:
        try:
            val = row[key]
        except (KeyError, IndexError, TypeError):
            return ''
    if val is None:
        return ''
    if isinstance(val, float) and pd.isna(val):
        return ''
    return str(val).strip()
