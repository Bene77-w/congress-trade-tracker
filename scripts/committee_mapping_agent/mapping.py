"""Mapping-Agent: ordnet einer Einheit (Subcommittee ODER Committee-Ebene)
hierarchisch Sektoren -> Industries zu.

Zwei Stufen (User-Entscheidung):
  Stufe 1  — aus der vollstaendigen Sektor-Allow-Liste die relevanten Sektoren
             waehlen + ranken (Sector_Rank, 1 = staerkster Bezug).
  Stufe 2  — pro gewaehltem Sektor NUR dessen Industries im Kontext -> relevante
             Industries waehlen.

Der Taxonomie-Block steht als byte-stabiler Prefix am Anfang der System-Message
(Stufe 1: volle Taxonomie, Stufe 2: Sektor-Block) -> DeepSeek-Auto-Caching.
Jede Zeile traegt grounding_source (URL + Zitat aus der Research-Einheit); die
deterministische validate-Schicht deckelt die Konfidenz gemaess grounding.
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from . import config, llm_client, taxonomy, validate
from .committees import slugify

logger = logging.getLogger(__name__)

# Bewusst PERMISSIV: DeepSeek variiert Schluesselnamen (sector/name,
# industry/name) und laesst Felder weg. Die Korrektheit erzwingen ohnehin die
# Allow-Liste (taxonomy.is_valid_*) + validate.validate_rows; das Schema dient
# nur dem Parsen. Defensive Extraktion in _extract_* unten.
_STAGE1_SCHEMA = {
    "type": "object",
    "properties": {"sectors": {"type": "array", "items": {"type": "object"}}},
    "required": ["sectors"],
    "additionalProperties": True,
}

_STAGE2_SCHEMA = {
    "type": "object",
    "properties": {"industries": {"type": "array", "items": {"type": "object"}}},
    "required": ["industries"],
    "additionalProperties": True,
}


def _extract_sector(item: Dict[str, Any]) -> tuple:
    """(name, rank, confidence, rationale) aus einem lockeren Stufe-1-Item.

    Stufe 1 liefert (anders als Stufe 2) meist differenzierte Konfidenz je
    Sektor — diese dient als Fallback-Konfidenz fuer die Zeilen, falls Stufe 2
    keine pro-Industry-Konfidenz mitgibt. Fehlt auch hier -> 'medium'.
    """
    name = (item.get("sector") or item.get("name")
            or item.get("Sector") or item.get("title") or "")
    try:
        rank = int(item.get("rank", item.get("Sector_Rank", 99)))
    except (TypeError, ValueError):
        rank = 99
    confidence = config.normalize_confidence(item.get("confidence", "medium"))
    rationale = item.get("rationale") or item.get("reason") or ""
    return str(name), rank, confidence, str(rationale)


def _extract_industry(item: Dict[str, Any]) -> tuple:
    """(name, confidence|None, rationale) aus einem lockeren Stufe-2-Item.

    confidence ist None, wenn das Modell keine mitgab -> Aufrufer nutzt dann die
    Sektor-Konfidenz aus Stufe 1.
    """
    name = (item.get("industry") or item.get("name")
            or item.get("Industry") or item.get("title") or "")
    raw_conf = item.get("confidence", None)
    confidence = config.normalize_confidence(raw_conf) if raw_conf is not None else None
    rationale = item.get("rationale") or item.get("reason") or ""
    return str(name), confidence, str(rationale)

_STAGE1_INSTRUCT = (
    "\nAUFGABE (Stufe 1 — Sektoren): Du bekommst den Jurisdiktionstext einer "
    "Kongress-Ausschuss-Einheit. Waehle aus der obigen Allow-Liste NUR die "
    "Sektoren, zu denen die Jurisdiktion einen echten thematischen Bezug hat. "
    "Sei konservativ — kein Sektor ohne klaren Bezug. Ranke sie nach Staerke "
    "des Bezugs (rank=1 staerkster). Begruende jeden Sektor knapp mit Bezug "
    "zum Text. Nutze exakt die Sektor-Namen der Allow-Liste. Gib je Sektor "
    "eine confidence an." + config.CONFIDENCE_RUBRIC
)

_STAGE2_INSTRUCT = (
    "\nAUFGABE (Stufe 2 — Industries): Fuer den genannten Sektor: waehle aus "
    "den obigen Industries NUR die, die die Jurisdiktion konkret beruehrt. "
    "Konservativ bleiben. Nutze exakt die Industry-Namen der Liste. Gib je "
    "Industry eine EIGENE, einzeln abgewogene confidence an (nicht die "
    "Sektor-Konfidenz kopieren) plus eine knappe Begruendung (rationale)."
    + config.CONFIDENCE_RUBRIC
)


def _grounding_source(entry: Dict[str, Any]) -> str:
    url = str(entry.get("source_url", "")).strip()
    quote = str(entry.get("quote", "")).strip()
    if url and quote:
        return f'{url} — "{quote}"'
    return url or quote


def _unit_context(committee: str, unit_label: str, entry: Dict[str, Any]) -> str:
    text = str(entry.get("text", "")).strip()
    quote = str(entry.get("quote", "")).strip()
    return (
        f"Committee: {committee}\nEinheit: {unit_label}\n\n"
        f"Jurisdiktion (Zusammenfassung):\n{text or '(keine)'}\n\n"
        f"Woertliches Zitat aus der Quelle:\n{quote or '(keines)'}"
    )


def cache_path(chamber: str, committee: str, unit_key: str) -> str:
    return os.path.join(
        config.CACHE_MAPPING,
        f"{chamber}_{slugify(committee)}_{slugify(unit_key)}.json",
    )


def map_unit(
    chamber: str,
    committee: str,
    unit_key: str,
    entry: Dict[str, Any],
    taxonomy_data: Dict[str, Any],
    *,
    client: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Mappt eine Einheit hierarchisch und validiert die Zeilen.

    ``unit_key`` ist der Subcommittee-Name oder ``__committee__`` (Committee-
    Ebene). ``entry`` ist die Research-Jurisdiktions-Einheit
    {text, source_url, quote, grounding_quality}.
    """
    path = cache_path(chamber, committee, unit_key)
    if os.path.exists(path) and not force:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    grounding_quality = str(entry.get("grounding_quality", "none"))
    grounding = _grounding_source(entry)
    context = _unit_context(committee, unit_key, entry)

    def _map_once() -> tuple:
        """Ein vollstaendiger Stufe-1+2-Durchlauf -> (clean_rows, messages).

        Frischer API-Call je Aufruf -> die Lauf-Varianz des Modells gibt einem
        Retry eine echte zweite Chance (kein Caching auf dieser Ebene)."""
        # ---- Stufe 1: Sektoren --------------------------------------------
        stage1_system = taxonomy.build_prefix_block(taxonomy_data) + _STAGE1_INSTRUCT
        stage1 = llm_client.call_json(
            "mapping", stage1_system, context, _STAGE1_SCHEMA, client=client,
        )
        chosen_sectors = []
        for item in stage1.get("sectors", []):
            sector, rank, sconf, _rat = _extract_sector(item)
            if taxonomy.is_valid_sector(taxonomy_data, sector):
                chosen_sectors.append((sector, rank, sconf))

        # ---- Stufe 2: Industries je Sektor --------------------------------
        rows: List[Dict[str, Any]] = []
        for sector, rank, sector_conf in chosen_sectors:
            stage2_system = taxonomy.build_sector_block(taxonomy_data, sector) + _STAGE2_INSTRUCT
            stage2_user = context + f"\n\nZu mappender Sektor: {sector}"
            stage2 = llm_client.call_json(
                "mapping", stage2_system, stage2_user, _STAGE2_SCHEMA, client=client,
            )
            for item in stage2.get("industries", []):
                industry, ind_conf, _rationale = _extract_industry(item)
                if not taxonomy.is_valid_industry(taxonomy_data, sector, industry):
                    continue
                rows.append({
                    "sector": sector,
                    "industry": industry,
                    "rank": rank,
                    # Stufe-2-Konfidenz (Pflicht lt. Prompt); Sektor-Konfidenz
                    # nur als defensiver Fallback, falls das Feld doch fehlt.
                    "confidence": ind_conf or sector_conf,
                    "grounding_source": grounding,
                })
        return validate.validate_rows(rows, taxonomy_data, grounding_quality)

    clean_rows, messages = _map_once()
    attempts = 1

    # Leeres-Mapping-Retry mit exponentiellem Backoff. Gegated: bei
    # MAP_RETRY_ONLY_GROUNDED feuert er nur, wenn echtes Grounding vorliegt
    # ("direct") — sonst ist leer haeufig korrekt (kein Branchen-Nexus).
    may_retry = grounding_quality == "direct" or not config.MAP_RETRY_ONLY_GROUNDED
    if not clean_rows and may_retry:
        for i in range(config.MAP_EMPTY_RETRIES):
            backoff = config.MAP_EMPTY_BACKOFF[
                min(i, len(config.MAP_EMPTY_BACKOFF) - 1)]
            logger.warning(
                "Mapping leer (%s/%s/%s) — Retry %d/%d in %ds.",
                chamber, committee, unit_key, i + 1,
                config.MAP_EMPTY_RETRIES, backoff)
            time.sleep(backoff)
            clean_rows, messages = _map_once()
            attempts += 1
            if clean_rows:
                break

    no_mapping = not clean_rows
    if no_mapping:
        logger.warning("Kein Mapping fuer %s/%s/%s nach %d Versuch(en).",
                       chamber, committee, unit_key, attempts)

    result = {
        "chamber": chamber,
        "committee": committee,
        "unit_key": unit_key,
        "grounding_quality": grounding_quality,
        "rows": clean_rows,
        "no_mapping": no_mapping,
        "attempts": attempts,
        "validation_messages": messages,
    }
    os.makedirs(config.CACHE_MAPPING, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return result
