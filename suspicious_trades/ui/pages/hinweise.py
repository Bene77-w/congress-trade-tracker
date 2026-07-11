"""Seite ``hinweise``: User-Hinweise erfassen und verwalten (§5a Block 4).

Neue Hinweise landen append-only in ``user_hints/hints.jsonl`` und werden beim
naechsten Lauf in die vorgesehenen Prompts injiziert (Verdrahtung je Variante).
Konsumierte Hinweise bleiben einsehbar; Archivieren blendet dauerhaft aus.
"""

from __future__ import annotations

import logging

import pandas as pd
import taipy.gui.builder as tgb
from taipy.gui import notify

from ... import hints
from .. import state as st

logger = logging.getLogger(__name__)

hint_text = ""
hint_ticker = ""
hint_topic = ""
hints_open_df = pd.DataFrame()
hints_done_df = pd.DataFrame()
hint_selected_id = ""
hints_enabled = True
hints_note = ""


def refresh(s) -> None:
    settings = st.effective_settings()
    frame = st.hints_df(settings)
    open_mask = frame["status"] == "offen" if not frame.empty else []
    with s as ss:
        ss.hints_open_df = frame[open_mask] if not frame.empty else frame
        ss.hints_done_df = (
            frame[~frame["status"].eq("offen")] if not frame.empty else frame
        )
        ss.hints_enabled = bool(settings.get("USER_HINTS_ENABLED", True))
        ss.hints_note = (
            "Offene Hinweise werden beim naechsten Lauf injiziert und dann als "
            "konsumiert markiert." if ss.hints_enabled
            else "USER_HINTS_ENABLED ist AUS - Hinweise werden NICHT injiziert."
        )


def on_save_hint(s, id=None, payload=None) -> None:
    text = (s.hint_text or "").strip()
    if not text:
        notify(s, "warning", "Hinweistext ist leer.")
        return
    settings = st.effective_settings()
    hint = hints.append_hint(
        settings,
        text,
        ticker=(s.hint_ticker or "").strip() or None,
        topic=(s.hint_topic or "").strip() or None,
    )
    notify(s, "success", f"Hinweis {hint['hint_id']} gespeichert.")
    with s as ss:
        ss.hint_text = ""
        ss.hint_ticker = ""
        ss.hint_topic = ""
    refresh(s)


def on_open_click(s, var_name=None, payload=None) -> None:
    index = (payload or {}).get("index")
    if index is None:
        return
    try:
        hint_id = str(s.hints_open_df.iloc[int(index)]["hint_id"])
    except (IndexError, ValueError):
        return
    with s as ss:
        ss.hint_selected_id = hint_id


def on_archive(s, id=None, payload=None) -> None:
    if not s.hint_selected_id:
        notify(s, "warning", "Erst einen offenen Hinweis anklicken.")
        return
    settings = st.effective_settings()
    if hints.archive_hint(settings, s.hint_selected_id):
        notify(s, "info", f"Hinweis {s.hint_selected_id} archiviert.")
    with s as ss:
        ss.hint_selected_id = ""
    refresh(s)


def on_toggle_enabled(s, var_name=None, value=None) -> None:
    st.set_override("USER_HINTS_ENABLED", bool(s.hints_enabled))
    refresh(s)


with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Neuer Hinweis", mode="md")
        tgb.input(
            value="{hint_text}",
            label="Eigene Information / Ereignis-Anregung (wird 1:1 injiziert)",
            multiline=True,
            lines_shown=4,
            class_name="fullwidth",
        )
        with tgb.layout(columns="1 1 1 2"):
            tgb.input(value="{hint_ticker}", label="Ticker (optional)")
            tgb.input(value="{hint_topic}", label="Thema (optional)")
            tgb.button("Hinweis speichern", on_action=on_save_hint, class_name="st-accent")
            tgb.toggle(
                value="{hints_enabled}",
                label="Injektion aktiv (USER_HINTS_ENABLED)",
                on_change=on_toggle_enabled,
            )
        tgb.text("{hints_note}", class_name="st-muted")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Offene Hinweise (Klick = auswaehlen)", mode="md")
        tgb.table(
            data="{hints_open_df}",
            page_size=8,
            on_action=on_open_click,
            rebuild=True,
            class_name="st-table",
        )
        tgb.button("Ausgewaehlten Hinweis archivieren", on_action=on_archive)

    with tgb.part(class_name="st-panel"):
        tgb.text("## Konsumierte / archivierte Hinweise", mode="md")
        tgb.table(data="{hints_done_df}", page_size=8, rebuild=True, class_name="st-table")
