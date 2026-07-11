"""UI-Smoke: Seiten importierbar, Gui headless konstruierbar, Helfer-Logik (B4)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from suspicious_trades import config, prompts
from suspicious_trades.ui import state as ui_state

PAGE_MODULES = [
    "suspicious_trades.ui.pages.lauf",
    "suspicious_trades.ui.pages.berichte",
    "suspicious_trades.ui.pages.events",
    "suspicious_trades.ui.pages.trades",
    "suspicious_trades.ui.pages.hinweise",
    "suspicious_trades.ui.pages.konfiguration",
    "suspicious_trades.ui.pages.kosten",
]


@pytest.mark.parametrize("module_name", PAGE_MODULES)
def test_page_module_importable_with_page_and_refresh(module_name):
    module = importlib.import_module(module_name)
    assert hasattr(module, "page")
    assert callable(getattr(module, "refresh"))


def test_gui_constructs_headless():
    from suspicious_trades import app

    gui = app.build_gui()
    assert gui is not None
    assert set(app.pages) == {
        "/",
        "lauf",
        "berichte",
        "events",
        "trades",
        "hinweise",
        "konfiguration",
        "kosten",
    }


def test_board_svg_states_and_data_url():
    status = {
        "steps": [
            {"step": "ingest", "state": "done", "started_at": "2026-07-01T10:00:00",
             "finished_at": "2026-07-01T10:00:05", "cost_usd": 0.5},
            {"step": "research", "state": "running", "started_at": "2026-07-01T10:00:05"},
            {"step": "dossier", "state": "pending"},
            {"step": "outputs", "state": "failed", "started_at": "2026-07-01T10:00:06"},
            {"step": "recommend", "state": "aborted"},
        ]
    }
    svg = ui_state.build_board_svg(status)
    assert svg.startswith("<svg")
    for name in ("ingest", "research", "dossier", "outputs", "recommend"):
        assert name in svg
    # Zustandsfarben: running cyan, done gruen, failed rot, aborted orange.
    assert "#4cc9f0" in svg and "#2ee6a8" in svg
    assert "#ff5470" in svg and "#ffb454" in svg
    assert 'class="pulse"' in svg  # running pulsiert

    url = ui_state.board_data_url(status)
    assert url.startswith("data:image/svg+xml;base64,")

    empty = ui_state.build_board_svg(None)
    assert "Kein Lauf" in empty


@pytest.fixture
def patched_data_dir(tmp_path, monkeypatch):
    """Leitet config.DATA_DIR auf tmp um und stellt das Memo danach wieder her."""

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    config.effective(reload=True)
    yield tmp_path
    monkeypatch.undo()
    config.effective(reload=True)


def test_config_override_write_only_diffs(patched_data_dir):
    ui_state.set_override("TRADE_WINDOW_DAYS", 30)
    payload = json.loads(config.overrides_path().read_text(encoding="utf-8"))
    assert payload == {"TRADE_WINDOW_DAYS": 30}
    assert config.effective(reload=True)["TRADE_WINDOW_DAYS"] == 30

    # Default-Wert setzen entfernt den Schluessel wieder (nur Abweichungen).
    ui_state.set_override("TRADE_WINDOW_DAYS", 14)
    assert json.loads(config.overrides_path().read_text(encoding="utf-8")) == {}

    ui_state.set_override("TRADE_WINDOW_DAYS", 30)
    ui_state.reset_override("TRADE_WINDOW_DAYS")
    assert json.loads(config.overrides_path().read_text(encoding="utf-8")) == {}
    assert config.effective(reload=True)["TRADE_WINDOW_DAYS"] == 14


def test_source_toggles_deep_merge(patched_data_dir):
    ui_state.set_source_enabled("sam_gov", False)
    payload = json.loads(config.overrides_path().read_text(encoding="utf-8"))
    assert payload == {"INGEST_SOURCES_ENABLED": {"sam_gov": False}}
    merged = config.effective(reload=True)["INGEST_SOURCES_ENABLED"]
    assert merged["sam_gov"] is False
    assert merged["congress_gov"] is True

    ui_state.disable_all_sources()
    merged = config.effective(reload=True)["INGEST_SOURCES_ENABLED"]
    assert all(value is False for value in merged.values())

    ui_state.set_source_enabled("sam_gov", True)
    merged = config.effective(reload=True)["INGEST_SOURCES_ENABLED"]
    assert merged["sam_gov"] is True


def test_parse_config_text_types():
    assert ui_state.parse_config_text("TRADE_WINDOW_DAYS", "21") == 21
    assert ui_state.parse_config_text("TRADE_SOURCE", "window") == "window"
    assert ui_state.parse_config_text("FEDREG_DOC_TYPES", '["RULE"]') == ["RULE"]
    with pytest.raises(ValueError):
        ui_state.parse_config_text("TRADE_WINDOW_DAYS", "nicht-json")
    with pytest.raises(ValueError):
        ui_state.parse_config_text("TRADE_WINDOW_DAYS", '"text"')


def test_editable_keys_exclude_paths():
    keys = {key for _group, key in ui_state.editable_config_keys()}
    assert "TRADE_WINDOW_DAYS" in keys
    assert "AGENT_SPECS" in keys
    assert "DATA_DIR" not in keys
    assert "TRADES_MASTER_PATH" not in keys


def test_prompt_override_roundtrip(tmp_path):
    settings = dict(config.effective())
    settings["DATA_DIR"] = tmp_path

    roles = prompts.list_prompt_roles(settings)
    assert "_conventions" in roles

    default_text = prompts.load_prompt("_conventions", settings)
    assert "Template-Konventionen" in default_text
    assert prompts.has_override("_conventions", settings) is False

    path = prompts.save_prompt_override("_conventions", "MEIN OVERRIDE", settings)
    assert path.is_file()
    assert prompts.has_override("_conventions", settings) is True
    assert prompts.load_prompt("_conventions", settings) == "MEIN OVERRIDE"
    assert "Template-Konventionen" in prompts.load_default_prompt("_conventions", settings)

    assert prompts.reset_prompt_override("_conventions", settings) is True
    assert prompts.load_prompt("_conventions", settings) == default_text
    assert prompts.reset_prompt_override("_conventions", settings) is False


def test_adopt_staging_file_copies_into_staged(tmp_path):
    staging = tmp_path / "agent_staging"
    staging.mkdir()
    (staging / "trade_selection_x.csv").write_text("trade_id\nt1\n", encoding="utf-8")
    settings = {
        "DATA_DIR": tmp_path / "data_dir",
        "STAGING_DIR": staging,
        "STAGED_TRADES_DIRNAME": "staged_trades",
        "UI_PREVIEW_ROWS": 10,
    }
    target = ui_state.adopt_staging_file(settings, "trade_selection_x.csv")
    assert target.is_file()
    assert target.parent.name == "staged_trades"
    assert ui_state.staged_files_df(settings)["Datei"].tolist() == [
        "trade_selection_x.csv"
    ]
    assert ui_state.remove_staged_file(settings, "trade_selection_x.csv") is True
    assert ui_state.staged_files_df(settings).empty

    with pytest.raises(FileNotFoundError):
        ui_state.adopt_staging_file(settings, "fehlt.csv")


def test_dummy_run_command_offline(tmp_path, monkeypatch):
    from suspicious_trades import run as run_module

    settings = dict(config.effective())
    settings["DATA_DIR"] = tmp_path
    settings["DUMMY_RUN_STEPS"] = ["ingest", "outputs"]
    settings["DUMMY_RUN_SECONDS_PER_STEP"] = 0.0
    monkeypatch.setattr(run_module.config, "effective", lambda **_kw: settings)

    import argparse

    exit_code = run_module.dummy_run_command(
        argparse.Namespace(run_id="dummy-test", seconds_per_step=0.0)
    )
    assert exit_code == 0
    status = json.loads(
        (tmp_path / "runs" / "dummy-test.status.json").read_text(encoding="utf-8")
    )
    assert status["state"] == "done"
    assert [step["state"] for step in status["steps"]] == ["done", "done"]
    assert (tmp_path / "outputs" / "dummy-test" / "empfehlung.md").is_file()

    # Stop-Datei VOR dem Lauf -> sauberer Abbruch als aborted.
    stop = tmp_path / "runs" / "dummy-stop.stop"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("stop", encoding="utf-8")
    exit_code = run_module.dummy_run_command(
        argparse.Namespace(run_id="dummy-stop", seconds_per_step=0.0)
    )
    assert exit_code == 1
    status = json.loads(
        (tmp_path / "runs" / "dummy-stop.status.json").read_text(encoding="utf-8")
    )
    assert status["state"] == "aborted"
