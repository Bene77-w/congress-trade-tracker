"""Deterministische Validierungs-/Bereinigungsschicht zwischen Mapping & Review.

Prueft jede Mapping-Zeile gegen harte Regeln, BEVOR ein teurer Review-Call
laeuft — fuengt Halluzinationen ab, die das LLM trotz Allow-Liste produziert:

  * (Sector, Industry) muss in der Taxonomie-Allow-Liste sein  -> sonst DROP
  * Sector_Rank muss eine positive ganze Zahl sein             -> sonst Coerce/DROP
  * confidence in {low, medium, high}                          -> sonst 'low'
  * grounding_source darf nicht leer sein                      -> sonst Cap auf low
  * confidence wird durch grounding_quality gedeckelt (GROUNDING_CONFIDENCE_CAP)

Liefert (bereinigte Zeilen, Fehler-/Warnungsliste). Idempotent.
"""
from typing import Any, Dict, List, Optional, Tuple

from . import config, taxonomy

_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


def _cap_confidence(conf: str, cap: str) -> str:
    """Senkt conf auf cap, wenn conf hoeher ist; sonst unveraendert."""
    if _LEVEL_ORDER.get(conf, 0) > _LEVEL_ORDER.get(cap, 2):
        return cap
    return conf


def validate_rows(
    rows: List[Dict[str, Any]],
    taxonomy_data: Dict[str, Any],
    grounding_quality: str = "none",
    *,
    taxonomy_pairs: Optional[set] = None,
    enforce_grounding_cap: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validiert/bereinigt Mapping-Zeilen einer Einheit.

    Args:
        rows: Liste von {sector, industry, rank, confidence, ai_comment,
              grounding_source}
        taxonomy_data: geladene Taxonomie (Allow-Liste)
        grounding_quality: direct|fallback|none der Einheit -> Konfidenz-Deckel
        taxonomy_pairs: optionaler vorberechneter Allow-Pairs-Set (Perf)
        enforce_grounding_cap: bei False werden die Grounding-Deckel (leere
            grounding_source + grounding_quality) NICHT angewandt — fuer
            ausdrueckliche User-Kommentare im Finalize, die den Deckel
            ueberstimmen duerfen. Allow-Liste/Rank/Konfidenz-Validitaet gelten
            weiterhin.

    Returns:
        (clean_rows, messages) — clean_rows sind valide + gedeckelt,
        messages beschreibt jede Verwerfung/Korrektur.
    """
    pairs = taxonomy_pairs if taxonomy_pairs is not None else taxonomy.allowed_pairs(taxonomy_data)
    cap = config.GROUNDING_CONFIDENCE_CAP.get(grounding_quality, "low")

    clean: List[Dict[str, Any]] = []
    messages: List[str] = []
    seen: set = set()

    for idx, row in enumerate(rows):
        sector = str(row.get("sector", "")).strip()
        industry = str(row.get("industry", "")).strip()
        label = f"Zeile {idx} ({sector} / {industry})"

        # 1. Allow-Liste (hart) -> DROP
        if (sector, industry) not in pairs:
            messages.append(f"{label}: nicht in Allow-Liste -> verworfen.")
            continue

        # 2. Duplikat (sector, industry) -> DROP (Dedup spaeter ohnehin)
        if (sector, industry) in seen:
            messages.append(f"{label}: Duplikat -> verworfen.")
            continue

        # 3. Rank
        try:
            rank = int(row.get("rank"))
            if rank < 1:
                raise ValueError
        except (TypeError, ValueError):
            messages.append(f"{label}: ungueltiger Rank '{row.get('rank')}' -> 99.")
            rank = 99

        # 4. Konfidenz
        conf = str(row.get("confidence", "")).strip().lower()
        if conf not in config.CONFIDENCE_LEVELS:
            messages.append(f"{label}: ungueltige confidence '{conf}' -> low.")
            conf = "low"

        # 5. grounding_source
        grounding = str(row.get("grounding_source", "")).strip()
        conf_before = conf
        if enforce_grounding_cap and not grounding:
            conf = _cap_confidence(conf, "low")
            messages.append(f"{label}: grounding_source leer -> confidence auf low gedeckelt.")

        # 6. grounding_quality-Deckel
        if enforce_grounding_cap:
            capped = _cap_confidence(conf, cap)
            if capped != conf and conf == conf_before:
                messages.append(
                    f"{label}: confidence '{conf}' durch grounding_quality "
                    f"'{grounding_quality}' auf '{capped}' gedeckelt."
                )
            conf = capped

        seen.add((sector, industry))
        clean.append({
            "sector": sector,
            "industry": industry,
            "rank": rank,
            "confidence": conf,
            "ai_comment": str(row.get("ai_comment", "")).strip(),
            "grounding_source": grounding,
        })

    return clean, messages
