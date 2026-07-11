"""Provider-neutral LiteLLM completion and JSON helpers."""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import litellm

from .. import config
from .usage import current_usage_log

logger = logging.getLogger(__name__)

litellm.drop_params = True
litellm.suppress_debug_info = True


KEY_ENV_BY_PREFIX = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class MissingAPIKeyError(RuntimeError):
    """A model's expected provider key is absent."""


class InvalidAPIKeyError(RuntimeError):
    """A configured provider key is an obvious local placeholder."""


class LLMValidationError(RuntimeError):
    """A JSON response remained invalid after all repair attempts."""


def key_env_for_model(model: str) -> str | None:
    """Return the conventional API-key variable for a LiteLLM model string."""

    prefix = model.partition("/")[0].lower()
    return KEY_ENV_BY_PREFIX.get(prefix)


def ensure_api_key(model: str) -> None:
    """Fail before LiteLLM with a provider-specific, actionable message."""

    key_env = key_env_for_model(model)
    status = config.credential_status(key_env) if key_env else "set"
    if key_env and status == "missing":
        raise MissingAPIKeyError(
            f"{key_env} ist fuer Modell {model!r} nicht gesetzt. "
            f"Trage den Key in {config.PACKAGE_ROOT / '.env'} ein."
        )
    if key_env and status == "placeholder":
        raise InvalidAPIKeyError(
            f"{key_env} enthaelt fuer Modell {model!r} nur einen Platzhalter."
        )


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _spec_for(
    role: str,
    settings: Mapping[str, Any],
    spec_override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    configured = settings.get("AGENT_SPECS", {})
    if role in configured:
        spec = copy.deepcopy(configured[role])
    elif spec_override is not None:
        spec = {}
    else:
        raise KeyError(f"Unbekannte Agent-Rolle: {role}")
    if spec_override is not None:
        spec = _deep_merge(spec, spec_override)
    if not spec.get("model"):
        raise ValueError(f"Fuer Rolle {role!r} ist kein LiteLLM-Modell konfiguriert.")
    return spec


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, Mapping):
        return [copy.deepcopy(dict(content))]
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, str):
                blocks.append({"type": "text", "text": block})
            elif isinstance(block, Mapping):
                blocks.append(copy.deepcopy(dict(block)))
        return blocks
    return []


def inject_cache_markers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place one stable system and one moving history cache breakpoint."""

    prepared = copy.deepcopy(messages)
    candidate_indexes: list[int] = []
    last_system: int | None = None
    for index, message in enumerate(prepared):
        blocks = _content_blocks(message.get("content"))
        for block in blocks:
            block.pop("cache_control", None)
        if blocks:
            message["content"] = blocks
            candidate_indexes.append(index)
            if message.get("role") == "system":
                last_system = index

    marked: list[int] = []
    if last_system is not None:
        marked.append(last_system)
    if candidate_indexes and candidate_indexes[-1] not in marked:
        marked.append(candidate_indexes[-1])
    for index in marked:
        prepared[index]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return prepared


def _strip_cache_markers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(messages)
    for message in prepared:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    return prepared


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return True
    transient_names = {
        "Timeout",
        "RateLimitError",
        "ServiceUnavailableError",
        "APIConnectionError",
        "InternalServerError",
    }
    return exc.__class__.__name__ in transient_names


def _response_content(response: Any) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
            for block in content
        )
    return "" if content is None else str(content)


def _completion(
    role: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    parallel_tool_calls: bool | None = None,
    spec_override: dict[str, Any] | None = None,
    extra_kwargs: Mapping[str, Any] | None = None,
) -> Any:
    settings = config.effective()
    spec = _spec_for(role, settings, spec_override)
    model = str(spec["model"])
    ensure_api_key(model)

    usage_log = current_usage_log()
    if usage_log is not None:
        usage_log.check_cap(role)

    cache_enabled = bool(spec.get("cache", False))
    prepared = inject_cache_markers(messages) if cache_enabled else copy.deepcopy(messages)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": prepared,
        "max_tokens": int(spec["max_output_tokens"]),
        "num_retries": int(spec.get("num_retries", settings["LLM_NUM_RETRIES"])),
        "timeout": float(settings["LLM_TIMEOUT_SECONDS"]),
    }
    effort = spec.get("effort", "none")
    if effort not in (None, "none"):
        kwargs["reasoning_effort"] = effort
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
        if parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = parallel_tool_calls
    if extra_kwargs:
        kwargs.update(extra_kwargs)

    used_model = model
    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:
        fallback_model = settings["OPENROUTER_MODEL_MAP"].get(model)
        can_fallback = (
            _is_transient(exc)
            and settings["LLM_FALLBACK_ENABLED"]
            and bool(fallback_model)
            and bool(os.getenv("OPENROUTER_API_KEY"))
        )
        if not can_fallback:
            raise
        used_model = str(fallback_model)
        ensure_api_key(used_model)
        logger.warning(
            "Primaermodell %s nicht erreichbar; Fallback %s ohne Cache-Fidelity.",
            model,
            used_model,
        )
        fallback_kwargs = dict(kwargs)
        fallback_kwargs["model"] = used_model
        fallback_kwargs["messages"] = _strip_cache_markers(prepared)
        fallback_kwargs["num_retries"] = 0
        response = litellm.completion(**fallback_kwargs)

    if usage_log is not None:
        usage_log.record(role, used_model, response)
    return response


def complete(
    role: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    parallel_tool_calls: bool | None = None,
    spec_override: dict[str, Any] | None = None,
) -> Any:
    """Make one normalized LLM call for a configured agent role."""

    return _completion(
        role,
        messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        spec_override=spec_override,
    )


def call_json(
    role: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    max_repair_attempts: int = 2,
) -> dict[str, Any]:
    """Request JSON, validate it and repair invalid responses with reprompts."""

    validator = Draft202012Validator(schema)
    conversation = copy.deepcopy(messages)
    conversation.append(
        {
            "role": "user",
            "content": "Antworte ausschliesslich mit einem gueltigen JSON-Objekt.",
        }
    )
    last_error = ""
    for attempt in range(max_repair_attempts + 1):
        response = _completion(
            role,
            conversation,
            extra_kwargs={"response_format": {"type": "json_object"}},
        )
        content = _response_content(response)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            last_error = f"JSON-Parse-Fehler: {exc}"
        else:
            errors = sorted(
                validator.iter_errors(parsed),
                key=lambda item: tuple(str(part) for part in item.path),
            )
            if not errors:
                return parsed
            last_error = "; ".join(
                f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
                for error in errors[:8]
            )
        if attempt < max_repair_attempts:
            conversation.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "Die vorige JSON-Antwort war ungueltig. "
                            f"Fehler: {last_error}\nGib nur das korrigierte JSON aus."
                        ),
                    },
                ]
            )
    raise LLMValidationError(
        f"[{role}] JSON nach {max_repair_attempts + 1} Versuchen ungueltig: "
        f"{last_error}"
    )
