from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from suspicious_trades import config
from suspicious_trades.search_api import (
    MissingSearchAPIKeyError,
    SearchBudgetExhausted,
    SearchProvider,
    SearchRateLimitError,
    SearchToolExecutor,
    SearchToolLimitError,
    TavilySearchAPI,
    tool_schemas_for_role,
)


def test_missing_tavily_key_points_to_package_env(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(MissingSearchAPIKeyError) as exc_info:
        TavilySearchAPI(
            run_id="missing-key",
            role="test",
            settings=_settings(tmp_path),
        )

    assert str(config.PACKAGE_ROOT / ".env") in str(exc_info.value)


def _settings(tmp_path: Path, **tavily: Any) -> dict[str, Any]:
    tavily_config = {
        "use_research_endpoint": True,
        "research_model": "mini",
        "monthly_credit_budget": 100,
        "credit_reserve": 10,
        "project_ids": {"suspicious_trades": "st-test"},
    }
    tavily_config.update(tavily)
    return {
        "DATA_DIR": tmp_path,
        "SEARCH_MAX_RETRIES": 2,
        "SEARCH_API": {
            "max_results": 15,
            "depth": "basic",
            "extract_score_threshold": 0.5,
            "extract_chunks_per_source": 3,
            "map": {"max_depth": 2, "max_breadth": 50, "limit": 100},
            "crawl": {
                "max_depth": 2,
                "max_breadth": 20,
                "limit": 30,
                "extract_depth": "basic",
            },
        },
        "TAVILY": tavily_config,
    }


class FakeSDK:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}

    def _call(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, params))
        response = self.responses.get(endpoint, {"usage": {"credits": 1}})
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("search", kwargs)

    def extract(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("extract", kwargs)

    def map(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("map", kwargs)

    def crawl(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("crawl", kwargs)

    def research(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("research", kwargs)


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHTTPClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class HTTPFailure(RuntimeError):
    def __init__(self, status_code: int, retry_after: str = "0") -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = FakeResponse({}, status_code, {"retry-after": retry_after})


def _adapter(
    tmp_path: Path,
    sdk: FakeSDK | None = None,
    **kwargs: Any,
) -> TavilySearchAPI:
    return TavilySearchAPI(
        run_id="run-1",
        role="researcher",
        api_key="tvly-test",
        settings=_settings(tmp_path),
        sdk_client=sdk or FakeSDK(),
        **kwargs,
    )


def _log_rows(adapter: TavilySearchAPI) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in adapter.usage_log_path.read_text(encoding="utf-8").splitlines()
    ]


def test_sdk_constructor_receives_package_and_run_attribution(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeSDK:
        captured.update(kwargs)
        return FakeSDK()

    TavilySearchAPI(
        run_id="session-77",
        role="smoke",
        api_key="tvly-secret",
        settings=_settings(tmp_path),
        sdk_factory=factory,
    )

    assert captured == {
        "api_key": "tvly-secret",
        "project_id": "st-test",
        "session_id": "session-77",
    }


def test_default_sdk_preserves_retry_and_budget_status(monkeypatch, tmp_path: Path) -> None:
    from tavily import TavilyClient

    captured: dict[str, Any] = {}

    def fake_init(self, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(TavilyClient, "__init__", fake_init)
    adapter = TavilySearchAPI(
        run_id="status-run",
        role="smoke",
        api_key="tvly-test",
        settings=_settings(tmp_path),
    )
    assert captured["project_id"] == "st-test"

    response = FakeResponse({}, 429, {"retry-after": "2.5"})
    with pytest.raises(Exception) as exc_info:
        adapter._sdk._handle_error_response(response)
    assert getattr(exc_info.value, "status_code", None) == 429
    assert getattr(exc_info.value, "retry_after", None) == 2.5

    with pytest.raises(Exception) as exc_info:
        adapter._sdk._handle_error_response(FakeResponse({}, 433))
    assert getattr(exc_info.value, "status_code", None) == 433


def test_functional_endpoints_force_usage_but_research_does_not(tmp_path: Path) -> None:
    sdk = FakeSDK()
    adapter = _adapter(tmp_path, sdk)

    adapter.search("query", auto_parameters=True, include_usage=False)
    adapter.extract(["https://a.test"], "needle", include_usage=False)
    adapter.map_site(
        "https://a.test",
        max_depth=99,
        max_breadth=99,
        limit=999,
        instructions="double the cost",
        include_usage=False,
    )
    adapter.crawl("https://a.test", auto_parameters=True, include_usage=False)
    adapter.research("question", include_usage=True)

    calls = {name: params for name, params in sdk.calls}
    for endpoint in ("search", "extract", "map", "crawl"):
        assert calls[endpoint]["include_usage"] is True
    assert "auto_parameters" not in calls["search"]
    assert "instructions" not in calls["map"]
    assert "auto_parameters" not in calls["crawl"]
    assert calls["map"]["max_depth"] == 2
    assert calls["map"]["max_breadth"] == 50
    assert calls["map"]["limit"] == 100
    assert "include_usage" not in calls["research"]

    research_row = _log_rows(adapter)[-1]
    assert research_row["endpoint"] == "research"
    assert research_row["credits"] is None
    assert research_row["usage_reported"] is False


def test_credit_log_records_success_and_final_failure_once(tmp_path: Path) -> None:
    sdk = FakeSDK()
    sdk.responses["search"] = {"results": [], "usage": {"credits": 2}}
    sdk.responses["extract"] = ValueError("broken fixture")
    adapter = _adapter(tmp_path, sdk)

    adapter.search("query")
    with pytest.raises(Exception, match="broken fixture"):
        adapter.extract(["https://a.test"], "query")

    rows = _log_rows(adapter)
    assert [(row["endpoint"], row["credits"], row["ok"]) for row in rows] == [
        ("search", 2.0, True),
        ("extract", 0.0, False),
    ]


def test_failed_results_are_logged_as_zero_credit_failure(tmp_path: Path) -> None:
    sdk = FakeSDK()
    sdk.responses["extract"] = {
        "results": [],
        "failed_results": [{"url": "https://a.test", "error": "blocked"}],
        "usage": {"credits": 9},
    }
    adapter = _adapter(tmp_path, sdk)

    adapter.extract(["https://a.test"], "query")

    row = _log_rows(adapter)[0]
    assert row["ok"] is False
    assert row["credits"] == 0.0


def test_429_uses_retry_after_then_succeeds(tmp_path: Path) -> None:
    sdk = FakeSDK()
    sdk.responses["search"] = [
        HTTPFailure(429, "1.25"),
        {"results": [], "usage": {"credits": 1}},
    ]
    sleeps: list[float] = []
    adapter = _adapter(tmp_path, sdk, sleep=sleeps.append)

    result = adapter.search("query")

    assert result["usage"]["credits"] == 1
    assert sleeps == [1.25]
    assert len(sdk.calls) == 2
    assert len(_log_rows(adapter)) == 1


def test_429_after_retries_has_typed_error_and_zero_credit_log(
    tmp_path: Path,
) -> None:
    sdk = FakeSDK()
    sdk.responses["search"] = [HTTPFailure(429)] * 3
    adapter = _adapter(tmp_path, sdk, sleep=lambda _: None)

    with pytest.raises(SearchRateLimitError) as exc_info:
        adapter.search("query")

    assert exc_info.value.status_code == 429
    assert len(sdk.calls) == 3
    assert _log_rows(adapter)[0]["credits"] == 0.0


@pytest.mark.parametrize("status_code", [432, 433])
def test_budget_http_errors_abort_without_retry(
    tmp_path: Path, status_code: int
) -> None:
    sdk = FakeSDK()
    sdk.responses["search"] = HTTPFailure(status_code)
    adapter = _adapter(tmp_path, sdk)

    with pytest.raises(SearchBudgetExhausted) as exc_info:
        adapter.search("query")

    assert exc_info.value.status_code == status_code
    assert len(sdk.calls) == 1
    assert _log_rows(adapter)[0]["ok"] is False


def test_get_usage_uses_direct_http_with_attribution_headers(tmp_path: Path) -> None:
    usage_http = FakeHTTPClient(
        [FakeResponse({"account": {"plan_usage": 10, "plan_limit": 100}})]
    )
    adapter = _adapter(tmp_path, usage_client=usage_http)

    result = adapter.get_usage()

    assert result["account"]["plan_usage"] == 10
    call = usage_http.calls[0]
    assert call["url"] == "https://api.tavily.com/usage"
    assert call["headers"]["Authorization"] == "Bearer tvly-test"
    assert call["headers"]["X-Project-ID"] == "st-test"
    assert call["headers"]["X-Session-Id"] == "run-1"


def test_budget_gate_uses_lower_configured_limit_and_reserve(tmp_path: Path) -> None:
    usage_http = FakeHTTPClient(
        [FakeResponse({"account": {"plan_usage": 91, "plan_limit": 1000}})]
    )
    adapter = _adapter(tmp_path, usage_client=usage_http)

    with pytest.raises(SearchBudgetExhausted, match="9 Credits"):
        adapter.check_budget()


def test_search_then_extract_filters_score_and_deduplicates_urls(
    tmp_path: Path,
) -> None:
    sdk = FakeSDK()
    sdk.responses["search"] = {
        "results": [
            {"url": "https://Example.test/a/?utm_source=x", "score": 0.8},
            {"url": "https://example.test/a", "score": 0.9},
            {"url": "https://example.test/b", "score": 0.49},
            {"url": "https://example.test/c", "score": 0.5},
        ],
        "usage": {"credits": 1},
    }
    sdk.responses["extract"] = {"results": [], "usage": {"credits": 1}}
    adapter = _adapter(tmp_path, sdk)

    result = adapter.search_then_extract(
        "query",
        score_threshold=0.5,
        max_urls=20,
    )

    assert result["selected_urls"] == [
        "https://Example.test/a/?utm_source=x",
        "https://example.test/c",
    ]
    extract_call = next(params for name, params in sdk.calls if name == "extract")
    assert extract_call["urls"] == result["selected_urls"]
    assert extract_call["query"] == "query"


def test_tool_schemas_are_role_scoped() -> None:
    search_only = tool_schemas_for_role({})
    with_extract = tool_schemas_for_role(
        {"extract": {"enabled": True, "max_urls": 7}}
    )
    with_crawl = tool_schemas_for_role(
        {"crawl": {"enabled": True, "max_calls": 2}}
    )

    names = lambda tools: {tool["function"]["name"] for tool in tools}
    assert names(search_only) == {"web_search"}
    assert names(with_extract) == {"web_search", "web_extract"}
    assert names(with_crawl) == {"web_search", "web_map", "web_crawl"}


def test_executor_enforces_combined_call_limit_and_minimum_caps(
    tmp_path: Path,
) -> None:
    sdk = FakeSDK()
    adapter = _adapter(tmp_path, sdk)
    role_spec = {
        "crawl": {
            "enabled": True,
            "max_calls": 2,
            "max_depth": 1,
            "max_breadth": 10,
            "limit": 25,
        }
    }
    executor = SearchToolExecutor(
        adapter,
        role_spec,
        _settings(tmp_path)["SEARCH_API"],
    )

    executor.execute(
        "web_map",
        {
            "url": "https://a.test",
            "max_depth": 100,
            "max_breadth": 100,
            "limit": 1000,
            "instructions": "must never leave process",
        },
    )
    executor.execute("web_crawl", {"url": "https://a.test"})
    with pytest.raises(SearchToolLimitError):
        executor.execute("web_map", {"url": "https://a.test"})

    map_call = next(params for name, params in sdk.calls if name == "map")
    assert map_call["max_depth"] == 1
    assert map_call["max_breadth"] == 10
    assert map_call["limit"] == 25
    assert "instructions" not in map_call
    assert executor.map_crawl_calls == 2


def test_executor_strips_search_auto_parameters(tmp_path: Path) -> None:
    sdk = FakeSDK()
    executor = SearchToolExecutor(
        _adapter(tmp_path, sdk),
        {"search": {"depth": "advanced", "auto_parameters": True}},
        _settings(tmp_path)["SEARCH_API"],
    )

    executor.execute(
        "web_search",
        {"query": "query", "auto_parameters": True},
    )

    search_call = sdk.calls[0][1]
    assert search_call["search_depth"] == "advanced"
    assert "auto_parameters" not in search_call


def test_base_provider_has_explicit_not_implemented_path() -> None:
    provider = SearchProvider()
    with pytest.raises(NotImplementedError):
        provider.map_site("https://example.test")
