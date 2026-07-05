"""Research-Agent: sammelt Jurisdiktionstext je (Sub-)Committee.

Strategie (mit User abgestimmt):
  1. Offizielle Committee-Site (*.house.gov / *.senate.gov, Jurisdiction-Seite)
     ist der vorgegebene Primaer-Anlauf. Bei Treffer SOFORT stoppen.
  2. Nur bei Miss autonome Recherche mit Fallback-Toolbox (GovInfo House Rule X /
     Senate Committee Prints / Committee-Rules-PDF) + hartes Such-Budget.
  3. Jede Einheit braucht ein woertliches Kurz-Zitat + Quell-URL. Ohne echtes
     Zitat -> grounding_quality="none" (nichts erfinden).

Output je Committee -> cache/research/{chamber}_{committee_slug}.json
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from . import config, llm_client
from .committees import slugify

logger = logging.getLogger(__name__)

COMMITTEE_KEY = "__committee__"
GROUNDING_QUALITIES = ("direct", "fallback", "none")

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CommitteeMappingAgent/1.0; research)"
    )
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def web_search(args: Dict[str, Any]) -> str:
    """DuckDuckGo-Suche (ddgs). Liefert nummerierte Treffer (Titel/URL/Snippet)."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "FEHLER: leere query."
    max_results = int(args.get("max_results", 6))
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # Netzwerk/Rate-Limit -> ans Modell zurueck
        return f"FEHLER bei web_search: {exc}"
    if not hits:
        return "Keine Treffer."
    lines = []
    for i, h in enumerate(hits, 1):
        title = h.get("title", "")
        href = h.get("href") or h.get("url", "")
        body = (h.get("body") or "")[:300]
        lines.append(f"{i}. {title}\n   URL: {href}\n   {body}")
    return "\n".join(lines)


def fetch_page(args: Dict[str, Any]) -> str:
    """Laedt eine URL und liefert den extrahierten Text (fensterweise).

    Lange Seiten gehen nicht verloren: geliefert wird das Fenster
    [start_char, start_char + FETCH_MAX_CHARS); der Kappungs-Marker am Ende
    nennt den start_char, mit dem das Modell die Fortsetzung abrufen kann.
    """
    url = str(args.get("url", "")).strip()
    if not url:
        return "FEHLER: leere url."
    try:
        start_char = max(0, int(args.get("start_char") or 0))
    except (TypeError, ValueError):
        start_char = 0
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS,
                            timeout=config.FETCH_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        return f"FEHLER bei fetch_page({url}): {exc}"

    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type or url.endswith((".htm", ".html", "/")):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    else:
        text = resp.text

    total = len(text)
    if start_char and start_char >= total:
        return (f"URL: {url}\n\nFEHLER: start_char={start_char} liegt hinter "
                f"dem Textende ({total} Zeichen).")
    end = min(start_char + config.FETCH_MAX_CHARS, total)
    window = text[start_char:end]
    if start_char:
        window = f"[Fortsetzung ab Zeichen {start_char}]\n" + window
    if end < total:
        window += (f"\n[... gekappt bei Zeichen {end} von {total} — Rest via "
                   f"fetch_page mit start_char={end} abrufbar ...]")
    return f"URL: {url}\n\n{window}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Websuche; liefert Titel, URL und Snippet je Treffer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Laedt eine URL und liefert ihren Textinhalt. Lange Seiten "
                "kommen fensterweise: endet die Antwort mit einem Kappungs-"
                "Marker, den Rest mit start_char (Wert aus dem Marker) abrufen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "start_char": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Startposition in Zeichen fuer die "
                                       "Fortsetzung einer gekappten Seite.",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

TOOL_HANDLERS = {"web_search": web_search, "fetch_page": fetch_page}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def _system_prompt(chamber: str, committee: str) -> str:
    site = "house.gov" if chamber == "House" else "senate.gov"
    return (
        "Du bist ein Rechercheur fuer US-Kongress-Ausschuss-Jurisdiktionen "
        "(119. Kongress). Deine Aufgabe: fuer das vorgegebene Committee und "
        "jede genannte Subcommittee den OFFIZIELLEN Jurisdiktionstext finden "
        "und ein woertliches Kurz-Zitat sichern.\n\n"
        "STRATEGIE — strikt einhalten:\n"
        f"1. PRIMAER: die offizielle Committee-Website (*.{site}), dort die "
        "Seite 'Jurisdiction' / 'About' / 'Rules'. Suche zuerst gezielt danach "
        f"(z.B. '{committee} committee {site} jurisdiction'). Findest du dort "
        "den Jurisdiktionstext, STOPP — keine weiteren Quellen.\n"
        "2. NUR bei Miss: Fallback-Quellen — GovInfo House Rule X bzw. Senate "
        "Rule XXV / Senate Committee Prints / das Committee-Rules-PDF.\n"
        "3. Arbeite sparsam: so wenige Tool-Aufrufe wie moeglich. Erfinde "
        "NICHTS. Findest du fuer eine Einheit keinen belastbaren Text, ist das "
        "ein legitimes Ergebnis (grounding_quality='none').\n\n"
        "Wenn du fertig bist, gib AUSSCHLIESSLICH ein JSON-Objekt aus (keine "
        "Codefences) mit dieser Form:\n"
        "{\"jurisdiction\": { \"<Subcommittee-Name oder __committee__>\": "
        "{\"text\": <knappe Zusammenfassung der Jurisdiktion auf Deutsch>, "
        "\"source_url\": <URL>, \"quote\": <woertliches Zitat aus der Quelle>, "
        "\"grounding_quality\": \"direct\"|\"fallback\"|\"none\"} } }\n"
        "grounding_quality: 'direct' = Zitat von der offiziellen Committee-Site; "
        "'fallback' = Zitat aus einer Fallback-Quelle; 'none' = kein "
        "verwertbares Zitat gefunden."
    )


