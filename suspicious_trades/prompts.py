"""Prompt-Templates und der Override-Layer des Control-UI (§5d).

Defaults liegen als md-Dateien im package-eigenen ``agents/prompts/``-Ordner
(Code-seitig einfach anpassbar). UI-Aenderungen landen als VOLLSTAENDIGE Datei
unter ``data/<package>/prompt_overrides/<rolle>.md`` (kein Diff); Reset =
Override-Datei loeschen. ``load_prompt(role)`` ist der einzige Lade-Einstieg
fuer Pipeline-Code — Overrides gewinnen immer gegen das Default-Template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import config


def templates_dir(settings: Mapping[str, Any] | None = None) -> Path:
    """Package-relativer Ordner der versionierten Default-Templates."""

    settings = settings or config.effective()
    return config.PACKAGE_ROOT / str(settings.get("PROMPT_DIR", "agents/prompts"))


def overrides_dir(settings: Mapping[str, Any] | None = None) -> Path:
    """DATA_DIR-Unterordner der vollstaendigen Override-Dateien."""

    settings = settings or config.effective()
    return Path(settings["DATA_DIR"]) / str(
        settings.get("PROMPT_OVERRIDES_DIRNAME", "prompt_overrides")
    )


def default_prompt_path(role: str, settings: Mapping[str, Any] | None = None) -> Path:
    """Pfad des Default-Templates einer Rolle.

    Rollen mit ``AGENT_SPECS[role]["prompt_template"]`` nutzen diesen (package-
    relativen) Pfad; sonst gilt die Konvention ``agents/prompts/<rolle>.md``.
    """

    settings = settings or config.effective()
    spec = settings.get("AGENT_SPECS", {}).get(role)
    template = spec.get("prompt_template") if isinstance(spec, Mapping) else None
    if isinstance(template, str) and template.strip():
        return config.PACKAGE_ROOT / template
    return templates_dir(settings) / f"{role}.md"


def override_prompt_path(role: str, settings: Mapping[str, Any] | None = None) -> Path:
    """Pfad der (vollstaendigen) Override-Datei einer Rolle."""

    return overrides_dir(settings) / f"{role}.md"


def list_prompt_roles(settings: Mapping[str, Any] | None = None) -> list[str]:
    """Alle editierbaren Rollen: AGENT_SPECS-Rollen plus Template-Dateien.

    In der neutralen Basis ist ``AGENT_SPECS`` leer; dann zaehlen die im
    Template-Ordner liegenden md-Dateien (Dateistamm = Rollenname).
    """

    settings = settings or config.effective()
    roles = {
        role
        for role, spec in settings.get("AGENT_SPECS", {}).items()
        if isinstance(spec, Mapping)
    }
    directory = templates_dir(settings)
    if directory.is_dir():
        roles.update(path.stem for path in directory.glob("*.md"))
    return sorted(roles)


def has_override(role: str, settings: Mapping[str, Any] | None = None) -> bool:
    """True, sobald fuer die Rolle eine Override-Datei existiert."""

    return override_prompt_path(role, settings).is_file()


def load_prompt(role: str, settings: Mapping[str, Any] | None = None) -> str:
    """Effektives Template einer Rolle (Override vor Default, §5d)."""

    settings = settings or config.effective()
    override = override_prompt_path(role, settings)
    if override.is_file():
        return override.read_text(encoding="utf-8")
    default = default_prompt_path(role, settings)
    if default.is_file():
        return default.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"Kein Prompt-Template fuer Rolle '{role}' gefunden "
        f"(gesucht: {override} und {default})."
    )


def load_default_prompt(role: str, settings: Mapping[str, Any] | None = None) -> str:
    """Default-Template einer Rolle (ignoriert einen etwaigen Override)."""

    default = default_prompt_path(role, settings)
    if default.is_file():
        return default.read_text(encoding="utf-8")
    return ""


def save_prompt_override(
    role: str, text: str, settings: Mapping[str, Any] | None = None
) -> Path:
    """Schreibt den Override als vollstaendige Datei atomar (tmp + rename)."""

    path = override_prompt_path(role, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def reset_prompt_override(role: str, settings: Mapping[str, Any] | None = None) -> bool:
    """Loescht die Override-Datei (Reset auf Default); True bei Erfolg."""

    path = override_prompt_path(role, settings)
    if path.is_file():
        path.unlink()
        return True
    return False
