"""Seite ``berichte``: Uebergabe-Artefakte + Ergebnis-Ansicht (§5a Bloecke 3, 8).

Run-Auswahl -> Step-/Artefakt-Auswahl -> gerendertes Artefakt (md gestylt, JSON
strukturiert, CSV tabellarisch) ueber die Renderer-Route ``/st-doc`` als iframe.
Die Ergebnis-Ansicht rendert ``empfehlung.md``/``bericht.md`` des Laufs in
Artefakt-Qualitaet (reporting/renderer.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

import taipy.gui.builder as tgb
from taipy.gui import notify

from ... import reporting
from .. import runctl, state as st

logger = logging.getLogger(__name__)

ber_run_options: list[str] = []
ber_selected_run = ""
ber_steps_df = st.steps_df(None)
ber_artifact_options: list[str] = []
ber_selected_artifact = ""
ber_doc_url = ""
ber_doc_visible = False
ber_result_note = ""
ber_has_recommendation = False
ber_has_report = False


def _outputs_rel(settings, run_id: str, filename_key: str) -> str:
    filename = str(settings.get(filename_key))
    return f"{settings.get('OUTPUTS_DIRNAME', 'outputs')}/{run_id}/{filename}"


def _load_run(s, run_id: str, *, step: str | None = None) -> None:
    settings = st.effective_settings()
    status = runctl.load_status(settings, run_id) if run_id else None
    artifacts = st.artifacts_of_run(status)
    data_dir = Path(settings["DATA_DIR"])
    rec_rel = _outputs_rel(settings, run_id, "RECOMMENDATION_DOC_FILENAME")
    rep_rel = _outputs_rel(settings, run_id, "REPORT_DOC_FILENAME")
    selected = ""
    if step and status:
        for item in status.get("steps", []):
            if item.get("step") == step and item.get("artifacts"):
                selected = str(item["artifacts"][0])
                break
    if not selected and artifacts:
        selected = artifacts[0]
    with s as ss:
        ss.ber_selected_run = run_id
        ss.ber_steps_df = st.steps_df(status)
        ss.ber_artifact_options = artifacts
        ss.ber_selected_artifact = selected
        ss.ber_has_recommendation = bool(run_id) and (data_dir / rec_rel).is_file()
        ss.ber_has_report = bool(run_id) and (data_dir / rep_rel).is_file()
        ss.ber_result_note = (
            "Enddokumente vorhanden."
            if (ss.ber_has_recommendation or ss.ber_has_report)
            else "Fuer diesen Lauf liegen (noch) keine Enddokumente vor."
        )
    _show_artifact(s, selected)


def _show_artifact(s, rel_path: str) -> None:
    with s as ss:
        if rel_path:
            ss.ber_doc_url = st.doc_url(rel_path)
            ss.ber_doc_visible = True
        else:
            ss.ber_doc_url = ""
            ss.ber_doc_visible = False


def focus_run(s, run_id: str, *, step: str | None = None) -> None:
    """Einsprung vom Status-Board (§5a Punkt 3: max. zwei Klicks zum Bericht)."""

    refresh(s)
    if run_id:
        _load_run(s, run_id, step=step)


def refresh(s) -> None:
    settings = st.effective_settings()
    options = st.run_ids(settings)
    with s as ss:
        ss.ber_run_options = options
    target = s.ber_selected_run or (options[0] if options else "")
    _load_run(s, target)


def on_run_selected(s, var_name=None, value=None) -> None:
    _load_run(s, s.ber_selected_run)


def on_artifact_selected(s, var_name=None, value=None) -> None:
    _show_artifact(s, s.ber_selected_artifact)


def on_step_click(s, var_name=None, payload=None) -> None:
    index = (payload or {}).get("index")
    if index is None:
        return
    settings = st.effective_settings()
    status = runctl.load_status(settings, s.ber_selected_run)
    if not status:
        return
    steps = status.get("steps", [])
    try:
        step = steps[int(index)]
    except (IndexError, ValueError):
        return
    artifacts = step.get("artifacts") or []
    if not artifacts:
        notify(s, "info", f"Step {step.get('step')} hat keine Artefakte.")
        return
    with s as ss:
        ss.ber_selected_artifact = str(artifacts[0])
    _show_artifact(s, str(artifacts[0]))


def on_show_recommendation(s, id=None, payload=None) -> None:
    settings = st.effective_settings()
    _show_artifact(s, _outputs_rel(settings, s.ber_selected_run, "RECOMMENDATION_DOC_FILENAME"))


def on_show_report(s, id=None, payload=None) -> None:
    settings = st.effective_settings()
    _show_artifact(s, _outputs_rel(settings, s.ber_selected_run, "REPORT_DOC_FILENAME"))


with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Uebergabe-Berichte & Ergebnis-Ansicht", mode="md")
        with tgb.layout(columns="2 2 1 1"):
            tgb.selector(
                value="{ber_selected_run}",
                lov="{ber_run_options}",
                dropdown=True,
                label="Lauf",
                on_change=on_run_selected,
                class_name="fullwidth",
            )
            tgb.selector(
                value="{ber_selected_artifact}",
                lov="{ber_artifact_options}",
                dropdown=True,
                label="Artefakt",
                on_change=on_artifact_selected,
                class_name="fullwidth",
            )
            tgb.button(
                "empfehlung.md",
                on_action=on_show_recommendation,
                active="{ber_has_recommendation}",
                class_name="st-accent",
            )
            tgb.button(
                "bericht.md",
                on_action=on_show_report,
                active="{ber_has_report}",
                class_name="st-accent",
            )
        tgb.text("{ber_result_note}", class_name="st-muted")
        with tgb.expandable(title="Steps dieses Laufs (Klick = Artefakt oeffnen)", expanded=False):
            tgb.table(
                data="{ber_steps_df}",
                page_size=12,
                on_action=on_step_click,
                rebuild=True,
                class_name="st-table",
            )

    with tgb.part(class_name="st-panel", render="{ber_doc_visible}"):
        tgb.part(page="{ber_doc_url}", height="72vh", class_name="st-frame")
    with tgb.part(class_name="st-panel", render="{not ber_doc_visible}"):
        tgb.text("Kein Artefakt ausgewaehlt.", class_name="st-muted")
