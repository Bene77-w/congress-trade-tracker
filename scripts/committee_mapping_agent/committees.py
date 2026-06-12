"""Laden der Roh-Committee-Listen (Input) + Slug-Helfer.

Die Input-CSVs (``us_house_all_committees.csv`` / ``us_senate_all_committees.csv``)
haben die Spalten ``Committee,Subcommittee``. Committees ohne eigene
Subcommittee-Zeile (Budget/Ethics/Joint/Select) erhalten eine leere Liste —
nachgelagert wird dafuer auf Committee-Ebene gemappt (Platzhalter = Committee-
Name als Subcommittee).
"""
import csv
import re
from typing import Dict, List

from . import config

CHAMBER_CSV = {
    "House": config.HOUSE_COMMITTEES_CSV,
    "Senate": config.SENATE_COMMITTEES_CSV,
}


def slugify(text: str) -> str:
    """Dateinamen-tauglicher Slug (a-z0-9_), stabil ueber Re-Runs."""
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower())
    return s.strip("_") or "x"


def load_committees(chamber: str) -> Dict[str, List[str]]:
    """{Committee -> [Subcommittee, ...]} fuer eine Kammer, Reihenfolge erhalten.

    Leere Subcommittee-Zellen werden uebersprungen; Committees, die nur ohne
    Subcommittee auftauchen, bekommen eine leere Liste.
    """
    path = CHAMBER_CSV[chamber]
    result: Dict[str, List[str]] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            committee = (row.get("Committee") or "").strip()
            if not committee:
                continue
            sub = (row.get("Subcommittee") or "").strip()
            result.setdefault(committee, [])
            if sub and sub not in result[committee]:
                result[committee].append(sub)
    return result


def iter_all_committees():
    """Liefert (chamber, committee, [subcommittees]) ueber beide Kammern."""
    for chamber in ("House", "Senate"):
        for committee, subs in load_committees(chamber).items():
            yield chamber, committee, subs
