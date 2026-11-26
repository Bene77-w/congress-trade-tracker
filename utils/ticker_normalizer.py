"""
OpenFIGI-Wrapper — Ticker-Normalisierung (FIGI, ticker_figi, security_type_figi).

Spezifikation: rebuild_pitch.md §6 + §11 (Defensiv-Mechanismen).

Provider-agnostische Schnittstelle: gibt NormalizedTicker zurück. Bei
Provider-Wechsel (FMP, SEC) wird genau diese Klasse ersetzt — der
Pipeline-Code sieht nur die Dataclass.

Alle aus OpenFIGI abgeleiteten Felder tragen das Suffix `_figi`, um
sie eindeutig von Classifier-Outputs (z.B. ticker_type) und yfinance-
Feldern (Suffix `_yf`) abzugrenzen.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import requests

from config import settings
from utils.network_retry import is_transient_exception
from utils.ticker_classifier import (
    ApiStrategy,
    TickerClassification,
    TickerType,
)


logger = logging.getLogger(__name__)


# Identische Pattern-Liste wie in ticker_sector (bewusst dupliziert, Pitch §16).
_NETWORK_ERROR_PATTERNS = (
    "Could not resolve host",
    "TLS connect error",
    "Connection refused",
    "Connection reset",
    "Connection timed out",
    "Network is unreachable",
)

# Search-Filter (Request-Parameter marketSecDes). Schickt OpenFIGI nur passende Treffer.
_SEARCH_MARKET_SEC_DES = {
    TickerType.PREFERRED:     "Pfd",
    TickerType.WARRANT:       "Equity",
    TickerType.UNIT:          "Equity",
    TickerType.FOREIGN_STOCK: "Equity",
}

# Response-Validierung: akzeptabler marketSector pro ticker_type (lowercase).
_ACCEPTABLE_MARKET_SECTOR = {
    TickerType.PREFERRED:     {"pfd"},
    TickerType.WARRANT:       {"equity"},
    TickerType.UNIT:          {"equity"},
    TickerType.FOREIGN_STOCK: {"equity"},
}

# Response-Validierung: securityType muss eines dieser Substrings enthalten (lowercase).
# None → kein securityType-Filter (marketSector allein reicht).
_ACCEPTABLE_SECURITY_TYPE_SUBSTRINGS = {
    TickerType.PREFERRED:     None,
    TickerType.WARRANT:       ("warrant",),
    TickerType.UNIT:          ("unit", "receipt"),
    TickerType.FOREIGN_STOCK: ("common stock", "ordinary", "depositary receipt", "adr"),
}

# Suffix-Cleaner für Search-Queries. OpenFIGI-Search ist wenig fuzzy — Suffixe wie
# "CMN" / "DEPOSITARY SHARES" / lange Series-Beschreibungen killen Matches.
# Order matters: längere/spezifischere Patterns vor kürzeren.
_QUERY_CUT_PATTERNS = (
    " DEPOSITARY SHARES", " DEPOSITARY SH",
    " PERPETUAL PREFERRED", " PREFERRED STOCK",
    " WARRANTS EXPIRING", " WARRANT",
    " SPONSORED ADR",
)

# Generische Firmen-Suffixe, von rechts iterativ abschneiden.
_QUERY_TRAIL_SUFFIXES = (
    " CMN", " COM", " COMMON", " SERIES",
    " CORPORATION", " CORP",
    " INC.", " INC", " CO.", " CO",
    " LLC", " LP", " LTD",
)


def _clean_query_name(name: str) -> str:
    """Normalisiert asset_name für OpenFIGI-Search-Query.

    OpenFIGI-Search matcht nur stark eingegrenzte Strings. Empirisch:
    - "REGIONS FINANCIAL CORPORATION CMN" → 0 Treffer
    - "REGIONS FINANCIAL" → 11 Treffer
    """
    n = (name or "").upper().strip()
    if not n:
        return ""
    # Bis zum ersten Komma (Series-Beschreibungen kommen meist nach Komma)
    n = n.split(",")[0].strip()
    # Klammer-Suffixe wie " (NYSE)"
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    # Spezifische Cut-Patterns (mehrwortig)
    for cut in _QUERY_CUT_PATTERNS:
        idx = n.find(cut)
        if idx > 0:
            n = n[:idx].strip()
    # Trailing-Suffixe iterativ entfernen
    changed = True
    while changed:
        changed = False
        for suf in _QUERY_TRAIL_SUFFIXES:
            if n.endswith(suf):
                n = n[:-len(suf)].strip()
                changed = True
    return n.rstrip(".").strip()


@dataclass(frozen=True)
class NormalizedTicker:
    """Ergebnis der Normalisierungs-Stage. Provider-agnostisch."""
    classification: TickerClassification
    figi: Optional[str]
    ticker_figi: Optional[str]
    asset_name_figi: Optional[str]
    security_type_figi: Optional[str]
    market_sector_figi: Optional[str]
    share_class_figi: Optional[str]
    normalization_errors: list[str]


class TickerNormalizer:
    """OpenFIGI-Wrapper mit allen Defensiv-Mechanismen aus Pitch §11.

    Tuning-Parameter (URLs, Batch-Sizes, Pausen, Backoff, Schwellen) liegen
    in config/settings.py — nicht hier.
    """

    def __init__(
        self,
        cache_db_path: str,
        api_key: Optional[str] = None,
        network_pause_seconds: Optional[int] = None,
        network_abort_threshold: Optional[int] = None,
        cache_checkpoint_interval: Optional[int] = None,
        rate_limit_abort_threshold: Optional[int] = None,
    ):
        self.cache_db_path = cache_db_path
        self.api_key = api_key or ""

        # URLs + Tuning aus settings (mit getattr-Defaults für Tests).
        self.api_url = getattr(
            settings, "OPENFIGI_API_URL", "https://api.openfigi.com/v3/mapping"
        )
        self.search_url = getattr(
            settings, "OPENFIGI_SEARCH_URL", "https://api.openfigi.com/v3/search"
        )
        self.request_timeout = getattr(settings, "OPENFIGI_REQUEST_TIMEOUT", 30)
        self.backoff_base = getattr(settings, "OPENFIGI_RATE_LIMIT_BACKOFF_BASE", 2.0)
        self.backoff_max = getattr(settings, "OPENFIGI_RATE_LIMIT_BACKOFF_MAX", 60.0)
        self.net_error_pause_threshold = getattr(
            settings, "TICKER_LOOKUP_NET_ERROR_PAUSE_THRESHOLD", 3
        )

        batch_with_key = getattr(settings, "OPENFIGI_BATCH_SIZE_WITH_KEY", 100)
        batch_without_key = getattr(settings, "OPENFIGI_BATCH_SIZE_WITHOUT_KEY", 10)
        # Separate Pausen pro Endpoint (Mapping vs. Search haben verschiedene Limits).
        mapping_pause_with_key = getattr(settings, "OPENFIGI_MAPPING_PAUSE_WITH_KEY", 0.3)
        mapping_pause_without_key = getattr(settings, "OPENFIGI_MAPPING_PAUSE_WITHOUT_KEY", 3.5)
        search_pause_with_key = getattr(settings, "OPENFIGI_SEARCH_PAUSE_WITH_KEY", 1.2)
        search_pause_without_key = getattr(settings, "OPENFIGI_SEARCH_PAUSE_WITHOUT_KEY", 13.0)

        self.batch_size = batch_with_key if self.api_key else batch_without_key
        self.mapping_pause = mapping_pause_with_key if self.api_key else mapping_pause_without_key
        self.search_pause = search_pause_with_key if self.api_key else search_pause_without_key

        # Defensive-Schwellen — Constructor-Argument > settings > Fallback.
        self.network_pause_seconds = (
            network_pause_seconds
            if network_pause_seconds is not None
            else getattr(settings, "TICKER_LOOKUP_NET_ERROR_PAUSE", 30)
        )
        self.network_abort_threshold = (
            network_abort_threshold
            if network_abort_threshold is not None
            else getattr(settings, "TICKER_LOOKUP_NET_ERROR_ABORT", 5)
        )
        self.cache_checkpoint_interval = (
            cache_checkpoint_interval
            if cache_checkpoint_interval is not None
            else getattr(settings, "TICKER_LOOKUP_CACHE_INTERVAL", 100)
        )
        self.rate_limit_abort_threshold = (
            rate_limit_abort_threshold
            if rate_limit_abort_threshold is not None
            else getattr(settings, "OPENFIGI_BATCH_ABORT_THRESHOLD", 3)
        )

        self._consecutive_network_errors = 0
        self._consecutive_429 = 0
        self._lookups_since_checkpoint = 0
        self._retry_queue: list[TickerClassification] = []
        self._db_initialized = False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def normalize_batch(
        self, classifications: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        """Batch-Normalisierung mit Reihenfolge-Erhalt.

        Routet pro api_strategy, behält Input-Reihenfolge im Output bei.
        Wirft RuntimeError bei zu vielen consecutive Network-Errors oder 429.

        Dedup-Schicht: identische TickerClassifications (frozen dataclass,
        hashbar) werden nur einmal verarbeitet und das Resultat auf alle
        Vorkommen expandiert. Spart API-Calls bei Trade-Listen mit
        wiederkehrenden Tickern und verhindert doppelte Log-Zeilen.
        """
        if not classifications:
            return []

        # Reset Counter pro Batch (Lifecycle-Methode).
        self._consecutive_network_errors = 0
        self._consecutive_429 = 0

        # Dedup-Mapping cls -> position in unique-Liste.
        unique_index: dict[TickerClassification, int] = {}
        unique_classifications: list[TickerClassification] = []
        for cls in classifications:
            if cls not in unique_index:
                unique_index[cls] = len(unique_classifications)
                unique_classifications.append(cls)

        # Index merken für Reihenfolge-Erhalt (unique-Ebene).
        indexed = list(enumerate(unique_classifications))
        results: list[Optional[NormalizedTicker]] = [None] * len(unique_classifications)

        # Routing pro Strategie.
        groups = self._route_by_strategy(indexed)

        for strategy, items in groups.items():
            if strategy == ApiStrategy.SKIP:
                processed = self._process_skip([c for _, c in items])
            elif strategy == ApiStrategy.FIGI_MAPPING:
                processed = self._process_mapping([c for _, c in items])
            elif strategy == ApiStrategy.FIGI_SEARCH:
                processed = self._process_search([c for _, c in items])
            elif strategy == ApiStrategy.CUSIP_LOOKUP:
                processed = self._process_cusip([c for _, c in items])
            elif strategy == ApiStrategy.YFINANCE_DIRECT:
                processed = self._process_yfinance_direct([c for _, c in items])
            elif strategy == ApiStrategy.MANUAL_REVIEW:
                processed = self._process_manual_review([c for _, c in items])
            else:
                # Unbekannte Strategie — defensiv leeres Result.
                processed = [self._empty_result(c, [f"unknown_strategy:{strategy}"])
                             for _, c in items]

            for (orig_idx, _), result in zip(items, processed):
                results[orig_idx] = result

        # Retry-Queue: einmaliger Wiederholungslauf für Network-Error-Tickers.
        if self._retry_queue:
            self._run_retry_round(results, unique_classifications)

        # Final cache flush nach Batch.
        self._cache_flush()

        # Unique-Resultate auf Original-Reihenfolge expandieren.
        unique_results = [
            r if r is not None
            else self._empty_result(unique_classifications[i], ["routing_lost"])
            for i, r in enumerate(results)
        ]
        return [unique_results[unique_index[cls]] for cls in classifications]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _route_by_strategy(
        self, indexed: list[tuple[int, TickerClassification]]
    ) -> dict[ApiStrategy, list[tuple[int, TickerClassification]]]:
        groups: dict[ApiStrategy, list[tuple[int, TickerClassification]]] = {}
        for idx, cls in indexed:
            groups.setdefault(cls.api_strategy, []).append((idx, cls))
        return groups

    def _process_skip(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        return [self._empty_result(c, []) for c in items]

    def _process_yfinance_direct(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        """YFINANCE_DIRECT (ETFs, NUMERIC_SUFFIX, manche SHARECLASS).

        P5: optimistischer /v3/mapping-Versuch fuer share_class_figi-Coverage.
        yfinance Sector/Industry wird unveraendert in der naechsten Stage gezogen.
        """
        return self._process_optimistic_mapping(items)

    def _process_manual_review(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        """MANUAL_REVIEW (UNKNOWN, LONG_ALPHA_SUSPECT, INVALID).

        P5: optimistischer /v3/mapping-Versuch. Negativ-Cache verhindert Wiederholungen.
        """
        # Leere ticker_base -> kein sinnvoller Mapping-Versuch moeglich (Cash etc.).
        candidates = [c for c in items if c.ticker_base]
        skipped = [c for c in items if not c.ticker_base]
        result_map: dict[str, NormalizedTicker] = {}

        if candidates:
            for cls, res in zip(candidates, self._process_optimistic_mapping(candidates)):
                result_map[id(cls)] = res
        for cls in skipped:
            result_map[id(cls)] = self._empty_result(cls, ["manual_review_no_ticker"])

        return [result_map[id(c)] for c in items]

    def _process_optimistic_mapping(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        """Optimistischer /v3/mapping-Versuch (kein Search-Fallback).

        Verwendet die existierende Mapping-Batch-Logik mit Cache + Negativ-Cache.
        Treffer -> vollwertiger NormalizedTicker mit share_class_figi.
        Miss    -> _empty_result + Cache-Eintrag mit source='empty'.
        Logging gedaempft (kein per-Miss print, da typischerweise Misses zu erwarten sind).
        """
        results: list[NormalizedTicker] = []

        for batch_start in range(0, len(items), self.batch_size):
            batch = items[batch_start:batch_start + self.batch_size]

            uncached: list[TickerClassification] = []
            payloads: dict[str, Optional[dict]] = {}
            for cls in batch:
                key = self._cache_key(cls, "TICKER")
                cached = self._cache_lookup(key)
                if cached is not None:
                    payloads[cls.raw_ticker] = cached
                else:
                    uncached.append(cls)

            if uncached:
                jobs = [self._build_mapping_job(c) for c in uncached]
                response, error_kind = self._api_post(self.api_url, jobs)

                if error_kind == "network":
                    for cls in uncached:
                        payloads[cls.raw_ticker] = None
                        if cls not in self._retry_queue:
                            self._retry_queue.append(cls)
                elif error_kind == "http":
                    # Permanenter HTTP-/Parse-Fehler: KEIN Cache-Write (sonst
                    # wuerde ein API-Fehler als Negativ-Ergebnis persistiert).
                    for cls in uncached:
                        payloads[cls.raw_ticker] = None
                else:
                    for cls, resp_item in zip(uncached, response or []):
                        parsed = self._parse_mapping_response(resp_item)
                        # Misses zu explizitem empty-Payload normalisieren,
                        # damit der Negativ-Cache greift.
                        if parsed is None:
                            parsed = {
                                "figi": None, "ticker_figi": None,
                                "asset_name_figi": None, "security_type_figi": None,
                                "raw_security_type": None, "raw_security_type2": None,
                                "market_sector_figi": None, "share_class_figi": None,
                                "source": "empty",
                            }
                        payloads[cls.raw_ticker] = parsed
                        self._cache_write(self._cache_key(cls, "TICKER"), parsed,
                                          cls.ticker_type.value)
                        self._lookups_since_checkpoint += 1

                if batch_start + self.batch_size < len(items):
                    time.sleep(self.mapping_pause)

            for cls in batch:
                payload = payloads.get(cls.raw_ticker)
                if payload is None or not payload.get("figi"):
                    results.append(self._empty_result(cls, []))
                    continue
                results.append(self._build_normalized(cls, payload, []))

            if self._lookups_since_checkpoint >= self.cache_checkpoint_interval:
                self._cache_flush()
                self._lookups_since_checkpoint = 0

        return results

    def _process_mapping(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        results: list[NormalizedTicker] = []

        for batch_start in range(0, len(items), self.batch_size):
            batch = items[batch_start:batch_start + self.batch_size]

            # 1. Cache-Lookup pro Item.
            uncached: list[TickerClassification] = []
            payloads: dict[str, Optional[dict]] = {}
            uncached_rawtickers: set[str] = set()
            for cls in batch:
                key = self._cache_key(cls, "TICKER")
                cached = self._cache_lookup(key)
                if cached is not None:
                    payloads[cls.raw_ticker] = cached
                else:
                    uncached.append(cls)
                    uncached_rawtickers.add(cls.raw_ticker)

            # 2. Uncached → API-Call.
            http_failed: set[str] = set()
            if uncached:
                jobs = [self._build_mapping_job(c) for c in uncached]
                response, error_kind = self._api_post(self.api_url, jobs)

                if error_kind == "network":
                    # Items in Retry-Queue, kein Cache-Eintrag.
                    for cls in uncached:
                        payloads[cls.raw_ticker] = None
                        if cls not in self._retry_queue:
                            self._retry_queue.append(cls)
                elif error_kind == "http":
                    # Permanenter HTTP-/Parse-Fehler: KEIN Cache-Write und KEIN
                    # Search-Fallback — sonst degradiert ein einzelner 4xx den
                    # ganzen Batch zur teuren Einzel-Search-Kaskade (search_pause
                    # pro Ticker) bzw. persistiert Fehler als Negativ-Eintrag.
                    for cls in uncached:
                        payloads[cls.raw_ticker] = None
                        http_failed.add(cls.raw_ticker)
                else:
                    # Response parsen.
                    for cls, resp_item in zip(uncached, response or []):
                        parsed = self._parse_mapping_response(resp_item)
                        # Misses zu explizitem empty-Payload normalisieren,
                        # damit der Negativ-Cache greift (verhindert wiederholte
                        # Mapping-API-Calls + Log-Spam ueber Batches/Runs).
                        if parsed is None:
                            parsed = {
                                "figi": None, "ticker_figi": None,
                                "asset_name_figi": None, "security_type_figi": None,
                                "raw_security_type": None, "raw_security_type2": None,
                                "market_sector_figi": None, "share_class_figi": None,
                                "source": "empty",
                            }
                        payloads[cls.raw_ticker] = parsed
                        self._cache_write(self._cache_key(cls, "TICKER"), parsed,
                                          cls.ticker_type.value)
                        self._lookups_since_checkpoint += 1

                # Rate-Limiting zwischen Mapping-Batches.
                if batch_start + self.batch_size < len(items):
                    time.sleep(self.mapping_pause)

            # 3. Result-Konstruktion mit Mapping-Search-Fallback.
            for cls in batch:
                payload = payloads.get(cls.raw_ticker)

                if cls.raw_ticker in http_failed:
                    # s. oben: HTTP-Fehler -> kein Search-Fallback, kein Cache.
                    results.append(self._empty_result(cls, ["http_error"]))
                    continue

                if payload is None or not payload.get("figi"):
                    # 0 Treffer → Fallback zu Search.
                    # Pause vor Search, damit Search-Endpoint nicht direkt nach
                    # Mapping-Batch getroffen wird (Search-Limit ist viel strenger).
                    # Print nur fuer frische API-Misses (uncached), nicht fuer
                    # bereits in der DB persistierte Negativ-Cache-Hits.
                    if cls.raw_ticker in uncached_rawtickers:
                        print(f"      [i] Kein Mapping-Hit für {cls.raw_ticker} → Fallback /v3/search")
                        time.sleep(self.search_pause)
                    fallback_results = self._process_search([cls])
                    results.append(fallback_results[0])
                    continue

                results.append(self._build_normalized(cls, payload, []))

            # Cache-Checkpoint.
            if self._lookups_since_checkpoint >= self.cache_checkpoint_interval:
                self._cache_flush()
                print(f"      [i] Cache-Checkpoint: {self._lookups_since_checkpoint} Lookups gespeichert")
                self._lookups_since_checkpoint = 0

        return results

    @staticmethod
    def _is_acceptable_search_item(item: dict, cls: TickerClassification) -> bool:
        """Whitelist-Filter: passt marketSector + securityType zur ticker_type?"""
        accept_sector = _ACCEPTABLE_MARKET_SECTOR.get(cls.ticker_type)
        if accept_sector:
            sector = (item.get("marketSector") or "").lower()
            if sector not in accept_sector:
                return False
        accept_types = _ACCEPTABLE_SECURITY_TYPE_SUBSTRINGS.get(cls.ticker_type)
        if accept_types:
            sec_type = (item.get("securityType") or "").lower()
            sec_type2 = (item.get("securityType2") or "").lower()
            combined = f"{sec_type} {sec_type2}"
            if not any(token in combined for token in accept_types):
                return False
        return True

    def _first_acceptable_search_item(
        self, data_list: list, cls: TickerClassification
    ) -> Optional[dict]:
        if not data_list:
            return None
        # Kein Filter konfiguriert → Legacy-Verhalten (erstes Element).
        if (cls.ticker_type not in _ACCEPTABLE_MARKET_SECTOR
                and cls.ticker_type not in _ACCEPTABLE_SECURITY_TYPE_SUBSTRINGS):
            return data_list[0]
        for item in data_list:
            if self._is_acceptable_search_item(item, cls):
                return item
        return None

    def _process_search(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        results: list[NormalizedTicker] = []

        for cls in items:
            # Cache-Lookup mit search-spezifischem Key.
            key = self._cache_key(cls, "SEARCH")
            cached = self._cache_lookup(key)
            if cached is not None:
                if cached.get("figi"):
                    results.append(self._build_normalized(cls, cached, []))
                else:
                    results.append(self._empty_result(cls, ["search_cached_empty"]))
                continue

            # Search-Body nach OpenFIGI-Spec.
            # exchCode NICHT setzen — OpenFIGI-Search liefert sonst 0 Treffer
            # bei US-Preferreds (empirisch). Filter geschieht via marketSecDes
            # + Response-Validierung.
            raw_q = cls.company_name or cls.raw_ticker
            cleaned_q = _clean_query_name(raw_q) or raw_q
            body = {"query": cleaned_q}
            sec_des = _SEARCH_MARKET_SEC_DES.get(cls.ticker_type)
            if sec_des:
                body["marketSecDes"] = sec_des

            response, error_kind = self._api_post(self.search_url, body)

            if error_kind == "network":
                if cls not in self._retry_queue:
                    self._retry_queue.append(cls)
                results.append(self._empty_result(cls, [f"network_error:search"]))
                continue
            if error_kind == "http":
                # Permanenter HTTP-/Parse-Fehler: NICHT als search_empty
                # negativ-cachen (ein API-Fehler ist kein "existiert nicht").
                results.append(self._empty_result(cls, ["http_error"]))
                continue

            data_list = (response or {}).get("data", []) if isinstance(response, dict) else []
            accepted = self._first_acceptable_search_item(data_list, cls)
            if accepted is None:
                reason = "search_no_match" if data_list else "search_no_hit"
                print(f"      [!] Kein passender Treffer für {cls.raw_ticker} "
                      f"({cls.company_name}) — {reason}")
                empty_payload = {
                    "figi": None, "ticker_figi": None, "asset_name_figi": None,
                    "security_type_figi": None, "raw_security_type": None,
                    "raw_security_type2": None, "market_sector_figi": None,
                    "share_class_figi": None,
                    "source": "search_empty",
                }
                self._cache_write(key, empty_payload, cls.ticker_type.value)
                self._lookups_since_checkpoint += 1
                results.append(self._empty_result(cls, [reason]))
                time.sleep(self.search_pause)
                continue

            payload = self._parse_data_item(accepted, source="search")
            self._cache_write(key, payload, cls.ticker_type.value)
            self._lookups_since_checkpoint += 1
            results.append(self._build_normalized(cls, payload, []))

            time.sleep(self.search_pause)

        return results

    def _process_cusip(
        self, items: list[TickerClassification]
    ) -> list[NormalizedTicker]:
        results: list[NormalizedTicker] = []

        for batch_start in range(0, len(items), self.batch_size):
            batch = items[batch_start:batch_start + self.batch_size]

            uncached: list[TickerClassification] = []
            payloads: dict[str, Optional[dict]] = {}
            for cls in batch:
                key = self._cache_key(cls, "ID_CUSIP")
                cached = self._cache_lookup(key)
                if cached is not None:
                    payloads[cls.raw_ticker] = cached
                else:
                    uncached.append(cls)

            if uncached:
                jobs = [
                    {"idType": "ID_CUSIP", "idValue": c.ticker_base}
                    for c in uncached
                ]
                response, error_kind = self._api_post(self.api_url, jobs)

                if error_kind == "network":
                    for cls in uncached:
                        payloads[cls.raw_ticker] = None
                        if cls not in self._retry_queue:
                            self._retry_queue.append(cls)
                elif error_kind == "http":
                    # Permanenter HTTP-/Parse-Fehler: kein Cache-Write.
                    for cls in uncached:
                        payloads[cls.raw_ticker] = None
                else:
                    for cls, resp_item in zip(uncached, response or []):
                        parsed = self._parse_mapping_response(resp_item)
                        # Misses zu explizitem empty-Payload normalisieren, damit
                        # der Negativ-Cache greift (wie _process_mapping) — sonst
                        # wiederholen sich treffersfreie CUSIP-Calls jeden Lauf.
                        if parsed is None:
                            parsed = {
                                "figi": None, "ticker_figi": None,
                                "asset_name_figi": None, "security_type_figi": None,
                                "raw_security_type": None, "raw_security_type2": None,
                                "market_sector_figi": None, "share_class_figi": None,
                                "source": "empty",
                            }
                        payloads[cls.raw_ticker] = parsed
                        self._cache_write(self._cache_key(cls, "ID_CUSIP"), parsed,
                                          cls.ticker_type.value)
                        self._lookups_since_checkpoint += 1

                if batch_start + self.batch_size < len(items):
                    time.sleep(self.mapping_pause)

            for cls in batch:
                payload = payloads.get(cls.raw_ticker)
                if payload is None or not payload.get("figi"):
                    results.append(self._empty_result(cls, ["cusip_no_hit"]))
                    continue
                results.append(self._build_normalized(cls, payload, []))

        return results

    def _run_retry_round(
        self,
        results: list[Optional[NormalizedTicker]],
        original: list[TickerClassification],
    ) -> None:
        retries = list(self._retry_queue)
        self._retry_queue = []
        print(f"      [i] Retry-Runde: {len(retries)} Ticker mit Netzwerk-Fehler...")

        ok = 0
        fail = 0
        # Counter für Retry zurücksetzen.
        self._consecutive_network_errors = 0

        try:
            retry_results = self._process_mapping(retries)
        except RuntimeError as e:
            print(f"      [!] Retry-Runde abgebrochen: {e}")
            return

        # Ergebnisse in results einsetzen (an Original-Index).
        index_by_raw = {c.raw_ticker: i for i, c in enumerate(original)}
        for cls, res in zip(retries, retry_results):
            idx = index_by_raw.get(cls.raw_ticker)
            if idx is not None:
                results[idx] = res
            if res.figi:
                ok += 1
            else:
                fail += 1

        print(f"      [i] Retry: {ok} OK, {fail} weiterhin fehlgeschlagen")

    # ------------------------------------------------------------------
    # API-Calls
    # ------------------------------------------------------------------

    def _api_post(
        self, url: str, body
    ) -> tuple[Optional[object], Optional[str]]:
        """Single POST. Returns (json_or_None, error_kind).

        error_kind in {None, 'network', 'http'}. 429 wird intern per Backoff
        wiederholt (bei Dauer-429: RuntimeError); Abbrueche werfen RuntimeError.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key

        attempt = 0          # 429-Versuche (eigener Backoff, behaelt Vorrang)
        net_attempt = 0      # transiente Netzfehler-Versuche (Standard-Backoff)
        net_backoffs = settings.NETWORK_RETRY_BACKOFFS
        while True:
            try:
                resp = requests.post(
                    url, json=body, headers=headers, timeout=self.request_timeout
                )
            except requests.RequestException as exc:
                err_str = str(exc)
                # Transient (Timeout/ConnErr/DNS/SSL) -> Standard-Backoff, dann Retry.
                # is_transient_exception ist typ-basiert und faengt damit auch
                # Timeouts, die das alte String-Pattern verfehlte (R6).
                if is_transient_exception(exc) or self._is_network_error(err_str):
                    if net_attempt < len(net_backoffs):
                        wait = net_backoffs[net_attempt]
                        net_attempt += 1
                        print(f"      [!] OpenFIGI Netzfehler (Versuch {net_attempt}/"
                              f"{len(net_backoffs)}) → Backoff {wait}s: {err_str[:120]}")
                        logger.warning(f"OpenFIGI Netzfehler Backoff {wait}s: {err_str[:120]}")
                        time.sleep(wait)
                        continue
                    # Budget erschoepft -> aeusseren Consecutive-Counter EINMAL fuehren.
                    aborted = not self._handle_network_error(err_str)
                    if aborted:
                        raise RuntimeError(
                            f"OpenFIGI: {self._consecutive_network_errors} consecutive "
                            f"network errors → Stage-Abbruch"
                        )
                    return None, "network"
                logger.error(f"OpenFIGI Request-Exception (nicht-Netzwerk): {exc}")
                return None, "http"

            # Successful response — counters resetten.
            if resp.status_code == 200:
                self._consecutive_network_errors = 0
                self._consecutive_429 = 0
                try:
                    return resp.json(), None
                except ValueError as exc:
                    logger.error(f"OpenFIGI Response unparseable: {exc}")
                    return None, "http"

            if resp.status_code == 429:
                attempt += 1
                ok = self._handle_429(attempt, resp)
                if not ok:
                    raise RuntimeError(
                        f"OpenFIGI: {self._consecutive_429} consecutive 429 → Stage-Abbruch"
                    )
                continue

            # HTTP 5xx -> transient: Standard-Backoff, dann Retry.
            if 500 <= resp.status_code < 600:
                if net_attempt < len(net_backoffs):
                    wait = net_backoffs[net_attempt]
                    net_attempt += 1
                    print(f"      [!] OpenFIGI HTTP {resp.status_code} (Versuch "
                          f"{net_attempt}/{len(net_backoffs)}) → Backoff {wait}s")
                    logger.warning(f"OpenFIGI HTTP {resp.status_code} Backoff {wait}s")
                    time.sleep(wait)
                    continue
                aborted = not self._handle_network_error(f"HTTP {resp.status_code}")
                if aborted:
                    raise RuntimeError(
                        f"OpenFIGI: {self._consecutive_network_errors} consecutive "
                        f"network errors → Stage-Abbruch"
                    )
                return None, "network"

            # Andere HTTP-Fehler (4xx) -> permanent, kein Retry.
            print(f"      [!] OpenFIGI HTTP {resp.status_code}: {resp.text[:200]}")
            logger.error(f"OpenFIGI HTTP {resp.status_code}: {resp.text[:200]}")
            return None, "http"

    def _build_mapping_job(self, cls: TickerClassification) -> dict:
        job = {
            "idType": "TICKER",
            "idValue": self._normalize_for_openfigi(cls.ticker_base),
        }
        if cls.exch_code:
            job["exchCode"] = cls.exch_code
        else:
            job["exchCode"] = "US"
        return job

    @staticmethod
    def _normalize_for_openfigi(ticker: str) -> str:
        """OpenFIGI-Bloomberg-Composite-Format.

        - Preferred '<root> PR<series>' → '<root>/P<series>' (WRB PRG → WRB/PG)
        - Class Share BRK-B → BRK/B (BRK.B liefert bei OpenFIGI keinen Treffer)
        - sonst unverändert
        """
        if " PR" in ticker:
            root, _, series = ticker.partition(" PR")
            return f"{root}/P{series}" if series else f"{root}/P"
        parts = ticker.split("-")
        if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
            return f"{parts[0]}/{parts[1]}"
        return ticker

    def _parse_mapping_response(self, resp_item) -> Optional[dict]:
        """Parst ein /v3/mapping-Response-Item (für einen Ticker)."""
        if not isinstance(resp_item, dict):
            return None
        if "warning" in resp_item or "data" not in resp_item:
            return {
                "figi": None, "ticker_figi": None, "asset_name_figi": None,
                "security_type_figi": None, "raw_security_type": None,
                "raw_security_type2": None, "market_sector_figi": None,
                "share_class_figi": None,
                "source": "empty",
            }
        data = resp_item["data"]
        if not data:
            return None
        return self._parse_data_item(data[0], source="mapping")

    @staticmethod
    def _parse_data_item(item: dict, source: str) -> dict:
        """Parst ein einzelnes data-Item aus mapping/search-Response."""
        return {
            "figi": item.get("figi"),
            "ticker_figi": item.get("ticker"),
            "asset_name_figi": item.get("name"),
            "security_type_figi": item.get("securityType"),
            "raw_security_type": item.get("securityType"),
            "raw_security_type2": item.get("securityType2"),
            "market_sector_figi": item.get("marketSector"),
            "share_class_figi": item.get("shareClassFIGI"),
            "source": source,
        }

    # ------------------------------------------------------------------
    # Cache (SQLite, lazy init)
    # ------------------------------------------------------------------

    def _ensure_db(self) -> None:
        if self._db_initialized:
            return
        os.makedirs(os.path.dirname(self.cache_db_path) or ".", exist_ok=True)
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ticker_normalizer (
                    cache_key           TEXT PRIMARY KEY,
                    classification_type TEXT NOT NULL,
                    figi                TEXT,
                    ticker_figi         TEXT,
                    asset_name_figi     TEXT,
                    security_type_figi  TEXT,
                    raw_security_type   TEXT,
                    raw_security_type2  TEXT,
                    market_sector_figi  TEXT,
                    share_class_figi    TEXT,
                    source              TEXT NOT NULL,
                    checked_date        TEXT NOT NULL
                )
            """)
            # Migration: Spalte name_figi → asset_name_figi (SQLite ≥ 3.25)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(ticker_normalizer)")}
            if "name_figi" in cols:
                conn.execute(
                    "ALTER TABLE ticker_normalizer RENAME COLUMN name_figi TO asset_name_figi"
                )
                cols.add("asset_name_figi")
                cols.discard("name_figi")
            # Migration P5: Spalte share_class_figi nachziehen
            if "share_class_figi" not in cols:
                conn.execute("ALTER TABLE ticker_normalizer ADD COLUMN share_class_figi TEXT")
        self._db_initialized = True

    @staticmethod
    def _cache_key(cls: TickerClassification, id_type: str) -> str:
        if id_type == "SEARCH":
            # ticker_type gehoert in den SEARCH-Key: Warrant (ABC-W) und Unit
            # (ABC.U) derselben Base (SPAC-Paare!) haetten sonst denselben Key
            # und erbten gegenseitig den FIGI aus dem Cache — die Typ-Filter
            # (_is_acceptable_search_item) greifen nur beim Erst-Write.
            # Alt-Format-Eintraege verfehlen schlicht (Cache kalt, refetch).
            return (f"{cls.ticker_base}:{id_type}:"
                    f"{cls.ticker_type.value}:{cls.exch_code or '*'}")
        return f"{cls.ticker_base}:{id_type}:{cls.exch_code or '*'}"

    def _cache_lookup(self, key: str) -> Optional[dict]:
        self._ensure_db()
        ttl_normal = timedelta(days=settings.TICKER_TYPE_CACHE_TTL_DAYS)
        ttl_unknown = timedelta(days=settings.TICKER_TYPE_UNKNOWN_TTL_DAYS)
        now = datetime.now()

        with sqlite3.connect(self.cache_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM ticker_normalizer WHERE cache_key = ?", (key,)
            ).fetchone()

        if row is None:
            return None

        try:
            checked = datetime.fromisoformat(row["checked_date"])
        except (TypeError, ValueError):
            return None

        ttl = ttl_unknown if row["source"] in ("empty", "search_empty") else ttl_normal
        if now - checked > ttl:
            return None

        # P5-Migration: alter Hit mit erfolgreichem FIGI aber fehlendem
        # share_class_figi → als Cache-Miss behandeln, damit der API-Call das
        # Feld nachzieht. Negative Cache-Eintraege ('empty', 'search_empty')
        # bleiben gueltig — fehlender share_class_figi ist dort erwartet.
        if (row["figi"] is not None
                and row["share_class_figi"] is None
                and row["source"] not in ("empty", "search_empty")):
            return None

        return {
            "figi": row["figi"],
            "ticker_figi": row["ticker_figi"],
            "asset_name_figi": row["asset_name_figi"],
            "security_type_figi": row["security_type_figi"],
            "raw_security_type": row["raw_security_type"],
            "raw_security_type2": row["raw_security_type2"],
            "market_sector_figi": row["market_sector_figi"],
            "share_class_figi": row["share_class_figi"],
            "source": row["source"],
        }

    def _cache_write(self, key: str, payload: dict,
                     classification_type: Optional[str] = None) -> None:
        self._ensure_db()
        now_iso = datetime.now().isoformat()
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute("""
                INSERT INTO ticker_normalizer
                    (cache_key, classification_type, figi, ticker_figi, asset_name_figi,
                     security_type_figi, raw_security_type, raw_security_type2,
                     market_sector_figi, share_class_figi, source, checked_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    classification_type = excluded.classification_type,
                    figi = excluded.figi,
                    ticker_figi = excluded.ticker_figi,
                    asset_name_figi = excluded.asset_name_figi,
                    security_type_figi = excluded.security_type_figi,
                    raw_security_type = excluded.raw_security_type,
                    raw_security_type2 = excluded.raw_security_type2,
                    market_sector_figi = excluded.market_sector_figi,
                    share_class_figi = excluded.share_class_figi,
                    source = excluded.source,
                    checked_date = excluded.checked_date
            """, (
                key,
                # Echter Klassifikationstyp (z. B. 'stock', 'unit') — vorher
                # wurde hier faelschlich die source dupliziert, wodurch die
                # Typ-Statistik falsch war.
                classification_type or "unknown",
                payload.get("figi"),
                payload.get("ticker_figi"),
                payload.get("asset_name_figi"),
                payload.get("security_type_figi"),
                payload.get("raw_security_type"),
                payload.get("raw_security_type2"),
                payload.get("market_sector_figi"),
                payload.get("share_class_figi"),
                payload.get("source", "unknown"),
                now_iso,
            ))

    def _cache_flush(self) -> None:
        # SQLite in autocommit mode (with-context). Hook für Konsistenz/Tests.
        pass

    # ------------------------------------------------------------------
    # Defensive Helpers
    # ------------------------------------------------------------------

    def _is_network_error(self, error_msg: str) -> bool:
        return any(p in error_msg for p in _NETWORK_ERROR_PATTERNS)

    def _handle_network_error(self, error_msg: str) -> bool:
        """Erhöht Counter, gibt Pause/Abort-Signal aus.

        Returns False, wenn Abort-Threshold erreicht wurde.
        """
        self._consecutive_network_errors += 1
        n = self._consecutive_network_errors
        print(f"      [!] Netzwerk-Fehler {n}/{self.network_abort_threshold}: {error_msg}")
        logger.warning(f"OpenFIGI Netzwerk-Fehler {n}: {error_msg}")

        if n >= self.network_abort_threshold:
            print(f"      [!!] {n} Netzwerk-Fehler in Folge → STAGE-ABBRUCH "
                  f"({len(self._retry_queue)} Ticker in Retry-Queue)")
            return False
        if n >= self.net_error_pause_threshold:
            print(f"      [!] {n} Netzwerk-Fehler in Folge → "
                  f"{self.network_pause_seconds}s Pause...")
            time.sleep(self.network_pause_seconds)
        return True

    def _handle_429(self, attempt: int, resp=None) -> bool:
        """Backoff bei HTTP 429. Returns False bei Abort.

        Vorrang hat ein vom Server vorgegebener ``Retry-After``-Header (API-eigenes
        Schema, R5). Fehlt er, greift der berechnete Backoff ``2^n`` (max
        ``self.backoff_max``).
        """
        self._consecutive_429 += 1
        n = self._consecutive_429
        if n >= self.rate_limit_abort_threshold:
            print(f"      [!!] {n} consecutive 429 → STAGE-ABBRUCH")
            return False

        retry_after = self._parse_retry_after(resp)
        if retry_after is not None:
            backoff = retry_after  # API-Vorgabe hat Vorrang vor dem 2^n-Backoff
            print(f"      [!] OpenFIGI 429 (Versuch {attempt}/{self.rate_limit_abort_threshold}) → "
                  f"Retry-After {backoff:.1f}s (API-Vorgabe)")
        else:
            backoff = min(self.backoff_base ** attempt, self.backoff_max)
            print(f"      [!] OpenFIGI 429 (Versuch {attempt}/{self.rate_limit_abort_threshold}) → "
                  f"Backoff {backoff:.1f}s")
        logger.warning(f"OpenFIGI 429 backoff {backoff:.1f}s (consecutive {n})")
        time.sleep(backoff)
        return True

    @staticmethod
    def _parse_retry_after(resp) -> Optional[float]:
        """Liest den ``Retry-After``-Header: Sekunden-Ganzzahl ODER HTTP-Datum.

        Returns Sekunden (>=0) als float, sonst None (Header fehlt/ungueltig).
        """
        if resp is None:
            return None
        try:
            val = resp.headers.get("Retry-After")
        except AttributeError:
            return None
        if not val:
            return None
        val = str(val).strip()
        if val.isdigit():
            return float(val)
        try:  # HTTP-Datum-Form (RFC 7231)
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(val)
            if dt is None:
                return None
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            return max((dt - now).total_seconds(), 0.0)
        except (TypeError, ValueError, OverflowError):
            return None

    # ------------------------------------------------------------------
    # Result-Konstruktion
    # ------------------------------------------------------------------

    def _build_normalized(
        self,
        classification: TickerClassification,
        payload: dict,
        errors: list[str],
    ) -> NormalizedTicker:
        """Wendet Type-Korrektur an: STOCK + securityType='ETP' → ETF.

        OpenFIGI meldet Fonds als securityType='ETP' (securityType2 ist dort
        'Mutual Fund'), s. tests/fixtures/openfigi/mapping_responses.json.
        """
        cls = classification

        if cls.ticker_type == TickerType.STOCK and payload.get("security_type_figi") == "ETP":
            new_reason = cls.classification_reason + "+openfigi_corrected"
            cls = dataclasses.replace(
                cls,
                ticker_type=TickerType.ETF,
                classification_reason=new_reason,
            )
            print(f"      [i] {classification.raw_ticker}: Type-Korrektur "
                  f"STOCK → ETF (securityType=ETP)")

        return NormalizedTicker(
            classification=cls,
            figi=payload.get("figi"),
            ticker_figi=payload.get("ticker_figi"),
            asset_name_figi=payload.get("asset_name_figi"),
            security_type_figi=payload.get("security_type_figi"),
            market_sector_figi=payload.get("market_sector_figi"),
            share_class_figi=payload.get("share_class_figi"),
            normalization_errors=list(errors),
        )

    def _empty_result(
        self, classification: TickerClassification, errors: list[str]
    ) -> NormalizedTicker:
        return NormalizedTicker(
            classification=classification,
            figi=None,
            ticker_figi=None,
            asset_name_figi=None,
            security_type_figi=None,
            market_sector_figi=None,
            share_class_figi=None,
            normalization_errors=list(errors),
        )
