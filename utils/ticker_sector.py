"""
yfinance-Wrapper — Sektor/Industry-Anreicherung.

Spezifikation: rebuild_pitch.md §7 + §11 (Defensiv-Mechanismen).

Provider-agnostische Schnittstelle: gibt SectorInfo zurück. Bei
Provider-Wechsel (FMP, Alpha Vantage) wird genau diese Klasse ersetzt.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import yfinance as yf

from config import settings
from utils.ticker_classifier import TickerType
from utils.ticker_normalizer import NormalizedTicker


logger = logging.getLogger(__name__)


# Identische Pattern-Liste wie in ticker_normalizer (bewusst dupliziert, Pitch §16).
_NETWORK_ERROR_PATTERNS = (
    "Could not resolve host",
    "TLS connect error",
    "Connection refused",
    "Connection reset",
    "Connection timed out",
    "Network is unreachable",
)


# Welche TickerType-Werte gehen direkt an yfinance.
_YF_ELIGIBLE_TYPES = frozenset({
    TickerType.STOCK,
    TickerType.SHARECLASS,
    TickerType.FOREIGN_STOCK,
    TickerType.ETF,
    TickerType.NUMERIC_SUFFIX,
})


@dataclass(frozen=True)
class SectorInfo:
    """Ergebnis der Sektor-Anreicherung. Provider-agnostisch.

    Felder mit `_yf`-Suffix stammen aus yfinance — eindeutige Abgrenzung
    von OpenFIGI-Feldern (Suffix `_figi`) und Classifier-Outputs.
    """
    normalized: NormalizedTicker
    sector_yf: Optional[str]
    industry_yf: Optional[str]
    quote_type_yf: Optional[str]
    sector_errors: list[str]


class TickerSectorEnricher:
    """yfinance-Wrapper mit allen Defensiv-Mechanismen aus Pitch §11."""

    def __init__(
        self,
        cache_dir: str,
        preferred_mapping_path: Optional[str] = None,
        delisted_output_path: Optional[str] = None,
        unmatched_preferred_path: Optional[str] = None,
        confirm_threshold: int = settings.TICKER_LOOKUP_CONFIRM_THRESHOLD,
        pause_seconds: float = settings.TICKER_LOOKUP_PAUSE_SECONDS,
        net_error_pause: int = settings.TICKER_LOOKUP_NET_ERROR_PAUSE,
        net_error_abort: int = settings.TICKER_LOOKUP_NET_ERROR_ABORT,
        cache_checkpoint_interval: int = settings.TICKER_LOOKUP_CACHE_INTERVAL,
        confirm_callback: Optional[Callable[[int], bool]] = None,
        cache_ttl_days: Optional[int] = None,
    ):
        self.cache_dir = cache_dir
        self.positive_cache_path = os.path.join(
            cache_dir, settings.TICKER_INDUSTRY_CACHE_FILE
        )
        self.null_cache_path = os.path.join(
            cache_dir, settings.TICKER_0_CACHE_FILE
        )
        self.preferred_mapping_path = preferred_mapping_path
        self.delisted_output_path = delisted_output_path
        self.unmatched_preferred_path = unmatched_preferred_path

        self.confirm_threshold = confirm_threshold
        self.pause_seconds = pause_seconds
        self.net_error_pause = net_error_pause
        self.net_error_abort = net_error_abort
        self.net_error_pause_threshold = getattr(
            settings, "TICKER_LOOKUP_NET_ERROR_PAUSE_THRESHOLD", 3
        )
        self.cache_checkpoint_interval = cache_checkpoint_interval
        self.confirm_callback = confirm_callback
        self.cache_ttl_days = (
            cache_ttl_days
            if cache_ttl_days is not None
            else getattr(settings, "TICKER_SECTOR_CACHE_TTL_DAYS", 30)
        )

        # State
        self._positive_cache: dict[str, dict] = {}
        self._null_cache: set[str] = set()
        self._preferred_map: dict[str, str] = {}
        self._lookups_since_checkpoint = 0
        self._consecutive_network_errors = 0
        self._retry_queue: list[NormalizedTicker] = []
        self._delisted_records: list[dict] = []
        self._pref_stats: Counter = Counter()
        self._unmatched_preferred: list[str] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def enrich_batch(
        self,
        normalized: list[NormalizedTicker],
        transaction_dates: Optional[list] = None,
    ) -> list[SectorInfo]:
        """Anreicherung für einen Batch von NormalizedTicker.

        Routing:
        - Eligible Types (STOCK, ETF, ...) → direkter yfinance-Call
        - PREFERRED → via Vorzugsaktien-Mapping → yfinance(stammaktie)
        - Sonst → leeres SectorInfo (kein Call)

        transaction_dates ist eine optionale parallele Liste (gleiche Länge wie
        normalized) mit dem transaction_date pro Trade — wird ausschließlich für
        Year-Aggregation im Delisted-Report (Pitch §11.7) genutzt.

        Wirft RuntimeError bei zu vielen consecutive Network-Errors.
        """
        if not normalized:
            return []
        if transaction_dates is not None and len(transaction_dates) != len(normalized):
            raise ValueError(
                f"transaction_dates hat Laenge {len(transaction_dates)}, "
                f"normalized hat Laenge {len(normalized)} — muss identisch sein."
            )

        self._ensure_loaded()
        self._consecutive_network_errors = 0

        # 1. Vorab klassifizieren — was muss überhaupt gefetcht werden?
        targets: list[tuple[int, NormalizedTicker, Optional[str]]] = []
        # (input_idx, normalized, lookup_ticker_or_None)
        for idx, norm in enumerate(normalized):
            ttype = norm.classification.ticker_type
            raw = norm.classification.raw_ticker

            if ttype == TickerType.PREFERRED:
                parent = self._resolve_preferred(norm)
                if parent is None:
                    self._pref_stats["unmatched"] += 1
                    self._unmatched_preferred.append(raw)
                    print(f"      [!] Preferred-Ticker {raw} ohne Mapping")
                    targets.append((idx, norm, None))
                else:
                    self._pref_stats["matched"] += 1
                    targets.append((idx, norm, parent))
            elif ttype in _YF_ELIGIBLE_TYPES:
                targets.append((idx, norm, raw))
            else:
                targets.append((idx, norm, None))

        # 2. Cache-Status für noch zu fetchende Ticker.
        misses: list[str] = []
        seen: set[str] = set()
        for _, _, lookup in targets:
            if lookup is None or lookup in seen:
                continue
            seen.add(lookup)
            if lookup in self._null_cache:
                continue
            if lookup in self._positive_cache:
                if self._is_cache_entry_fresh(self._positive_cache[lookup]):
                    continue
            misses.append(lookup)

        # 3. Confirmation-Prompt bei vielen neuen Tickern.
        if misses and len(misses) >= self.confirm_threshold:
            est_min = len(misses) * self.pause_seconds / 60
            if not self._confirm_large_batch(len(misses), est_min):
                print(f"      [SKIP] Sector-Lookup abgebrochen — {len(misses)} Ticker ungelookt")
                # Cache trotzdem speichern (z.B. preferred-mapping-resolutions).
                self._save_caches()
                return [self._empty_result(n, ["user_skip"]) for n in normalized]

        # 4. yfinance-Loop mit Logger-Suppression.
        if misses:
            yf_logger = logging.getLogger("yfinance")
            previous_level = yf_logger.level
            yf_logger.setLevel(logging.CRITICAL)
            try:
                self._fetch_misses(misses)
            finally:
                yf_logger.setLevel(previous_level)

        # 5. Retry-Runde für Network-Error-Tickers.
        if self._retry_queue:
            self._run_retry_round()

        # 6. SectorInfo aus Cache zusammenbauen.
        results: list[SectorInfo] = []
        for idx, norm, lookup in targets:
            tx_date = transaction_dates[idx] if transaction_dates is not None else None
            results.append(self._build_sector_info(norm, lookup, tx_date))

        # 7. Final save + Delisted-Report + Preferred-Mapping-Report (§11.8).
        self._save_caches()
        self._write_delisted_stage()
        self._write_unmatched_preferred()
        self._print_preferred_summary()

        return results

    # ------------------------------------------------------------------
    # Cache (JSON split, lazy load)
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_caches()
        self._preferred_map = self._load_preferred_mapping()
        self._loaded = True

    def _load_caches(self) -> None:
        # Positive Cache.
        if os.path.isfile(self.positive_cache_path):
            try:
                with open(self.positive_cache_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    # Bestand kann {ticker: {sector, industry}} (ohne cached_at) sein.
                    for ticker, payload in raw.items():
                        if isinstance(payload, dict):
                            self._positive_cache[ticker] = payload
                logger.info(
                    f"Positive Cache geladen: {len(self._positive_cache)} Eintraege"
                )
            except (OSError, ValueError) as exc:
                print(f"      [!] Cache-Datei {self.positive_cache_path} unlesbar "
                      f"({exc}) → starte mit leerem Cache")

        # Null Cache.
        if os.path.isfile(self.null_cache_path):
            try:
                with open(self.null_cache_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    self._null_cache = set(raw)
                elif isinstance(raw, dict):
                    self._null_cache = set(raw.keys())
                logger.info(
                    f"Null Cache geladen: {len(self._null_cache)} Eintraege"
                )
            except (OSError, ValueError) as exc:
                print(f"      [!] Cache-Datei {self.null_cache_path} unlesbar "
                      f"({exc}) → starte mit leerem Cache")

    def _save_caches(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)

        with open(self.positive_cache_path, "w", encoding="utf-8") as f:
            json.dump(self._positive_cache, f, ensure_ascii=False, indent=2)

        with open(self.null_cache_path, "w", encoding="utf-8") as f:
            json.dump(sorted(self._null_cache), f, ensure_ascii=False, indent=2)

    def _save_checkpoint(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self.positive_cache_path, "w", encoding="utf-8") as f:
            json.dump(self._positive_cache, f, ensure_ascii=False, indent=2)

    def _is_cache_entry_fresh(self, entry: dict) -> bool:
        """Bestand ohne cached_at = ewig gültig (Migrations-Toleranz)."""
        cached_at = entry.get("cached_at")
        if not cached_at:
            return True
        try:
            ts = datetime.fromisoformat(cached_at)
        except (TypeError, ValueError):
            return True
        return datetime.now() - ts <= timedelta(days=self.cache_ttl_days)

    # ------------------------------------------------------------------
    # Preferred-Mapping
    # ------------------------------------------------------------------

    def _load_preferred_mapping(self) -> dict[str, str]:
        if not self.preferred_mapping_path:
            return {}
        if not os.path.isfile(self.preferred_mapping_path):
            return {}

        mapping: dict[str, str] = {}
        try:
            with open(self.preferred_mapping_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pref = (row.get("vorzugsaktien_ticker") or "").strip()
                    parent = (row.get("stammaktien_ticker") or "").strip()
                    if pref and parent:
                        mapping[pref] = parent
        except OSError as exc:
            print(f"      [!] Preferred-Mapping {self.preferred_mapping_path} unlesbar: {exc}")
            return {}

        logger.info(f"Vorzugsaktien-Mapping geladen: {len(mapping)} Eintraege")
        return mapping

    def _resolve_preferred(self, normalized: NormalizedTicker) -> Optional[str]:
        raw = normalized.classification.raw_ticker
        return self._preferred_map.get(raw)

    # ------------------------------------------------------------------
    # API-Calls
    # ------------------------------------------------------------------

    def _fetch_misses(self, misses: list[str]) -> None:
        success = 0
        empty = 0
        net_errors = 0

        for i, ticker in enumerate(misses):
            info, error_kind = self._fetch_with_hyphen_fallback(ticker)

            if error_kind == "network":
                net_errors += 1
                # Item für Retry merken (nur einmal).
                if ticker not in {n.classification.raw_ticker for n in self._retry_queue}:
                    # Wir merken hier nur den Ticker-String — der Retry geht über
                    # _fetch_with_hyphen_fallback erneut.
                    self._retry_queue.append(self._fake_normalized_for_retry(ticker))
                continue

            if info is None or not info.get("sector"):
                self._null_cache.add(ticker)
                empty += 1
            else:
                self._positive_cache[ticker] = {
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "quoteType": info.get("quoteType"),
                    "cached_at": datetime.now().isoformat(),
                }
                success += 1

            self._lookups_since_checkpoint += 1

            if self._lookups_since_checkpoint >= self.cache_checkpoint_interval:
                self._save_checkpoint()
                print(f"      [i] Cache-Checkpoint: "
                      f"{self._lookups_since_checkpoint} Lookups gespeichert")
                self._lookups_since_checkpoint = 0

            time.sleep(self.pause_seconds)

        print(f"      Sector-Fetch fertig: {success} OK, {empty} leer, "
              f"{net_errors} Netz-Fehler")

    def _fetch_with_hyphen_fallback(
        self, ticker: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """yfinance(ticker); bei leerem Result einmaliger Retry mit /-Variante."""
        info, error_kind = self._fetch_yf_info(ticker)
        if error_kind is not None:
            return None, error_kind

        # Erfolg oder leerer Treffer → Hyphen-Fallback nur, wenn '-' im Ticker
        # und Result leer.
        if (info is None or not info.get("sector")) and "-" in ticker:
            alt = ticker.replace("-", "/")
            print(f"      [i] {ticker} ohne Sektor → Retry mit Slash-Variante {alt}")
            info_alt, error_kind_alt = self._fetch_yf_info(alt)
            if error_kind_alt is not None:
                return None, error_kind_alt
            if info_alt and info_alt.get("sector"):
                return info_alt, None

        return info, None

    def _fetch_yf_info(self, ticker: str) -> tuple[Optional[dict], Optional[str]]:
        try:
            t = yf.Ticker(ticker)
            info = getattr(t, "info", None) or {}
        except Exception as exc:
            err_str = str(exc)
            if self._is_network_error(err_str):
                aborted = not self._handle_network_error(err_str)
                if aborted:
                    raise RuntimeError(
                        f"yfinance: {self._consecutive_network_errors} consecutive "
                        f"network errors → Stage-Abbruch"
                    )
                return None, "network"
            logger.debug(f"yfinance Exception fuer {ticker}: {exc}")
            return None, "other"

        # Erfolg → Counter resetten.
        self._consecutive_network_errors = 0
        sector = info.get("sector") if isinstance(info, dict) else None
        if not sector:
            print(f"      [i] {ticker}: kein Sektor (delisted/unbekannt)")
            return info if isinstance(info, dict) else {}, None
        return info, None

    def _run_retry_round(self) -> None:
        retries = list({n.classification.raw_ticker for n in self._retry_queue})
        self._retry_queue = []
        print(f"      [i] Retry-Runde: {len(retries)} Ticker mit Netzwerk-Fehler...")

        ok = 0
        fail = 0
        self._consecutive_network_errors = 0

        yf_logger = logging.getLogger("yfinance")
        previous_level = yf_logger.level
        yf_logger.setLevel(logging.CRITICAL)
        try:
            for ticker in retries:
                info, error_kind = self._fetch_with_hyphen_fallback(ticker)
                if error_kind == "network":
                    fail += 1
                    continue
                if info is None or not info.get("sector"):
                    self._null_cache.add(ticker)
                    fail += 1
                else:
                    self._positive_cache[ticker] = {
                        "sector": info.get("sector"),
                        "industry": info.get("industry"),
                        "quoteType": info.get("quoteType"),
                        "cached_at": datetime.now().isoformat(),
                    }
                    ok += 1
                time.sleep(self.pause_seconds)
        finally:
            yf_logger.setLevel(previous_level)

        print(f"      [i] Retry: {ok} OK, {fail} weiterhin fehlgeschlagen")

    @staticmethod
    def _fake_normalized_for_retry(ticker: str) -> NormalizedTicker:
        """Minimaler Stub um Ticker in Retry-Queue zu halten (nur raw_ticker zählt)."""
        from utils.ticker_classifier import (
            ApiStrategy, Confidence, TickerClassification,
        )
        cls = TickerClassification(
            raw_ticker=ticker, company_name="", ticker_base=ticker,
            ticker_type=TickerType.STOCK, api_strategy=ApiStrategy.YFINANCE_DIRECT,
            exch_code=None, confidence=Confidence.LOW,
            classification_reason="retry_stub", manual_review=False,
            series_letter=None, status_suffix=None,
        )
        return NormalizedTicker(
            classification=cls, figi=None, ticker_figi=None,
            name_figi=None, security_type_figi=None, market_sector_figi=None,
            normalization_errors=[],
        )

    # ------------------------------------------------------------------
    # Defensive Helpers
    # ------------------------------------------------------------------

    def _is_network_error(self, error_msg: str) -> bool:
        return any(p in error_msg for p in _NETWORK_ERROR_PATTERNS)

    def _handle_network_error(self, error_msg: str) -> bool:
        self._consecutive_network_errors += 1
        n = self._consecutive_network_errors
        print(f"      [!] yfinance Netzwerk-Fehler {n}/{self.net_error_abort}: {error_msg}")
        logger.warning(f"yfinance Netzwerk-Fehler {n}: {error_msg}")

        if n >= self.net_error_abort:
            print(f"      [!!] {n} Netzwerk-Fehler in Folge → STAGE-ABBRUCH")
            return False
        if n >= self.net_error_pause_threshold:
            print(f"      [!] {n} Netzwerk-Fehler in Folge → "
                  f"{self.net_error_pause}s Pause...")
            time.sleep(self.net_error_pause)
        return True

    def _confirm_large_batch(self, n_misses: int, est_minutes: float) -> bool:
        msg = (f"      [?] {n_misses} neue Ticker zu fetchen — "
               f"geschätzt {est_minutes:.1f}min. [Enter]/Ctrl+C: ")
        if self.confirm_callback is not None:
            return self.confirm_callback(n_misses)
        try:
            input(msg)
            return True
        except (KeyboardInterrupt, EOFError):
            return False

    # ------------------------------------------------------------------
    # Delisted-Tracking
    # ------------------------------------------------------------------

    def _track_delisted(
        self,
        normalized: NormalizedTicker,
        transaction_date=None,
    ) -> None:
        cls = normalized.classification
        self._delisted_records.append({
            "ticker": cls.raw_ticker,
            "ticker_type": cls.ticker_type.value,
            "company_name": cls.company_name,
            "classification_reason": cls.classification_reason,
            "transaction_date": self._normalize_year_input(transaction_date),
        })

    @staticmethod
    def _normalize_year_input(value) -> Optional[str]:
        """Bringt einen Datumswert in ISO-String-Form (für CSV + Year-Extract).

        Akzeptiert: None, str (ISO/Datum), datetime, pandas Timestamp.
        """
        if value is None:
            return None
        if isinstance(value, str):
            return value if value else None
        try:
            return value.isoformat()
        except AttributeError:
            return str(value)

    @staticmethod
    def _extract_year(value) -> Optional[int]:
        if not value:
            return None
        try:
            return int(str(value)[:4])
        except (TypeError, ValueError):
            return None

    def _write_delisted_stage(self) -> None:
        if not self._delisted_records or not self.delisted_output_path:
            return
        os.makedirs(os.path.dirname(self.delisted_output_path) or ".", exist_ok=True)
        try:
            with open(self.delisted_output_path, "w", encoding="utf-8", newline="") as f:
                fieldnames = list(self._delisted_records[0].keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._delisted_records)
        except PermissionError:
            print(f"      [!] {self.delisted_output_path} nicht schreibbar (offen?)")
            return

        n = len(self._delisted_records)
        print(f"      [i] Delisted/Unbekannte Ticker: {n} Eintraege → "
              f"{self.delisted_output_path}")

        # Pitch §11.7: Year-Aggregation als Terminal-Report.
        year_counter: Counter = Counter()
        n_no_date = 0
        for rec in self._delisted_records:
            year = self._extract_year(rec.get("transaction_date"))
            if year is None:
                n_no_date += 1
            else:
                year_counter[year] += 1
        if year_counter:
            parts = [f"{year}: {count}" for year, count in sorted(year_counter.items())]
            print(f"      [i] Delisted nach Jahr: {', '.join(parts)}"
                  + (f" (ohne Datum: {n_no_date})" if n_no_date else ""))

    def _print_preferred_summary(self) -> None:
        """Pitch §11.8: Terminal-Report Vorzugsaktien-Mapping."""
        if not self._pref_stats:
            return
        matched = self._pref_stats.get("matched", 0)
        unmatched = self._pref_stats.get("unmatched", 0)
        if matched == 0 and unmatched == 0:
            return
        print(f"      [i] Preferred-Mapping: matched={matched}, "
              f"unmatched={unmatched}")

    def _write_unmatched_preferred(self) -> None:
        if not self._unmatched_preferred or not self.unmatched_preferred_path:
            return
        os.makedirs(os.path.dirname(self.unmatched_preferred_path) or ".", exist_ok=True)
        try:
            with open(self.unmatched_preferred_path, "w",
                      encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["preferred_ticker"])
                for ticker in sorted(set(self._unmatched_preferred)):
                    writer.writerow([ticker])
        except PermissionError:
            print(f"      [!] {self.unmatched_preferred_path} nicht schreibbar")

    # ------------------------------------------------------------------
    # Result-Konstruktion
    # ------------------------------------------------------------------

    def _build_sector_info(
        self,
        normalized: NormalizedTicker,
        lookup_ticker: Optional[str],
        transaction_date=None,
    ) -> SectorInfo:
        if lookup_ticker is None:
            return self._empty_result(normalized, [])

        if lookup_ticker in self._null_cache:
            self._track_delisted(normalized, transaction_date)
            return self._empty_result(normalized, ["delisted_or_unknown"])

        entry = self._positive_cache.get(lookup_ticker)
        if entry is None:
            # Fetch hat fehlgeschlagen (Network/Retry erschöpft).
            return self._empty_result(normalized, ["fetch_failed"])

        return SectorInfo(
            normalized=normalized,
            sector_yf=entry.get("sector"),
            industry_yf=entry.get("industry"),
            quote_type_yf=entry.get("quoteType"),
            sector_errors=[],
        )

    def _empty_result(
        self, normalized: NormalizedTicker, errors: list[str]
    ) -> SectorInfo:
        return SectorInfo(
            normalized=normalized,
            sector_yf=None,
            industry_yf=None,
            quote_type_yf=None,
            sector_errors=list(errors),
        )
