"""Subprozess-Start/-Stop, PID-Handling, Status-Poll-Thread und Reconnect (§5a).

Persistenter Zustand liegt ausschliesslich in Dateien unter ``runs/`` — das UI
ist stateless neu startbar. Der Start eines Laufs ist ein ``subprocess.Popen``
auf ``python -m <package>.run <subcommand> --run-id <id>`` mit Log-Umleitung
nach ``runs/<run_id>.log``; PID und Zustand schreibt die Pipeline selbst ueber
``status.py`` in ``runs/<run_id>.status.json``. Stop laeuft kooperativ ueber die
Stop-Datei ``runs/<run_id>.stop``; SIGTERM ist nur der Fallback nach Ablauf von
``STOP_GRACE_SECONDS``. Der Poll-Thread liest Status- und Usage-Dateien alle
``STATUS_POLL_SECONDS`` und pusht Aenderungen via ``invoke_callback`` in den
Client-State; er endet bei jedem terminalen Run-Zustand.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Any, Callable, Mapping

from .. import config

logger = logging.getLogger(__name__)

# Terminale Run-Zustaende — beenden das Polling.
TERMINAL_STATES = {"done", "failed", "aborted"}

# Top-Level-Package (fuer -m Aufrufe und Klonbarkeit).
_PACKAGE = __package__.split(".")[0]

# Gui-Instanz fuer invoke_callback aus Hintergrund-Threads (Setter in app.py).
_GUI: Any = None

# Laufende Popen-Handles dieses UI-Prozesses (nur Komfort; PID-Wahrheit = status.json).
_PROCESSES: dict[str, subprocess.Popen] = {}

# Aktive Poll-Threads je (state_id, run_id) gegen Doppel-Polling.
_POLLERS: dict[tuple[str, str], "StatusPoller"] = {}
_POLLERS_LOCK = threading.Lock()


def set_gui(gui: Any) -> None:
    """Registriert die Gui-Instanz fuer Thread-Callbacks (einmalig in app.py)."""

    global _GUI
    _GUI = gui


def get_gui() -> Any:
    return _GUI


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runs_dir(settings: Mapping[str, Any]) -> Path:
    """Runs-Ordner der Variante (Status-, Log-, Usage- und Stop-Dateien)."""

    return Path(settings["DATA_DIR"]) / str(settings.get("RUNS_DIRNAME", "runs"))


def status_path(settings: Mapping[str, Any], run_id: str) -> Path:
    return runs_dir(settings) / f"{run_id}.status.json"


def stop_path(settings: Mapping[str, Any], run_id: str) -> Path:
    return runs_dir(settings) / f"{run_id}.stop"


def log_path(settings: Mapping[str, Any], run_id: str) -> Path:
    return runs_dir(settings) / f"{run_id}.log"


def load_status(settings: Mapping[str, Any], run_id: str) -> dict[str, Any] | None:
    """Liest ein Status-JSON fehlertolerant (None bei fehlend/kaputt)."""

    path = status_path(settings, run_id)
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def list_runs(
    settings: Mapping[str, Any], *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Alle bekannten Laeufe (Status-Dateien), juengste zuerst."""

    directory = runs_dir(settings)
    if not directory.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for path in directory.glob("*.status.json"):
        run_id = path.name[: -len(".status.json")]
        payload = load_status(settings, run_id)
        if payload is not None:
            runs.append(payload)
    runs.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    if limit is not None:
        runs = runs[: int(limit)]
    return runs


def pid_alive(pid: int | None) -> bool:
    """Lebend-Check ohne Signalwirkung (``os.kill(pid, 0)``)."""

    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def find_running(settings: Mapping[str, Any]) -> dict[str, Any] | None:
    """Juengstes Status-JSON mit ``state == "running"`` (Reconnect-Kandidat)."""

    for payload in list_runs(settings):
        if payload.get("state") == "running":
            return payload
    return None


