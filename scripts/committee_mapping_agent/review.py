"""Review-Agent: kontrolliert die gemappten Zeilen eines Committees.

Pruefkriterien (User-Vorgabe): Rechtfertigt das Zitat den Sektor? Fehlt ein
naheliegender Sektor/Industry? Ist die Konfidenz ehrlich? Der Reviewer aendert
die Allow-Liste NICHT — er flaggt nur (review_flag + Begruendung) und schlaegt
fehlende Zuordnungen vor (advisory, landet im xlsx fuer den menschlichen Review).

Output -> cache/review/{chamber}_{committee_slug}.json
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, llm_client, taxonomy
from .committees import slugify
from .rowid import mapping_id, resolve_subcommittee_label

logger = logging.getLogger(__name__)

REVIEW_FLAGS = ("ok", "confidence_too_high", "confidence_too_low",
                "weak_grounding", "off_topic")

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "row_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "review_flag": {"type": "string", "enum": list(REVIEW_FLAGS)},
                    "reason": {"type": "string"},
                },
                "required": ["index", "review_flag", "reason"],
                "additionalProperties": False,
            },
        },
        "missing": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit": {"type": "string"},
                    "sector": {"type": "string"},
                    "industry": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["unit", "sector", "industry", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["row_reviews", "missing"],
    "additionalProperties": False,
}

_REVIEW_INSTRUCT = (
    "\nAUFGABE (Review): Unten stehen die fuer ein Committee vorgeschlagenen "
    "Sektor/Industry-Zuordnungen samt Jurisdiktions-Zitaten. Pruefe jede "
    "nummerierte Zeile:\n"
    "- Rechtfertigt das Zitat/die Jurisdiktion diesen Sektor & diese Industry?\n"
    "- Ist die Konfidenz ehrlich (nicht zu hoch trotz schwacher Groundung, "
    "nicht zu niedrig trotz klarem Bezug)?\n"
    "Setze pro Zeile genau einen review_flag aus: ok, confidence_too_high, "
    "confidence_too_low, weak_grounding, off_topic — mit knapper Begruendung.\n"
    "Nenne ausserdem in 'missing' naheliegende Sektor/Industry-Paare aus der "
    "Allow-Liste, die fehlen (mit Einheit + Begruendung). Erfinde nichts "
    "ausserhalb der Allow-Liste."
)


def _flatten(committee: str, jurisdiction: Dict[str, Any],
             mapping_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Erzeugt die flache, indizierte Zeilenliste (ueber alle Einheiten)."""
    flat: List[Dict[str, Any]] = []
    for mres in mapping_results:
        unit_key = mres["unit_key"]
        entry = jurisdiction.get(unit_key, {})
        for row in mres["rows"]:
            flat.append({
                "unit_key": unit_key,
                "subcommittee": resolve_subcommittee_label(committee, unit_key),
                "sector": row["sector"],
                "industry": row["industry"],
                "rank": row["rank"],
                "confidence": row["confidence"],
                "ai_comment": row.get("ai_comment", ""),
                "grounding_source": row.get("grounding_source", ""),
                "grounding_quality": entry.get("grounding_quality", "none"),
                "quote": entry.get("quote", ""),
            })
    return flat


def _render_rows(flat: List[Dict[str, Any]]) -> str:
    lines = []
    for i, r in enumerate(flat):
        lines.append(
            f"[{i}] Einheit='{r['unit_key']}' Sektor='{r['sector']}' "
            f"Industry='{r['industry']}' Rank={r['rank']} "
            f"Konfidenz={r['confidence']} grounding_quality={r['grounding_quality']}\n"
            f"     Zitat: {r['quote'][:280] or '(keines)'}"
        )
    return "\n".join(lines) if lines else "(keine Zeilen)"


def cache_path(chamber: str, committee: str) -> str:
    return os.path.join(config.CACHE_REVIEW, f"{chamber}_{slugify(committee)}.json")


def review_committee(
    chamber: str,
    committee: str,
    jurisdiction: Dict[str, Any],
    mapping_results: List[Dict[str, Any]],
    taxonomy_data: Dict[str, Any],
    *,
    client: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Reviewt alle gemappten Zeilen eines Committees; haengt Flags an."""
    path = cache_path(chamber, committee)
    if os.path.exists(path) and not force:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    flat = _flatten(committee, jurisdiction, mapping_results)

    if flat:
        system = taxonomy.build_prefix_block(taxonomy_data) + _REVIEW_INSTRUCT
        user = f"Committee: {committee} ({chamber})\n\nZeilen:\n" + _render_rows(flat)
        review = llm_client.call_json("review", system, user, _REVIEW_SCHEMA,
                                      client=client)
        by_index = {rv["index"]: rv for rv in review.get("row_reviews", [])}
        missing = review.get("missing", [])
    else:
        by_index, missing = {}, []

    reviewed_rows: List[Dict[str, Any]] = []
    for i, r in enumerate(flat):
        rv = by_index.get(i, {})
        reviewed_rows.append({
            "mapping_id": mapping_id(chamber, committee, r["subcommittee"],
                                     r["sector"], r["industry"]),
            "chamber": chamber,
            "committee": committee,
            "subcommittee": r["subcommittee"],
            "sector": r["sector"],
            "industry": r["industry"],
            "rank": r["rank"],
            "confidence": r["confidence"],
            "ai_comment": r["ai_comment"],
            "grounding_source": r["grounding_source"],
            "review_flag": rv.get("review_flag", "ok"),
            "review_reason": rv.get("reason", ""),
        })

    result = {
        "chamber": chamber,
        "committee": committee,
        "reviewed_rows": reviewed_rows,
        "missing": missing,
    }
    os.makedirs(config.CACHE_REVIEW, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return result