def _user_prompt(committee: str, subcommittees: List[str]) -> str:
    if subcommittees:
        units = "\n".join(f"- {s}" for s in subcommittees)
        return (
            f"Committee: {committee}\n"
            f"Finde Jurisdiktionstext + Zitat fuer das Committee selbst "
            f"(Schluessel '{COMMITTEE_KEY}') UND fuer jede dieser "
            f"Subcommittees:\n{units}"
        )
    return (
        f"Committee: {committee}\n"
        f"Dieses Committee hat keine Subcommittees. Finde Jurisdiktionstext + "
        f"Zitat fuer das Committee selbst (Schluessel '{COMMITTEE_KEY}')."
    )


# ---------------------------------------------------------------------------
# Strukturierung / Validierung
# ---------------------------------------------------------------------------
_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_url": {"type": "string"},
        "quote": {"type": "string"},
        "grounding_quality": {"type": "string", "enum": list(GROUNDING_QUALITIES)},
    },
    "required": ["text", "source_url", "quote", "grounding_quality"],
    "additionalProperties": True,
}
_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "jurisdiction": {
            "type": "object",
            "additionalProperties": _ENTRY_SCHEMA,
        }
    },
    "required": ["jurisdiction"],
    "additionalProperties": True,
}


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _expected_keys(subcommittees: List[str]) -> List[str]:
    return [COMMITTEE_KEY] + list(subcommittees) if subcommittees else [COMMITTEE_KEY]


def _normalize(raw: Dict[str, Any], subcommittees: List[str]) -> Dict[str, Any]:
    """Saeubert das Modell-Ergebnis: Vollstaendigkeit + grounding-Cap.

    Fehlt eine erwartete Einheit, wird sie als grounding_quality='none'
    ergaenzt. Ohne echtes Zitat wird grounding_quality auf 'none' gedeckelt.
    """
    juris = raw.get("jurisdiction", {}) or {}
    out: Dict[str, Any] = {}
    for key in _expected_keys(subcommittees):
        entry = juris.get(key) or {}
        quote = str(entry.get("quote", "")).strip()
        gq = entry.get("grounding_quality", "none")
        if gq not in GROUNDING_QUALITIES:
            gq = "none"
        # Ehrlichkeits-Deckel: ohne (ausreichendes) Zitat keine direkte/Fallback-
        # Groundung beanspruchen.
        if len(quote) < 15:
            gq = "none"
        out[key] = {
            "text": str(entry.get("text", "")).strip(),
            "source_url": str(entry.get("source_url", "")).strip(),
            "quote": quote,
            "grounding_quality": gq,
        }
    return out


# ---------------------------------------------------------------------------
# Hauptfunktion + Cache
# ---------------------------------------------------------------------------
def cache_path(chamber: str, committee: str) -> str:
    return os.path.join(config.CACHE_RESEARCH, f"{chamber}_{slugify(committee)}.json")


def research_committee(
    chamber: str,
    committee: str,
    subcommittees: List[str],
    *,
    client: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Recherchiert ein Committee; nutzt/erzeugt den Research-Cache."""
    path = cache_path(chamber, committee)
    if os.path.exists(path) and not force:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    messages = [
        {"role": "system", "content": _system_prompt(chamber, committee)},
        {"role": "user", "content": _user_prompt(committee, subcommittees)},
    ]
    final_text, history = llm_client.call_with_tools(
        "research", messages, TOOLS, TOOL_HANDLERS, client=client,
    )

    # Finalen Text als JSON parsen; bei Fehlschlag Repair via call_json.
    raw: Optional[Dict[str, Any]] = None
    try:
        raw = json.loads(_strip_fences(final_text))
    except json.JSONDecodeError:
        logger.warning("[research] %s: finaler Text kein valides JSON -> Repair.",
                       committee)
    if raw is None or "jurisdiction" not in raw:
        raw = llm_client.call_json(
            "research",
            "Du strukturierst Rechercheergebnisse in JSON.",
            "Wandle die folgenden Rechercheergebnisse in das geforderte JSON-"
            f"Schema um:\n\n{final_text}",
            _RESEARCH_SCHEMA,
            client=client,
        )

    normalized = _normalize(raw, subcommittees)
    result = {
        "chamber": chamber,
        "committee": committee,
        "subcommittees": list(subcommittees),
        "jurisdiction": normalized,
        "search_log": [
            m for m in history
            if m.get("role") == "assistant" and m.get("tool_calls")
        ],
    }

    os.makedirs(config.CACHE_RESEARCH, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return result
