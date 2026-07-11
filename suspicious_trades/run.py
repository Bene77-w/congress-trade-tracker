"""CLI registry for the cloneable Suspicious-Trades base package."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
import re
from typing import Any, Iterable, Mapping, Sequence

import litellm

from . import config
from .llm import RunContext, UsageLog, call_with_tools, complete
from .llm.client import InvalidAPIKeyError, MissingAPIKeyError, key_env_for_model
from .llm.usage import extract_usage
from .search_api import (
    InvalidSearchAPIKeyError,
    MissingSearchAPIKeyError,
    SearchBudgetExhausted,
    TavilySearchAPI,
)


FAILING_SMOKE_STATUSES = {"AUTH_FAIL", "MODEL_CONFIG_FAIL", "FAIL"}


@dataclass(frozen=True)
class SmokeResult:
    """One printable live-check outcome."""

    check: str
    status: str
    detail: str


def _run_id() -> str:
    return "smoke-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _unique_models(settings: Mapping[str, Any]) -> list[str]:
    models = list(settings.get("SMOKE_MODELS", []))
    models.extend(
        spec["model"]
        for spec in settings.get("AGENT_SPECS", {}).values()
        if isinstance(spec, Mapping) and isinstance(spec.get("model"), str)
    )
    return list(dict.fromkeys(models))


def _model_spec(model: str, settings: Mapping[str, Any], *, cache: bool) -> dict[str, Any]:
    return {
        "model": model,
        "effort": "none",
        "max_output_tokens": int(
            settings[
                "CACHE_CHECK_MAX_OUTPUT_TOKENS"
                if cache
                else "SMOKE_MAX_OUTPUT_TOKENS"
            ]
        ),
        "cache": cache,
        "num_retries": 0,
    }


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _safe_error_detail(exc: Exception) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    for key_env in {
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "TAVILY_API_KEY",
    }:
        value = os.getenv(key_env)
        if value and len(value) >= 4:
            detail = detail.replace(value, "<redacted>")
    detail = re.sub(
        r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+",
        r"\1<redacted>",
        detail,
    )
    return re.sub(r"\s+", " ", detail).strip()[:700]


def _classify_exception(exc: Exception) -> str:
    if isinstance(exc, (MissingAPIKeyError, MissingSearchAPIKeyError)):
        return "SKIP"
    if isinstance(exc, (InvalidAPIKeyError, InvalidSearchAPIKeyError)):
        return "AUTH_FAIL"
    if isinstance(exc, SearchBudgetExhausted):
        return "ACCESS_BLOCKED"

    name = type(exc).__name__.lower()
    message = str(exc).lower()
    status = _status_code(exc)
    auth_markers = (
        "invalid api key",
        "invalid_api_key",
        "authentication",
        "unauthorized",
        "incorrect api key",
        "api key not valid",
    )
    quota_markers = (
        "quota exceeded",
        "exceeded your current quota",
        "insufficient_quota",
        "resource_exhausted",
        "limit: 0",
        "billing",
        "exhausted credits",
    )
    region_markers = (
        "not available in your region",
        "region is not supported",
        "unsupported region",
        "regional restriction",
    )
    model_markers = (
        "model_not_found",
        "unknown model",
        "invalid model",
        "unsupported model",
        "model is not supported",
        "model does not exist",
        "model not found",
        "llm provider not provided",
    )

    if status == 401 or "authenticationerror" in name or any(
        marker in message for marker in auth_markers
    ):
        return "AUTH_FAIL"
    if status in {432, 433}:
        return "ACCESS_BLOCKED"
    if any(marker in message for marker in quota_markers + region_markers):
        return "ACCESS_BLOCKED"
    if status == 404 or "notfounderror" in name or any(
        marker in message for marker in model_markers
    ):
        return "MODEL_CONFIG_FAIL"
    if status == 403 or "permissiondeniederror" in name:
        return "ACCESS_BLOCKED"
    return "FAIL"


def _credential_gate(check: str, key_env: str | None) -> SmokeResult | None:
    if not key_env:
        return None
    status = config.credential_status(key_env)
    source = config.credential_source(key_env)
    if status == "missing":
        return SmokeResult(check, "SKIP", f"{key_env} fehlt; source={source}")
    if status == "placeholder":
        return SmokeResult(
            check,
            "AUTH_FAIL",
            f"{key_env} enthaelt einen Platzhalter; source={source}",
        )
    return None


def _smoke_models(
    settings: Mapping[str, Any],
    usage_log: UsageLog,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    with usage_log.activate():
        for model in _unique_models(settings):
            key_env = key_env_for_model(model)
            blocked = _credential_gate(model, key_env)
            if blocked is not None:
                results.append(blocked)
                continue
            try:
                response = complete(
                    "smoke",
                    [{"role": "user", "content": "Reply with OK."}],
                    spec_override=_model_spec(model, settings, cache=False),
                )
                content = response.choices[0].message.content or ""
                source = config.credential_source(key_env) if key_env else "unknown"
                results.append(
                    SmokeResult(
                        model,
                        "PASS",
                        f"{str(content).strip()[:80]}; key_source={source}",
                    )
                )
            except Exception as exc:
                results.append(
                    SmokeResult(model, _classify_exception(exc), _safe_error_detail(exc))
                )
    return results


def _smoke_tavily(settings: Mapping[str, Any], run_id: str) -> SmokeResult:
    blocked = _credential_gate("tavily", "TAVILY_API_KEY")
    if blocked is not None:
        return blocked
    today = date.today()
    start = today - timedelta(days=int(settings["SMOKE_SEARCH_LOOKBACK_DAYS"]))
    try:
        client = TavilySearchAPI(run_id=run_id, role="smoke", settings=settings)
        response = client.search(
            str(settings["SMOKE_SEARCH_QUERY"]),
            search_depth=settings["SEARCH_API"]["depth"],
            max_results=int(settings["SMOKE_SEARCH_MAX_RESULTS"]),
            start_date=start.isoformat(),
            end_date=today.isoformat(),
        )
        usage = client.get_usage()
        result_count = len(response.get("results", [])) if isinstance(response, Mapping) else 0
        if not isinstance(usage, Mapping):
            raise TypeError("Tavily /usage lieferte kein JSON-Objekt")
        return SmokeResult(
            "tavily",
            "PASS",
            f"search_results={result_count}; usage erreichbar",
        )
    except Exception as exc:
        return SmokeResult(
            "tavily", _classify_exception(exc), _safe_error_detail(exc)
        )


def _cache_system_prompt(
    model: str,
    target_tokens: int,
    provider_to_local_ratio: float,
) -> str:
    """Build the smallest stable local prompt at or above the token target."""

    prefix = "Cache fidelity probe. Keep this prefix stable.\n"
    unit = "cache fidelity stable prefix. "

    def prompt(repetitions: int) -> str:
        return prefix + unit * repetitions

    def count(repetitions: int) -> int:
        return int(
            litellm.token_counter(
                model=model,
                messages=[{"role": "system", "content": prompt(repetitions)}],
            )
        )

    local_target = max(1, int(target_tokens / provider_to_local_ratio + 0.999))
    high = max(1, local_target // 4)
    while count(high) < local_target:
        high *= 2
    low = 1
    while low < high:
        middle = (low + high) // 2
        if count(middle) >= local_target:
            high = middle
        else:
            low = middle + 1
    return prompt(low).rstrip()


def _cache_check(settings: Mapping[str, Any], run_id: str) -> SmokeResult:
    model = str(settings["CACHE_CHECK_MODEL"])
    key_env = key_env_for_model(model)
    blocked = _credential_gate("anthropic-cache", key_env)
    if blocked is not None:
        return blocked

    spec = _model_spec(model, settings, cache=True)
    identical_messages = [
        {
            "role": "system",
            "content": _cache_system_prompt(
                model,
                int(settings["CACHE_CHECK_SYSTEM_TOKENS"]),
                float(settings["CACHE_CHECK_PROVIDER_TO_LOCAL_TOKEN_RATIO"]),
            ),
        },
        {"role": "user", "content": "Reply only with OK."},
    ]
    ledger = UsageLog(run_id, settings=settings)
    try:
        with ledger.activate():
            complete("cache_check", identical_messages, spec_override=spec)
            second = complete("cache_check", identical_messages, spec_override=spec)
        second_usage = extract_usage(second)
        if second_usage.cache_read <= 0:
            raise AssertionError("zweiter identischer Call meldet cache_read=0")

        before_loop = len(ledger.records)
        tool_messages = [
            identical_messages[0],
            {
                "role": "user",
                "content": (
                    "Call cache_probe in two consecutive tool turns (step 1, then step 2). "
                    "After the second tool result, answer only CACHE_OK."
                ),
            },
        ]
        tool_schema = [
            {
                "type": "function",
                "function": {
                    "name": "cache_probe",
                    "description": "Return a deterministic cache-check acknowledgement.",
                    "parameters": {
                        "type": "object",
                        "properties": {"step": {"type": "integer"}},
                        "required": ["step"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        tool_spec = dict(spec)
        tool_spec["max_output_tokens"] = int(
            settings["CACHE_CHECK_TOOL_MAX_OUTPUT_TOKENS"]
        )
        context = RunContext(
            run_id,
            usage_log=ledger,
            spec_override=tool_spec,
        )
        steps: list[int] = []

        def execute_cache_probe(name: str, arguments: dict[str, Any]) -> str:
            expected = len(steps) + 1
            step = int(arguments.get("step", 0))
            if name != "cache_probe" or step != expected:
                return f"Expected cache_probe step {expected}; retry correctly."
            steps.append(step)
            return (
                "ACK; call cache_probe once more with step 2"
                if step == 1
                else "ACK; now provide the final answer CACHE_OK"
            )

        required_tool_turns = int(settings["CACHE_CHECK_REQUIRED_TOOL_TURNS"])
        result = call_with_tools(
            "cache_check",
            tool_messages,
            tool_schema,
            execute_cache_probe,
            max_turns=required_tool_turns,
            run_ctx=context,
            required_tool_turns=required_tool_turns,
        )
        loop_records = ledger.records[before_loop:]
        expected_turns = required_tool_turns + 1
        if result.turns != expected_turns:
            raise AssertionError(
                f"Mini-Tool-Loop endete nach {result.turns} statt "
                f"{expected_turns} Turns"
            )
        if steps != list(range(1, required_tool_turns + 1)):
            raise AssertionError(f"Mini-Tool-Loop meldete Schritte {steps!r}")
        if result.final_text.strip().upper() != "CACHE_OK":
            raise AssertionError("Mini-Tool-Loop lieferte nicht CACHE_OK")
        if not any(int(row["cache_read"]) > 0 for row in loop_records):
            raise AssertionError("Mini-Tool-Loop meldet keinen Cache-Hit")
        return SmokeResult(
            "anthropic-cache",
            "PASS",
            f"identical_cache_read={second_usage.cache_read}; tool_turns={result.turns}",
        )
    except Exception as exc:
        return SmokeResult(
            "anthropic-cache", _classify_exception(exc), _safe_error_detail(exc)
        )


def _print_results(results: Iterable[SmokeResult]) -> None:
    for result in results:
        print(f"[{result.status}] {result.check}: {result.detail}")
    skipped = [result.check for result in results if result.status == "SKIP"]
    if skipped:
        print("Offene B1-Live-Abnahmepunkte (fehlende Keys): " + ", ".join(skipped))
        print(f"Keys eintragen in: {config.PACKAGE_ROOT / '.env'}")
    blocked = [result.check for result in results if result.status == "ACCESS_BLOCKED"]
    if blocked:
        print("Externer Zugang blockiert: " + ", ".join(blocked))


def smoke_command(args: argparse.Namespace) -> int:
    if not getattr(args, "confirm_live_costs", False):
        print(
            "[BLOCKED] Live-API-Smoke nicht gestartet. "
            "Explizit mit --confirm-live-costs bestaetigen."
        )
        return 2
    settings = config.effective()
    run_id = _run_id()
    ledger = UsageLog(run_id, settings=settings)
    results = _smoke_models(settings, ledger)
    results.append(_smoke_tavily(settings, run_id))
    if args.cache_check:
        results.append(_cache_check(settings, run_id + "-cache"))
    _print_results(results)
    return 1 if any(result.status in FAILING_SMOKE_STATUSES for result in results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m {__package__}.run")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="kleine Live-Pings aller B1-Provider")
    smoke.add_argument(
        "--cache-check",
        action="store_true",
        help="zusaetzlich Anthropic-Prompt-Caching im Mini-Tool-Loop pruefen",
    )
    smoke.add_argument(
        "--confirm-live-costs",
        action="store_true",
        help="bestaetigt ausdruecklich kostenpflichtige LLM-/Tavily-Aufrufe",
    )
    smoke.set_defaults(handler=smoke_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
