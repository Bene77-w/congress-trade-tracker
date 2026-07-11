"""User-Hinweis-Speicher ``user_hints/hints.jsonl`` (§5e Punkt 2).

Der Speicher ist append-only (eine JSONL-Zeile je Hinweis). Konsum- und
Archiv-Markierungen schreiben die Datei komplett neu, damit sie klein bleibt.
Fuer die Injektion in Agent-Prompts zaehlen ausschliesslich Hinweise mit
``consumed_by_run is None`` und ``archived == False``. Der Speicher liegt bewusst
je Variante getrennt unter der package-eigenen ``DATA_DIR``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hints_path(settings: Mapping[str, Any]) -> Path:
    """Pfad des variantenspezifischen Hinweis-Speichers unter DATA_DIR."""

    return (
        Path(settings["DATA_DIR"])
        / settings.get("USER_HINTS_DIRNAME", "user_hints")
        / settings.get("HINTS_FILENAME", "hints.jsonl")
    )


def load_hints(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Liest alle Hinweise (fehlertolerant; kaputte Zeilen werden uebersprungen)."""

    path = hints_path(settings)
    if not path.is_file():
        return []
    hints: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Ungueltige hints.jsonl-Zeile %d wird ignoriert.", line_number)
            continue
        if isinstance(payload, dict):
            hints.append(payload)
    return hints


def _rewrite(settings: Mapping[str, Any], hints: Iterable[Mapping[str, Any]]) -> Path:
    path = hints_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for hint in hints:
            handle.write(json.dumps(hint, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def append_hint(
    settings: Mapping[str, Any],
    text: str,
    *,
    ticker: str | None = None,
    topic: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Haengt einen neuen, unkonsumierten Hinweis an den Speicher an."""

    path = hints_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = created_at or _now()
    existing_ids = {str(h.get("hint_id")) for h in load_hints(settings)}
    base = "h-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    hint_id = base
    suffix = 1
    while hint_id in existing_ids:
        hint_id = f"{base}-{suffix}"
        suffix += 1

    hint = {
        "hint_id": hint_id,
        "created_at": timestamp,
        "text": text,
        "ticker": ticker,
        "topic": topic,
        "consumed_by_run": None,
        "consumed_at": None,
        "archived": False,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(hint, ensure_ascii=False) + "\n")
    return hint


def open_hints(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Hinweise fuer die Injektion: ``consumed_by_run is None`` und nicht archiviert."""

    return [
        hint
        for hint in load_hints(settings)
        if hint.get("consumed_by_run") in (None, "") and not hint.get("archived")
    ]


# Alias fuer die Injektions-Selektion (identische Semantik wie open_hints).
select_for_injection = open_hints


def consume_hints(
    settings: Mapping[str, Any],
    run_id: str,
    *,
    hint_ids: Iterable[str] | None = None,
    consumed_at: str | None = None,
) -> int:
    """Markiert offene Hinweise als von ``run_id`` konsumiert (Datei neu schreiben).

    Ohne ``hint_ids`` werden alle aktuell offenen Hinweise konsumiert. Rueckgabe =
    Anzahl der markierten Hinweise. Bereits konsumierte/archivierte bleiben unberuehrt.
    """

    hints = load_hints(settings)
    target_ids = set(hint_ids) if hint_ids is not None else None
    stamp = consumed_at or _now()
    marked = 0
    for hint in hints:
        if hint.get("consumed_by_run") not in (None, "") or hint.get("archived"):
            continue
        if target_ids is not None and str(hint.get("hint_id")) not in target_ids:
            continue
        hint["consumed_by_run"] = run_id
        hint["consumed_at"] = stamp
        marked += 1
    if marked:
        _rewrite(settings, hints)
    return marked


def archive_hint(settings: Mapping[str, Any], hint_id: str) -> bool:
    """Setzt ``archived=True`` fuer einen Hinweis (Datei neu schreiben)."""

    hints = load_hints(settings)
    changed = False
    for hint in hints:
        if str(hint.get("hint_id")) == hint_id and not hint.get("archived"):
            hint["archived"] = True
            changed = True
    if changed:
        _rewrite(settings, hints)
    return changed
