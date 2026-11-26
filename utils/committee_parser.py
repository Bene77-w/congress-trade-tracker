"""Zentraler F2-Parser fuer ``Member_Committees_Full`` (Phase 3).

EIN Parser fuer beide Consumer (``tags/committee_tag.py`` und
``app/member_metrics.py``). Rein und abhaengigkeitsfrei (kein pandas/Streamlit/
Settings) -> ueberall importierbar, voll unit-testbar.

F2-Grammatik (kanonische Spec: ``organisation/tool_specific_md/csv_zielstruktur.md``;
Quell-Roster ``data/mappings/*_member&(sub)committees.csv``, Spalte ``Committees``
-> 1:1 in ``Member_Committees_Full``):
  - ``;`` trennt NUR Committees (Top-Level)
  - ``:`` trennt Committee-Header von seiner Subcommittee-Liste
  - ``~`` trennt Subcommittees
  - ``|`` trennt ``Name|Rolle`` (Rolle optional/leer)

Damit ist jeder ``;``-Teil IMMER ein Committee — die Mehrdeutigkeit des alten
Formats (``;`` fuer Committees UND Subcommittees) ist strukturell geloest;
ein Committee ohne Subcommittees ist eindeutig (kein ``:``-Teil).

Rollen sind ebenen-abhaengig normalisiert (Committee: ``Chairman``/``Vice
Chairman``/``Ranking Member``; Subcommittee: ``Chair``/``Vice Chair``/``Ranking
Member``; beide Ebenen: ``Ex Officio``/``Cochairman``; leer = Mitglied).
"""
from __future__ import annotations

from typing import Any


def _split_name_role(token: str) -> tuple[str, str]:
    """``"Name|Role"`` oder ``"Name"`` -> ``(name, role)`` (beide getrimmt)."""
    name, _, role = token.partition('|')
    return name.strip(), role.strip()


def parse_committees_full(committee_string: Any) -> list[dict]:
    """Parst einen F2-String in eine verschachtelte Struktur.

    Defensiv: Nicht-Strings (None/NaN/Zahlen) und leere Strings -> ``[]``.

    Returns:
        ``[{'committee': str, 'role': str,
            'subcommittees': [{'name': str, 'role': str}, ...]}, ...]``
    """
    if not isinstance(committee_string, str) or not committee_string.strip():
        return []

    committees: list[dict] = []
    for raw in committee_string.split(';'):
        entry = raw.strip()
        if not entry:
            continue

        head, _, subs_part = entry.partition(':')
        cname, crole = _split_name_role(head)
        if not cname:
            continue

        subcommittees: list[dict] = []
        for sub in subs_part.split('~') if subs_part else []:
            sname, srole = _split_name_role(sub.strip())
            if sname:
                subcommittees.append({'name': sname, 'role': srole})

        committees.append({'committee': cname, 'role': crole,
                           'subcommittees': subcommittees})

    return committees
