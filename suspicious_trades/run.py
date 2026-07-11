"""CLI registry for the cloneable Suspicious-Trades base package."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from typing import Any, Iterable, Mapping, Sequence

from . import config
from .llm import RunContext, UsageLog, call_with_tools, complete
from .llm.client import MissingAPIKeyError, key_env_for_model
from .llm.usage import extract_usage
from .search_api import TavilySearchAPI


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
    }


def _smoke_models(
    settings: Mapping[str, Any],
    usage_log: UsageLog,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    with usage_log.activate():
        for model in _unique_models(settings):
            key_env = key_env_for_model(model)
            if key_env and not os.getenv(key_env):
                results.append(
                    SmokeResult(model, "SKIP", f"{key_env} fehlt")
                )
                continue
            try:
                response = complete(
                    "smoke",
                    [{"role": "user", "content": "Reply with OK."}],
                    spec_override=_model_spec(model, settings, cache=False),
                )
                content = response.choices[0].message.content or ""
                results.append(SmokeResult(model, "PASS", str(content).strip()[:80]))
            except MissingAPIKeyError as exc:
                results.append(SmokeResult(model, "SKIP", str(exc)))
            except Exception as exc:
                results.append(
                    SmokeResult(model, "FAIL", f"{type(exc).__name__}: {exc}")
                )
    return results


def _smoke_tavily(settings: Mapping[str, Any], run_id: str) -> SmokeResult:
    if not os.getenv("TAVILY_API_KEY"):
        return SmokeResult("tavily", "SKIP", "TAVILY_API_KEY fehlt")
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
        return SmokeResult("tavily", "FAIL", f"{type(exc).__name__}: {exc}")


def _cache_check(settings: Mapping[str, Any], run_id: str) -> SmokeResult:
    model = str(settings["CACHE_CHECK_MODEL"])
    key_env = key_env_for_model(model)
    if key_env and not os.getenv(key_env):
        return SmokeResult("anthropic-cache", "SKIP", f"{key_env} fehlt")

    spec = _model_spec(model, settings, cache=True)
    padding = ("cache " * int(settings["CACHE_CHECK_SYSTEM_TOKENS"])).strip()
    identical_messages = [
        {
            "role": "system",
            "content": f"Cache fidelity probe. Keep this prefix stable.\n{padding}",
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
        context = RunContext(
            run_id,
            usage_log=ledger,
            spec_override=spec,
        )
        result = call_with_tools(
            "cache_check",
            tool_messages,
            tool_schema,
            lambda name, arguments: (
                "ACK; call cache_probe once more with step 2"
                if int(arguments.get("step", 0)) == 1
                else "ACK; now provide the final answer"
            ),
            max_turns=int(settings["CACHE_CHECK_TOOL_TURNS"]),
            run_ctx=context,
        )
        loop_records = ledger.records[before_loop:]
        if result.turns < int(settings["CACHE_CHECK_TOOL_TURNS"]):
            raise AssertionError(
                f"Mini-Tool-Loop endete nach {result.turns} statt "
                f"{settings['CACHE_CHECK_TOOL_TURNS']} Turns"
            )
        if not any(int(row["cache_read"]) > 0 for row in loop_records):
            raise AssertionError("Mini-Tool-Loop meldet keinen Cache-Hit")
        return SmokeResult(
            "anthropic-cache",
            "PASS",
            f"identical_cache_read={second_usage.cache_read}; tool_turns={result.turns}",
        )
    except MissingAPIKeyError as exc:
        return SmokeResult("anthropic-cache", "SKIP", str(exc))
    except Exception as exc:
        return SmokeResult(
            "anthropic-cache", "FAIL", f"{type(exc).__name__}: {exc}"
        )


def _print_results(results: Iterable[SmokeResult]) -> None:
    for result in results:
        print(f"[{result.status}] {result.check}: {result.detail}")
    skipped = [result.check for result in results if result.status == "SKIP"]
    if skipped:
        print("Offene B1-Live-Abnahmepunkte (fehlende Keys): " + ", ".join(skipped))


def smoke_command(args: argparse.Namespace) -> int:
    settings = config.effective()
    run_id = _run_id()
    ledger = UsageLog(run_id, settings=settings)
    results = _smoke_models(settings, ledger)
    results.append(_smoke_tavily(settings, run_id))
    if args.cache_check:
        results.append(_cache_check(settings, run_id + "-cache"))
    _print_results(results)
    return 1 if any(result.status == "FAIL" for result in results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m {__package__}.run")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="kleine Live-Pings aller B1-Provider")
    smoke.add_argument(
        "--cache-check",
        action="store_true",
        help="zusaetzlich Anthropic-Prompt-Caching im Mini-Tool-Loop pruefen",
    )
    smoke.set_defaults(handler=smoke_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
