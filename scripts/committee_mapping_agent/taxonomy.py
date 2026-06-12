"""Taxonomie: Laden, Anreicherung (einmalig) und byte-stabiler Prefix-Block.

Die kanonische Allow-Liste (11 Sektoren + ~145 Industries) stammt IMMER aus der
Quell-Datei ``yf_sectors_with_industries.json``. Die Anreicherung fuegt nur
Kurzbeschreibungen hinzu (``..._enriched.json``) — sie aendert die Mengen nie,
damit ein LLM-Fehler bei der Anreicherung die Allow-Liste nicht korrumpieren
kann.

``build_prefix_block`` rendert die Taxonomie deterministisch (byte-stabil) als
Text. Dieser Block wird als identischer Prefix an den Anfang jeder System-
Message gestellt -> DeepSeek-Auto-Prompt-Caching (Cache-Hit ~10 % Input-Preis).
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import config


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------
def load_source() -> Dict[str, Any]:
    """Kanonische Quell-Taxonomie (nur Sektoren + Industries, keine Texte)."""
    with open(config.TAXONOMY_SOURCE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_enriched() -> Dict[str, Any]:
    """Angereicherte Taxonomie; Fehler mit Hinweis, wenn noch nicht erzeugt."""
    if not os.path.exists(config.TAXONOMY_ENRICHED):
        raise FileNotFoundError(
            "Angereicherte Taxonomie fehlt. Erst erzeugen:\n"
            "    python -m scripts.committee_mapping_agent.run enrich-taxonomy"
        )
    with open(config.TAXONOMY_ENRICHED, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load(prefer_enriched: bool = True) -> Dict[str, Any]:
    """Angereicherte Taxonomie wenn vorhanden, sonst die Quelle."""
    if prefer_enriched and os.path.exists(config.TAXONOMY_ENRICHED):
        return load_enriched()
    return load_source()


# ---------------------------------------------------------------------------
# Allow-Listen-Helfer  (kanonische Menge IMMER aus der jeweiligen data-Struktur)
# ---------------------------------------------------------------------------
def _industry_name(item: Any) -> str:
    """Industry-Eintrag kann str (Quelle) oder dict (enriched) sein."""
    return item["industry"] if isinstance(item, dict) else item


def sector_names(data: Dict[str, Any]) -> List[str]:
    return [s["sector"] for s in data["sectors"]]


def industries_for_sector(data: Dict[str, Any], sector: str) -> List[str]:
    for s in data["sectors"]:
        if s["sector"] == sector:
            return [_industry_name(i) for i in s["industries"]]
    return []


def allowed_pairs(data: Dict[str, Any]) -> Set[Tuple[str, str]]:
    pairs: Set[Tuple[str, str]] = set()
    for s in data["sectors"]:
        for i in s["industries"]:
            pairs.add((s["sector"], _industry_name(i)))
    return pairs


def is_valid_sector(data: Dict[str, Any], sector: str) -> bool:
    return sector in set(sector_names(data))


def is_valid_industry(data: Dict[str, Any], sector: str, industry: str) -> bool:
    return (sector, industry) in allowed_pairs(data)


# ---------------------------------------------------------------------------
# Byte-stabiler Prefix-Block
# ---------------------------------------------------------------------------
def build_prefix_block(data: Optional[Dict[str, Any]] = None) -> str:
    """Deterministischer Text-Block der Taxonomie (byte-stabil bei gleichem Input).

    Reihenfolge folgt der data-Struktur (nicht sortiert), damit die kuratierte
    Rang-Reihenfolge der Sektoren erhalten bleibt. Keine Zeitstempel, keine
    nicht-deterministischen Elemente -> identischer Prefix ueber Re-Runs.
    """
    if data is None:
        data = load()
    lines: List[str] = [
        "# ERLAUBTE TAXONOMIE (Allow-Liste — Yahoo-Finance-Sektoren/Industries)",
        "# Nutze AUSSCHLIESSLICH exakt diese Sektor- und Industry-Namen.",
        "",
    ]
    for s in data["sectors"]:
        sector = s["sector"]
        sdesc = s.get("description", "")
        lines.append(f"## {sector}" + (f" — {sdesc}" if sdesc else ""))
        for item in s["industries"]:
            lines.append(_industry_line(item))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _industry_line(item: Any) -> str:
    if isinstance(item, dict):
        name = item["industry"]
        idesc = item.get("description", "")
        return f"- {name}" + (f": {idesc}" if idesc else "")
    return f"- {item}"


def build_sector_block(data: Dict[str, Any], sector: str) -> str:
    """Byte-stabiler Block NUR der Industries eines Sektors (Stufe-2-Hierarchie).

    Wird in der zweiten Mapping-Stufe als identischer Prefix verwendet — pro
    Sektor identisch ueber alle Subcommittees -> gutes Prompt-Caching, ohne dem
    Modell sektor-fremde Industries zu zeigen.
    """
    for s in data["sectors"]:
        if s["sector"] == sector:
            sdesc = s.get("description", "")
            lines = [
                f"# ERLAUBTE INDUSTRIES fuer Sektor '{sector}'"
                + (f" — {sdesc}" if sdesc else ""),
                "# Nutze AUSSCHLIESSLICH exakt diese Industry-Namen.",
                "",
            ]
            lines += [_industry_line(i) for i in s["industries"]]
            return "\n".join(lines).rstrip() + "\n"
    return ""


# ---------------------------------------------------------------------------
# Anreicherung (einmalig, eingefroren)
# ---------------------------------------------------------------------------
# Bewusst PERMISSIV: das Modell variiert die Schluesselnamen (industry/name,
# description/desc). Da die kanonische Industry-Menge ohnehin aus der Quelle
# stammt und die Beschreibungen nur advisorisch sind, akzeptieren wir lockere
# Objekte und gleichen in Code per Namen ab -> robust gegen Format-Drift.
_ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "sector_description": {"type": "string"},
        "industries": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["sector_description", "industries"],
    "additionalProperties": True,
}


def _extract_industry(item: Dict[str, Any]) -> tuple:
    """(name, description) aus einem lockeren Enrich-Item, schluesseltolerant."""
    name = (item.get("industry") or item.get("name")
            or item.get("Industry") or item.get("title") or "")
    desc = (item.get("description") or item.get("desc")
            or item.get("Description") or "")
    return str(name), str(desc)

_ENRICH_SYSTEM = (
    "Du bist ein Finanz-Taxonomie-Experte. Du beschreibst Yahoo-Finance-"
    "Sektoren und ihre Industries jeweils in EINEM praezisen deutschen Satz, "
    "der die wirtschaftliche Aktivitaet abgrenzt. Die Beschreibungen dienen "
    "spaeter als Entscheidungshilfe, um Kongress-Ausschuesse den richtigen "
    "Sektoren/Industries zuzuordnen."
)


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def enrich(client: Any = None, role: str = "review") -> Dict[str, Any]:
    """Erzeugt die angereicherte Taxonomie und schreibt sie nach TAXONOMY_ENRICHED.

    Pro Sektor ein LLM-Call. Die kanonische Industry-Menge stammt aus der
    Quelle; die LLM-Beschreibungen werden nur per Namen zugeordnet. Fehlt eine
    Beschreibung, bleibt sie leer — die Menge bleibt unveraendert.
    """
    from . import llm_client  # lazy, damit Tests ohne API importierbar bleiben

    source = load_source()
    enriched_sectors: List[Dict[str, Any]] = []

    for s in source["sectors"]:
        sector = s["sector"]
        canonical = [_industry_name(i) for i in s["industries"]]
        user = (
            f"Sektor: {sector}\n"
            f"Industries (genau diese, gleiche Schreibweise zurueckgeben):\n"
            + "\n".join(f"- {name}" for name in canonical)
        )
        result = llm_client.call_json(
            role, _ENRICH_SYSTEM, user, _ENRICH_SCHEMA, client=client,
        )
        desc_by_name = {}
        for it in result.get("industries", []):
            name, desc = _extract_industry(it)
            if name:
                desc_by_name[_norm_name(name)] = desc
        enriched_sectors.append({
            "sector": sector,
            "description": result.get("sector_description", ""),
            "industries": [
                {"industry": name, "description": desc_by_name.get(_norm_name(name), "")}
                for name in canonical
            ],
        })

    enriched = {"sectors": enriched_sectors}
    os.makedirs(os.path.dirname(config.TAXONOMY_ENRICHED), exist_ok=True)
    with open(config.TAXONOMY_ENRICHED, "w", encoding="utf-8") as fh:
        json.dump(enriched, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return enriched
