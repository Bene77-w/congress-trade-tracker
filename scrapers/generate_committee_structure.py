"""Struktur-Auflösung aller (Sub-)Committees beider Kammern → data/processed/.

Erzeugt eine kammer-übergreifende Liste aller aktuell aufgelösten Committees +
Subcommittees (House + Senate) als Single-Source-Strukturdatei. Ziel: keine manuelle
Pflege — die Liste entsteht bei jedem Lauf aus den tatsächlichen Scraper-Quellen
(Senate: committee-XMLs via Discovery; House: PDF-Parser).

Output: data/processed/committees_structure.csv  (Chamber,Committee,Subcommittee)
  - eine Zeile pro Committee (Subcommittee leer)
  - eine Zeile pro (Committee, Subcommittee)

Stand: Senate = vollständig (XML). House = Standing Committees (validierter
Phase-2-Spike). House Select/Joint werden mit Abschluss von Phase 2 ergänzt.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENATE_RAW = ROOT / "scrapers" / "senate_committee_xml" / "raw"
HOUSE_PDF = ROOT / "scrapers" / "house_pdf_parsing_tool" / "scsoal.pdf"
HOUSE_PARSER = ROOT / "scrapers" / "house_pdf_parsing_tool" / "parse_house_committee_pdf.py"
HOUSE_STANDING_PAGES = list(range(4, 56))  # validierter Spike-Bereich
OUT = ROOT / "data" / "processed" / "committees_structure.csv"
COLUMNS = ["Chamber", "Committee", "Subcommittee"]


def senate_pairs() -> list[tuple[str, str, str]]:
    """(Chamber, Committee, Subcommittee) aus den Senate-XML-Snapshots."""
    rows = []
    for path in sorted(p for p in SENATE_RAW.glob("*.xml") if not p.name.startswith("_")):
        comm = ET.fromstring(path.read_text(encoding="utf-8")).find("committees")
        cname = comm.findtext("committee_name", "").strip()
        rows.append(("Senate", cname, ""))
        for sub in comm.findall("subcommittee"):
            rows.append(("Senate", cname, sub.findtext("subcommittee_name", "").strip()))
    return rows


def house_pairs() -> list[tuple[str, str, str]]:
    """(Chamber, Committee, Subcommittee) aus dem House-PDF (Standing Committees).

    Gibt eine leere Liste zurück, wenn PDF/Parser fehlen (Branch ohne scsoal.pdf),
    statt zu brechen — Senate-Output bleibt dann trotzdem gültig.
    """
    if not HOUSE_PDF.exists() or not HOUSE_PARSER.exists():
        print(f"  House übersprungen (PDF/Parser fehlt: {HOUSE_PDF.name})")
        return []
    spec = importlib.util.spec_from_file_location("hp", HOUSE_PARSER)
    hp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hp)
    members, _detected = hp.parse_all_committees(str(HOUSE_PDF), HOUSE_STANDING_PAGES, cross_ref=False)
    seen_comm = {}          # cname -> set(subs)
    for md in members.values():
        for cname, cd in md.get("committees", {}).items():
            subs = seen_comm.setdefault(cname, set())
            for sn in cd.get("subcommittees", []):
                subs.add(sn)
    rows = []
    for cname in sorted(seen_comm):
        rows.append(("House", cname, ""))
        for sn in sorted(seen_comm[cname]):
            rows.append(("House", cname, sn))
    return rows


def generate(out: Path = OUT) -> list[tuple[str, str, str]]:
    print("Senate (XML-Snapshots) …")
    rows = senate_pairs()
    print(f"  Senate: {sum(1 for r in rows if not r[2])} Committees, "
          f"{sum(1 for r in rows if r[2])} Subcommittees")
    print("House (PDF, Standing Committees) …")
    h = house_pairs()
    print(f"  House: {sum(1 for r in h if not r[2])} Committees, "
          f"{sum(1 for r in h if r[2])} Subcommittees")
    rows += h
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    print(f"→ {len(rows)} Zeilen → {out}")
    return rows


if __name__ == "__main__":
    generate()
    sys.exit(0)
