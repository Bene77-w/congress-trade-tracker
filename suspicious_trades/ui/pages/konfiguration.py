"""Seite ``konfiguration``: Config-Formular + Prompt-Editor (§5a Bloecke 7, 9).

Das Formular wird aus den UPPERCASE-Defaults generiert (gruppiert nach
Bloecken, ein Control je Schluessel). Gespeichert werden NUR Abweichungen vom
Default (``config_overrides.json``, atomar, §5d); je Schluessel gibt es einen
Reset-Knopf. Pfad-Konstanten sind read-only (abgeleitet aus PROJECT_ROOT).
Der Prompt-Editor zeigt Default vs. Override je Rolle; ein Override ist immer
die vollstaendige Template-Datei, Reset loescht sie.
"""

from __future__ import annotations

import logging

import pandas as pd
import taipy.gui.builder as tgb
from taipy.gui import notify

from ... import config, prompts
from .. import state as st

logger = logging.getLogger(__name__)

# (Gruppe, Schluessel) in Formular-Reihenfolge; einmalig beim Import bestimmt.
_KEYS = st.editable_config_keys()
_DEFAULTS = {key: value for key, value in vars(config).items() if key.isupper()}


def _var(key: str) -> str:
    return f"cfg__{key}"


def _is_multiline(key: str) -> bool:
    text = st.config_value_to_text(_DEFAULTS.get(key))
    return "\n" in text or len(text) > 64


def _default_caption(key: str) -> str:
    text = st.config_value_to_text(_DEFAULTS.get(key)).replace("\n", " ")
    if len(text) > 90:
        text = text[:87] + "..."
    return f"Default: {text}"


# Ein Modul-Global je Schluessel (Bindungsziel der generierten Controls).
_initial = config.effective(reload=True)
for _group, _key in _KEYS:
    globals()[_var(_key)] = st.config_value_to_text(_initial.get(_key))

cfg_status = ""
cfg_paths_df = st.path_config_df()

pr_roles: list[str] = []
pr_selected = ""
pr_text = ""
pr_status = ""
pr_default_text = ""


def _override_summary(settings) -> str:
    overrides = st.current_overrides()
    if not overrides:
        return "Keine Overrides aktiv - alle Werte auf Default."
    keys = ", ".join(sorted(overrides))
    return f"Aktive Overrides ({len(overrides)}): {keys}"


def refresh(s) -> None:
    settings = st.effective_settings()
    with s as ss:
        for _group, key in _KEYS:
            ss.assign(_var(key), st.config_value_to_text(settings.get(key)))
        ss.cfg_status = _override_summary(settings)
        ss.pr_roles = st.prompt_roles(settings)
        if ss.pr_selected not in ss.pr_roles:
            ss.pr_selected = ss.pr_roles[0] if ss.pr_roles else ""
    _load_prompt(s, s.pr_selected)


def on_save_all(s, id=None, payload=None) -> None:
    """Sammelt alle Formularwerte, validiert und schreibt NUR Abweichungen."""

    overrides: dict = {}
    errors: list[str] = []
    for _group, key in _KEYS:
        text = str(getattr(s, _var(key)))
        try:
            value = st.parse_config_text(key, text)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if value != _DEFAULTS.get(key):
            overrides[key] = value
    if errors:
        notify(s, "error", " | ".join(errors[:3]))
        return
    config.write_overrides(overrides)
    config.effective(reload=True)
    notify(
        s,
        "success",
        f"{len(overrides)} Override(s) gespeichert (wirken ab dem naechsten Lauf).",
    )
    refresh(s)


def on_reset_key(s, id=None, payload=None) -> None:
    key = str(id or "").replace("reset::", "")
    if not key:
        return
    st.reset_override(key)
    with s as ss:
        ss.assign(_var(key), st.config_value_to_text(_DEFAULTS.get(key)))
        ss.cfg_status = _override_summary(None)
    notify(s, "info", f"{key} auf Default zurueckgesetzt.")


def on_reset_all(s, id=None, payload=None) -> None:
    config.write_overrides({})
    config.effective(reload=True)
    notify(s, "info", "Alle Overrides entfernt.")
    refresh(s)


# -- Prompt-Editor ---------------------------------------------------------------


