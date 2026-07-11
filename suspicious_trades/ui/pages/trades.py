"""Seite ``trades``: Trade-Auswahl aus dem Zwischenlager (§5a Block 5).

Listet die Export-Dateien der Haupt-App aus ``data/agent_staging/`` mit
Vorschau; "Fuer naechsten Lauf uebernehmen" kopiert die Auswahl nach
``data/<package>/staged_trades/`` — von dort liest ``trades_access`` bei
``TRADE_SOURCE='staging'``. Darunter die aktuell aktive Auswahl (entfernbar).
"""

from __future__ import annotations

import logging

import pandas as pd
import taipy.gui.builder as tgb
from taipy.gui import notify

from .. import state as st

logger = logging.getLogger(__name__)

stg_files_df = pd.DataFrame(columns=["Datei", "Geaendert", "Zeilen"])
stg_selected_file = ""
stg_preview_df = pd.DataFrame()
stg_preview_label = "Keine Datei ausgewaehlt."
staged_df = pd.DataFrame(columns=["Datei", "Geaendert", "Zeilen"])
staged_selected_file = ""
staged_summary = ""
stg_source_label = ""


def refresh(s) -> None:
    settings = st.effective_settings()
    with s as ss:
        ss.stg_files_df = st.staging_files_df(settings)
        ss.staged_df = st.staged_files_df(settings)
        ss.staged_summary = st.active_selection_summary(settings)
        ss.stg_source_label = (
            f"TRADE_SOURCE = {settings.get('TRADE_SOURCE')} · "
            f"Zwischenlager: {st.staging_dir(settings)} · "
            f"aktive Auswahl: {st.staged_dir(settings)}"
        )
        if ss.stg_selected_file and ss.stg_selected_file not in set(
            ss.stg_files_df["Datei"].astype(str)
        ):
            ss.stg_selected_file = ""
            ss.stg_preview_df = pd.DataFrame()
            ss.stg_preview_label = "Keine Datei ausgewaehlt."


def _preview(s, filename: str) -> None:
    settings = st.effective_settings()
    path = st.staging_dir(settings) / filename
    frame = st.preview_csv(settings, path)
    with s as ss:
        ss.stg_selected_file = filename
        ss.stg_preview_df = frame
        ss.stg_preview_label = f"Vorschau {filename} ({len(frame)} Zeilen angezeigt)"


def on_staging_click(s, var_name=None, payload=None) -> None:
    index = (payload or {}).get("index")
    if index is None:
        return
    try:
        filename = str(s.stg_files_df.iloc[int(index)]["Datei"])
    except (IndexError, ValueError):
        return
    _preview(s, filename)


def on_adopt(s, id=None, payload=None) -> None:
    if not s.stg_selected_file:
        notify(s, "warning", "Erst eine Export-Datei anklicken.")
        return
    settings = st.effective_settings()
    try:
        target = st.adopt_staging_file(settings, s.stg_selected_file)
    except Exception as exc:
        notify(s, "error", f"Uebernahme fehlgeschlagen: {exc}")
        return
    notify(s, "success", f"Uebernommen nach {target}.")
    refresh(s)


def on_staged_click(s, var_name=None, payload=None) -> None:
    index = (payload or {}).get("index")
    if index is None:
        return
    try:
        filename = str(s.staged_df.iloc[int(index)]["Datei"])
    except (IndexError, ValueError):
        return
    with s as ss:
        ss.staged_selected_file = filename
    notify(s, "info", f"Ausgewaehlt: {filename} (Entfernen ueber den Button).")


def on_remove_staged(s, id=None, payload=None) -> None:
    if not s.staged_selected_file:
        notify(s, "warning", "Erst eine Datei in der aktiven Auswahl anklicken.")
        return
    settings = st.effective_settings()
    if st.remove_staged_file(settings, s.staged_selected_file):
        notify(s, "info", f"{s.staged_selected_file} aus der aktiven Auswahl entfernt.")
    with s as ss:
        ss.staged_selected_file = ""
    refresh(s)


with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Zwischenlager (Exporte der Haupt-App)", mode="md")
        tgb.text("{stg_source_label}", class_name="st-muted")
        with tgb.layout(columns="2 1"):
            with tgb.part():
                tgb.table(
                    data="{stg_files_df}",
                    page_size=8,
                    on_action=on_staging_click,
                    rebuild=True,
                    class_name="st-table",
                )
            with tgb.part():
                tgb.text("{stg_preview_label}", class_name="st-muted")
                tgb.button(
                    "Fuer naechsten Lauf uebernehmen",
                    on_action=on_adopt,
                    class_name="st-accent",
                )
        with tgb.expandable(title="Vorschau", expanded=True):
            tgb.table(data="{stg_preview_df}", page_size=10, rebuild=True, class_name="st-table")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Aktive Auswahl (staged_trades/)", mode="md")
        tgb.text("{staged_summary}")
        tgb.table(
            data="{staged_df}",
            page_size=8,
            on_action=on_staged_click,
            rebuild=True,
            class_name="st-table",
        )
        tgb.button("Ausgewaehlte Datei entfernen", on_action=on_remove_staged)
