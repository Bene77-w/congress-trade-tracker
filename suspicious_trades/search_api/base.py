"""Provider-neutral web-search interface and agent-tool wiring.

The public provider contract deliberately contains Tavily's five functional
endpoints plus the usage endpoint.  Providers that do not implement an
endpoint inherit the explicit ``NotImplementedError`` path below.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping, Sequence
import copy
from datetime import date, timedelta
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SearchAPIError(RuntimeError):
    """A search provider request failed."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.retry_after = retry_after


class SearchRateLimitError(SearchAPIError):
    """The provider still rate-limited the request after all retries."""


class SearchBudgetExhausted(SearchAPIError):
    """The provider or the configured monthly budget is exhausted."""


class MissingSearchAPIKeyError(SearchAPIError):
    """The selected search provider's API key is absent."""


class InvalidSearchAPIKeyError(SearchAPIError):
    """The selected search provider's API key is an obvious placeholder."""


class SearchToolLimitError(SearchAPIError):
    """A role exceeded its combined map/crawl call allowance."""


class SearchProvider(ABC):
    """Neutral 5+1 search-provider interface.

    Concrete providers may implement only a subset.  Calling an unsupported
    operation fails predictably instead of relying on attribute discovery.
    """

    def search(self, query: str, **params: Any) -> dict[str, Any]:
        raise NotImplementedError("Dieser Search-Provider unterstuetzt search nicht.")

    def extract(
        self, urls: Sequence[str], query: str, **params: Any
    ) -> dict[str, Any]:
        raise NotImplementedError("Dieser Search-Provider unterstuetzt extract nicht.")

    def map_site(self, url: str, **params: Any) -> dict[str, Any]:
        raise NotImplementedError("Dieser Search-Provider unterstuetzt map_site nicht.")

    def crawl(self, url: str, **params: Any) -> dict[str, Any]:
        raise NotImplementedError("Dieser Search-Provider unterstuetzt crawl nicht.")

    def research(self, input: str, **params: Any) -> dict[str, Any]:
        raise NotImplementedError("Dieser Search-Provider unterstuetzt research nicht.")

    def get_usage(self) -> dict[str, Any]:
        raise NotImplementedError("Dieser Search-Provider unterstuetzt get_usage nicht.")

    def search_then_extract(
        self,
        query: str,
        *,
        score_threshold: float,
        max_urls: int = 20,
        search_params: Mapping[str, Any] | None = None,
        extract_params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Search first, then extract only unique URLs above the score floor."""

        search_kwargs = dict(search_params or {})
        search_kwargs.pop("query", None)
        search_result = self.search(query, **search_kwargs)
        selected_urls = select_result_urls(
            search_result,
            score_threshold=score_threshold,
            max_urls=max_urls,
        )
        if selected_urls:
            extract_kwargs = dict(extract_params or {})
            extract_kwargs.pop("query", None)
            extract_result = self.extract(
                selected_urls,
                query,
                **extract_kwargs,
            )
        else:
            extract_result = {"results": [], "failed_results": []}
        return {
            "search": search_result,
            "selected_urls": selected_urls,
            "extract": extract_result,
        }


def canonical_url(url: str) -> str:
    """Return a stable URL identity while preserving meaningful query data."""

    raw = str(url).strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    tracking_prefixes = ("utm_",)
    tracking_names = {"fbclid", "gclid"}
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in tracking_names
            and not key.lower().startswith(tracking_prefixes)
        ],
        doseq=True,
    )
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, query, "")
    )


def select_result_urls(
    search_result: Mapping[str, Any],
    *,
    score_threshold: float,
    max_urls: int = 20,
) -> list[str]:
    """Select score-qualified result URLs, preserving order and deduplicating."""

    selected: list[str] = []
    seen: set[str] = set()
    for result in search_result.get("results", []) or []:
        if not isinstance(result, Mapping):
            continue
        try:
            score = float(result.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        url = str(result.get("url", "")).strip()
        identity = canonical_url(url)
        if score < score_threshold or not identity or identity in seen:
            continue
        seen.add(identity)
        selected.append(url)
        if len(selected) >= min(max(0, int(max_urls)), 20):
            break
    return selected


def _function_tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def tool_schemas_for_role(role_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build neutral search tools for one configured role.

    Search is always available. Extract requires an enabled ``extract`` block;
    map and crawl require an enabled ``crawl`` block and share its call budget.
    The costly autonomous research endpoint is intentionally never an agent
    tool in the basis version.
    """

    tools = [
        _function_tool(
            "web_search",
            "Search the web. Prefer this before mapping or crawling a domain.",
            {
                "query": {"type": "string", "maxLength": 399},
                "start_date": {
                    "type": "string",
                    "description": "Inclusive YYYY-MM-DD lower date bound.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Inclusive YYYY-MM-DD upper date bound.",
                },
            },
            ["query"],
        )
    ]
    extract_spec = role_spec.get("extract")
    if isinstance(extract_spec, Mapping) and extract_spec.get("enabled", False):
        tools.append(
            _function_tool(
                "web_extract",
                "Extract relevant passages from known result URLs.",
                {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": min(
                            max(1, int(extract_spec.get("max_urls", 20))), 20
                        ),
                    },
                    "query": {"type": "string"},
                },
                ["urls", "query"],
            )
        )
    crawl_spec = role_spec.get("crawl")
    if isinstance(crawl_spec, Mapping) and crawl_spec.get("enabled", False):
        tools.extend(
            [
                _function_tool(
                    "web_map",
                    (
                        "List URLs on one already-known domain when complete "
                        "domain coverage matters; search first."
                    ),
                    {"url": {"type": "string"}},
                    ["url"],
                ),
                _function_tool(
                    "web_crawl",
                    (
                        "Read many pages on one already-known domain only when "
                        "mapping plus selective extraction is insufficient."
                    ),
                    {"url": {"type": "string"}},
                    ["url"],
                ),
            ]
        )
    return tools