def _load_prompt(s, role: str) -> None:
    if not role:
        with s as ss:
            ss.pr_text = ""
            ss.pr_default_text = ""
            ss.pr_status = "Keine Rolle/kein Template vorhanden (AGENT_SPECS ist in der Basis leer)."
        return
    settings = st.effective_settings()
    info = st.prompt_info(role, settings)
    with s as ss:
        ss.pr_text = info["effective_text"]
        ss.pr_default_text = info["default_text"]
        ss.pr_status = (
            f"Override AKTIV ({prompts.override_prompt_path(role, settings)})"
            if info["has_override"]
            else "Default aktiv (kein Override)."
        )


def on_prompt_selected(s, var_name=None, value=None) -> None:
    _load_prompt(s, s.pr_selected)


def on_prompt_save(s, id=None, payload=None) -> None:
    if not s.pr_selected:
        return
    settings = st.effective_settings()
    path = prompts.save_prompt_override(s.pr_selected, str(s.pr_text), settings)
    notify(s, "success", f"Prompt-Override gespeichert: {path}")
    _load_prompt(s, s.pr_selected)


def on_prompt_reset(s, id=None, payload=None) -> None:
    if not s.pr_selected:
        return
    settings = st.effective_settings()
    if prompts.reset_prompt_override(s.pr_selected, settings):
        notify(s, "info", f"Override fuer {s.pr_selected} geloescht (Default aktiv).")
    _load_prompt(s, s.pr_selected)


# -- Seite -------------------------------------------------------------------------

with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Konfiguration (effektive Werte, Overrides ab naechstem Lauf)", mode="md")
        with tgb.layout(columns="1 1 4"):
            tgb.button("Overrides speichern", on_action=on_save_all, class_name="st-accent")
            tgb.button("Alle zuruecksetzen", on_action=on_reset_all)
            tgb.text("{cfg_status}", class_name="st-muted")

    _groups: dict[str, list[str]] = {}
    for _group, _key in _KEYS:
        _groups.setdefault(_group, []).append(_key)
    for _group, _group_keys in _groups.items():
        with tgb.part(class_name="st-panel"):
            with tgb.expandable(title=_group, expanded=False):
                for _key in _group_keys:
                    with tgb.layout(columns="2 4 1", class_name="st-cfgrow"):
                        with tgb.part():
                            tgb.text(f"**{_key}**", mode="md")
                            tgb.text(_default_caption(_key), class_name="st-muted st-small")
                        tgb.input(
                            value="{" + _var(_key) + "}",
                            multiline=_is_multiline(_key),
                            lines_shown=min(
                                10,
                                max(
                                    2,
                                    st.config_value_to_text(
                                        _DEFAULTS.get(_key)
                                    ).count("\n")
                                    + 1,
                                ),
                            )
                            if _is_multiline(_key)
                            else 1,
                            class_name="fullwidth",
                        )
                        tgb.button(
                            "Reset",
                            id=f"reset::{_key}",
                            on_action=on_reset_key,
                        )

    with tgb.part(class_name="st-panel"):
        with tgb.expandable(title="Pfade (read-only, aus PROJECT_ROOT abgeleitet)", expanded=False):
            tgb.table(data="{cfg_paths_df}", page_size=20, class_name="st-table")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Prompt-Editor (Default vs. Override je Rolle)", mode="md")
        with tgb.layout(columns="2 2 1 1"):
            tgb.selector(
                value="{pr_selected}",
                lov="{pr_roles}",
                dropdown=True,
                label="Rolle / Template",
                on_change=on_prompt_selected,
                class_name="fullwidth",
            )
            tgb.text("{pr_status}", class_name="st-muted")
            tgb.button("Override speichern", on_action=on_prompt_save, class_name="st-accent")
            tgb.button("Reset auf Default", on_action=on_prompt_reset)
        tgb.input(
            value="{pr_text}",
            multiline=True,
            lines_shown=18,
            label="Effektives Template (Speichern = vollstaendiger Override)",
            class_name="fullwidth st-mono",
        )
        with tgb.expandable(title="Default-Template (read-only)", expanded=False):
            tgb.text("{pr_default_text}", mode="pre")
