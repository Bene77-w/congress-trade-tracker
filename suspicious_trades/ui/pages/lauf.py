"""Seite ``lauf``: Run-Steuerung, Quellen-Schalter, Status-Board, Live-Kosten.

Buendelt die §5a-Bloecke 1 (Start/Stop + Quellen-Schalter), 2 (Agent-Status-
Board als Node-Diagramm mit Auto-Refresh) und 6-live (LLM-USD + Tavily-Credits
mit Cap-Balken des aktiven Runs).
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

import pandas as pd
import taipy.gui.builder as tgb
from taipy.gui import get_state_id, navigate, notify

from .. import runctl, state as st

logger = logging.getLogger(__name__)

_SOURCES = ["congress_gov", "federal_register", "war_gov_rss", "sam_gov"]

# -- Session-State-Defaults ----------------------------------------------------

_settings = st.effective_settings()

lauf_commands = list(_settings.get("UI_START_COMMANDS", ["_dummy-run"]))
lauf_command = lauf_commands[0] if lauf_commands else "_dummy-run"
lauf_extra_args = ""
lauf_run_options: list[str] = []
lauf_selected_run = ""
lauf_state_label = "Kein Lauf ausgewaehlt."
lauf_is_running = False
lauf_stop_pending = False
lauf_grace_elapsed = False
lauf_board_url = st.board_data_url(None)
lauf_steps_df = st.steps_df(None)
lauf_log_tail = ""
lauf_reconnect_dead_run = ""
lauf_reconnect_note = ""
lauf_llm_label = "0.0000 USD"
lauf_cap_pct = 0
lauf_cap_label = ""
lauf_credits_label = "0.0 Credits"
lauf_sources_note = (
    "Schalter wirken ab dem naechsten Lauf (Override-Layer, config_overrides.json)."
)
src_congress_gov = True
src_federal_register = True
src_war_gov_rss = True
src_sam_gov = True


# -- Ableitungen ---------------------------------------------------------------


def _apply_snapshot(s, payload: dict[str, Any]) -> None:
    """Schreibt einen Live-/Datei-Schnappschuss in den Session-State."""

    settings = st.effective_settings()
    status = payload.get("status")
    run_id = str(payload.get("run_id") or "")
    with s as ss:
        ss.lauf_selected_run = run_id
        ss.lauf_board_url = st.board_data_url(status)
        ss.lauf_steps_df = st.steps_df(status)
        ss.lauf_log_tail = payload.get("log_tail") or ""
        if status is None:
            ss.lauf_state_label = (
                f"Kein Status fuer {run_id} gefunden." if run_id else "Kein Lauf ausgewaehlt."
            )
            ss.lauf_is_running = False
            ss.lauf_stop_pending = False
            ss.lauf_grace_elapsed = False
        else:
            run_state = str(status.get("state"))
            ss.lauf_state_label = (
                f"{run_id} · Pipeline {status.get('pipeline')} · Zustand {run_state}"
                f" · PID {status.get('pid')} · gestartet {st.fmt_ts(status.get('started_at'))}"
            )
            ss.lauf_is_running = run_state == "running"
            ss.lauf_stop_pending = bool(status.get("stop_requested")) and run_state == "running"
            ss.lauf_grace_elapsed = (
                ss.lauf_stop_pending and runctl.grace_elapsed(settings, run_id)
            )
        llm = payload.get("llm") or {}
        status_cost = float((status or {}).get("cost_usd_total") or 0.0)
        usd = max(float(llm.get("usd", 0.0) or 0.0), status_cost)
        cap = float(settings.get("COST_CAP_USD_PER_RUN", 0.0) or 0.0)
        ss.lauf_llm_label = f"{usd:.4f} USD ({int(llm.get('calls', 0))} Calls)"
        ss.lauf_cap_pct = int(min(100, (usd / cap) * 100)) if cap > 0 else 0
        cap_enabled = bool(settings.get("COST_CAP_ENABLED", True))
        ss.lauf_cap_label = (
            f"Cap {cap:.2f} USD ({'aktiv' if cap_enabled else 'INAKTIV'})"
        )
        search = payload.get("search") or {}
        credits = float(search.get("credits", 0.0) or 0.0)
        unreported = int(search.get("unreported", 0) or 0)
        note = f" · {unreported} Calls ohne Credit-Angabe" if unreported else ""
        ss.lauf_credits_label = (
            f"{credits:.1f} Credits ({int(search.get('calls', 0))} Calls){note}"
        )


def _live_update(s, payload: dict[str, Any]) -> None:
    """Callback des Poll-Threads (via ``invoke_callback``)."""

    if payload.get("run_id") != s.lauf_selected_run:
        return
    _apply_snapshot(s, payload)


def _load_run(s, run_id: str, *, start_poll: bool = True) -> None:
    settings = st.effective_settings()
    payload = runctl.collect_live(settings, run_id) if run_id else {"run_id": ""}
    _apply_snapshot(s, payload)
    status = payload.get("status")
    if (
        start_poll
        and status is not None
        and status.get("state") == "running"
        and runctl.pid_alive(status.get("pid"))
    ):
        runctl.start_poller(settings, run_id, get_state_id(s), _live_update)


def refresh(s) -> None:
    """Kompletter Seiten-Refresh (on_init, Navigation, nach Aktionen)."""

    settings = st.effective_settings()
    with s as ss:
        ss.lauf_commands = list(settings.get("UI_START_COMMANDS", ["_dummy-run"]))
        if ss.lauf_command not in ss.lauf_commands and ss.lauf_commands:
            ss.lauf_command = ss.lauf_commands[0]
        ss.lauf_run_options = st.run_ids(settings)
        enabled = settings.get("INGEST_SOURCES_ENABLED", {})
        ss.src_congress_gov = bool(enabled.get("congress_gov", True))
        ss.src_federal_register = bool(enabled.get("federal_register", True))
        ss.src_war_gov_rss = bool(enabled.get("war_gov_rss", True))
        ss.src_sam_gov = bool(enabled.get("sam_gov", True))

    # Reconnect-Logik: juengster running-Run gewinnt die Vorauswahl.
    info = runctl.reconnect_info(settings)
    if info is not None and not info["alive"]:
        with s as ss:
            ss.lauf_reconnect_dead_run = info["run_id"]
            ss.lauf_reconnect_note = (
                f"Run {info['run_id']} steht auf 'running', der Prozess "
                f"(PID {info['status'].get('pid')}) lebt aber nicht mehr."
            )
    else:
        with s as ss:
            ss.lauf_reconnect_dead_run = ""
            ss.lauf_reconnect_note = ""

    target = s.lauf_selected_run
    if info is not None and info["alive"]:
        target = info["run_id"]
    elif not target:
        options = st.run_ids(settings)
        target = options[0] if options else ""
    _load_run(s, target)


# -- Aktionen -------------------------------------------------------------------


def on_start(s, id=None, payload=None) -> None:
    settings = st.effective_settings()
    try:
        extra = shlex.split(s.lauf_extra_args or "")
        run_id = runctl.start_run(settings, s.lauf_command, extra_args=extra)
    except Exception as exc:
        notify(s, "error", f"Start fehlgeschlagen: {exc}")
        return
    notify(s, "success", f"Lauf {run_id} gestartet.")
    with s as ss:
        ss.lauf_run_options = [run_id] + [r for r in ss.lauf_run_options if r != run_id]
        ss.lauf_selected_run = run_id
    _load_run(s, run_id)
    runctl.start_poller(settings, run_id, get_state_id(s), _live_update)


def on_stop(s, id=None, payload=None) -> None:
    if not s.lauf_selected_run:
        return
    settings = st.effective_settings()
    path = runctl.request_stop(settings, s.lauf_selected_run)
    notify(s, "warning", f"Stop-Datei geschrieben: {path.name} (kooperativer Abbruch).")
    _load_run(s, s.lauf_selected_run)


def on_sigterm(s, id=None, payload=None) -> None:
    settings = st.effective_settings()
    if runctl.send_sigterm(settings, s.lauf_selected_run):
        notify(s, "warning", "SIGTERM gesendet (Fallback).")
    else:
        notify(s, "error", "SIGTERM nicht zustellbar (Prozess lebt nicht mehr).")
    _load_run(s, s.lauf_selected_run)


def on_mark_failed(s, id=None, payload=None) -> None:
    settings = st.effective_settings()
    run_id = s.lauf_reconnect_dead_run
    if run_id and runctl.mark_failed(settings, run_id):
        notify(s, "info", f"Run {run_id} als failed markiert.")
    refresh(s)


def on_run_selected(s, var_name=None, value=None) -> None:
    _load_run(s, s.lauf_selected_run)


def on_source_toggle(s, var_name: str, value) -> None:
    source = var_name.replace("src_", "")
    st.set_source_enabled(source, bool(value))
    notify(s, "info", f"Quelle {source}: {'aktiv' if value else 'deaktiviert'} (ab naechstem Lauf).")


def on_disable_all_sources(s, id=None, payload=None) -> None:
    st.disable_all_sources()
    with s as ss:
        ss.src_congress_gov = False
        ss.src_federal_register = False
        ss.src_war_gov_rss = False
        ss.src_sam_gov = False
    notify(s, "warning", "Alle Gov-Quellen deaktiviert (ab naechstem Lauf).")


def on_step_click(s, var_name=None, payload=None) -> None:
    """Klick auf eine Step-Zeile: direkt zum Uebergabe-Artefakt (max. 2 Klicks)."""

    from . import berichte

    index = (payload or {}).get("index")
    step_name = None
    if index is not None and isinstance(s.lauf_steps_df, pd.DataFrame):
        try:
            step_name = str(s.lauf_steps_df.iloc[int(index)]["Step"])
        except (IndexError, ValueError, KeyError):
            step_name = None
    berichte.focus_run(s, s.lauf_selected_run, step=step_name)
    navigate(s, "berichte")


# -- Seite ----------------------------------------------------------------------

with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Run-Steuerung", mode="md")
        with tgb.layout(columns="2 3 1 1 1"):
            tgb.selector(
                value="{lauf_command}",
                lov="{lauf_commands}",
                dropdown=True,
                label="Subcommand",
                class_name="fullwidth",
            )
            tgb.input(
                value="{lauf_extra_args}",
                label="Zusatz-Argumente (z.B. --today 2026-07-01)",
                class_name="fullwidth",
            )
            tgb.button("Start", on_action=on_start, class_name="st-accent")
            tgb.button("Stop", on_action=on_stop, active="{lauf_is_running}")
            tgb.button(
                "SIGTERM",
                on_action=on_sigterm,
                active="{lauf_grace_elapsed}",
                class_name="st-danger",
            )
        tgb.text(
            "Stop schreibt die Stop-Datei `runs/<run_id>.stop`; die Pipeline bricht "
            "kooperativ ab. SIGTERM wird erst nach Ablauf von STOP_GRACE_SECONDS aktiv.",
            mode="md",
            class_name="st-muted",
        )
        with tgb.part(render="{lauf_reconnect_dead_run != ''}", class_name="st-warnbox"):
            tgb.text("{lauf_reconnect_note}")
            tgb.button("Als failed markieren", on_action=on_mark_failed)

    with tgb.part(class_name="st-panel"):
        tgb.text("## Quellen-Schalter", mode="md")
        with tgb.layout(columns="1 1 1 1 1"):
            tgb.toggle(value="{src_congress_gov}", label="Congress.gov", on_change=on_source_toggle)
            tgb.toggle(
                value="{src_federal_register}",
                label="Federal Register",
                on_change=on_source_toggle,
            )
            tgb.toggle(value="{src_war_gov_rss}", label="war.gov RSS", on_change=on_source_toggle)
            tgb.toggle(value="{src_sam_gov}", label="SAM.gov", on_change=on_source_toggle)
            tgb.button("Alle Quellen deaktivieren", on_action=on_disable_all_sources)
        tgb.text("{lauf_sources_note}", class_name="st-muted")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Agent-Status-Board", mode="md")
        with tgb.layout(columns="2 3"):
            tgb.selector(
                value="{lauf_selected_run}",
                lov="{lauf_run_options}",
                dropdown=True,
                label="Lauf",
                on_change=on_run_selected,
                class_name="fullwidth",
            )
            tgb.text("{lauf_state_label}", class_name="st-muted")
        tgb.image(content="{lauf_board_url}", width="100%", class_name="st-board")
        tgb.table(
            data="{lauf_steps_df}",
            page_size=12,
            on_action=on_step_click,
            rebuild=True,
            class_name="st-table",
        )
        with tgb.expandable(title="Log-Tail", expanded=False):
            tgb.text("{lauf_log_tail}", mode="pre")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Live-Kosten des Laufs", mode="md")
        with tgb.layout(columns="1 1 1"):
            with tgb.part():
                tgb.text("LLM-Kosten", class_name="st-muted")
                tgb.text("{lauf_llm_label}", class_name="st-big")
                tgb.progress(value="{lauf_cap_pct}")
                tgb.text("{lauf_cap_label}", class_name="st-muted")
            with tgb.part():
                tgb.text("Tavily-Credits (dieser Lauf)", class_name="st-muted")
                tgb.text("{lauf_credits_label}", class_name="st-big")
                tgb.text(
                    "Monats-Budget-Stand: Seite Kosten (GET /usage nur auf Klick).",
                    class_name="st-muted",
                )
            with tgb.part():
                tgb.text("Auto-Refresh", class_name="st-muted")
                tgb.text(
                    "Poll-Thread aktualisiert Status/Kosten alle STATUS_POLL_SECONDS, "
                    "solange der Lauf laeuft.",
                    class_name="st-muted",
                )
