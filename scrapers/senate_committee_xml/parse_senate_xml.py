"""Senate-Committee-XML → F2-CSV (Phase 1b, Variante A).

Liest die raw/<code>.xml-Snapshots (s. fetch_senate_xml.py) und erzeugt eine
F2-formatierte CSV mit identischer Spaltenstruktur wie der bisherige PDF-Output
(`senate_member&(sub)committees.csv`): Name,Party,State,Committees,ExOfficio.

Quelle ist strukturiert → keine Heuristik. Pro Member über alle Committees +
Subcommittees aggregiert. Rollen ebenen-abhängig auf F2 normalisiert.

F2-String:  Committee|Rolle:Sub1|SubRolle~Sub2|SubRolle;NextCommittee|Rolle
Name:       "Last, First Middle"  (matcht die bestehende Senate-Baseline)
"""
from __future__ import annotations

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent / "raw"
COLUMNS = ["Name", "Party", "State", "Committees", "ExOfficio"]


def map_role(position: str, level: str) -> str:
    """XML <position> → F2-Rolle. level ∈ {'committee','sub'}.

    Member            → ''            (reguläres Mitglied, keine Rolle)
    Ranking           → 'Ranking Member' (beide Ebenen)
    Ex Officio        → 'Ex Officio'  (XML materialisiert es bereits pro Sub)
    Cochairman        → 'Cochairman'  (Senate Joint/Commission)
    Chairman/Vice Chairman → Committee: unverändert; Sub: 'Chair'/'Vice Chair'
    """
    p = position.strip()
    if p in ("", "Member"):
        return ""
    if p == "Ranking":
        return "Ranking Member"
    if p == "Ex Officio":
        return "Ex Officio"
    if p == "Cochairman":
        return "Cochairman"
    if level == "sub":
        if p == "Chairman":
            return "Chair"
        if p == "Vice Chairman":
            return "Vice Chair"
    return p  # Chairman / Vice Chairman auf Committee-Ebene


def _member_key(member: ET.Element) -> tuple[str, str, str]:
    name = member.find("name")
    first = (name.findtext("first", "") if name is not None else "").strip()
    last = (name.findtext("last", "") if name is not None else "").strip()
    state = member.findtext("state", "").strip()
    return last, first, state


def _display_name(last: str, first: str) -> str:
    # Quell-XML enthält vereinzelt doppelte Leerzeichen ("John  R.") → kollabieren.
    name = f"{last}, {first}".strip().rstrip(",")
    return re.sub(r"\s+", " ", name)


def _xml_files(raw_dir: Path) -> list[Path]:
    """Alle Committee-XMLs in raw/ (ohne _-präfixierte wie _manifest.json)."""
    return sorted(p for p in raw_dir.glob("*.xml") if not p.name.startswith("_"))


def parse_all(raw_dir: Path = RAW_DIR) -> list[dict]:
    """Aggregiert alle Member über alle raw/*.xml. Returnt Zeilen-Dicts (COLUMNS).

    Quell-Liste = was fetch_senate_xml.py (Discovery) abgelegt hat — kein Hardcoding.
    """
    # key (last,first,state) -> record
    members: dict[tuple, dict] = {}

    for path in _xml_files(raw_dir):
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        comm = root.find("committees")
        committee_name = comm.findtext("committee_name", "").strip()

        # --- Committee-Ebene ---
        comm_members = comm.find("members")
        for m in (comm_members.findall("member") if comm_members is not None else []):
            key = _member_key(m)
            party = m.findtext("party", "").strip()
            rec = members.setdefault(key, {
                "last": key[0], "first": key[1], "state": key[2],
                "party": party, "committees": {}, "ex_officio": False,
            })
            role = map_role(m.findtext("position", ""), "committee")
            centry = rec["committees"].setdefault(
                committee_name, {"role": "", "subs": {}})
            centry["role"] = role
            if role == "Ex Officio":
                rec["ex_officio"] = True

        # --- Subcommittee-Ebene ---
        for sub in comm.findall("subcommittee"):
            sub_name = sub.findtext("subcommittee_name", "").strip()
            sub_members = sub.find("members")
            for m in (sub_members.findall("member") if sub_members is not None else []):
                key = _member_key(m)
                party = m.findtext("party", "").strip()
                rec = members.setdefault(key, {
                    "last": key[0], "first": key[1], "state": key[2],
                    "party": party, "committees": {}, "ex_officio": False,
                })
                # Sub-Membership impliziert Committee-Membership
                centry = rec["committees"].setdefault(
                    committee_name, {"role": "", "subs": {}})
                srole = map_role(m.findtext("position", ""), "sub")
                centry["subs"][sub_name] = srole
                if srole == "Ex Officio":
                    rec["ex_officio"] = True

    return [_to_row(rec) for rec in members.values()]


def _to_row(rec: dict) -> dict:
    parts = []
    for cname, centry in rec["committees"].items():
        base = f"{cname}|{centry['role']}"
        if centry["subs"]:
            sub_strs = [f"{sn}|{sr}" for sn, sr in centry["subs"].items()]
            base += ":" + "~".join(sub_strs)
        parts.append(base)
    return {
        "Name": _display_name(rec["last"], rec["first"]),
        "Party": rec["party"],
        "State": rec["state"],
        "Committees": ";".join(parts),
        "ExOfficio": "true" if rec["ex_officio"] else "false",
    }


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sorted(rows, key=lambda r: r["Name"].casefold())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows_sorted)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/senate_f2_xml_output.csv")
    rows = parse_all()
    write_csv(rows, out)
    print(f"{len(rows)} Member → {out}")
