"""Live-Status-Writer und kooperative Stop-Mechanik (§5e Punkt 1).

``RunStatus`` fuehrt ``runs/<run_id>.status.json`` (bei jedem Update atomar komplett
neu geschrieben). Die Step-Liste wird beim Run-Start aus der Pipeline-Definition
mit ``pending`` vorbefuellt; jeder Step meldet Beginn, Ende, Fehler, Artefakte,
Kosten und die §5e-``counts``. Der Stop wird kooperativ ueber
``runs/<run_id>.stop`` geprueft – dieselbe Datei, die der B1-``RunContext.check_stop``
liest. ``make_run_context`` reicht diesen Pfad an den Tool-Loop weiter, damit es
keine Doppel-Implementierung gibt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .llm import RunContext, StopRequested, UsageLog

logger = logging.getLogger(__name__)


# Run-Zustaende.
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_ABORTED = "aborted"

# Step-Zustaende.
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_ABORTED = "aborted"
STEP_SKIPPED = "skipped"

_TERMINAL_RUN_STATES = {STATE_DONE, STATE_FAILED, STATE_ABORTED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_tokens() -> dict[str, int]:
    return {"prompt": 0, "completion": 0, "cache_read": 0, "cache_write": 0}


@dataclass
class StepOutcome:
    """Rueckgabe einer Step-Funktion an den Pipeline-Treiber (alles optional)."""

    artifacts: list[str] | None = None
    cost_usd: float = 0.0
    tokens: Mapping[str, int] | None = None
    counts: Mapping[str, Any] | None = None


class RunStatus:
    """Atomarer Live-Status-Writer eines Laufs samt Stop-Datei-Pruefung."""

    def __init__(
        self,
        run_id: str,
        pipeline: str,
        step_names: Sequence[str],
        *,
        settings: Mapping[str, Any],
        pid: int | None = None,
    ) -> None:
        self.run_id = run_id
        self.settings = dict(settings)
        data_dir = Path(settings["DATA_DIR"])
        self.runs_dir = data_dir / settings.get("RUNS_DIRNAME", "runs")
        self.path = self.runs_dir / f"{run_id}.status.json"
        self.stop_path = self.runs_dir / f"{run_id}.stop"
        self._state: dict[str, Any] = {
            "run_id": run_id,
            "variant": data_dir.name,
            "pipeline": pipeline,
            "pid": pid if pid is not None else os.getpid(),
            "started_at": _now(),
            "finished_at": None,
            "state": STATE_RUNNING,
            "stop_requested": False,
            "cost_usd_total": 0.0,
            "steps": [
                {
                    "step": name,
                    "state": STEP_PENDING,
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                    "artifacts": [],
                    "cost_usd": 0.0,
                    "tokens": _empty_tokens(),
                    "counts": {},
                }
                for name in step_names
            ],
        }
        self._write()

    # -- interne Helfer -------------------------------------------------

    def _write(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.path)

    def _step(self, name: str) -> dict[str, Any]:
        for step in self._state["steps"]:
            if step["step"] == name:
                return step
        raise KeyError(f"Unbekannter Step '{name}' im Status von {self.run_id}.")

    # -- Stop-Mechanik --------------------------------------------------

    def check_stop(self) -> bool:
        """Kooperative Stop-Pruefung; markiert ``stop_requested`` persistiert."""

        requested = self.stop_path.exists()
        if requested and not self._state["stop_requested"]:
            self._state["stop_requested"] = True
            self._write()
        return requested

    def request_stop(self) -> None:
        """Legt die Stop-Datei an (fuer UI/Tests); Pipeline reagiert kooperativ."""

        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text(_now(), encoding="utf-8")

    def make_run_context(
        self,
        *,
        usage_log: UsageLog | None = None,
        spec_override: dict[str, Any] | None = None,
    ) -> RunContext:
        """RunContext auf DIESELBE Stop-Datei (keine Doppel-Implementierung)."""

        return RunContext(
            self.run_id,
            stop_path=self.stop_path,
            usage_log=usage_log,
            spec_override=spec_override,
        )

    # -- Step-Uebergaenge ----------------------------------------------

    def start_step(self, name: str) -> None:
        step = self._step(name)
        step["state"] = STEP_RUNNING
        step["started_at"] = _now()
        self._write()

    def finish_step(
        self,
        name: str,
        *,
        artifacts: Iterable[str] | None = None,
        cost_usd: float = 0.0,
        tokens: Mapping[str, int] | None = None,
        counts: Mapping[str, Any] | None = None,
    ) -> None:
        step = self._step(name)
        step["state"] = STEP_DONE
        step["finished_at"] = _now()
        step["artifacts"] = list(artifacts or [])
        step["cost_usd"] = float(cost_usd)
        if tokens:
            merged = _empty_tokens()
            merged.update({k: int(v) for k, v in tokens.items() if k in merged})
            step["tokens"] = merged
        step["counts"] = dict(counts or {})
        self._recompute_cost()
        self._write()

    def fail_step(self, name: str, error: str) -> None:
        step = self._step(name)
        step["state"] = STEP_FAILED
        step["finished_at"] = _now()
        step["error"] = error
        self._write()

    def skip_step(self, name: str) -> None:
        step = self._step(name)
        step["state"] = STEP_SKIPPED
        step["finished_at"] = _now()
        self._write()

    def abort_from(self, name: str) -> None:
        """Markiert den Step und alle folgenden pending/running Steps als aborted."""

        reached = False
        for step in self._state["steps"]:
            if step["step"] == name:
                reached = True
            if reached and step["state"] in {STEP_PENDING, STEP_RUNNING}:
                step["state"] = STEP_ABORTED
                step["finished_at"] = _now()
        self._state["stop_requested"] = True
        self.finish_run(STATE_ABORTED)

    def _recompute_cost(self) -> None:
        self._state["cost_usd_total"] = float(
            sum(step["cost_usd"] for step in self._state["steps"])
        )

    def set_cost_total(self, total_usd: float) -> None:
        self._state["cost_usd_total"] = float(total_usd)
        self._write()

    def finish_run(self, state: str = STATE_DONE) -> None:
        if state not in _TERMINAL_RUN_STATES:
            raise ValueError(f"Kein terminaler Run-Zustand: {state}")
        self._state["state"] = state
        self._state["finished_at"] = _now()
        self._recompute_cost()
        self._write()

    @property
    def state(self) -> dict[str, Any]:
        """Tiefe Momentaufnahme des Status (fuer Tests/UI)."""

        return json.loads(json.dumps(self._state))


def run_pipeline(
    status: RunStatus,
    steps: Sequence[tuple[str, Callable[[], StepOutcome | None]]],
) -> str:
    """Fuehrt die Steps sequentiell und meldet jeden Uebergang an den Status.

    Vor jedem Step wird die Stop-Datei geprueft (kooperativ). Bei Stop oder einem
    aus dem Step propagierten :class:`StopRequested` enden Lauf und Reststeps als
    ``aborted``; eine Exception setzt den Step ``failed`` und den Lauf ``failed``.
    Rueckgabe = terminaler Run-Zustand.
    """

    for name, func in steps:
        if status.check_stop():
            status.abort_from(name)
            return STATE_ABORTED
        status.start_step(name)
        try:
            outcome = func()
        except StopRequested:
            status.abort_from(name)
            return STATE_ABORTED
        except Exception as exc:  # pragma: no cover - defensiv geloggt
            logger.exception("Step %s fehlgeschlagen.", name)
            status.fail_step(name, f"{type(exc).__name__}: {exc}")
            status.finish_run(STATE_FAILED)
            return STATE_FAILED
        outcome = outcome or StepOutcome()
        status.finish_step(
            name,
            artifacts=outcome.artifacts,
            cost_usd=outcome.cost_usd,
            tokens=outcome.tokens,
            counts=outcome.counts,
        )
    status.finish_run(STATE_DONE)
    return STATE_DONE
