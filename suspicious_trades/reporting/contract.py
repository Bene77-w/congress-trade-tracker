"""Output-Kontrakt (§5c): Pfade, Schema und Budget-Check der Lauf-Dokumente.

Jeder Lauf endet mit genau zwei Markdown-Dateien in ``outputs/<run_id>/``:
``empfehlung.md`` (kompaktes Ranking, Laengenbudget ``SUMMARY_MAX_TOKENS``) und
``bericht.md`` (ausfuehrliche Kausalitaetsketten). Diese Datei definiert nur den
Kontrakt (Persistenz + Budgetpruefung); die gestylte Lese-Ansicht (``renderer.py``)
gehoert zu B4 und wird hier bewusst nicht gebaut.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BudgetCheck:
    """Ergebnis der Laengenpruefung eines Dokuments gegen sein Token-Budget."""

    tokens: int
    limit: int
    within_budget: bool


@dataclass(frozen=True)
class OutputContract:
    """Pfade und Budget-Status der beiden persistierten Lauf-Dokumente."""

    run_id: str
    recommendation_path: Path
    report_path: Path
    recommendation_budget: BudgetCheck


def outputs_dir(settings: Mapping[str, Any], run_id: str) -> Path:
    """DATA_DIR-Unterordner der Enddokumente eines Laufs."""

    return (
        Path(settings["DATA_DIR"])
        / settings.get("OUTPUTS_DIRNAME", "outputs")
        / run_id
    )


def recommendation_doc_path(settings: Mapping[str, Any], run_id: str) -> Path:
    """Pfad des kompakten Empfehlungsdokuments ``empfehlung.md``."""

    return outputs_dir(settings, run_id) / settings.get(
        "RECOMMENDATION_DOC_FILENAME", "empfehlung.md"
    )


def report_doc_path(settings: Mapping[str, Any], run_id: str) -> Path:
    """Pfad des ausfuehrlichen Berichts ``bericht.md``."""

    return outputs_dir(settings, run_id) / settings.get(
        "REPORT_DOC_FILENAME", "bericht.md"
    )


def count_tokens(text: str, *, model: str | None = None) -> int:
    """Schaetzt die Tokenzahl (LiteLLM falls Modell bekannt, sonst Heuristik).

    Ohne Netz und Modell wird konservativ das Maximum aus Wortanzahl und
    ``len/4`` verwendet – gut genug fuer die Budget-Ampel des Kontrakts.
    """

    if not text:
        return 0
    if model:
        try:
            import litellm

            return int(
                litellm.token_counter(
                    model=model, messages=[{"role": "user", "content": text}]
                )
            )
        except Exception as exc:  # pragma: no cover - depends on LiteLLM map
            logger.warning("token_counter fuer %s fehlgeschlagen: %s", model, exc)
    words = len(re.findall(r"\S+", text))
    return max(words, (len(text) + 3) // 4)


def check_summary_budget(
    text: str,
    settings: Mapping[str, Any],
    *,
    model: str | None = None,
) -> BudgetCheck:
    """Prueft ``empfehlung.md`` gegen ``SUMMARY_MAX_TOKENS`` (WARN bei Ueberschreitung)."""

    limit = int(settings.get("SUMMARY_MAX_TOKENS", 1500))
    tokens = count_tokens(text, model=model)
    within = tokens <= limit
    if not within:
        logger.warning(
            "empfehlung.md ueberschreitet SUMMARY_MAX_TOKENS: %d > %d.",
            tokens,
            limit,
        )
    return BudgetCheck(tokens=tokens, limit=limit, within_budget=within)


def persist_outputs(
    settings: Mapping[str, Any],
    run_id: str,
    *,
    recommendation_md: str,
    report_md: str,
    model: str | None = None,
) -> OutputContract:
    """Schreibt beide Dokumente atomar und prueft das Empfehlungs-Budget.

    Rueckgabe = Kontrakt mit den (relativ zur DATA_DIR nutzbaren) Pfaden und dem
    Budget-Status. Der variantenspezifische Formatter erzeugt den Markdown-Text;
    hier wird er nur persistiert und geprueft.
    """

    directory = outputs_dir(settings, run_id)
    directory.mkdir(parents=True, exist_ok=True)

    budget = check_summary_budget(recommendation_md, settings, model=model)

    rec_path = recommendation_doc_path(settings, run_id)
    report_path = report_doc_path(settings, run_id)
    _atomic_write(rec_path, recommendation_md)
    _atomic_write(report_path, report_md)

    return OutputContract(
        run_id=run_id,
        recommendation_path=rec_path,
        report_path=report_path,
        recommendation_budget=budget,
    )


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