def reconnect_info(settings: Mapping[str, Any]) -> dict[str, Any] | None:
    """Reconnect-Pruefung nach UI-Neustart (§5a-Feinstruktur).

    Rueckgabe ``{"run_id", "alive", "status"}`` fuer den juengsten laufenden
    Run oder None. ``alive=False`` bedeutet: Prozess tot, aber Status noch
    ``running`` — das UI bietet dann "als failed markieren" an (kein
    Auto-Umschreiben).
    """

    payload = find_running(settings)
    if payload is None:
        return None
    return {
        "run_id": str(payload.get("run_id")),
        "alive": pid_alive(payload.get("pid")),
        "status": payload,
    }


def start_run(
    settings: Mapping[str, Any],
    subcommand: str,
    *,
    extra_args: list[str] | None = None,
    run_id: str | None = None,
) -> str:
    """Startet ``python -m <package>.run <subcommand>`` als Subprozess.

    stdout/stderr laufen nach ``runs/<run_id>.log``; PID/Zustand schreibt die
    Pipeline selbst ins Status-JSON. Rueckgabe = vergebene run_id.
    """

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = run_id or f"{subcommand.lstrip('_')}-{stamp}"
    directory = runs_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        f"{_PACKAGE}.run",
        subcommand,
        "--run-id",
        run_id,
        *(extra_args or []),
    ]
    log_file = log_path(settings, run_id).open("ab")
    try:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(config.PROJECT_ROOT),
            start_new_session=True,
        )
    finally:
        log_file.close()
    _PROCESSES[run_id] = process
    logger.info("Run %s gestartet: %s (pid=%s)", run_id, " ".join(command), process.pid)
    return run_id


def request_stop(settings: Mapping[str, Any], run_id: str) -> Path:
    """Schreibt die Stop-Datei; die Pipeline bricht kooperativ ab."""

    directory = runs_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    path = stop_path(settings, run_id)
    path.write_text(_now(), encoding="utf-8")
    return path


def stop_requested_at(settings: Mapping[str, Any], run_id: str) -> datetime | None:
    """Zeitpunkt der Stop-Anforderung (mtime der Stop-Datei) oder None."""

    path = stop_path(settings, run_id)
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def grace_elapsed(settings: Mapping[str, Any], run_id: str) -> bool:
    """True, wenn seit der Stop-Datei ``STOP_GRACE_SECONDS`` vergangen sind."""

    requested = stop_requested_at(settings, run_id)
    if requested is None:
        return False
    grace = float(settings.get("STOP_GRACE_SECONDS", 120))
    return (datetime.now(timezone.utc) - requested).total_seconds() >= grace


def send_sigterm(settings: Mapping[str, Any], run_id: str) -> bool:
    """SIGTERM-Fallback nach abgelaufener Grace-Zeit; True wenn zugestellt."""

    payload = load_status(settings, run_id)
    pid = payload.get("pid") if payload else None
    if not pid or not pid_alive(pid):
        return False
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return False
    return True


