"""Offline fakes shared by the Suspicious-Trades B1 tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_usage(
    *,
    prompt: int = 100,
    completion: int = 20,
    cache_read: int = 0,
    cache_write: int = 0,
    openai_cached: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        prompt_tokens_details=SimpleNamespace(cached_tokens=openai_cached),
    )


def make_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def make_response(
    content: str = "ok",
    *,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = "stop",
    usage: Any | None = None,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage or make_usage(),
        model="mock/model",
    )


@pytest.fixture
def llm_settings(tmp_path):
    return {
        "AGENT_SPECS": {
            "worker": {
                "model": "anthropic/test-model",
                "effort": "high",
                "max_output_tokens": 128,
                "cache": True,
            },
            "plain": {
                "model": "deepseek/test-model",
                "effort": "none",
                "max_output_tokens": 64,
                "cache": False,
            },
        },
        "LLM_NUM_RETRIES": 2,
        "LLM_FALLBACK_ENABLED": False,
        "OPENROUTER_MODEL_MAP": {
            "anthropic/test-model": "openrouter/anthropic/test-model"
        },
        "PRICING": {
            "anthropic/test-model": {
                "in": 5.0,
                "out": 25.0,
                "cache_read": 0.5,
                "cache_write": 6.25,
            },
            "deepseek/test-model": {
                "in": 1.0,
                "out": 2.0,
                "cache_read": 0.1,
                "cache_write": 1.0,
            },
            "openrouter/anthropic/test-model": {
                "in": 5.0,
                "out": 25.0,
                "cache_read": 0.5,
                "cache_write": 6.25,
            },
        },
        "COST_CAP_ENABLED": True,
        "COST_CAP_USD_PER_RUN": 20.0,
        "DATA_DIR": tmp_path,
    }


@pytest.fixture
def response_factory():
    return make_response


@pytest.fixture
def tool_call_factory():
    return make_tool_call
