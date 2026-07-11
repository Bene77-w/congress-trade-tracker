"""Usage normalization, deterministic pricing and per-run cost limits."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Iterator, Mapping

import litellm

from .. import config

logger = logging.getLogger(__name__)


class CostCapExceeded(RuntimeError):
    """Raised before an LLM call would reach the configured run cap."""


@dataclass(frozen=True)
class UsageRecord:
    """Provider-neutral usage values for one completion."""

    prompt: int
    completion: int
    cache_read: int = 0
    cache_write: int = 0


_current_log: ContextVar["UsageLog | None"] = ContextVar(
    "suspicious_trades_usage_log", default=None
)
_current_turn: ContextVar[int | None] = ContextVar(
    "suspicious_trades_usage_turn", default=None
)


def _mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, Mapping) else {}
    try:
        return vars(value)
    except TypeError:
        return {}


def _int(mapping: Mapping[str, Any], *names: str) -> int:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def extract_usage(response: Any) -> UsageRecord:
    """Normalize Anthropic- and OpenAI-style cache token fields."""

    raw = _mapping(getattr(response, "usage", None))
    details = _mapping(raw.get("prompt_tokens_details"))
    cache_read = _int(raw, "cache_read_input_tokens")
    if not cache_read:
        cache_read = _int(details, "cached_tokens")
    return UsageRecord(
        prompt=_int(raw, "prompt_tokens", "input_tokens"),
        completion=_int(raw, "completion_tokens", "output_tokens"),
        cache_read=cache_read,
        cache_write=_int(raw, "cache_creation_input_tokens"),
    )


def calculate_cost(tokens: UsageRecord, prices: Mapping[str, float]) -> float:
    """Calculate USD using the repository-owned prices per one million tokens."""

    uncached = max(0, tokens.prompt - tokens.cache_read - tokens.cache_write)
    total = (
        uncached * float(prices["in"])
        + tokens.completion * float(prices["out"])
        + tokens.cache_read * float(prices["cache_read"])
        + tokens.cache_write * float(prices["cache_write"])
    )
    return total / 1_000_000


def resolve_pricing(
    settings: Mapping[str, Any],
    model: str,
    *,
    at: datetime | None = None,
    prompt_tokens: int = 0,
) -> Mapping[str, float] | None:
    """Resolve temporary dated prices before the stable model tariff."""

    instant = at or datetime.now(timezone.utc)
    current_date = instant.astimezone(timezone.utc).date().isoformat()
    periods = settings.get("PRICING_PERIODS", {}).get(model, [])
    if isinstance(periods, list):
        for period in periods:
            if not isinstance(period, Mapping):
                continue
            through = period.get("through")
            prices = period.get("prices")
            if (
                isinstance(through, str)
                and current_date <= through
                and isinstance(prices, Mapping)
            ):
                return prices
    prices = settings.get("PRICING", {}).get(model)
    resolved = prices if isinstance(prices, Mapping) else None
    tiers = settings.get("PRICING_TOKEN_TIERS", {}).get(model, [])
    if isinstance(tiers, list):
        for tier in sorted(
            (item for item in tiers if isinstance(item, Mapping)),
            key=lambda item: int(item.get("above_prompt_tokens", 0)),
        ):
            threshold = int(tier.get("above_prompt_tokens", 0))
            tier_prices = tier.get("prices")
            if prompt_tokens > threshold and isinstance(tier_prices, Mapping):
                resolved = tier_prices
    return resolved


class UsageLog:
    """Append-only usage ledger and cost-cap state for one run."""

    def __init__(
        self,
        run_id: str,
        *,
        runs_dir: Path | str | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.settings = dict(settings or config.effective())
        self.runs_dir = Path(runs_dir or self.settings["DATA_DIR"] / "runs")
        self.path = self.runs_dir / f"{run_id}.usage.jsonl"
        self.summary_path = self.runs_dir / f"{run_id}.json"
        self.records: list[dict[str, Any]] = []
        self.total_usd = 0.0
        self._last_cost_by_role: dict[str, float] = {}

    @contextmanager
    def activate(self) -> Iterator["UsageLog"]:
        token = _current_log.set(self)
        try:
            yield self
        finally:
            _current_log.reset(token)

    def check_cap(self, role: str) -> None:
        """Abort before a predicted next call reaches the configured cap."""

        if not self.settings.get("COST_CAP_ENABLED", True):
            return
        cap = float(self.settings["COST_CAP_USD_PER_RUN"])
        estimate = self._last_cost_by_role.get(role, 0.0)
        if self.total_usd + estimate >= cap:
            raise CostCapExceeded(
                f"LLM-Kostenlimit fuer Run {self.run_id} erreicht: "
                f"{self.total_usd:.6f} USD + Schaetzung {estimate:.6f} USD "
                f">= {cap:.2f} USD."
            )

    def record(self, role: str, model: str, response: Any) -> UsageRecord:
        """Price and persist one normalized response usage object."""

        tokens = extract_usage(response)
        recorded_at = datetime.now(timezone.utc)
        prices = resolve_pricing(
            self.settings,
            model,
            at=recorded_at,
            prompt_tokens=tokens.prompt,
        )
        estimated = prices is None
        if prices is None:
            try:
                usd = float(
                    litellm.completion_cost(
                        completion_response=response,
                        model=model,
                    )
                )
            except Exception as exc:  # pragma: no cover - depends on LiteLLM map
                logger.warning(
                    "Kein eigener Preis fuer %s und LiteLLM-Schaetzung fehlgeschlagen: %s",
                    model,
                    exc,
                )
                usd = 0.0
            else:
                logger.warning(
                    "Kein autoritativer PRICING-Eintrag fuer %s; "
                    "LiteLLM-Schaetzung %.6f USD wird verwendet.",
                    model,
                    usd,
                )
        else:
            usd = calculate_cost(tokens, prices)

        entry = {
            "ts": recorded_at.isoformat(),
            "run_id": self.run_id,
            "role": role,
            "model": model,
            **asdict(tokens),
            "usd": usd,
            "turn": _current_turn.get(),
            "estimated": estimated,
        }
        self.records.append(entry)
        self.total_usd += usd
        self._last_cost_by_role[role] = usd
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._write_summary()
        return tokens

    def _write_summary(self) -> None:
        grouped: dict[str, dict[str, float | int]] = {}
        for row in self.records:
            key = f"{row['role']}|{row['model']}"
            bucket = grouped.setdefault(
                key,
                {
                    "calls": 0,
                    "prompt": 0,
                    "completion": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "usd": 0.0,
                },
            )
            bucket["calls"] += 1
            for field in ("prompt", "completion", "cache_read", "cache_write"):
                bucket[field] += int(row[field])
            bucket["usd"] += float(row["usd"])
        payload = {
            "run_id": self.run_id,
            "cost_usd_total": self.total_usd,
            "usage_by_role_model": grouped,
        }
        tmp = self.summary_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.summary_path)


def current_usage_log() -> UsageLog | None:
    """Return the log activated by the surrounding run, if any."""

    return _current_log.get()


@contextmanager
def usage_turn(turn: int) -> Iterator[None]:
    """Attach a tool-loop turn number to records made in this context."""

    token = _current_turn.set(turn)
    try:
        yield
    finally:
        _current_turn.reset(token)