def mark_failed(
    settings: Mapping[str, Any],
    run_id: str,
    *,
    reason: str = "Prozess nicht mehr vorhanden; vom Control-UI als failed markiert.",
) -> bool:
    """Markiert einen verwaisten ``running``-Run als ``failed`` (User-Aktion).

    Laufende Steps werden ``failed`` (mit Begruendung), wartende ``aborted``.
    Atomar geschrieben; True bei Erfolg.
    """

    payload = load_status(settings, run_id)
    if payload is None or payload.get("state") != "running":
        return False
    now = _now()
    for step in payload.get("steps", []):
        if step.get("state") == "running":
            step["state"] = "failed"
            step["finished_at"] = now
            step["error"] = reason
        elif step.get("state") == "pending":
            step["state"] = "aborted"
            step["finished_at"] = now
    payload["state"] = "failed"
    payload["finished_at"] = now
    path = status_path(settings, run_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return True


# -- Kosten-/Log-Ableitungen ------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def llm_usage_totals(settings: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    """Summen aus ``runs/<run_id>.usage.jsonl`` (USD, Calls, Tokens)."""

    rows = _read_jsonl(runs_dir(settings) / f"{run_id}.usage.jsonl")
    totals = {
        "usd": 0.0,
        "calls": 0,
        "prompt": 0,
        "completion": 0,
        "cache_read": 0,
        "cache_write": 0,
        "estimated": False,
    }
    for row in rows:
        totals["usd"] += float(row.get("usd", 0.0) or 0.0)
        totals["calls"] += 1
        for field in ("prompt", "completion", "cache_read", "cache_write"):
            totals[field] += int(row.get(field, 0) or 0)
        totals["estimated"] = totals["estimated"] or bool(row.get("estimated"))
    return totals


def search_usage_totals(settings: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    """Summen aus ``runs/<run_id>.search_usage.jsonl`` (Tavily-Credits)."""

    rows = _read_jsonl(runs_dir(settings) / f"{run_id}.search_usage.jsonl")
    totals: dict[str, Any] = {"credits": 0.0, "calls": 0, "unreported": 0}
    for row in rows:
        totals["calls"] += 1
        credits = row.get("credits")
        if credits is None or not row.get("usage_reported", True):
            totals["unreported"] += 1
        try:
            totals["credits"] += float(credits or 0.0)
        except (TypeError, ValueError):
            totals["unreported"] += 1
    return totals


def read_log_tail(settings: Mapping[str, Any], run_id: str) -> str:
    """Letzte ``UI_LOG_TAIL_LINES`` Zeilen des Run-Logs (leer, falls fehlend)."""

    path = log_path(settings, run_id)
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    limit = int(settings.get("UI_LOG_TAIL_LINES", 200))
    return "\n".join(lines[-limit:])


def collect_live(settings: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    """Ein Poll-Schnappschuss: Status + LLM-/Search-Kosten + Log-Tail."""

    return {
        "run_id": run_id,
        "status": load_status(settings, run_id),
        "llm": llm_usage_totals(settings, run_id),
        "search": search_usage_totals(settings, run_id),
        "log_tail": read_log_tail(settings, run_id),
        "polled_at": _now(),
    }


# -- Poll-Thread -------------------------------------------------------------


class StatusPoller(threading.Thread):
    """Liest Status/Usage alle ``STATUS_POLL_SECONDS`` und pusht in den State.

    Endet selbststaendig bei terminalem Run-Zustand (letztes Update wird noch
    zugestellt) oder wenn ``stop()`` gerufen wird. Kein Polling ohne aktiven Run.
    """

    def __init__(
        self,
        settings: Mapping[str, Any],
        run_id: str,
        state_id: str,
        callback: Callable[..., None],
    ) -> None:
        super().__init__(daemon=True, name=f"status-poller-{run_id}")
        self.settings = dict(settings)
        self.run_id = run_id
        self.state_id = state_id
        self.callback = callback
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _push(self, payload: dict[str, Any]) -> None:
        gui = get_gui()
        if gui is None:  # pragma: no cover - nur ohne laufende Gui
            return
        from taipy.gui import invoke_callback

        try:
            invoke_callback(gui, self.state_id, self.callback, [payload])
        except Exception:  # pragma: no cover - Client evtl. getrennt
            logger.exception("Status-Push fuer %s fehlgeschlagen.", self.run_id)
            self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - Thread-Schleife (Integrationspfad)
        poll_seconds = float(self.settings.get("STATUS_POLL_SECONDS", 2))
        try:
            while not self._stop_event.is_set():
                payload = collect_live(self.settings, self.run_id)
                self._push(payload)
                status = payload.get("status")
                if status is not None and status.get("state") in TERMINAL_STATES:
                    break
                self._stop_event.wait(poll_seconds)
        finally:
            with _POLLERS_LOCK:
                _POLLERS.pop((self.state_id, self.run_id), None)


def start_poller(
    settings: Mapping[str, Any],
    run_id: str,
    state_id: str,
    callback: Callable[..., None],
) -> StatusPoller:
    """Startet (hoechstens) einen Poll-Thread je Client und Run."""

    key = (state_id, run_id)
    with _POLLERS_LOCK:
        existing = _POLLERS.get(key)
        if existing is not None and existing.is_alive():
            return existing
        # Alte Poller desselben Clients (anderer Run) beenden.
        for (sid, rid), poller in list(_POLLERS.items()):
            if sid == state_id:
                poller.stop()
                _POLLERS.pop((sid, rid), None)
        poller = StatusPoller(settings, run_id, state_id, callback)
        _POLLERS[key] = poller
        poller.start()
        return poller
