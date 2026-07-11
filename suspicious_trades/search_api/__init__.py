"""Provider-neutral web-search layer."""

from .base import (
    InvalidSearchAPIKeyError,
    MissingSearchAPIKeyError,
    SearchAPIError,
    SearchBudgetExhausted,
    SearchProvider,
    SearchRateLimitError,
    SearchToolExecutor,
    SearchToolLimitError,
    build_search_tools,
    canonical_url,
    select_result_urls,
    tool_schemas_for_role,
)
from .tavily import TavilyProvider, TavilySearchAPI, TavilySearchClient

__all__ = [
    "InvalidSearchAPIKeyError",
    "MissingSearchAPIKeyError",
    "SearchAPIError",
    "SearchBudgetExhausted",
    "SearchProvider",
    "SearchRateLimitError",
    "SearchToolExecutor",
    "SearchToolLimitError",
    "TavilyProvider",
    "TavilySearchAPI",
    "TavilySearchClient",
    "build_search_tools",
    "canonical_url",
    "select_result_urls",
    "tool_schemas_for_role",
]
