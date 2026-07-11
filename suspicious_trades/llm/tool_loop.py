"""Manual provider-neutral tool loop with stop and turn-budget checks."""

from __future__ import annotations

from contextlib import nullcontext
import copy
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .. import config
from .client import complete
from .usage import UsageLog, usage_turn

logger = logging.getLogger(__name__)


class StopRequested(RuntimeError):
    """The cooperative run stop file was observed."""


class RunContextProtocol(Protocol):
    """Minimal B1 contract; the full status implementation follows in B3."""

    run_id: str
    usage_log: UsageLog | None
    spec_override: dict[str, Any] | None

    def check_stop(self) -> bool: ...


@dataclass
class RunContext:
    """Small standalone context usable until B3 provides the status layer."""

    run_id: str
    stop_path: Path | None = None
    usage_log: UsageLog | None = None
    spec_override: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.stop_path is None:
            settings = config.effective()
            self.stop_path = Path(settings["DATA_DIR"]) / "runs" / f"{self.run_id}.stop"

    def check_stop(self) -> bool:
        return bool(self.stop_path and self.stop_path.exists())


@dataclass(frozen=True)
class ToolLoopResult:
    """Final answer plus the complete normalized conversation."""

    final_text: str
    messages: list[dict[str, Any]]
    turns: int
    finish_reason: str | None


def _tool_calls(message: Any) -> list[Any]:
    calls = getattr(message, "tool_calls", None)
    if calls is None and isinstance(message, Mapping):
        calls = message.get("tool_calls")
    return list(calls or [])


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _assistant_entry(message: Any, calls: list[Any]) -> dict[str, Any]:
    content = _value(message, "content", "") or ""
    entry: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        entry["tool_calls"] = []
        for call in calls:
            function = _value(call, "function", {})
            entry["tool_calls"].append(
                {
                    "id": _value(call, "id", ""),
                    "type": "function",
                    "function": {
                        "name": _value(function, "name", ""),
                        "arguments": _value(function, "arguments", "{}"),
                    },
                }
            )
    return entry


def _check_stop(run_ctx: RunContextProtocol) -> None:
    if run_ctx.check_stop():
        raise StopRequested(f"Stop fuer Run {run_ctx.run_id} angefordert.")


def call_with_tools(
    role: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_executor: Callable[[str, dict[str, Any]], str],
    *,
    max_turns: int,
    run_ctx: RunContextProtocol,
    required_tool_turns: int = 0,
) -> ToolLoopResult:
    """Run tools until the model finishes, then force a final answer at the cap."""

    conversation = copy.deepcopy(messages)
    activation = run_ctx.usage_log.activate() if run_ctx.usage_log else nullcontext()
    spec_override = getattr(run_ctx, "spec_override", None)
    with activation:
        for turn in range(1, max_turns + 1):
            _check_stop(run_ctx)
            with usage_turn(turn):
                response = complete(
                    role,
                    conversation,
                    tools=tools,
                    tool_choice="required" if turn <= required_tool_turns else "auto",
                    parallel_tool_calls=False if required_tool_turns else None,
                    spec_override=spec_override,
                )
            choice = response.choices[0]
            message = choice.message
            calls = _tool_calls(message)
            conversation.append(_assistant_entry(message, calls))
            finish_reason = _value(choice, "finish_reason")
            if not calls:
                return ToolLoopResult(
                    final_text=str(_value(message, "content", "") or ""),
                    messages=conversation,
                    turns=turn,
                    finish_reason=finish_reason,
                )

            for call in calls:
                function = _value(call, "function", {})
                name = str(_value(function, "name", ""))
                raw_arguments = _value(function, "arguments", "{}") or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool-Argumente muessen ein JSON-Objekt sein.")
                    result = tool_executor(name, arguments)
                except Exception as exc:
                    logger.warning("Tool %s fehlgeschlagen: %s", name, exc)
                    result = f"FEHLER bei Tool {name!r}: {exc}"
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(_value(call, "id", "")),
                        "content": str(result),
                    }
                )

        logger.warning("[%s] max_turns=%d erreicht; erzwinge Abschluss.", role, max_turns)
        conversation.append(
            {
                "role": "user",
                "content": (
                    "Das Tool-Turn-Budget ist erschoepft. Antworte jetzt final "
                    "mit den vorhandenen Informationen und ohne weitere Tools."
                ),
            }
        )
        _check_stop(run_ctx)
        with usage_turn(max_turns + 1):
            response = complete(role, conversation, spec_override=spec_override)
        choice = response.choices[0]
        message = choice.message
        conversation.append(_assistant_entry(message, []))
        return ToolLoopResult(
            final_text=str(_value(message, "content", "") or ""),
            messages=conversation,
            turns=max_turns + 1,
            finish_reason=_value(choice, "finish_reason"),
        )