# A descriptive alias makes call sites read naturally and keeps the neutral API
# independent from a particular LLM client implementation.
build_search_tools = tool_schemas_for_role


class SearchToolExecutor:
    """Execute role-scoped tools while enforcing all configured search caps."""

    def __init__(
        self,
        provider: SearchProvider,
        role_spec: Mapping[str, Any],
        search_api_config: Mapping[str, Any],
    ) -> None:
        self.provider = provider
        self.role_spec = copy.deepcopy(dict(role_spec))
        self.search_api = copy.deepcopy(dict(search_api_config))
        self._map_crawl_calls = 0

    @property
    def tools(self) -> list[dict[str, Any]]:
        return tool_schemas_for_role(self.role_spec)

    @property
    def map_crawl_calls(self) -> int:
        return self._map_crawl_calls

    def __call__(self, name: str, arguments: dict[str, Any]) -> str:
        """Tool-loop adapter returning compact JSON for the next LLM turn."""

        return json.dumps(
            self.execute(name, arguments),
            ensure_ascii=False,
            default=str,
        )

    def execute(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = copy.deepcopy(dict(arguments))
        if name == "web_search":
            return self._search(args)
        if name == "web_extract":
            return self._extract(args)
        if name in {"web_map", "web_crawl"}:
            return self._map_or_crawl(name, args)
        raise KeyError(f"Unbekanntes Search-Tool: {name}")

    def search_then_extract(self, query: str, **params: Any) -> dict[str, Any]:
        """Apply the role's standard two-stage search/extract policy."""

        search_spec = self._search_params({})
        search_spec.update(params)
        search_spec.pop("auto_parameters", None)
        extract_spec = self.role_spec.get("extract")
        if not isinstance(extract_spec, Mapping) or not extract_spec.get(
            "enabled", False
        ):
            raise SearchAPIError("Extract ist fuer diese Rolle nicht aktiviert.")
        max_urls = min(int(extract_spec.get("max_urls", 20)), 20)
        return self.provider.search_then_extract(
            query,
            score_threshold=float(
                self.search_api.get("extract_score_threshold", 0.5)
            ),
            max_urls=max_urls,
            search_params=search_spec,
            extract_params=self._extract_params({}),
        )

    def _search_params(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "max_results": self.search_api.get("max_results", 15),
            "search_depth": self.search_api.get("depth", "basic"),
        }
        role_search = self.role_spec.get("search")
        if isinstance(role_search, Mapping):
            defaults.update(dict(role_search))
        defaults.update(dict(arguments))
        if "depth" in defaults:
            defaults["search_depth"] = defaults.pop("depth")
        days = defaults.pop("days", None)
        if days is not None and (
            "start_date" not in defaults or "end_date" not in defaults
        ):
            end = date.today()
            defaults.setdefault("start_date", (end - timedelta(days=int(days))).isoformat())
            defaults.setdefault("end_date", end.isoformat())
        defaults.pop("auto_parameters", None)
        defaults.pop("include_usage", None)
        return {key: value for key, value in defaults.items() if value is not None}

    def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if "query" not in arguments:
            raise ValueError("web_search benoetigt query.")
        query = str(arguments.pop("query"))
        return self.provider.search(query, **self._search_params(arguments))

    def _extract_params(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chunks_per_source": self.search_api.get(
                "extract_chunks_per_source", 3
            )
        }
        extract_spec = self.role_spec.get("extract")
        if isinstance(extract_spec, Mapping):
            params.update(
                {
                    key: value
                    for key, value in extract_spec.items()
                    if key not in {"enabled", "max_urls"}
                }
            )
        params.update(dict(arguments))
        if "depth" in params:
            params["extract_depth"] = params.pop("depth")
        params.pop("include_usage", None)
        return {key: value for key, value in params.items() if value is not None}

    def _extract(self, arguments: dict[str, Any]) -> dict[str, Any]:
        extract_spec = self.role_spec.get("extract")
        if not isinstance(extract_spec, Mapping) or not extract_spec.get(
            "enabled", False
        ):
            raise SearchAPIError("web_extract ist fuer diese Rolle nicht aktiviert.")
        try:
            urls = list(arguments.pop("urls"))
            query = str(arguments.pop("query"))
        except KeyError as exc:
            raise ValueError("web_extract benoetigt urls und query.") from exc
        max_urls = min(max(1, int(extract_spec.get("max_urls", 20))), 20)
        unique_urls = select_result_urls(
            {"results": [{"url": url, "score": 1.0} for url in urls]},
            score_threshold=0.0,
            max_urls=max_urls,
        )
        return self.provider.extract(
            unique_urls,
            query,
            **self._extract_params(arguments),
        )

    def _map_or_crawl(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        crawl_spec = self.role_spec.get("crawl")
        if not isinstance(crawl_spec, Mapping) or not crawl_spec.get(
            "enabled", False
        ):
            raise SearchAPIError(f"{name} ist fuer diese Rolle nicht aktiviert.")
        max_calls = max(0, int(crawl_spec.get("max_calls", 0)))
        if self._map_crawl_calls >= max_calls:
            raise SearchToolLimitError(
                f"Kombiniertes map/crawl-Limit von {max_calls} erreicht."
            )
        try:
            url = str(arguments.pop("url"))
        except KeyError as exc:
            raise ValueError(f"{name} benoetigt url.") from exc
        self._map_crawl_calls += 1
        endpoint = "map" if name == "web_map" else "crawl"
        params = self._capped_site_params(endpoint, arguments, crawl_spec)
        if endpoint == "map":
            return self.provider.map_site(url, **params)
        return self.provider.crawl(url, **params)

    def _capped_site_params(
        self,
        endpoint: str,
        arguments: Mapping[str, Any],
        crawl_spec: Mapping[str, Any],
    ) -> dict[str, Any]:
        global_caps = self.search_api.get(endpoint, {})
        params: dict[str, Any] = {}
        for key in ("max_depth", "max_breadth", "limit"):
            candidates = []
            for source in (global_caps, crawl_spec, arguments):
                value = source.get(key) if isinstance(source, Mapping) else None
                if value is not None:
                    candidates.append(int(value))
            if candidates:
                params[key] = min(candidates)
        if endpoint == "crawl" and isinstance(global_caps, Mapping):
            if global_caps.get("extract_depth") is not None:
                params["extract_depth"] = global_caps["extract_depth"]
        for source in (crawl_spec, arguments):
            if isinstance(source, Mapping) and source.get("extract_depth") is not None:
                params["extract_depth"] = source["extract_depth"]
        # Both costly instruction variants are forbidden by policy.  The
        # adapter repeats this defence so direct callers cannot bypass it.
        params.pop("instructions", None)
        params.pop("auto_parameters", None)
        return params
