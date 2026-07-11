"""Tavily implementation of the provider-neutral search interface.

Five functional endpoints use the official ``tavily-python`` SDK.  Version
0.7.26 does not expose ``GET /usage``, so that single monitoring endpoint is
called directly via httpx while retaining the same attribution headers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import httpx

from .. import config
from .base import (
    MissingSearchAPIKeyError,
    SearchAPIError,
    SearchBudgetExhausted,
    SearchProvider,
    SearchRateLimitError,
)

logger = logging.getLogger(__name__)

USAGE_URL = "https://api.tavily.com/usage"


class TavilySearchAPI(SearchProvider):
    """Official-SDK Tavily adapter with retry, attribution and credit logging."""

    def __init__(
        self,
        *,
        run_id: str,
        role: str,
        api_key: str | None = None,
        settings: Mapping[str, Any] | None = None,
        sdk_client: Any | None = None,
        sdk_factory: Callable[..., Any] | None = None,
        usage_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = dict(settings or config.effective())
        self.run_id = str(run_id)
        self.role = str(role)
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise MissingSearchAPIKeyError(
                "TAVILY_API_KEY ist nicht gesetzt. Trage den Key in "
                f"{config.PACKAGE_ROOT / '.env'} ein."
            )

        package_name = __package__.split(".")[0]
        tavily_config = self.settings.get("TAVILY", {})
        project_ids = (
            tavily_config.get("project_ids", {})
            if isinstance(tavily_config, Mapping)
            else {}
        )
        if package_name not in project_ids:
            raise SearchAPIError(
                f"Keine Tavily project_id fuer Package {package_name!r} konfiguriert."
            )
        self.project_id = str(project_ids[package_name])
        self.session_id = self.run_id
        self.max_retries = int(self.settings.get("SEARCH_MAX_RETRIES", 2))
        self._sleep = sleep
        self._usage_client = usage_client or httpx.Client()

        if sdk_client is None:
            if sdk_factory is None:
                try:
                    from tavily import TavilyClient
                except ImportError as exc:  # pragma: no cover - packaging guard
                    raise SearchAPIError(
                        "tavily-python fehlt; installiere die gepinnte Dependency."
                    ) from exc
                # tavily-python 0.7.26 collapses 432/433 into ForbiddenError
                # and discards retry-after on 429.  The exact pin makes this
                # narrow private-hook override stable and preserves the status
                # required by our retry/budget policy.
                class StatusPreservingTavilyClient(TavilyClient):
                    def _handle_error_response(self, response: Any) -> None:
                        status_code = int(response.status_code)
                        if status_code in {429, 432, 433}:
                            error = SearchAPIError(
                                f"Tavily antwortete mit HTTP {status_code}.",
                                status_code=status_code,
                                retry_after=_retry_after_from(response),
                            )
                            setattr(error, "response", response)
                            raise error
                        super()._handle_error_response(response)

                sdk_factory = StatusPreservingTavilyClient
            sdk_client = sdk_factory(
                api_key=self.api_key,
                project_id=self.project_id,
                session_id=self.session_id,
            )
        self._sdk = sdk_client

        runs_dir = Path(self.settings["DATA_DIR"]) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.usage_log_path = runs_dir / f"{self.run_id}.search_usage.jsonl"

    @property
    def attribution_headers(self) -> dict[str, str]:
        """Headers used by the direct usage endpoint (and by SDK metadata)."""

        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Project-ID": self.project_id,
            "X-Session-Id": self.session_id,
        }

    def search(self, query: str, **params: Any) -> dict[str, Any]:
        if len(query) >= 400:
            raise ValueError("Tavily-Suchanfragen muessen kuerzer als 400 Zeichen sein.")
        request = dict(params)
        request.pop("auto_parameters", None)
        request["include_usage"] = True
        if "depth" in request:
            request["search_depth"] = request.pop("depth")
        request.setdefault(
            "search_depth",
            self.settings.get("SEARCH_API", {}).get("depth", "basic"),
        )
        return self._sdk_call(
            "search",
            lambda: self._sdk.search(query=query, **request),
            depth=request.get("search_depth"),
        )

    def extract(
        self, urls: Sequence[str], query: str, **params: Any
    ) -> dict[str, Any]:
        request = dict(params)
        request.pop("auto_parameters", None)
        request["include_usage"] = True
        if "depth" in request:
            request["extract_depth"] = request.pop("depth")
        request["query"] = query
        return self._sdk_call(
            "extract",
            lambda: self._sdk.extract(urls=list(urls), **request),
            depth=request.get("extract_depth"),
        )

    def map_site(self, url: str, **params: Any) -> dict[str, Any]:
        request = self._site_request("map", params)
        return self._sdk_call(
            "map",
            lambda: self._sdk.map(url=url, **request),
            depth=request.get("max_depth"),
        )

    def crawl(self, url: str, **params: Any) -> dict[str, Any]:
        request = self._site_request("crawl", params)
        return self._sdk_call(
            "crawl",
            lambda: self._sdk.crawl(url=url, **request),
            depth=request.get("extract_depth") or request.get("max_depth"),
        )

    def research(self, input: str, **params: Any) -> dict[str, Any]:
        tavily_config = self.settings.get("TAVILY", {})
        if not tavily_config.get("use_research_endpoint", False):
            raise SearchAPIError(
                "Tavily /research ist per TAVILY.use_research_endpoint deaktiviert.",
                endpoint="research",
            )
        request = dict(params)
        # tavily-python 0.7.26 does not document include_usage for /research.
        # Sending it would rely on undefined API behaviour, so the successful
        # log row explicitly marks its credits as unavailable instead.
        request.pop("include_usage", None)
        request.pop("auto_parameters", None)
        request.setdefault("model", tavily_config.get("research_model", "mini"))
        return self._sdk_call(
            "research",
            lambda: self._sdk.research(input=input, **request),
            depth=request.get("model"),
            usage_expected=False,
        )

    def get_usage(self) -> dict[str, Any]:
        def request() -> dict[str, Any]:
            response = self._usage_client.get(
                USAGE_URL,
                headers=self.attribution_headers,
                timeout=30.0,
            )
            status_code = int(getattr(response, "status_code", 200))
            if status_code >= 400:
                error = SearchAPIError(
                    f"Tavily GET /usage antwortete mit HTTP {status_code}.",
                    endpoint="usage",
                    status_code=status_code,
                    retry_after=_retry_after_from(response),
                )
                setattr(error, "response", response)
                raise error
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise SearchAPIError(
                    "Tavily GET /usage lieferte kein JSON-Objekt.",
                    endpoint="usage",
                    status_code=status_code,
                )
            return dict(payload)

        return self._sdk_call(
            "usage",
            request,
            depth=None,
            usage_expected=False,
            usage_note="Monitoring-Endpunkt; verursacht keine Search-Credits.",
            unreported_credits=0.0,
        )

    def check_budget(self) -> dict[str, Any]:
        """Check the shared account cycle against plan and configured reserve.

        This method is intentionally not called before each API request.  The
        pipeline invokes it once at run start and once after every agent step,
        respecting Tavily's tight 10-requests-per-10-minutes usage rate limit.
        """

        usage = self.get_usage()
        used = _usage_number(
            usage,
            ("account", "plan_usage"),
            ("key", "usage"),
            ("usage",),
            ("plan_usage",),
        )
        reported_limit = _usage_number(
            usage,
            ("account", "plan_limit"),
            ("key", "limit"),
            ("limit",),
            ("plan_limit",),
        )
        tavily_config = self.settings.get("TAVILY", {})
        configured_limit = float(tavily_config.get("monthly_credit_budget", 0))
        limits = [value for value in (reported_limit, configured_limit) if value > 0]
        if not limits:
            raise SearchAPIError(
                "Tavily-Nutzungsantwort enthaelt kein auswertbares Credit-Limit.",
                endpoint="usage",
            )
        effective_limit = min(limits)
        reserve = float(tavily_config.get("credit_reserve", 0))
        remaining = effective_limit - used
        status = {
            "used": used,
            "limit": effective_limit,
            "remaining": remaining,
            "reserve": reserve,
            "raw": usage,
        }
        if remaining < reserve:
            raise SearchBudgetExhausted(
                (
                    "Tavily-Budget erschoepft: "
                    f"{remaining:g} Credits verbleiben, Reserve {reserve:g}."
                ),
                endpoint="usage",
            )
        return status

    def _site_request(
        self, endpoint: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        request = dict(params)
        request.pop("instructions", None)
        request.pop("auto_parameters", None)
        request["include_usage"] = True
        defaults = self.settings.get("SEARCH_API", {}).get(endpoint, {})
        for key in ("max_depth", "max_breadth", "limit"):
            candidates: list[int] = []
            if isinstance(defaults, Mapping) and defaults.get(key) is not None:
                candidates.append(int(defaults[key]))
            if request.get(key) is not None:
                candidates.append(int(request[key]))
            if candidates:
                request[key] = min(candidates)
        if endpoint == "crawl" and isinstance(defaults, Mapping):
            request.setdefault("extract_depth", defaults.get("extract_depth"))
        return {key: value for key, value in request.items() if value is not None}

    def _sdk_call(
        self,
        endpoint: str,
        operation: Callable[[], Any],
        *,
        depth: Any,
        usage_expected: bool = True,
        usage_note: str | None = None,
        unreported_credits: float | None = None,
    ) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                raw_result = operation()
                if not isinstance(raw_result, Mapping):
                    raise SearchAPIError(
                        f"Tavily {endpoint} lieferte kein JSON-Objekt.",
                        endpoint=endpoint,
                    )
                result = dict(raw_result)
            except Exception as exc:
                status_code, retry_after = _error_details(exc)
                if status_code in {432, 433}:
                    self._log_usage(
                        endpoint,
                        depth=depth,
                        credits=0.0,
                        ok=False,
                        usage_reported=True,
                        note=f"HTTP {status_code}: Budget erschoepft.",
                    )
                    raise SearchBudgetExhausted(
                        f"Tavily-Budget erschoepft (HTTP {status_code}).",
                        endpoint=endpoint,
                        status_code=status_code,
                        retry_after=retry_after,
                    ) from exc
                if status_code == 429 and attempt < self.max_retries:
                    delay = retry_after if retry_after is not None else 2.0**attempt
                    self._sleep(delay)
                    continue
                self._log_usage(
                    endpoint,
                    depth=depth,
                    credits=0.0,
                    ok=False,
                    usage_reported=True,
                    note=str(exc),
                )
                if status_code == 429:
                    raise SearchRateLimitError(
                        (
                            "Tavily-Rate-Limit nach "
                            f"{self.max_retries} Retries weiterhin aktiv."
                        ),
                        endpoint=endpoint,
                        status_code=status_code,
                        retry_after=retry_after,
                    ) from exc
                if isinstance(exc, SearchAPIError):
                    raise
                raise SearchAPIError(
                    f"Tavily {endpoint} fehlgeschlagen: {exc}",
                    endpoint=endpoint,
                    status_code=status_code,
                    retry_after=retry_after,
                ) from exc

            failed_results = result.get("failed_results") or []
            ok = not bool(failed_results)
            if failed_results:
                # Tavily reports failed Extract/Map items without charging
                # them.  Keep the failure row explicit and numeric so later
                # aggregations cannot mistake it for unknown Research costs.
                credits = 0.0
                usage_reported = True
                note = f"{len(failed_results)} failed_results; keine Credits."
            elif usage_expected:
                credits = _credits_from(result)
                usage_reported = _has_credit_usage(result)
                note = None if usage_reported else "Response enthaelt keine Credits."
            else:
                credits = unreported_credits
                usage_reported = unreported_credits is not None
                note = usage_note or "Credits fuer diesen Endpunkt nicht ausgewiesen."
            self._log_usage(
                endpoint,
                depth=depth,
                credits=credits,
                ok=ok,
                usage_reported=usage_reported,
                note=note,
            )
            return result

        raise AssertionError("unreachable")

    def _log_usage(
        self,
        endpoint: str,
        *,
        depth: Any,
        credits: float | None,
        ok: bool,
        usage_reported: bool,
        note: str | None,
    ) -> None:
        row: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "role": self.role,
            "endpoint": endpoint,
            "depth": depth,
            "credits": credits,
            "ok": ok,
            "usage_reported": usage_reported,
        }
        if note:
            row["note"] = note
        with self.usage_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


# Short aliases for callers that prefer provider/client terminology.
TavilyProvider = TavilySearchAPI
TavilySearchClient = TavilySearchAPI


def _error_details(exc: Exception) -> tuple[int | None, float | None]:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None and response is not None:
        retry_after = _retry_after_from(response)
    try:
        normalized_retry = float(retry_after) if retry_after is not None else None
    except (TypeError, ValueError):
        normalized_retry = None
    return normalized_status, normalized_retry


def _retry_after_from(response: Any) -> float | None:
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _has_credit_usage(result: Mapping[str, Any]) -> bool:
    usage = result.get("usage")
    if isinstance(usage, Mapping):
        return "credits" in usage
    return False


def _credits_from(result: Mapping[str, Any]) -> float | None:
    usage = result.get("usage")
    if not isinstance(usage, Mapping) or "credits" not in usage:
        return None
    credits = usage["credits"]
    if isinstance(credits, Mapping):
        values = []
        for value in credits.values():
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        return sum(values)
    try:
        return float(credits)
    except (TypeError, ValueError):
        return None


def _usage_number(data: Mapping[str, Any], *paths: tuple[str, ...]) -> float:
    for path in paths:
        value: Any = data
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                break
            value = value[key]
        else:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 0.0
