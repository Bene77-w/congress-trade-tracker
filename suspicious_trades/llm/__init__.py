"""Public API of the shared LiteLLM layer."""

from .client import (
    InvalidAPIKeyError,
    LLMValidationError,
    MissingAPIKeyError,
    call_json,
    complete,
)
from .tool_loop import (
    RunContext,
    RunContextProtocol,
    StopRequested,
    ToolLoopResult,
    call_with_tools,
)
from .usage import CostCapExceeded, UsageLog, UsageRecord

__all__ = [
    "CostCapExceeded",
    "InvalidAPIKeyError",
    "LLMValidationError",
    "MissingAPIKeyError",
    "RunContext",
    "RunContextProtocol",
    "StopRequested",
    "ToolLoopResult",
    "UsageLog",
    "UsageRecord",
    "call_json",
    "call_with_tools",
    "complete",
]
