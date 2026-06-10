"""Phase-5-Tests: xlsx-Round-Trip/Merge, Konfidenz-Uebersetzung, finalize, promote."""
import csv
import json

import pytest
from openpyxl import load_workbook

from scripts.committee_mapping_agent import config, finalize, review_xlsx
from scripts.committee_mapping_agent.tests.conftest import FakeClient, make_response
from scripts.committee_mapping_agent import taxonomy


def _reviewed_row(mid, conf="high", sector="Energy", industry="Uranium",
                  sub="Energy", comment=""):
    return {
        "mapping_id": mid, "chamber": "House", "committee": "Science",
        "subcommittee": sub, "sector": sector, "industry": industry,
        "rank": 1, "confidence": conf, "ai_comment": "c",
        "grounding_source": "https://x — \"zitat\"", "review_flag": "ok",
        "review_reason": "", "comment": comment,
    }


# ---------------------------------------------------------------------------
# Export + Coloring + Filterung
# ---------------------------------------------------------------------------
def test_export_three_files_and_filtering(tmp_path):
    rows = [
        _reviewed_row("id_high", "high"),
        _reviewed_row("id_low", "low", industry="Thermal Coal"),
        _reviewed_row("id_med", "medium", industry="Solar", sector="Technology"),
    ]
    paths = review_xlsx.export_review(rows, review_dir=str(tmp_path))
    assert all(__import__("os").path.exists(p) for p in paths.values())

    low_wb = load_workbook(paths["low"])
    low_rows = list(low_wb.active.iter_rows(min_row=2, values_only=True))
    assert len(low_rows) == 1
    assert low_rows[0][review_xlsx.ID_COL_IDX - 1] == "id_low"

    med_wb = load_workbook(paths["medium"])
    assert len(list(med_wb.active.iter_rows(min_row=2, values_only=True))) == 1

    all_wb = load_workbook(paths["all"])
    # Konfidenz-Zelle der high-Zeile ist gruen eingefaerbt.
    ws = all_wb.active
    for r in ws.iter_rows(min_row=2):
        conf_cell = r[review_xlsx.CONF_COL_IDX - 1]
        if conf_cell.value == "high":
            assert conf_cell.fill.fgColor.rgb.endswith("C6EFCE")


# ---------------------------------------------------------------------------
# Comment-Reimport (Round-Trip + Merge + Konflikt)
# ---------------------------------------------------------------------------
def test_comment_round_trip(tmp_path):
    rows = [_reviewed_row("id1", "low")]
    paths = review_xlsx.export_review(rows, review_dir=str(tmp_path))
    # User traegt Kommentar in review_all.xlsx ein.
    wb = load_workbook(paths["all"])
    ws = wb.active
    ws.cell(row=2, column=review_xlsx.COMMENT_COL_IDX, value="Konfidenz auf hoch")
    wb.save(paths["all"])

    comments, conflicts = review_xlsx.import_comments(review_dir=str(tmp_path))
    assert comments == {"id1": "Konfidenz auf hoch"}
    assert conflicts == []


def test_comment_conflict_detected(tmp_path):
    rows = [_reviewed_row("id1", "low")]
    paths = review_xlsx.export_review(rows, review_dir=str(tmp_path))
    # Widerspruechliche Kommentare in all vs. low.
    for fn, text in [(paths["all"], "A"), (paths["low"], "B")]:
        wb = load_workbook(fn)
        wb.active.cell(row=2, column=review_xlsx.COMMENT_COL_IDX, value=text)
        wb.save(fn)
    comments, conflicts = review_xlsx.import_comments(review_dir=str(tmp_path))
    assert len(conflicts) == 1
    assert "id1" in conflicts[0]


