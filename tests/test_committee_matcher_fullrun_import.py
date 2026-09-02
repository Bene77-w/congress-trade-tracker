"""Tests der Uebernahme eines Fullruns als Mapping.

Netzwerk- und CLI-frei: die Reviewmappe entsteht aus dem Trockenlauf des
Labelwerkzeugs, danach laeuft die Uebernahme gegen dieselbe Mappe, die Bene
abnehmen wuerde. Geprueft wird, was dabei allein in diesem Modul entschieden
wird - Spaltenvertrag, Herkunft je Zeile und die Weigerung, einen vorhandenen
Stand zu ueberschreiben.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import artifacts, fullrun_import, paths, review  # noqa: E402
from committee_matcher import goldset_label as gs  # noqa: E402

# ``pfade_zuruecksetzen`` ist autouse und wirkt erst, wenn es im Namensraum
# dieses Moduls steht: die Uebernahme laeuft in der Fullrun-Ablage, und ein
# vergessener Umschalter schriebe sonst in das echte Datenverzeichnis.
from tests.test_goldset_label import (  # noqa: E402,F401
    build_answer, fullrun_sandbox, pfade_zuruecksetzen, sample, taxonomy,
)

REVIEW_ID = "map-test"


@pytest.fixture()
def revisionen(tmp_path, monkeypatch):
    """Die Artefaktwurzel in tmp_path statt im echten Datenverzeichnis.

    Verlegt wird die Wurzel selbst und nicht nur ``review_path``: der Matcher
    schreibt ausschliesslich unterhalb von ``ARTIFACT_ROOT`` und weist jeden
    Pfad daneben ab. Die freigegebene Taxonomie wandert mit, weil der Export
    aus ihr die Industry- und Sektornamen holt.
    """
    wurzel = tmp_path / "artifacts"
    (wurzel / "taxonomy").mkdir(parents=True)
    shutil.copy2(gs.TAXONOMY_PATH, wurzel / "taxonomy" / gs.TAXONOMY_PATH.name)
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", wurzel)
    return wurzel / review.REVIEWS_DIR


@pytest.fixture()
def mappe(fullrun_sandbox, capsys):
    """Eine abgenommene Fullrun-Mappe aus dem Trockenlauf."""
    assert gs.main(["label", "--fullrun", "--engine", "claude"]) == 0
    capsys.readouterr()
    return gs.WORKBOOK_PATH


def uebernehmen(source: str = "claude", review_id: str = REVIEW_ID) -> int:
    return fullrun_import.main(["--review-id", review_id, "--source", source])


def zeilen(pfad: Path) -> list[dict[str, str]]:
    with pfad.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def blatt_setzen(pfad: Path, unit_id: str, spalte: str, wert: str) -> None:
    """Aendert die erste Zeile einer Unit im Blatt 'labels'."""
    book = load_workbook(pfad)
    sheet = book[gs.SHEET_LABELS]
    spalten = list(gs.LABEL_COLUMNS)
    for line in range(2, sheet.max_row + 1):
        if sheet.cell(row=line, column=1).value == unit_id:
            zelle = sheet.cell(row=line, column=spalten.index(spalte) + 1)
            zelle.value = wert
            zelle.data_type = "s"
            break
    else:
        raise AssertionError(f"{unit_id} steht nicht in der Mappe")
    book.save(pfad)


def test_uebernahme_schreibt_die_mappingdateien(mappe, revisionen, capsys) -> None:
    """Beide Dateien, mit dem Spaltenvertrag des Matchers."""
    assert uebernehmen() == 0
    capsys.readouterr()

    assignments = revisionen / REVIEW_ID / review.REVIEWED_ASSIGNMENTS_FILENAME
    coverage = revisionen / REVIEW_ID / review.REVIEWED_COVERAGE_FILENAME
    manifest = revisionen / REVIEW_ID / fullrun_import.MANIFEST_FILENAME
    assert assignments.exists() and coverage.exists() and manifest.exists()

    rows = zeilen(assignments)
    assert list(rows[0]) == list(review.REVIEWED_COLUMNS)
    assert list(zeilen(coverage)[0]) == list(review.REVIEWED_COVERAGE_COLUMNS)
    # Die Long-CSV des Matchers traegt eine Zeile je Unit-Industry-Paar.
    assert len(rows) == len(zeilen(coverage))
    assert all(row["review_id"] == REVIEW_ID for row in rows)
    # Ohne eigenen Matcherlauf gibt es keinen zweiten Namen fuer die Revision.
    assert all(row["run_id"] == REVIEW_ID for row in rows)


def test_unveraenderte_zeile_behaelt_die_herkunft_des_laufs(mappe, revisionen,
                                                            capsys) -> None:
    """Der Aufrufweg steht als Provider; erfundene Callfelder bleiben leer."""
    assert uebernehmen() == 0
    capsys.readouterr()
    rows = zeilen(revisionen / REVIEW_ID / review.REVIEWED_ASSIGNMENTS_FILENAME)

    assert {row["assignment_source"] for row in rows} == {"model"}
    assert {row["provider"] for row in rows} == {"claude-cli"}
    assert all(row["prompt_id"] and row["prompt_sha256"] for row in rows)
    assert all(not row["call_id"] and not row["generation_id"]
               and not row["from_repair"] for row in rows)
    # Industry- und Sektornamen kommen aus dem freigegebenen Snapshot.
    assert all(row["industry_name"] and row["sector_id"] for row in rows)


def test_geaenderte_zeile_gilt_als_handarbeit(mappe, revisionen, sample,
                                              capsys) -> None:
    """Wer eine Begruendung umschreibt, uebernimmt die Zeile."""
    unit_id = sample[0].unit_id
    blatt_setzen(mappe, unit_id, "rationale_short", "Von Hand nachgeschaerft.")

    assert uebernehmen() == 0
    capsys.readouterr()
    rows = {row["unit_id"]: row for row in
            zeilen(revisionen / REVIEW_ID / review.REVIEWED_ASSIGNMENTS_FILENAME)}

    assert rows[unit_id]["assignment_source"] == "manual"
    assert rows[unit_id]["rationale_short"] == "Von Hand nachgeschaerft."
    # Eine Handzeile hat keinen Modellaufruf hinter sich.
    assert rows[unit_id]["provider"] == ""
    assert rows[unit_id]["model"] == ""
    andere = [row for uid, row in rows.items() if uid != unit_id]
    assert {row["assignment_source"] for row in andere} == {"model"}


def test_geleerte_unit_wird_no_mapping(mappe, revisionen, sample, capsys) -> None:
    """Eine Unit ohne Industry ist 'no_mapping' und traegt ihren Grund."""
    unit_id = sample[0].unit_id
    blatt_setzen(mappe, unit_id, "industry_id", "")
    blatt_setzen(mappe, unit_id, "no_mapping_reason",
                 "Der Zustaendigkeitstext nennt keine handelbare Branche.")

    assert uebernehmen() == 0
    capsys.readouterr()
    coverage = {row["unit_id"]: row for row in
                zeilen(revisionen / REVIEW_ID / review.REVIEWED_COVERAGE_FILENAME)}

    assert coverage[unit_id]["decision"] == "no_mapping"
    assert coverage[unit_id]["assignment_count"] == "0"
    assert "handelbare Branche" in coverage[unit_id]["no_mapping_reason"]
    assert unit_id not in {
        row["unit_id"] for row in
        zeilen(revisionen / REVIEW_ID / review.REVIEWED_ASSIGNMENTS_FILENAME)}


def test_vorhandener_stand_wird_nicht_ueberschrieben(mappe, revisionen,
                                                     capsys) -> None:
    """Ein zweiter Aufruf darf ein abgenommenes Mapping nicht ersetzen."""
    assert uebernehmen() == 0
    capsys.readouterr()
    vorher = (revisionen / REVIEW_ID
              / review.REVIEWED_ASSIGNMENTS_FILENAME).read_text(encoding="utf-8")

    assert uebernehmen() == 1
    fehler = capsys.readouterr().err
    assert "traegt bereits einen Importstand" in fehler
    assert (revisionen / REVIEW_ID
            / review.REVIEWED_ASSIGNMENTS_FILENAME).read_text(
                encoding="utf-8") == vorher


def test_unbekannte_unit_haelt_die_uebernahme_an(mappe, revisionen,
                                                 sample, capsys) -> None:
    """Mappe und Korpus aus verschiedenen Staenden ergeben kein Mapping."""
    book = load_workbook(mappe)
    sheet = book[gs.SHEET_LABELS]
    zelle = sheet.cell(row=2, column=1)
    zelle.value = "house--gibt-es-nicht"
    zelle.data_type = "s"
    book.save(mappe)

    assert uebernehmen() == 1
    assert "kennt der Korpus nicht" in capsys.readouterr().err
    assert not (revisionen / REVIEW_ID).exists()


def test_umgebaute_mappe_wird_abgelehnt(mappe, revisionen, capsys) -> None:
    """Eine verschobene Spalte laese das Mapping spaltenweise falsch."""
    book = load_workbook(mappe)
    book[gs.SHEET_LABELS].cell(row=1, column=2).value = "industrie"
    book.save(mappe)

    assert uebernehmen() == 1
    assert "Kopfzeile" in capsys.readouterr().err


def test_spaltenvertrag_stammt_aus_dem_matcher() -> None:
    """Der Export erfindet keine eigene Spaltenfolge."""
    assert review.REVIEWED_COLUMNS == artifacts.ASSIGNMENT_COLUMNS + ("review_id",)
    assert set(fullrun_import.COMPARED_FIELDS) <= set(gs.LABEL_COLUMNS)


def test_manifest_nennt_herkunft_und_umfang(mappe, revisionen, capsys) -> None:
    assert uebernehmen() == 0
    capsys.readouterr()
    payload = json.loads((revisionen / REVIEW_ID
                          / fullrun_import.MANIFEST_FILENAME).read_text(
                              encoding="utf-8"))

    assert payload["version"] == fullrun_import.MANIFEST_VERSION
    assert payload["origin"] == "goldset_label.py --fullrun"
    assert payload["source"] == "claude"
    assert payload["unit_count"] == payload["assignment_count"]
    assert payload["review_status"] == review.REVIEW_STATUS_REVIEWED
