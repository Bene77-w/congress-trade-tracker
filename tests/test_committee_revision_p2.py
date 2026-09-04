"""Gate B des Integrationsplans: der Loader der aktiven Mappingrevision.

Geprüft wird beides — dass eine vollständige Revision angenommen wird und
dass jede unvollständige Variante mit einem eindeutigen Code abgewiesen wird.
Ein Loader, der nur den guten Fall kann, verschöbe den Fehler in die Pipeline:
dort fiele ein halbes Mapping als leerer Filter 3 auf, nicht als Fehler.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from committee_matcher import paths, review, taxonomy_snapshot  # noqa: E402
from config import committee_revision as cr  # noqa: E402

REVIEW_ID = "map-testrevision"

#: Industries, auf die sich die Testrevision beruft. Zwei genügen: der Loader
#: prüft die Menge, nicht die Taxonomie.
INDUSTRIES = (
    {"industry_id": "aerospace-defense", "industry_name": "Aerospace & Defense",
     "sector_id": "industrials", "sector_name": "Industrials",
     "description": "Waffensysteme, Militaerluftfahrt."},
    {"industry_id": "farm-products", "industry_name": "Farm Products",
     "sector_id": "consumer-defensive", "sector_name": "Consumer Defensive",
     "description": "Anbau landwirtschaftlicher Erzeugnisse."},
)

SNAPSHOT_ID = "tax-testlauf"


def _assignment(unit_id: str, industry: dict, confidence: str,
                decision: str = "mapped") -> dict:
    """Eine Artefaktzeile im vollen Spaltenvertrag."""
    zeile = {spalte: "" for spalte in review.REVIEWED_COLUMNS}
    zeile.update({
        "run_id": REVIEW_ID, "revision_id": REVIEW_ID, "review_id": REVIEW_ID,
        "unit_id": unit_id, "chamber": "senate",
        "committee_name": "Committee on Armed Services",
        "unit_name": "Committee on Armed Services",
        "unit_kind": "committee_parent_core", "input_basis": "jurisdiction_text",
        "decision": decision, "broad_scope_exception": "false",
        "industry_id": industry["industry_id"],
        "industry_name": industry["industry_name"],
        "sector_id": industry["sector_id"],
        "sector_name": industry["sector_name"],
        "confidence": confidence, "assignment_source": "model",
        "snapshot_id": SNAPSHOT_ID,
    })
    return zeile


def _write_csv(pfad: Path, spalten, zeilen) -> None:
    kopf = ",".join(spalten)
    koerper = "\n".join(
        ",".join('"' + str(zeile[spalte]).replace('"', '""') + '"'
                 for spalte in spalten)
        for zeile in zeilen)
    pfad.write_text(kopf + "\n" + koerper + ("\n" if zeilen else ""),
                    encoding="utf-8")


@pytest.fixture()
def revision(tmp_path, monkeypatch):
    """Eine vollständige, freigegebene Revision in einer eigenen Artefaktwurzel."""
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    taxonomy_snapshot.baseline_path().parent.mkdir(parents=True, exist_ok=True)
    taxonomy_snapshot.baseline_path().write_text(json.dumps({
        "content_version": "taxonomy-content-v1",
        "approved_run_id": SNAPSHOT_ID,
        # Nachgerechnet, nicht gesetzt: die Baseline prueft ihren Hash beim
        # Laden und weist eine von Hand gebaute Datei sonst als manipuliert ab.
        "taxonomy_content_hash": taxonomy_snapshot.hash_records(
            [dict(eintrag) for eintrag in INDUSTRIES]),
        "yfinance_version": "1.3.0",
        "industries": list(INDUSTRIES),
    }), encoding="utf-8")

    ordner = cr.revision_path(REVIEW_ID)
    ordner.mkdir(parents=True, exist_ok=True)
    zeilen = [
        _assignment("senate--armed-services", INDUSTRIES[0], "4"),
        _assignment("senate--agriculture", INDUSTRIES[1], "3"),
    ]
    _write_csv(ordner / review.REVIEWED_ASSIGNMENTS_FILENAME,
               review.REVIEWED_COLUMNS, zeilen)
    (ordner / review.IMPORT_MANIFEST_FILENAME).write_text(json.dumps({
        "version": "fullrun-import-v1", "review_id": REVIEW_ID,
        "run_id": REVIEW_ID, "origin": "submapper.py --fullrun",
        "scope": "fullrun", "source": "codex", "snapshot_id": SNAPSHOT_ID,
        "unit_count": 3, "assignment_count": 2,
        "review_status": review.REVIEW_STATUS_REVIEWED,
    }), encoding="utf-8")
    return ordner


def _manifest_aendern(ordner: Path, **felder) -> None:
    pfad = ordner / review.IMPORT_MANIFEST_FILENAME
    document = json.loads(pfad.read_text(encoding="utf-8"))
    document.update(felder)
    pfad.write_text(json.dumps(document), encoding="utf-8")


def _code(fehler) -> str:
    return fehler.value.code


# -- der gute Fall ---------------------------------------------------------
def test_eine_vollstaendige_revision_wird_angenommen(revision) -> None:
    geladen = cr.load_revision(REVIEW_ID)

    assert geladen.review_id == REVIEW_ID
    assert geladen.unit_count == 3
    assert geladen.scope == cr.SCOPE_FULLRUN
    assert geladen.is_fullrun
    assert geladen.snapshot_id == SNAPSHOT_ID
    assert [(a.unit_id, a.industry_id, a.confidence) for a in geladen.assignments] == [
        ("senate--armed-services", "aerospace-defense", 4),
        ("senate--agriculture", "farm-products", 3),
    ]
    assert geladen.assignments[0].sector_id == "industrials"


def test_die_unitzahl_steht_im_ausweis(revision, caplog) -> None:
    """Ein Lauf soll am Terminal sagen, wie breit sein Mapping ist."""
    with caplog.at_level("INFO"):
        geladen = cr.load_revision(REVIEW_ID)

    assert "3 Units" in geladen.summary
    assert "2 Zuordnungen" in geladen.summary
    assert geladen.summary in caplog.text


def test_zeilen_ohne_zuordnung_zaehlen_nicht_als_zuordnung(revision) -> None:
    """`no_mapping` steht im Deckungsartefakt; hier bleibt es unsichtbar."""
    zeilen = [
        _assignment("senate--armed-services", INDUSTRIES[0], "4"),
        _assignment("senate--agriculture", INDUSTRIES[1], "3"),
        _assignment("house--ohne", {"industry_id": "", "industry_name": "",
                                    "sector_id": "", "sector_name": ""},
                    "", decision="no_mapping"),
    ]
    _write_csv(revision / review.REVIEWED_ASSIGNMENTS_FILENAME,
               review.REVIEWED_COLUMNS, zeilen)
    _manifest_aendern(revision, assignment_count=3)

    assert len(cr.load_revision(REVIEW_ID).assignments) == 2


# -- das Probeartefakt -----------------------------------------------------
def test_ein_probeartefakt_wird_geladen_und_benannt(revision, caplog) -> None:
    """Keine Hartsperre - aber niemand soll es unbemerkt benutzen."""
    _manifest_aendern(revision, scope="test-fullrun", unit_count=5)

    with caplog.at_level("WARNING"):
        geladen = cr.load_revision(REVIEW_ID)

    assert geladen.scope == "test-fullrun"
    assert not geladen.is_fullrun
    assert "Probeartefakt" in caplog.text
    assert "5 Units" in caplog.text


# -- die abgewiesenen Faelle, je mit eigenem Code ---------------------------
def test_ohne_gesetzte_revision_haelt_der_loader_an(revision) -> None:
    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision("")

    assert _code(fehler) == "MAP_REVISION_UNSET"
    assert cr.REVIEW_ID_SETTING in str(fehler.value)


def test_eine_unbekannte_revision_haelt_den_loader_an(revision) -> None:
    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision("gibt-es-nicht")

    assert _code(fehler) == "MAP_REVISION_MISSING"


def test_ein_fehlendes_manifest_haelt_den_loader_an(revision) -> None:
    """Der Abschlussmarker fehlt: die Revision ist unvollstaendig geschrieben."""
    (revision / review.IMPORT_MANIFEST_FILENAME).unlink()

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_MANIFEST_MISSING"


def test_ein_unlesbares_manifest_haelt_den_loader_an(revision) -> None:
    (revision / review.IMPORT_MANIFEST_FILENAME).write_text("{kaputt",
                                                            encoding="utf-8")

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_MANIFEST_INVALID"


def test_eine_offene_revision_haelt_den_loader_an(revision) -> None:
    _manifest_aendern(revision, review_status="open")

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_NOT_REVIEWED"


def test_eine_abweichende_zeilenzahl_haelt_den_loader_an(revision) -> None:
    """Manifest und CSV muessen denselben Stand meinen."""
    _manifest_aendern(revision, assignment_count=9)

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_COUNT_MISMATCH"
    assert "9" in str(fehler.value)


def test_eine_unbekannte_industry_haelt_den_loader_an(revision) -> None:
    """Gegen die Baseline geprueft, nicht gegen den Taxonomiehash."""
    zeilen = [
        _assignment("senate--armed-services", INDUSTRIES[0], "4"),
        _assignment("senate--agriculture",
                    {"industry_id": "gibt-es-nicht", "industry_name": "Fremd",
                     "sector_id": "energy", "sector_name": "Energy"}, "3"),
    ]
    _write_csv(revision / review.REVIEWED_ASSIGNMENTS_FILENAME,
               review.REVIEWED_COLUMNS, zeilen)

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_INDUSTRY_UNKNOWN"
    assert "gibt-es-nicht" in str(fehler.value)


def test_ein_geaenderter_taxonomiehash_weist_die_revision_nicht_ab(
        revision) -> None:
    """Der Refresh aus P1 aendert den Hash bei jedem Lauf.

    Bindet der Loader daran, legte der naechste Refresh Filter 3 lahm. Geprueft
    wird deshalb die Menge der Industries, nicht ihr Hash.
    """
    baseline = json.loads(
        taxonomy_snapshot.baseline_path().read_text(encoding="utf-8"))
    baseline["industries"].append({
        "industry_id": "oil-gas-e-p", "industry_name": "Oil & Gas E&P",
        "sector_id": "energy", "sector_name": "Energy",
        "description": "Exploration und Foerderung."})
    baseline["taxonomy_content_hash"] = taxonomy_snapshot.hash_records(
        baseline["industries"])
    baseline["approved_run_id"] = "tax-spaeter"
    taxonomy_snapshot.baseline_path().write_text(json.dumps(baseline),
                                                 encoding="utf-8")

    geladen = cr.load_revision(REVIEW_ID)

    assert len(geladen.assignments) == 2
    # Der Snapshot der Revision bleibt ihre Provenienz; die Baseline ist eine
    # andere geworden, ohne dass das Mapping ungueltig wuerde.
    assert geladen.snapshot_id == SNAPSHOT_ID


def test_ein_abweichender_spaltenvertrag_haelt_den_loader_an(revision) -> None:
    (revision / review.REVIEWED_ASSIGNMENTS_FILENAME).write_text(
        "unit_id,industry_id\nsenate--armed-services,aerospace-defense\n",
        encoding="utf-8")

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_ARTIFACT_CONTRACT"


def test_eine_unlesbare_konfidenz_haelt_den_loader_an(revision) -> None:
    """Die Konfidenz steuert den Scorebeitrag; sie darf nicht raten lassen."""
    zeilen = [_assignment("senate--armed-services", INDUSTRIES[0], "hoch")]
    _write_csv(revision / review.REVIEWED_ASSIGNMENTS_FILENAME,
               review.REVIEWED_COLUMNS, zeilen)
    _manifest_aendern(revision, assignment_count=1)

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "MAP_CONFIDENCE_INVALID"


def test_ohne_freigegebene_taxonomie_haelt_der_loader_an(revision) -> None:
    taxonomy_snapshot.baseline_path().unlink()

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_revision(REVIEW_ID)

    assert _code(fehler) == "TAX_BASELINE_MISSING"


# -- die Bindung an config/settings.py -------------------------------------
def test_die_aktive_revision_kommt_aus_den_settings(revision) -> None:
    class Settings:
        COMMITTEE_MAPPING_REVIEW_ID = REVIEW_ID

    assert cr.active_review_id(Settings) == REVIEW_ID
    assert cr.load_active_revision(Settings).review_id == REVIEW_ID


def test_ein_leeres_setting_gilt_als_nicht_gesetzt(revision) -> None:
    class Settings:
        COMMITTEE_MAPPING_REVIEW_ID = "   "

    with pytest.raises(cr.RevisionError) as fehler:
        cr.load_active_revision(Settings)

    assert _code(fehler) == "MAP_REVISION_UNSET"
