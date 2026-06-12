"""Stabile mapping_id + Subcommittee-Label-Aufloesung.

mapping_id ist der Schluessel fuer den xlsx-Round-Trip (Review -> Finalize). Er
muss ueber Re-Runs identisch bleiben und basiert daher rein auf den
Identitaets-Feldern (Kammer/Committee/Subcommittee/Sektor/Industry).
"""
import hashlib

COMMITTEE_KEY = "__committee__"


def resolve_subcommittee_label(committee: str, unit_key: str) -> str:
    """Committee-Ebene (__committee__) -> Committee-Name als Subcommittee-
    Platzhalter; sonst der Subcommittee-Name unveraendert.

    Damit fallen Committees ohne eigene Subcommittees nicht mehr durch den
    Loader (config/committee_mapping.py ueberspringt leere Subcommittees).
    """
    return committee if unit_key == COMMITTEE_KEY else unit_key


def mapping_id(chamber: str, committee: str, subcommittee: str,
               sector: str, industry: str) -> str:
    raw = "|".join([chamber, committee, subcommittee, sector, industry])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
