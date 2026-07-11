"""Control-UI-Einstieg: ``python -m suspicious_trades.app`` (§5a).

Startet die Taipy-App mit den sieben Seiten aus ``ui/pages`` auf dem
paketgebundenen Port ``UI_PORTS[__package__]`` — Klone laufen dadurch parallel
ohne Code-Edit. Eine zusaetzliche Flask-Route ``/st-doc`` liefert die ueber
``reporting/renderer.py`` gestylten Artefakt-Ansichten (md/JSON/CSV) fuer die
iframe-Einbettung; sie serviert ausschliesslich Dateien unterhalb der
package-eigenen DATA_DIR.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, abort, request
import taipy.gui.builder as tgb
from taipy.gui import Gui

from . import config
from .reporting import renderer
from .ui import runctl
from .ui.pages import berichte, events, hinweise, konfiguration, kosten, lauf, trades

logger = logging.getLogger(__name__)

_PACKAGE = __package__

# MUI-Farbwelt des Dashboards (dunkles Blau/Violett mit Cyan-Akzenten).
_DARK_THEME = {
    "palette": {
        "background": {"default": "#070b18", "paper": "#0c1329"},
        "primary": {"main": "#4cc9f0"},
        "secondary": {"main": "#8f7bff"},
        "error": {"main": "#ff5470"},
        "warning": {"main": "#ffb454"},
        "success": {"main": "#2ee6a8"},
    },
    "typography": {"fontFamily": "'Segoe UI', 'Inter', Arial, sans-serif"},
}

# Seiten-Module in Navigations-Reihenfolge (§5a: 7 Seiten buendeln 10 Bloecke).
_PAGE_MODULES = {
    "lauf": lauf,
    "berichte": berichte,
    "events": events,
    "trades": trades,
    "hinweise": hinweise,
    "konfiguration": konfiguration,
    "kosten": kosten,
}

with tgb.Page() as root_page:
    with tgb.part(class_name="st-header"):
        with tgb.layout(columns="2 5"):
            tgb.text(f"### Suspicious Trades · Control ({_PACKAGE})", mode="md")
            tgb.navbar()
    tgb.content()

pages = {"/": root_page, **{name: module.page for name, module in _PAGE_MODULES.items()}}


def _refresh_page(state, name: str) -> None:
    module = _PAGE_MODULES.get(name)
    if module is None:
        return
    try:
        module.refresh(state)
    except Exception:  # UI darf durch defekte Daten nie crashen.
        logger.exception("Refresh der Seite %s fehlgeschlagen.", name)


def on_init(state) -> None:
    """Initialer Zustand je Client inkl. Reconnect-Pruefung (Seite lauf)."""

    for name in _PAGE_MODULES:
        _refresh_page(state, name)


def on_navigate(state, page_name: str, params: dict | None = None) -> str:
    """Frische Datei-Ableitungen bei jedem Seitenwechsel."""

    _refresh_page(state, page_name)
    return page_name


def _create_flask() -> Flask:
    """Flask-App mit der Renderer-Route fuer gestylte Artefakt-Ansichten."""

    flask_app = Flask(_PACKAGE)

    @flask_app.route("/st-doc")
    def _serve_doc():  # pragma: no cover - ueber HTTP-Tests abgedeckt
        rel = request.args.get("path", "")
        settings = config.effective(reload=True)
        data_dir = Path(settings["DATA_DIR"]).resolve()
        target = (data_dir / rel).resolve()
        if not target.is_relative_to(data_dir) or not target.is_file():
            abort(404)
        return renderer.render_artifact(target)

    return flask_app


def build_gui() -> Gui:
    """Konstruiert die Gui (auch headless testbar, ohne ``run``)."""

    gui = Gui(pages=pages, flask=_create_flask(), css_file="/ui/theme.css")
    runctl.set_gui(gui)
    return gui


def main() -> int:
    settings = config.effective()
    port = int(settings.get("UI_PORTS", {}).get(_PACKAGE, 5010))
    gui = build_gui()
    gui.run(
        host=str(settings.get("UI_HOST", "127.0.0.1")),
        port=port,
        title=f"{_PACKAGE} Control",
        dark_mode=bool(settings.get("UI_DARK_MODE", True)),
        debug=False,
        use_reloader=False,
        run_browser=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
