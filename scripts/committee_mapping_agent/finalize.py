"""Finalize: User-Kommentare einarbeiten -> Staging-CSV -> promote (mit Backup).

Ablauf:
  * Zeile MIT User-Kommentar -> Finalize-Agent (Kommentar + Originalzeile +
    Grounding) arbeitet die Anweisung ein und liefert Ersatzzeilen (0 = loeschen,
    1 = geaendert, n = erweitert), strikt in der Allow-Liste. Re-Validierung
    (ohne Grounding-Deckel — die ausdrueckliche User-Anweisung hat Vorrang).
  * Zeile OHNE Kommentar -> deterministischer Pass-Through.
  * Ergebnis -> Staging-CSV im exakten 7-Spalten-Master-Schema (deutsche
    Konfidenz). promote() sichert den alten Master mit Timestamp und ersetzt ihn.
"""
import csv
import logging
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import config, llm_client, taxonomy, validate

logger = logging.getLogger(__name__)

_FINALIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string"},
                    "industry": {"type": "string"},
                    "rank": {"type": "integer", "minimum": 1},
                    "confidence": {"type": ["string", "number"]},
                    "ai_comment": {"type": "string"},
                },
                "required": ["sector", "industry", "rank", "confidence", "ai_comment"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rows"],
    "additionalProperties": False,
}

_FINALIZE_INSTRUCT = (
    "\nAUFGABE (Finalize): Unten steht EINE vorgeschlagene Zuordnungszeile und "
    "eine ausdrueckliche ANWEISUNG des menschlichen Reviewers. Arbeite die "
    "Anweisung ein und gib die resultierenden Zeilen zurueck:\n"
    "- leeres rows-Array = Zeile loeschen,\n"
    "- eine Zeile = geaenderte Zuordnung,\n"
    "- mehrere Zeilen = Anweisung fuegt weitere Zuordnungen hinzu.\n"
    "Bleibe strikt in der Allow-Liste. Die Konfidenz soll die Sicht des "
    "Reviewers widerspiegeln. Aendere Sektor/Industry NUR, wenn die Anweisung "
    "es verlangt."
)


def _apply_comment(
    row: Dict[str, Any],
    comment: str,
    taxonomy_data: Dict[str, Any],
    *,
    client: Any = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Finalize-Agent fuer eine kommentierte Zeile -> validierte Ersatzzeilen."""
    system = taxonomy.build_prefix_block(taxonomy_data) + _FINALIZE_INSTRUCT
    user = (
        f"Zeile:\n"
        f"  Chamber: {row['chamber']}\n  Committee: {row['committee']}\n"
        f"  Subcommittee: {row['subcommittee']}\n  Sector: {row['sector']}\n"
        f"  Industry: {row['industry']}\n  Sector_Rank: {row['rank']}\n"
        f"  Konfidenz: {row['confidence']}\n"
        f"  grounding_source: {row.get('grounding_source', '')}\n\n"
        f"ANWEISUNG des Reviewers:\n{comment}"
    )
    result = llm_client.call_json("finalize", system, user, _FINALIZE_SCHEMA,
                                  client=client)
    candidates = [
        {
            "sector": r["sector"],
            "industry": r["industry"],
            "rank": r["rank"],
            "confidence": config.normalize_confidence(r["confidence"]),
            "ai_comment": f"[User] {comment} | {r.get('ai_comment', '')}",
            "grounding_source": row.get("grounding_source", ""),
        }
        for r in result.get("rows", [])
    ]
    # User-Anweisung hat Vorrang -> Grounding-Deckel aus.
    clean, msgs = validate.validate_rows(
        candidates, taxonomy_data, "direct", enforce_grounding_cap=False,
    )
    # chamber/committee/subcommittee an die Ergebniszeilen anheften.
    for c in clean:
        c["chamber"], c["committee"], c["subcommittee"] = (
            row["chamber"], row["committee"], row["subcommittee"])
    return clean, msgs


def finalize_rows(
    reviewed_rows: List[Dict[str, Any]],
    comments: Dict[str, str],
    taxonomy_data: Dict[str, Any],
    *,
    client: Any = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Wendet Kommentare an (Pass-Through sonst). Liefert (finale Zeilen, log)."""
    final: List[Dict[str, Any]] = []
    log: List[str] = []
    for row in reviewed_rows:
        comment = comments.get(row["mapping_id"], "").strip()
        if comment:
            replacements, msgs = _apply_comment(row, comment, taxonomy_data,
                                                client=client)
            log.append(
                f"{row['mapping_id']} ({row['committee']}/{row['subcommittee']} "
                f"{row['sector']}/{row['industry']}): Kommentar angewandt -> "
                f"{len(replacements)} Zeile(n)."
            )
            log.extend(msgs)
            final.extend(replacements)
        else:
            final.append({
                "chamber": row["chamber"], "committee": row["committee"],
                "subcommittee": row["subcommittee"], "sector": row["sector"],
                "industry": row["industry"], "rank": row["rank"],
                "confidence": row["confidence"],
            })
    return final, log


def _to_csv_rows(final_rows: List[Dict[str, Any]]) -> List[List[Any]]:
    """Dedup (Chamber,Committee,Subcommittee,Sector,Industry) -> niedrigster
    Rank; deutsche Konfidenz; deterministische Sortierung."""
    best: Dict[Tuple, Dict[str, Any]] = {}
    for r in final_rows:
        key = (r["chamber"], r["committee"], r["subcommittee"],
               r["sector"], r["industry"])
        if key not in best or r["rank"] < best[key]["rank"]:
            best[key] = r
    ordered = sorted(
        best.values(),
        key=lambda r: (r["chamber"], r["committee"], r["subcommittee"],
                       r["rank"], r["sector"], r["industry"]),
    )
    out = []
    for r in ordered:
        konfidenz = config.CONFIDENCE_TO_CSV.get(r["confidence"], "Niedrig")
        out.append([r["chamber"], r["committee"], r["subcommittee"],
                    r["sector"], r["industry"], r["rank"], konfidenz])
    return out


def write_staging(final_rows: List[Dict[str, Any]],
                  path: Optional[str] = None) -> str:
    """Schreibt die Staging-CSV im 7-Spalten-Master-Schema."""
    path = path or config.STAGING_CSV
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(config.MASTER_COLUMNS)
        writer.writerows(_to_csv_rows(final_rows))
    return path


def promote(staging: Optional[str] = None, master: Optional[str] = None) -> str:
    """Ersetzt den Master durch die Staging-CSV; sichert den alten mit Timestamp.

    Returns:
        Pfad der erstellten Backup-Datei ('' wenn kein Master existierte).
    """
    staging = staging or config.STAGING_CSV
    master = master or config.MASTER_CSV
    if not os.path.exists(staging):
        raise FileNotFoundError(f"Staging-CSV fehlt: {staging}")

    backup = ""
    if os.path.exists(master):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{master}.bak.{ts}"
        shutil.copy2(master, backup)
        logger.info("Master gesichert -> %s", backup)
    shutil.copy2(staging, master)
    logger.info("Master ersetzt durch Staging -> %s", master)
    return backup