# ---------------------------------------------------------------------------
# Konfidenz-Uebersetzung + Dedup + CSV
# ---------------------------------------------------------------------------
def test_to_csv_rows_translation_and_dedup():
    final = [
        {"chamber": "House", "committee": "Science", "subcommittee": "Energy",
         "sector": "Energy", "industry": "Uranium", "rank": 2, "confidence": "high"},
        {"chamber": "House", "committee": "Science", "subcommittee": "Energy",
         "sector": "Energy", "industry": "Uranium", "rank": 1, "confidence": "low"},
        {"chamber": "House", "committee": "Science", "subcommittee": "Energy",
         "sector": "Technology", "industry": "Solar", "rank": 1, "confidence": "medium"},
    ]
    csv_rows = finalize._to_csv_rows(final)
    # Dedup: gleiche (..,Uranium) -> niedrigster Rank (1) gewinnt.
    uranium = [r for r in csv_rows if r[4] == "Uranium"]
    assert len(uranium) == 1
    assert uranium[0][5] == 1
    # Deutsche Konfidenz.
    konf = {r[4]: r[6] for r in csv_rows}
    assert konf["Uranium"] == "Niedrig"
    assert konf["Solar"] == "Mittel"


def test_write_staging_header_and_content(tmp_path):
    final = [{"chamber": "House", "committee": "Science", "subcommittee": "Energy",
              "sector": "Energy", "industry": "Uranium", "rank": 1,
              "confidence": "high"}]
    path = finalize.write_staging(final, path=str(tmp_path / "stage.csv"))
    with open(path, encoding="utf-8") as fh:
        reader = list(csv.reader(fh))
    assert reader[0] == config.MASTER_COLUMNS
    assert reader[1] == ["House", "Science", "Energy", "Energy", "Uranium", "1", "Hoch"]


# ---------------------------------------------------------------------------
# finalize_rows: Pass-Through + Kommentar-Anwendung
# ---------------------------------------------------------------------------
def test_finalize_passthrough_without_comment():
    rows = [_reviewed_row("id1", "high")]
    final, log = finalize.finalize_rows(rows, {}, taxonomy.load_source())
    assert len(final) == 1
    assert final[0]["confidence"] == "high"
    assert log == []


def test_finalize_applies_comment_overrides_cap():
    tax = taxonomy.load_source()
    rows = [_reviewed_row("id1", "low")]  # grounding war none -> low
    # User: auf high setzen. Finalize-Agent liefert high; Deckel ist aus.
    resp = make_response(json.dumps({"rows": [
        {"sector": "Energy", "industry": "Uranium", "rank": 1,
         "confidence": "high", "ai_comment": "user wollte high"}]}))
    client = FakeClient([resp])
    final, log = finalize.finalize_rows(
        rows, {"id1": "Konfidenz auf hoch"}, tax, client=client,
    )
    assert len(final) == 1
    assert final[0]["confidence"] == "high"  # NICHT gedeckelt (User-Override)
    assert final[0]["committee"] == "Science"
    assert any("Kommentar angewandt" in m for m in log)


def test_finalize_comment_delete():
    tax = taxonomy.load_source()
    rows = [_reviewed_row("id1", "high")]
    resp = make_response(json.dumps({"rows": []}))  # loeschen
    client = FakeClient([resp])
    final, _ = finalize.finalize_rows(rows, {"id1": "entfernen"}, tax, client=client)
    assert final == []


# ---------------------------------------------------------------------------
# promote: Backup-Verhalten
# ---------------------------------------------------------------------------
def test_promote_backs_up_and_replaces(tmp_path, monkeypatch):
    master = tmp_path / "master.csv"
    staging = tmp_path / "stage.csv"
    master.write_text("ALT\n", encoding="utf-8")
    staging.write_text("NEU\n", encoding="utf-8")
    monkeypatch.setattr(config, "MASTER_CSV", str(master))
    monkeypatch.setattr(config, "STAGING_CSV", str(staging))

    backup = finalize.promote()
    assert backup and __import__("os").path.exists(backup)
    assert master.read_text(encoding="utf-8") == "NEU\n"      # ersetzt
    assert open(backup, encoding="utf-8").read() == "ALT\n"   # alter Inhalt gesichert


def test_promote_no_master_no_backup(tmp_path, monkeypatch):
    master = tmp_path / "master.csv"
    staging = tmp_path / "stage.csv"
    staging.write_text("NEU\n", encoding="utf-8")
    monkeypatch.setattr(config, "MASTER_CSV", str(master))
    monkeypatch.setattr(config, "STAGING_CSV", str(staging))
    backup = finalize.promote()
    assert backup == ""
    assert master.read_text(encoding="utf-8") == "NEU\n"
