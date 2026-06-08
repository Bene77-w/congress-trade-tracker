"""Phase 1b: Unit-Tests für den Senate-XML→F2-Producer (reine Logik).

Deckt die ebenen-abhängige Rollen-Normalisierung, Namens-Konstruktion und den
F2-String-Aufbau ab — ohne Netz/Dateien.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# Producer-Modul liegt in scrapers/senate_committee_xml (Skript-Stil) → Pfad ergänzen.
_PROD_DIR = Path(__file__).resolve().parents[1] / "scrapers" / "senate_committee_xml"
sys.path.insert(0, str(_PROD_DIR))

import parse_senate_xml as P  # noqa: E402


@pytest.mark.parametrize("pos,level,expected", [
    ("Member", "committee", ""),
    ("", "committee", ""),
    ("Ranking", "committee", "Ranking Member"),
    ("Ranking", "sub", "Ranking Member"),
    ("Ex Officio", "sub", "Ex Officio"),
    ("Cochairman", "committee", "Cochairman"),
    ("Chairman", "committee", "Chairman"),
    ("Vice Chairman", "committee", "Vice Chairman"),
    ("Chairman", "sub", "Chair"),            # ebenen-abhängiger Remap
    ("Vice Chairman", "sub", "Vice Chair"),  # ebenen-abhängiger Remap
])
def test_map_role(pos, level, expected):
    assert P.map_role(pos, level) == expected


def test_display_name_collapses_double_space():
    # Quell-XML enthält vereinzelt "John  R." (doppeltes Leerzeichen)
    assert P._display_name("Curtis", "John  R.") == "Curtis, John R."
    assert P._display_name("Boozman", "John") == "Boozman, John"


def test_to_row_f2_structure_and_exofficio():
    rec = {
        "last": "Boozman", "first": "John", "state": "AR", "party": "R",
        "ex_officio": True,
        "committees": {
            "Committee on Agriculture, Nutrition, and Forestry": {
                "role": "Chairman",
                "subs": {
                    "Subcommittee on Commodities": "Ex Officio",
                    "Subcommittee on Livestock": "Ex Officio",
                },
            },
            "Committee on Rules and Administration": {"role": "", "subs": {}},
        },
    }
    row = P._to_row(rec)
    assert row["Name"] == "Boozman, John"
    assert row["ExOfficio"] == "true"
    # F2: Committees per ';', Sub-Liste nach ':', Subs per '~', Name|Rolle per '|'
    assert row["Committees"] == (
        "Committee on Agriculture, Nutrition, and Forestry|Chairman:"
        "Subcommittee on Commodities|Ex Officio~Subcommittee on Livestock|Ex Officio;"
        "Committee on Rules and Administration|"
    )


def test_parse_aggregates_member_across_committee_and_sub(tmp_path):
    """Sub-Membership wird unter dasselbe Committee aggregiert (eine ';'-Gruppe)."""
    xml = (
        '<committee_membership><committees>'
        '<majority_party>R</majority_party>'
        '<committee_name>Committee on Testing</committee_name>'
        '<committee_code>SSTT00</committee_code>'
        '<members>'
        '<member><name><first>Jane</first><last>Doe</last></name>'
        '<state>CA</state><party>D</party><position>Ranking</position></member>'
        '</members>'
        '<subcommittee><subcommittee_name>Subcommittee on Widgets</subcommittee_name>'
        '<committee_code>SSTT01</committee_code><members>'
        '<member><name><first>Jane</first><last>Doe</last></name>'
        '<state>CA</state><party>D</party><position>Chairman</position></member>'
        '</members></subcommittee>'
        '</committees></committee_membership>'
    )
    (tmp_path / "SSTT.xml").write_text(xml, encoding="utf-8")
    # _manifest.json muss ignoriert werden (kein Committee-XML)
    (tmp_path / "_manifest.json").write_text("{}", encoding="utf-8")
    rows = P.parse_all(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["Name"] == "Doe, Jane"
    # Ranking auf Committee-Ebene, Chairman→Chair auf Sub-Ebene, eine ';'-Gruppe
    assert r["Committees"] == (
        "Committee on Testing|Ranking Member:Subcommittee on Widgets|Chair"
    )
