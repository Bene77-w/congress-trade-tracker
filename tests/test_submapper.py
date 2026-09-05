"""Tests des Gold-Set-Labelwerkzeugs (committee_matcher/submapper.py).

Netzwerk- und CLI-frei: der Trockenlauf ueber ``SUBMAPPER_FAKE_DIR``
ersetzt beide Engines durch vorbereitete Antwortdateien. Geprueft wird die
ganze Kette - Antwort, Sammeln, Reviewdatei, Rueckpruefung - und je Fehlercode
der Fall, der ihn ausloest.

Die Eingaben sind die echten Aufgabenpakete des Repos; ausgetauscht werden nur
die Schreibpfade. Ein eigener Miniaturkorpus wuerde genau die Bindung an
Taxonomie, Vertrag und Korpus nicht pruefen, die das Werkzeug herstellen soll.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import copy
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import submapper as gs  # noqa: E402

SCRIPT_PATH = ROOT / "committee_matcher" / "submapper.py"


# -- Abgrenzung zum Pruefling ---------------------------------------------
def test_keine_verbotenen_importe() -> None:
    """Der Produktivpfad ist der Pruefling und darf nicht mitlaufen."""
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    geladen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            geladen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            geladen.add(node.module)
            geladen.update(f"{node.module}.{alias.name}" for alias in node.names)
    for verboten in gs.FORBIDDEN_MODULES:
        assert verboten not in geladen, f"{verboten} wird importiert"


def _katalog_knoten(tree: ast.Module) -> set:
    """Die Schluessel des Katalogs selbst zaehlen nicht als Benutzung."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "ERROR_CATALOG" and isinstance(node.value, ast.Dict):
            return set(node.value.keys)
    return set()


def test_fehlercodes_stehen_im_katalog() -> None:
    """Jeder benutzte Code ist katalogisiert, jeder katalogisierte benutzt."""
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    katalog = _katalog_knoten(tree)
    benutzt = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and node.value.startswith("GS_") and node not in katalog}
    assert benutzt <= set(gs.ERROR_CATALOG), benutzt - set(gs.ERROR_CATALOG)
    assert set(gs.ERROR_CATALOG) - benutzt == set()


#: Pfade, die ``use_fullrun_paths`` als Modulglobals ersetzt.
UMGESCHALTETE_PFADE = ("TASK_ROOT", "RUN_ROOT", "COLLECT_ROOT", "MERGE_REPORT",
                       "WORKBOOK_PATH", "TEST_SELECTION_PATH")


@pytest.fixture(autouse=True)
def pfade_zuruecksetzen(monkeypatch):
    """Kein Test erbt die Ablage eines anderen.

    ``use_fullrun_paths`` ersetzt Modulglobals; monkeypatch stellt nur wieder
    her, was es kennt. Ohne diese Wache reichte ein einziger Aufruf ohne
    Sandbox - etwa aus einem Test der Bedienfehler - und alle folgenden Tests
    schrieben in das echte Datenverzeichnis.
    """
    for name in UMGESCHALTETE_PFADE:
        monkeypatch.setattr(gs, name, getattr(gs, name))


# -- Trockenlauf -----------------------------------------------------------
@pytest.fixture(scope="module")
def taxonomy():
    return gs.load_taxonomy()


@pytest.fixture(scope="module")
def sample():
    return gs.load_sample()


def build_answer(task: dict, industry_id: str) -> dict:
    """Eine formal einwandfreie Antwort zu einem Aufgabenpaket."""
    quelle = task["binding"].get("evidence_text") or ""
    if task["input_basis"] == "name_only" or not quelle:
        beleg = task["unit_name"]
    else:
        beleg = quelle[:80]
    if gs.evidence_word_count(beleg) < 2:
        beleg = task["unit_name"]
    return {
        "unit_id": task["unit_id"],
        "decision": "mapped",
        "no_mapping_reason": None,
        "broad_scope_exception": False,
        "broad_scope_reason": None,
        "assignments": [{
            "industry_id": industry_id,
            "confidence": 3,
            "evidence_excerpt": beleg,
            "rationale_short": "Die Zustaendigkeit trifft das Kerngeschaeft.",
        }],
        "sources": [{"url": "https://example.org/beleg", "belegt": "Kerngeschaeft"}],
        "label_uncertain": False,
        "uncertainty_note": None,
    }


@pytest.fixture()
def sandbox(tmp_path, monkeypatch, taxonomy, sample):
    """Schreibpfade in ein temporaeres Verzeichnis umlenken.

    Die Aufgabenpakete bleiben die echten: der Trockenlauf soll gegen dieselbe
    Bindung laufen wie der bezahlte Lauf.
    """
    industry = sorted(taxonomy.ids)[0]
    fake = tmp_path / "antworten"
    fake.mkdir()
    for item in sample:
        task = gs.load_task(item.unit_id)
        (fake / f"{item.unit_id}.json").write_text(
            json.dumps(build_answer(task, industry), ensure_ascii=False),
            encoding="utf-8")

    monkeypatch.setenv(gs.FAKE_DIR_ENV, str(fake))
    monkeypatch.setattr(gs, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(gs, "COLLECT_ROOT", tmp_path / "collected")
    monkeypatch.setattr(gs, "MERGE_REPORT", tmp_path / "disagreements.md")
    monkeypatch.setattr(gs, "WORKBOOK_PATH", tmp_path / "goldset_review.xlsx")
    return tmp_path


def lauf(*argv: str) -> int:
    return gs.main(list(argv))


def test_durchstich_bis_zur_reviewdatei(sandbox, sample, capsys) -> None:
    """Trockenlauf, Sammeln, Reviewdatei, Rueckpruefung - ohne Befund."""
    assert lauf("run", "--engine", "claude") == 0
    assert lauf("collect", "--engine", "claude") == 0
    assert lauf("to-xlsx", "--source", "claude") == 0
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 0

    book = load_workbook(gs.WORKBOOK_PATH)
    assert book.sheetnames == [gs.SHEET_LABELS, gs.SHEET_JURISDICTION,
                               gs.SHEET_TAXONOMY, gs.SHEET_NOTES, gs.SHEET_INFO]
    labels = book[gs.SHEET_LABELS]
    assert labels.max_row == len(sample) + 1
    assert [cell.value for cell in labels[1]] == list(gs.LABEL_COLUMNS)
    # Jede gefuellte Zelle ist Text; leere Zellen tragen gar keinen Wert.
    assert all(cell.value in (None, "") or cell.data_type == "s"
               for row in labels.iter_rows() for cell in row)


def test_gleichheitszeichen_bleibt_text(tmp_path) -> None:
    """Der 'packaged-foods'-Nachbarfall: ein Wert mit '=' wird keine Formel."""
    from openpyxl import Workbook

    book = Workbook()
    gs._write_sheet(book.active, ("wert",), [{"wert": "=SUM(1;2)"}])
    pfad = tmp_path / "text.xlsx"
    book.save(pfad)
    zelle = load_workbook(pfad)[book.active.title].cell(row=2, column=1)
    assert zelle.data_type == "s"
    assert zelle.value == "=SUM(1;2)"


def test_recherchezeile_wird_in_der_reviewmappe_markiert() -> None:
    from openpyxl import Workbook

    book = Workbook()
    row = {name: "" for name in gs.LABEL_COLUMNS}
    row.update(unit_id="u-1", industry_id="x", confidence="3",
               evidence_excerpt="Recherche", rationale_short="Belegt.")
    gs._write_sheet(book.active, gs.LABEL_COLUMNS, [row])
    gs._mark_research_rows(book.active, gs.LABEL_COLUMNS)
    assert {cell.fill.fgColor.rgb for cell in book.active[2]} == {"00FFF2CC"}


def test_rechercheauftrag_erlaubt_den_sentinel_statt_ihn_zu_widersprechen() -> None:
    prompt = gs.build_system_prompt("Modellvertrag")
    assert "evidence_excerpt` exakt `Recherche`" in prompt
    assert "Confidence hoechstens 3" in prompt
    assert "ersetzt den Text nicht" not in prompt


def test_wiederaufnahme_ueberspringt_vorhandene(sandbox, capsys) -> None:
    assert lauf("run", "--engine", "claude", "--unit-id",
                "house--energy-and-commerce--energy") == 0
    capsys.readouterr()
    assert lauf("run", "--engine", "claude", "--unit-id",
                "house--energy-and-commerce--energy") == 0
    assert "uebersprungen" in capsys.readouterr().out


def test_merge_meldet_dissens(sandbox, taxonomy, capsys) -> None:
    """Zwei Laeufe mit verschiedener Industry ergeben Dissens."""
    assert lauf("run", "--engine", "claude") == 0
    assert lauf("collect", "--engine", "claude") == 0

    zweite = sorted(taxonomy.ids)[1]
    fake = gs.fake_dir()
    assert fake is not None
    for path in fake.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["assignments"][0]["industry_id"] = zweite
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert lauf("run", "--engine", "codex") == 0
    assert lauf("collect", "--engine", "codex") == 0
    capsys.readouterr()

    # Dissens allein ist das Ergebnis des Vergleichs, kein Befund.
    assert lauf("merge", "--base", "claude", "--against", "codex") == 0
    bericht = gs.MERGE_REPORT.read_text(encoding="utf-8")
    assert "0 von 22 verglichenen Units einig" in bericht
    assert f"nur codex: {zweite}" in bericht

    notizen = gs.notes_path("merged").read_text(encoding="utf-8")
    assert f"nur codex: {zweite}" in notizen

    # Die Provenienz traegt beide Laeufe, nicht nur die Basis.
    provenienz = gs.read_csv(gs.info_path("merged"), gs.INFO_COLUMNS,
                             was="mit der Provenienz von merged")
    assert {row["engine"] for row in provenienz} == {"claude", "codex"}
    assert len(provenienz) == 44


def test_merge_meldet_einseitige_units(sandbox, capsys) -> None:
    """Eine Unit in nur einem Lauf ist ein Befund und kein Dissens."""
    assert lauf("run", "--engine", "claude") == 0
    assert lauf("collect", "--engine", "claude") == 0
    assert lauf("run", "--engine", "codex", "--unit-id",
                "house--energy-and-commerce--energy") == 0
    lauf("collect", "--engine", "codex")
    capsys.readouterr()

    assert lauf("merge", "--base", "claude", "--against", "codex") == 2
    assert "stehen nur in einem der beiden Laeufe" in capsys.readouterr().out


def test_merge_gegen_sich_selbst_blockiert(sandbox) -> None:
    assert lauf("merge", "--base", "claude", "--against", "claude") == 1


def test_collect_ohne_lauf_meldet_fehlende_units(sandbox, capsys) -> None:
    assert lauf("collect", "--engine", "claude") == 2
    assert "GS_UNIT_MISSING" in capsys.readouterr().out


def test_to_xlsx_ohne_sammlung_blockiert(sandbox, capsys) -> None:
    assert lauf("to-xlsx", "--source", "claude") == 1
    assert "GS_LABELS_MISSING" in capsys.readouterr().err


def test_verify_ohne_datei_blockiert(sandbox, capsys) -> None:
    assert lauf("verify", "--source", "claude") == 1
    assert "GS_XLSX_MISSING" in capsys.readouterr().err


def test_trockenlauf_ohne_verzeichnis_blockiert(sandbox, monkeypatch, capsys) -> None:
    monkeypatch.setenv(gs.FAKE_DIR_ENV, str(sandbox / "gibt-es-nicht"))
    assert lauf("run", "--engine", "claude") == 1
    assert "GS_FAKE_MODE" in capsys.readouterr().err


# -- Pruefung einer Antwort ------------------------------------------------
@pytest.fixture()
def paar(taxonomy):
    """Ein Aufgabenpaket samt formal einwandfreier Antwortdatei."""
    task = gs.load_task("house--energy-and-commerce--energy")
    record = {
        "answer_format": gs.ANSWER_FORMAT,
        "unit_id": task["unit_id"],
        "engine": "claude",
        "model": "opus",
        "effort": "high",
        "binding": task["binding"],
        "answer": build_answer(task, sorted(taxonomy.ids)[0]),
        "created_at": "2026-08-28T10:00:00+00:00",
    }
    return task, record


def codes(task, record, taxonomy) -> set[str]:
    return {found.code for found in gs.check_answer(task, record, taxonomy).problems}


def test_saubere_antwort_ohne_befund(paar, taxonomy) -> None:
    task, record = paar
    geprueft = gs.check_answer(task, record, taxonomy)
    assert geprueft.problems == []
    assert len(geprueft.rows) == 1
    assert geprueft.rows[0]["unit_id"] == task["unit_id"]


def test_recherche_ist_als_evidence_mit_confidence_drei_zulaessig(
        paar, taxonomy) -> None:
    task, record = paar
    record["answer"]["assignments"][0].update(
        evidence_excerpt="Recherche", confidence=3)
    assert codes(task, record, taxonomy) == set()


def test_recherche_deckelt_confidence_bei_drei(paar, taxonomy) -> None:
    task, record = paar
    record["answer"]["assignments"][0].update(
        evidence_excerpt="Recherche", confidence=4)
    assert "GS_RESEARCH_CONFIDENCE" in codes(task, record, taxonomy)


def test_recherche_ist_ohne_webzugriff_unzulaessig(paar, taxonomy) -> None:
    task, record = paar
    record["web_access"] = False
    record["answer"]["assignments"][0].update(
        evidence_excerpt="Recherche", confidence=3)
    assert "GS_RESEARCH_WITHOUT_WEB" in codes(task, record, taxonomy)


def test_einwort_evidence_wird_abgelehnt(paar, taxonomy) -> None:
    task, record = paar
    record["answer"]["assignments"][0]["evidence_excerpt"] = "jurisdiction"
    assert "GS_EVIDENCE_TOO_SHORT" in codes(task, record, taxonomy)


def test_recherche_nimmt_nicht_an_broad_scope_teil(paar, taxonomy) -> None:
    task, record = paar
    breit(record, taxonomy, gs.REGULAR_ASSIGNMENT_LIMIT + 1)
    record["answer"]["assignments"][0]["evidence_excerpt"] = "Recherche"
    assert "GS_BROAD_SCOPE_INVALID" in codes(task, record, taxonomy)


@pytest.mark.parametrize("aenderung, erwartet", [
    (lambda a: a.update(unit_id="senate--finance"), "GS_UNIT_ID_MISMATCH"),
    (lambda a: a["assignments"][0].update(industry_id="gibt-es-nicht"),
     "GS_INDUSTRY_UNKNOWN"),
    (lambda a: a["assignments"].append(copy.deepcopy(a["assignments"][0])),
     "GS_UNIT_DUPLICATE"),
    (lambda a: a["assignments"][0].update(evidence_excerpt="frei erfunden"),
     "GS_EVIDENCE_NOT_VERBATIM"),
    (lambda a: a["assignments"][0].update(confidence=9), "GS_SCHEMA_INVALID"),
    (lambda a: a.update(no_mapping_reason="keine Beruehrung"),
     "GS_DECISION_CONFLICT"),
    (lambda a: a.update(sources=[]), "GS_WEBSEARCH_OFF"),
    (lambda a: a.update(decision="unbekannt"), "GS_SCHEMA_INVALID"),
])
def test_befund_je_fall(paar, taxonomy, aenderung, erwartet) -> None:
    task, record = paar
    aenderung(record["answer"])
    assert erwartet in codes(task, record, taxonomy)


def breit(record, taxonomy, anzahl, **abweichung) -> None:
    """Setzt eine Antwort mit ``anzahl`` vertragsgemaessen Zuordnungen."""
    vorlage = record["answer"]["assignments"][0]
    record["answer"]["broad_scope_exception"] = True
    record["answer"]["broad_scope_reason"] = "Die Zustaendigkeit ist breit."
    beleg = vorlage["evidence_excerpt"]
    record["answer"]["assignments"] = [
        {**vorlage, "industry_id": name, "confidence": 3,
         "rationale_short": f"Grund {nr}",
         # Die Ausnahme verlangt einen eigenen Beleg je Industry; die
         # Ausschnitte kommen aus demselben Zustaendigkeitstext.
         "evidence_excerpt": beleg[: len(beleg) - nr + 1]}
        for nr, name in enumerate(sorted(taxonomy.ids)[:anzahl], start=1)]
    record["answer"].update(abweichung)


def test_zu_viele_zuordnungen(paar, taxonomy) -> None:
    """Ueber der Obergrenze der Unit; hier greift die Anzahlregel."""
    task, record = paar
    breit(record, taxonomy, gs.BROAD_SCOPE_ASSIGNMENT_LIMIT + 1)
    assert "GS_COUNT_EXCEEDED" in codes(task, record, taxonomy)


def test_breite_zustaendigkeit_erlaubt_mehr(paar, taxonomy) -> None:
    task, record = paar
    breit(record, taxonomy, gs.BROAD_SCOPE_ASSIGNMENT_LIMIT)
    assert codes(task, record, taxonomy) == set()


@pytest.mark.parametrize("abweichung", [
    {"broad_scope_exception": False},
    {"broad_scope_reason": None},
    {"broad_scope_reason": "  \u200b "},
])
def test_broad_scope_bedingungen(paar, taxonomy, abweichung) -> None:
    """Ausnahme ohne gesetztes Feld oder ohne sichtbare Begruendung."""
    task, record = paar
    breit(record, taxonomy, gs.REGULAR_ASSIGNMENT_LIMIT + 1, **abweichung)
    assert "GS_BROAD_SCOPE_INVALID" in codes(task, record, taxonomy)


def test_broad_scope_verlangt_hohe_confidence(paar, taxonomy) -> None:
    task, record = paar
    breit(record, taxonomy, gs.REGULAR_ASSIGNMENT_LIMIT + 1)
    record["answer"]["assignments"][0]["confidence"] = \
        gs.BROAD_SCOPE_MIN_CONFIDENCE - 1
    assert "GS_BROAD_SCOPE_INVALID" in codes(task, record, taxonomy)


@pytest.mark.parametrize("feld", ["evidence_excerpt", "rationale_short"])
def test_broad_scope_verlangt_eigene_begruendung(paar, taxonomy, feld) -> None:
    """Ein wiederholter Beleg oder Grund traegt die Ausnahme nicht."""
    task, record = paar
    breit(record, taxonomy, gs.REGULAR_ASSIGNMENT_LIMIT + 1)
    erste = record["answer"]["assignments"][0][feld]
    record["answer"]["assignments"][1][feld] = f"  {erste.upper()} "
    assert "GS_BROAD_SCOPE_INVALID" in codes(task, record, taxonomy)


def test_broad_scope_nicht_bei_no_mapping(paar, taxonomy) -> None:
    task, record = paar
    record["answer"].update(
        decision="no_mapping", assignments=[], broad_scope_exception=True,
        broad_scope_reason="Die Zustaendigkeit ist breit.",
        no_mapping_reason="Keine Beruehrung zu einer Industry.")
    assert "GS_BROAD_SCOPE_INVALID" in codes(task, record, taxonomy)


def test_no_mapping_reason_ohne_sichtbaren_text(paar, taxonomy) -> None:
    task, record = paar
    record["answer"].update(decision="no_mapping", assignments=[],
                            no_mapping_reason="\u200b\u200b")
    assert "GS_DECISION_CONFLICT" in codes(task, record, taxonomy)


@pytest.mark.parametrize("unit_id", [
    "senate--finance--health-care",
    "senate--homeland-security-and-governmental-affairs--"
    "border-management-federal-workforce-and-regulatory-affairs",
])
def test_name_only_kennt_keine_ausnahme(taxonomy, unit_id) -> None:
    """Ohne Zustaendigkeitstext bleibt es bei zwei Zuordnungen."""
    task = gs.load_task(unit_id)
    record = {
        "answer_format": gs.ANSWER_FORMAT, "unit_id": unit_id,
        "engine": "claude", "model": "opus", "effort": "high",
        "binding": task["binding"],
        "answer": build_answer(task, sorted(taxonomy.ids)[0]),
        "created_at": "2026-08-28T10:00:00+00:00",
    }
    assert gs.assignment_limit(task) == gs.REGULAR_ASSIGNMENT_LIMIT
    breit(record, taxonomy, gs.REGULAR_ASSIGNMENT_LIMIT + 1)
    name = task["unit_name"]
    for nr, zuordnung in enumerate(record["answer"]["assignments"]):
        zuordnung["evidence_excerpt"] = name[: len(name) - nr]
    gefunden = codes(task, record, taxonomy)
    assert "GS_COUNT_EXCEEDED" in gefunden
    assert "GS_BROAD_SCOPE_INVALID" in gefunden


def test_kernbereich_kennt_keine_ausnahme() -> None:
    task = {"unit_kind": "committee_parent_core", "input_basis": "jurisdiction_text"}
    assert gs.assignment_limit(task) == gs.REGULAR_ASSIGNMENT_LIMIT


@pytest.mark.parametrize("wert", ["", "ja", 1])
def test_broad_scope_muss_ein_wahrheitswert_sein(paar, taxonomy, wert) -> None:
    """``bool("")`` machte aus einem Textfeld klaglos ein Nein."""
    task, record = paar
    breit(record, taxonomy, gs.REGULAR_ASSIGNMENT_LIMIT + 1)
    record["answer"]["broad_scope_exception"] = wert
    assert "GS_SCHEMA_INVALID" in codes(task, record, taxonomy)


@pytest.mark.parametrize("feld", ["evidence_excerpt", "rationale_short"])
def test_pflichtfeld_aus_unsichtbaren_zeichen(paar, taxonomy, feld) -> None:
    """Zero-Width-Spaces sind kein Leerraum und bestuenden ein strip()."""
    task, record = paar
    record["answer"]["assignments"][0][feld] = "\u200b\u200b"
    assert "GS_SCHEMA_INVALID" in codes(task, record, taxonomy)


@pytest.mark.parametrize("wert", ["", False])
def test_mapped_duldet_keinen_no_mapping_reason(paar, taxonomy, wert) -> None:
    """Der Vertrag lehnt jeden Wert ausser ``None`` ab, auch den leeren."""
    task, record = paar
    record["answer"]["no_mapping_reason"] = wert
    assert "GS_DECISION_CONFLICT" in codes(task, record, taxonomy)


@pytest.mark.parametrize("feld", ["industry_id", "evidence_excerpt",
                                  "rationale_short"])
@pytest.mark.parametrize("wert", [None, 1])
def test_textfelder_muessen_text_sein(paar, taxonomy, feld, wert) -> None:
    """``str(None)`` ergaebe den sichtbaren Text 'None' und bestuende sonst."""
    task, record = paar
    record["answer"]["assignments"][0][feld] = wert
    assert "GS_SCHEMA_INVALID" in codes(task, record, taxonomy)


def test_typfehler_der_industry_id_meldet_nicht_die_folge(paar, taxonomy) -> None:
    """Gemeldet gehoert der Typfehler, nicht die daraus folgende leere ID."""
    task, record = paar
    record["answer"]["assignments"][0]["industry_id"] = None
    geprueft = gs.check_answer(task, record, taxonomy)
    assert {found.code for found in geprueft.problems} == {"GS_SCHEMA_INVALID"}
    # Eine Zeile ohne brauchbare ID gehoert nicht in die Zwischentabelle.
    assert geprueft.rows == []


def test_typfehler_verdeckt_die_uebrigen_felder_nicht(paar, taxonomy) -> None:
    """Confidence und Beleg haengen nicht an der ID und werden weiter geprueft.

    Der Sammelbericht soll die Unit in einem Durchgang vollstaendig zeigen.
    """
    task, record = paar
    record["answer"]["assignments"][0].update(
        industry_id=None, confidence=9, evidence_excerpt="frei erfunden")
    befunde = gs.check_answer(task, record, taxonomy).problems
    texte = " ".join(found.was for found in befunde)
    assert "industry_id=None ist kein Text" in texte
    assert "confidence=9" in texte
    assert "GS_EVIDENCE_NOT_VERBATIM" in {found.code for found in befunde}

    # Auch das leere Pflichtfeld faellt neben dem Typfehler noch auf.
    record["answer"]["assignments"][0]["rationale_short"] = "  "
    texte = " ".join(found.was
                     for found in gs.check_answer(task, record, taxonomy).problems)
    assert "industry_id=None ist kein Text" in texte
    assert "leeres Pflichtfeld" in texte


@pytest.mark.parametrize("feld", ["no_mapping_reason", "broad_scope_reason"])
def test_begruendungsfelder_sind_text_oder_leer(paar, taxonomy, feld) -> None:
    """``False`` waere falsy und ergaebe stillschweigend 'keine Begruendung'."""
    task, record = paar
    record["answer"][feld] = False
    assert "GS_SCHEMA_INVALID" in codes(task, record, taxonomy)


def test_no_mapping_ist_eine_zeile(paar, taxonomy) -> None:
    task, record = paar
    record["answer"].update(decision="no_mapping", assignments=[],
                            no_mapping_reason="Keine Beruehrung zu einer Industry.")
    geprueft = gs.check_answer(task, record, taxonomy)
    assert geprueft.problems == []
    assert len(geprueft.rows) == 1
    assert geprueft.rows[0]["industry_id"] == ""
    assert geprueft.rows[0]["no_mapping_reason"]


def test_name_only_belegt_ueber_den_namen(taxonomy) -> None:
    """Eine Unit ohne Zustaendigkeitstext darf nur ihren Namen belegen."""
    task = gs.load_task("senate--finance--health-care")
    assert task["input_basis"] == "name_only"
    assert gs.evidence_supported(task, task["unit_name"])
    assert not gs.evidence_supported(task, "Handel mit Waffen")


def test_parenttext_belegt_nicht() -> None:
    """Der Text des Parents ist Kontext, nie Beleg."""
    kind = gs.load_task("house--energy-and-commerce--health")
    eltern = gs.load_task("house--energy-and-commerce--energy")
    eigen = (kind["binding"]["evidence_text"] or "").lower()
    # Ein Ausschnitt, den nur der Parent traegt; ohne ihn liefe der Test leer
    # durch und behauptete eine Wache, die er nie geprueft hat.
    fremd = next(
        (stueck for start in range(0, 400, 20)
         if len(stueck := (eltern["binding"]["evidence_text"] or "")[start:start + 60]) == 60
         and stueck.lower() not in eigen),
        None)
    assert fremd, "kein parentfremder Ausschnitt gefunden"
    assert not gs.evidence_supported(kind, fremd)


# -- Rueckpruefung der Reviewdatei ----------------------------------------
def workbook_vorbereiten(sandbox) -> Path:
    assert lauf("run", "--engine", "claude") == 0
    assert lauf("collect", "--engine", "claude") == 0
    assert lauf("to-xlsx", "--source", "claude") == 0
    return gs.WORKBOOK_PATH


def zelle_setzen(pfad: Path, zeile: int, spalte: str, wert: str) -> None:
    book = load_workbook(pfad)
    sheet = book[gs.SHEET_LABELS]
    sheet.cell(row=zeile, column=gs.LABEL_COLUMNS.index(spalte) + 1, value=wert)
    book.save(pfad)


def test_verify_meldet_autokorrektur_und_repariert(sandbox, taxonomy, capsys) -> None:
    """Der 'packaged-foods'-Fall: nur die Schreibweise weicht ab."""
    pfad = workbook_vorbereiten(sandbox)
    echt = sorted(taxonomy.ids)[0]
    zelle_setzen(pfad, 2, "industry_id", echt.capitalize())
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 2
    assert "GS_INDUSTRY_CASE" in capsys.readouterr().out

    assert lauf("verify", "--source", "claude", "--fix-case") == 0
    book = load_workbook(pfad)
    assert book[gs.SHEET_LABELS].cell(row=2, column=2).value == echt


def test_verify_meldet_unbekannte_industry(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "industry_id", "gibt-es-nicht")
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 2
    assert "GS_INDUSTRY_UNKNOWN" in capsys.readouterr().out


def test_verify_meldet_zu_hohe_recherche_confidence(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "evidence_excerpt", "Recherche")
    zelle_setzen(pfad, 2, "confidence", "4")
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 2
    assert "GS_RESEARCH_CONFIDENCE" in capsys.readouterr().out


def test_verify_meldet_formelzelle(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "rationale_short", "=SUM(1;2)")
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 2
    assert "GS_XLSX_FORMULA" in capsys.readouterr().out


def test_verify_meldet_fehlende_unit(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    book[gs.SHEET_LABELS].delete_rows(2)
    book.save(pfad)
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 2
    assert "GS_UNIT_MISSING" in capsys.readouterr().out


def test_verify_meldet_widerspruch(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "no_mapping_reason", "keine Beruehrung")
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 2
    assert "GS_DECISION_CONFLICT" in capsys.readouterr().out


def test_verify_meldet_fremdes_blattbild(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    del book[gs.SHEET_INFO]
    book.save(pfad)
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 1
    assert "GS_XLSX_LAYOUT" in capsys.readouterr().err


def test_verify_meldet_fremde_spalte(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    book[gs.SHEET_LABELS].cell(row=1, column=2, value="industry")
    book.save(pfad)
    capsys.readouterr()
    assert lauf("verify", "--source", "claude") == 1
    assert "GS_XLSX_LAYOUT" in capsys.readouterr().err


def test_verify_meldet_randleerraum_und_repariert(sandbox, taxonomy, capsys) -> None:
    """Ein eingefuegtes Leerzeichen ist im Blatt unsichtbar, aber toedlich."""
    pfad = workbook_vorbereiten(sandbox)
    echt = sorted(taxonomy.ids)[0]
    zelle_setzen(pfad, 2, "industry_id", f" {echt}")
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 2
    assert "GS_XLSX_WHITESPACE" in capsys.readouterr().out

    assert lauf("verify", "--source", "claude", "--fix-case") == 0
    assert load_workbook(pfad)[gs.SHEET_LABELS].cell(row=2, column=2).value == echt


def test_verify_bilanziert_gegen_die_csv(sandbox, capsys) -> None:
    """Der Rueckvergleich zeigt Aenderungen an Feldern ohne eigene Regel."""
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "rationale_short", "Von Bene ueberarbeitet.")
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 0
    ausgabe = capsys.readouterr().out
    assert "Aenderungsbilanz" in ausgabe
    assert "rationale_short" in ausgabe
    assert "Von Bene ueberarbeitet." in ausgabe


def test_verify_bilanziert_entfernte_und_neue_zeilen(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    sheet = book[gs.SHEET_LABELS]
    entfernt = sheet.cell(row=2, column=1).value
    sheet.delete_rows(2)
    book.save(pfad)
    capsys.readouterr()

    lauf("verify", "--source", "claude")
    ausgabe = capsys.readouterr().out
    assert f"entfernt: {entfernt}" in ausgabe


def test_verify_sichert_vor_dem_zurueckschreiben(sandbox, taxonomy) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "industry_id", sorted(taxonomy.ids)[0].capitalize())
    assert lauf("verify", "--source", "claude", "--fix-case") == 0
    assert list(pfad.parent.glob(f"{pfad.stem}.*.bak.xlsx"))


def test_verify_weigert_sich_bei_sperrvermerk(sandbox, taxonomy, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "industry_id", sorted(taxonomy.ids)[0].capitalize())
    gs.lock_file(pfad).write_text("offen", encoding="utf-8")
    capsys.readouterr()
    assert lauf("verify", "--source", "claude", "--fix-case") == 1
    assert "GS_XLSX_LOCKED" in capsys.readouterr().err


def test_verify_prueft_broad_scope_ohne_zuordnung(sandbox, capsys) -> None:
    """Eine no_mapping-Unit steht in keiner Zuordnungsliste - und genau dort
    ist die Ausnahme unzulaessig."""
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    labels = book[gs.SHEET_LABELS]
    unit_id = labels.cell(row=2, column=1).value
    labels.cell(row=2, column=2, value="")
    labels.cell(row=2, column=3, value="")
    labels.cell(row=2, column=4, value="")
    labels.cell(row=2, column=5, value="")
    labels.cell(row=2, column=6, value="Keine Beruehrung zu einer Industry.")
    notes = book[gs.SHEET_NOTES]
    spalte = gs.NOTE_COLUMNS.index("broad_scope") + 1
    grund = gs.NOTE_COLUMNS.index("broad_scope_reason") + 1
    for zeile in range(2, notes.max_row + 1):
        if notes.cell(row=zeile, column=1).value == unit_id:
            notes.cell(row=zeile, column=spalte, value="ja")
            notes.cell(row=zeile, column=grund, value="Die Zustaendigkeit ist breit.")
    book.save(pfad)
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 2
    ausgabe = capsys.readouterr().out
    assert "GS_BROAD_SCOPE_INVALID" in ausgabe
    assert unit_id in ausgabe


def test_verify_bilanziert_dubletten_einzeln(sandbox, capsys) -> None:
    """Zwei gleiche Zeilen duerfen sich in der Bilanz nicht ueberschreiben."""
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    labels = book[gs.SHEET_LABELS]
    werte = [labels.cell(row=2, column=nr).value
             for nr in range(1, len(gs.LABEL_COLUMNS) + 1)]
    labels.append(werte)
    book.save(pfad)
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 2
    ausgabe = capsys.readouterr().out
    assert "GS_UNIT_DUPLICATE" in ausgabe
    # Die zweite Zeile ist neu, keine Aenderung der ersten.
    assert "neu:      " in ausgabe
    assert "geaendert" not in ausgabe


def test_verify_meldet_zwei_no_mapping_zeilen(sandbox, capsys) -> None:
    """Zwei Begruendungen liessen offen, welche das Gold-Set behauptet."""
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    labels = book[gs.SHEET_LABELS]
    unit_id = labels.cell(row=2, column=1).value
    for nr, grund in enumerate(("Keine Beruehrung.", "Doch eine andere."), start=2):
        labels.cell(row=nr, column=1, value=unit_id)
        for spalte in (2, 3, 4, 5):
            labels.cell(row=nr, column=spalte, value="")
        labels.cell(row=nr, column=6, value=grund)
    book.save(pfad)
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 2
    ausgabe = capsys.readouterr().out
    assert "GS_UNIT_DUPLICATE" in ausgabe
    assert "no_mapping-Zeile" in ausgabe


def test_bilanz_paart_gleiche_zeilen_vor_der_position(sandbox, capsys) -> None:
    """Umsortierte Dubletten sind keine Aenderung."""
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    labels = book[gs.SHEET_LABELS]
    werte = [labels.cell(row=2, column=nr).value
             for nr in range(1, len(gs.LABEL_COLUMNS) + 1)]
    # Dieselbe Zuordnung ein zweites Mal, mit anderer Begruendung.
    zweite = list(werte)
    zweite[4] = "Eine andere Begruendung."
    labels.append(zweite)
    book.save(pfad)
    capsys.readouterr()
    lauf("verify", "--source", "claude")
    zuerst = capsys.readouterr().out

    # Reihenfolge tauschen: die Bilanz darf sich dadurch nicht aendern.
    book = load_workbook(pfad)
    labels = book[gs.SHEET_LABELS]
    letzte = labels.max_row
    for spalte in range(1, len(gs.LABEL_COLUMNS) + 1):
        labels.cell(row=2, column=spalte, value=zweite[spalte - 1])
        labels.cell(row=letzte, column=spalte, value=werte[spalte - 1])
    book.save(pfad)
    capsys.readouterr()
    lauf("verify", "--source", "claude")
    danach = capsys.readouterr().out

    def bilanz(text: str) -> list[str]:
        return sorted(zeile.strip() for zeile in text.splitlines()
                      if zeile.strip().startswith(("neu:", "entfernt:", "geaendert")))

    assert bilanz(zuerst) == bilanz(danach)
    assert any("neu:" in zeile for zeile in bilanz(zuerst))


# -- Ueberschreibschutz ----------------------------------------------------
def test_to_xlsx_blockiert_vorhandene_datei(sandbox, capsys) -> None:
    """Die Reviewdatei traegt die Abnahmearbeit und darf nicht stillschweigend
    durch die unbearbeitete Fassung ersetzt werden."""
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "rationale_short", "Abnahme durch Bene.")
    capsys.readouterr()

    assert lauf("to-xlsx", "--source", "claude") == 1
    assert "GS_XLSX_OVERWRITE" in capsys.readouterr().err
    zelle = load_workbook(pfad)[gs.SHEET_LABELS].cell(row=2, column=5)
    assert zelle.value == "Abnahme durch Bene."


def test_to_xlsx_force_sichert_vorher(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "rationale_short", "Abnahme durch Bene.")
    capsys.readouterr()

    assert lauf("to-xlsx", "--source", "claude", "--force") == 0
    sicherungen = list(pfad.parent.glob(f"{pfad.stem}.*.bak.xlsx"))
    assert len(sicherungen) == 1
    gesichert = load_workbook(sicherungen[0])[gs.SHEET_LABELS]
    assert gesichert.cell(row=2, column=5).value == "Abnahme durch Bene."
    assert load_workbook(pfad)[gs.SHEET_LABELS].cell(row=2, column=5).value \
        != "Abnahme durch Bene."


def test_to_xlsx_ohne_source_nimmt_merged(sandbox, capsys) -> None:
    """Der Default der Quelle ist 'merged'; ohne Merge fehlt die Tabelle."""
    assert lauf("run", "--engine", "claude") == 0
    assert lauf("collect", "--engine", "claude") == 0
    capsys.readouterr()
    assert lauf("to-xlsx") == 1
    fehler = capsys.readouterr().err
    assert "GS_LABELS_MISSING" in fehler
    assert "labels_merged.csv" in fehler


def test_to_xlsx_ohne_source_nach_dem_merge(sandbox, taxonomy, capsys) -> None:
    """Nach dem Merge greift derselbe Default und erzeugt die Mappe."""
    for engine in ("claude", "codex"):
        assert lauf("run", "--engine", engine) == 0
        assert lauf("collect", "--engine", engine) == 0
    assert lauf("merge", "--base", "claude", "--against", "codex") == 0
    capsys.readouterr()

    assert lauf("to-xlsx") == 0
    assert gs.WORKBOOK_PATH.exists()
    assert lauf("verify") == 0


# -- Zwischentabellen ------------------------------------------------------
def test_csv_mit_fremder_kopfzeile_blockiert(sandbox, capsys) -> None:
    workbook_vorbereiten(sandbox)
    pfad = gs.labels_path("claude")
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    zeilen[0] = '"unit","industry_id","confidence","evidence_excerpt","rationale_short","no_mapping_reason"'
    pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    capsys.readouterr()
    assert lauf("to-xlsx", "--source", "claude") == 1
    assert "GS_LABELS_FORMAT" in capsys.readouterr().err


def test_fremde_antwortfassung(sandbox, capsys) -> None:
    assert lauf("run", "--engine", "claude", "--unit-id", "house--energy-and-commerce--energy") == 0
    pfad = gs.answer_path("claude", "house--energy-and-commerce--energy")
    payload = json.loads(pfad.read_text(encoding="utf-8"))
    payload["answer_format"] = "goldset-answer-v0"
    pfad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert lauf("collect", "--engine", "claude") == 2
    assert "GS_ANSWER_FORMAT" in capsys.readouterr().out


# -- Fehlerklassifikation und Laufwache -----------------------------------
def ergebnis(**felder) -> gs.EngineResult:
    grund = dict(answer=None, exit_code=1, duration_s=1.0, timed_out=False,
                 stdout="", stderr="")
    grund.update(felder)
    return gs.EngineResult(**grund)


def config(engine: str = "claude") -> gs.EngineConfig:
    return gs.EngineConfig(engine=engine, model="opus", effort="high",
                           timeout_seconds=1800, total_timeout_seconds=43200)


def test_status_schlaegt_textmerkmal(capsys) -> None:
    """Mehrere Merkmale zugleich: der strukturierte Status entscheidet."""
    result = ergebnis(stderr='unknown model foo; rate limit reached',
                      stdout='[{"type":"result","is_error":true,'
                             '"api_error_status":401,"result":"kaputt"}]')
    found = gs.classify_failure("claude", result, config())
    assert found.code == "GS_CLI_UNAUTHORIZED"
    assert "GS_MODEL_UNKNOWN" in found.abhilfe  # Mehrfachtreffer wird genannt


def test_status_schlaegt_die_textmarke(capsys) -> None:
    """Der HTTP-Status entscheidet, auch wenn eine Textmarke schon traf.

    Der gemeldete Fall: 429 zusammen mit 'rate limit' und 'unexpected
    argument' ergab frueher GS_CLI_FLAG, weil der Statuscode nicht nach vorn
    geholt wurde, wenn ihn eine Marke bereits gefunden hatte.
    """
    # Der Status steht strukturiert im Abschlussereignis, die beiden
    # Textmerkmale daneben im stderr.
    found = gs.classify_failure("claude", ergebnis(
        stderr="unexpected argument --effort\nrate limit reached",
        stdout='{"type":"result","is_error":true,"api_error_status":429}',
    ), config())
    assert found.code == "GS_RATE_LIMITED"
    # Die uebrigen Merkmale gehen nicht verloren, sie stehen nur hinten - und
    # der gewaehlte Code erscheint dort nicht doppelt.
    assert "GS_CLI_FLAG" in found.abhilfe
    assert "GS_RATE_LIMITED" not in found.abhilfe


def test_unbekannte_ursache_ist_kein_schemafehler() -> None:
    found = gs.classify_failure("claude", ergebnis(stderr="segfault"), config())
    assert found.code == "GS_RUN_FAILED"


def test_mehrdeutige_antwort_wird_erkannt() -> None:
    """Erkannt wird sie am Feld, nicht an einer Textmarke im Protokoll."""
    found = gs.classify_failure(
        "codex", ergebnis(stderr="die Abschlussdatei enthaelt 2", ambiguous=True),
        config("codex"))
    assert found.code == "GS_ANSWER_AMBIGUOUS"


def test_timeout_ausgabe_kommt_als_text(tmp_path) -> None:
    """Teilausgabe eines abgebrochenen Laufs darf keine Bytes sein."""
    result = gs._spawn(
        [sys.executable, "-c",
         "import sys, time; print('teilausgabe'); sys.stdout.flush(); time.sleep(5)"],
        cwd=tmp_path, timeout=1)
    assert result.timed_out
    assert isinstance(result.stdout, str)
    assert "teilausgabe" in result.stdout
    # Die Effortwache liest stderr - im Timeoutfall darf sie nicht scheitern.
    assert gs.effort_fallback(result) is None


def test_effortwache_bricht_bei_verworfener_stufe(capsys) -> None:
    result = ergebnis(stderr="Warning: Unknown --effort value 'ultra' - "
                             "ignoring it and using the default effort.")
    with pytest.raises(gs.GoldsetBlocked) as blocked:
        gs.assert_effort_accepted(result, config())
    assert blocked.value.problem.code == "GS_EFFORT_REJECTED"


@pytest.mark.parametrize("engine, felder, erwartet", [
    ("codex", dict(stdout='{"reasoning_effort":"high"}'), "bestaetigt"),
    ("claude", dict(stdout='{"effort": "high"}'), "bestaetigt"),
    ("claude", dict(stdout="{}"), "ohne-warnung"),
    ("codex", dict(stdout="{}"), "unbestaetigt"),
    ("claude", dict(stderr="Warning: Unknown --effort value 'high'"), "verworfen"),
])
def test_effort_bestaetigung_kommt_aus_dem_protokoll(engine, felder, erwartet) -> None:
    """Die Belastbarkeit haengt am Lauf, nicht am Enginenamen."""
    assert gs.effort_confirmation(ergebnis(**felder), config(engine)) == erwartet


# -- Form der Antwort ------------------------------------------------------
@pytest.mark.parametrize("aenderung, merkmal", [
    (lambda a: a.update(decision="mapped", assignments=[]), "ohne eine einzige"),
    (lambda a: a.update(decision="quatsch"), "decision="),
    (lambda a: a.pop("sources"), "Pflichtfelder fehlen"),
    (lambda a: a.update(unit_id="fremd"), "unit_id="),
    (lambda a: a.update(assignments={}), "keine Liste"),
])
def test_antwortform_wird_lokal_geprueft(paar, aenderung, merkmal) -> None:
    task, record = paar
    aenderung(record["answer"])
    grund = gs.answer_shape_problem(record["answer"], task)
    assert grund is not None and merkmal in grund


def test_saubere_antwortform_passiert(paar) -> None:
    task, record = paar
    assert gs.answer_shape_problem(record["answer"], task) is None


# -- Wiederaufnahme --------------------------------------------------------
def test_resume_meldet_fremdes_modell(sandbox, capsys) -> None:
    unit = "house--energy-and-commerce--energy"
    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 0
    pfad = gs.answer_path("claude", unit)
    payload = json.loads(pfad.read_text(encoding="utf-8"))
    payload["model"] = "ein-anderes-modell"
    pfad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 2
    ausgabe = capsys.readouterr().out
    assert "GS_RESUME_MISMATCH" in ausgabe
    assert "--force" in ausgabe


@pytest.mark.parametrize("feld", gs.RESUME_BINDING_FIELDS)
def test_resume_prueft_jede_bindung(sandbox, capsys, feld) -> None:
    """Auch ein geaenderter Systemprompt darf nicht uebersprungen werden."""
    unit = "house--energy-and-commerce--energy"
    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 0
    pfad = gs.answer_path("claude", unit)
    payload = json.loads(pfad.read_text(encoding="utf-8"))
    payload["binding"][feld] = "abweichender-hash"
    pfad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 2
    ausgabe = capsys.readouterr().out
    assert "GS_RESUME_MISMATCH" in ausgabe
    assert feld in ausgabe


def test_resume_meldet_kaputte_antwortdatei(sandbox, capsys) -> None:
    unit = "house--energy-and-commerce--energy"
    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 0
    gs.answer_path("claude", unit).write_text("{kaputt", encoding="utf-8")
    capsys.readouterr()
    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 2
    assert "GS_RESUME_MISMATCH" in capsys.readouterr().out


def test_lauf_bricht_bei_systemischem_fehler_ab(sandbox, monkeypatch, capsys) -> None:
    """Ein unbekanntes Modell trifft jeden Aufruf - der Satz bricht ab."""
    def scheitert(task, config, workdir):
        return ergebnis(stderr="[claude-code:unrecognized_model]")

    monkeypatch.setattr(gs, "engine_runner", lambda engine: scheitert)
    assert lauf("run", "--engine", "claude") == 2
    ausgabe = capsys.readouterr().out
    assert "GS_MODEL_UNKNOWN" in ausgabe
    assert "betrifft den ganzen Satz" in ausgabe
    assert ausgabe.count("laeuft ...") == 1


def test_formfehler_wird_nicht_gespeichert(sandbox, monkeypatch, capsys) -> None:
    def leer(task, config, workdir):
        return ergebnis(exit_code=0, answer={
            "unit_id": task["unit_id"], "decision": "mapped", "assignments": [],
            "sources": [], "no_mapping_reason": None,
            "broad_scope_exception": False, "broad_scope_reason": None,
            "label_uncertain": False, "uncertainty_note": None})

    monkeypatch.setattr(gs, "engine_runner", lambda engine: leer)
    unit = "house--energy-and-commerce--energy"
    assert lauf("run", "--engine", "claude", "--unit-id", unit) == 2
    assert "GS_SCHEMA_INVALID" in capsys.readouterr().out
    assert not gs.answer_path("claude", unit).exists()


# -- Aufloesung der Laufparameter -----------------------------------------
def namespace(**felder) -> object:
    grund = dict(engine=None, model=None, effort=None, timeout=None,
                 total_timeout=None)
    grund.update(felder)
    return type("Args", (), grund)()


def test_parameter_uebersteuert_unbrauchbares_setting(monkeypatch) -> None:
    """Ein ungenutztes, kaputtes Setting darf den Lauf nicht blockieren."""
    settings = gs.LabelSettings(engine="claude", model_claude="", model_codex="x",
                                effort="high", timeout_seconds=1800,
                                total_timeout_seconds=43200)
    aufgeloest = gs.engine_config(namespace(model="opus"), settings)
    assert aufgeloest.model == "opus"


def test_leerer_parameter_ist_ein_bedienfehler() -> None:
    settings = gs.load_label_settings()
    with pytest.raises(gs.GoldsetBlocked) as blocked:
        gs.engine_config(namespace(model=""), settings)
    assert blocked.value.problem.code == "GS_SETTING_INVALID"
    assert "--model" in blocked.value.problem.obj


@pytest.mark.parametrize("felder", [
    dict(timeout=-5), dict(timeout=200_000), dict(timeout=1800, total_timeout=600),
])
def test_unbrauchbare_deckel_blockieren(felder) -> None:
    settings = gs.load_label_settings()
    with pytest.raises(gs.GoldsetBlocked) as blocked:
        gs.engine_config(namespace(**felder), settings)
    assert blocked.value.problem.code == "GS_SETTING_INVALID"


def test_json_objekte_zaehlen() -> None:
    assert gs._json_objects('{"a": 1}') == ({"a": 1}, 0)
    assert gs._json_objects('{"a": 1} {"b": 2}')[1] == 1
    assert gs._json_objects("kein json") == (None, 0)


# -- Gesamtdeckel ----------------------------------------------------------
def uhr_und_laeufe(monkeypatch, schritt: float = 100.0) -> list[gs.EngineConfig]:
    """Ersetzt die Uhr durch einen Zaehler, den jeder Lauf weiterstellt.

    Der Trockenlauf braucht keine Zeit; ohne eigene Uhr liesse sich der
    Gesamtdeckel nur mit echtem Warten pruefen.
    """
    stand = {"t": 0.0}
    gesehen: list[gs.EngineConfig] = []
    echt = gs.run_fake

    def merken(root, task, config):
        gesehen.append(config)
        stand["t"] += schritt
        return echt(root, task, config)

    monkeypatch.setattr(gs.time, "monotonic", lambda: stand["t"])
    monkeypatch.setattr(gs, "run_fake", merken)
    return gesehen


def test_gesamtdeckel_kappt_die_restzeit(sandbox, monkeypatch, capsys) -> None:
    """Der Unitdeckel darf den Gesamtdeckel nicht ueberziehen."""
    gesehen = uhr_und_laeufe(monkeypatch)
    assert lauf("run", "--engine", "claude", "--timeout", "120",
                "--total-timeout", "200") == 2
    ausgabe = capsys.readouterr().out
    assert "Gesamtdeckel von 200 s erreicht" in ausgabe
    assert "Offen:" in ausgabe

    assert len(gesehen) == 2
    assert (gesehen[0].timeout_seconds, gesehen[0].capped) == (120, False)
    # Die zweite Unit bekommt nur noch die Restzeit des Gesamtdeckels.
    assert (gesehen[1].timeout_seconds, gesehen[1].capped) == (100, True)


def test_gesamtdeckel_gleich_unitdeckel_laeuft_an(sandbox, monkeypatch) -> None:
    """Gleiche Werte duerfen nicht null Laeufe ergeben."""
    gesehen = uhr_und_laeufe(monkeypatch)
    lauf("run", "--engine", "claude", "--timeout", "60", "--total-timeout", "60")
    assert len(gesehen) == 1


def test_gekappter_deckel_wird_nicht_als_haenger_gemeldet() -> None:
    gekappt = gs.EngineConfig(engine="claude", model="opus", effort="high",
                              timeout_seconds=90, total_timeout_seconds=43200,
                              capped=True)
    found = gs.classify_failure("claude", ergebnis(timed_out=True), gekappt)
    assert found.code == "GS_TIMEOUT"
    assert "Restzeit des Gesamtdeckels" in found.was
    assert "Gesamtdeckel anheben" in found.abhilfe


# -- Verdrahtung von run_codex --------------------------------------------
def codex_stub(tmp_path: Path, skript: str) -> Path:
    """Legt ein ausfuehrbares 'codex' in einem eigenen PATH-Verzeichnis ab.

    So laesst sich der Aufrufweg pruefen, ohne die echte CLI und ohne
    Abo-Kontingent: der Stub sieht dieselbe Kommandozeile wie Codex.
    """
    ordner = tmp_path / "bin"
    ordner.mkdir(exist_ok=True)
    pfad = ordner / "codex"
    pfad.write_text("#!/bin/sh\n" + skript, encoding="utf-8")
    pfad.chmod(0o755)
    return ordner


def codex_lauf(tmp_path, monkeypatch, skript: str) -> gs.EngineResult:
    monkeypatch.setenv("PATH", str(codex_stub(tmp_path, skript)))
    monkeypatch.delenv(gs.FAKE_DIR_ENV, raising=False)
    task = gs.load_task("house--energy-and-commerce--energy")
    workdir = tmp_path / "arbeit"
    workdir.mkdir()
    return gs.run_codex(task, config("codex"), workdir)


def antwort_schreiben(inhalt: str, exit_code: int = 0, echo: bool = False) -> str:
    """Ein Stub, der in die Datei hinter '--output-last-message' schreibt.

    Der Inhalt steht im Skript und nicht in der Umgebung: der Aufruf reicht
    nur die Positivliste ``ENV_ALLOWED`` weiter.
    """
    return "".join([
        'printf "%s\\n" "$@" >&2\n' if echo else "",
        'ziel=""\n',
        'while [ $# -gt 0 ]; do\n',
        '  if [ "$1" = "--output-last-message" ]; then ziel="$2"; fi\n',
        '  shift\n',
        'done\n',
        f"printf '%s' '{inhalt}' > \"$ziel\"\n" if inhalt else "",
        f"exit {exit_code}\n",
    ])


def test_run_codex_liest_die_abschlussdatei(tmp_path, monkeypatch) -> None:
    result = codex_lauf(tmp_path, monkeypatch, antwort_schreiben(
        '{"unit_id": "x", "decision": "no_mapping"}'))
    assert result.answer == {"unit_id": "x", "decision": "no_mapping"}
    assert result.ambiguous is False


def test_run_codex_verwirft_antwort_bei_exit_ungleich_null(tmp_path, monkeypatch) -> None:
    """Eine gefuellte Abschlussdatei ohne Exit 0 ist keine Antwort.

    Sonst uebernaehme der Lauf ein Fehlerereignis als Label und der Resume
    uebersprunge die Unit danach dauerhaft.
    """
    result = codex_lauf(tmp_path, monkeypatch, antwort_schreiben(
        '{"unit_id": "x", "decision": "no_mapping"}', exit_code=3))
    assert result.answer is None
    assert result.exit_code == 3


def test_run_codex_meldet_zwei_json_objekte(tmp_path, monkeypatch) -> None:
    result = codex_lauf(tmp_path, monkeypatch,
                        antwort_schreiben('{"a": 1} {"b": 2}'))
    assert result.answer is None
    assert result.ambiguous is True
    assert gs.classify_failure("codex", result, config("codex")).code \
        == "GS_ANSWER_AMBIGUOUS"


def test_run_codex_ohne_abschlussdatei(tmp_path, monkeypatch) -> None:
    result = codex_lauf(tmp_path, monkeypatch, antwort_schreiben(""))
    assert result.answer is None


def test_run_codex_reicht_modell_und_denktiefe_durch(tmp_path, monkeypatch) -> None:
    result = codex_lauf(tmp_path, monkeypatch,
                        antwort_schreiben('{"unit_id": "x"}', echo=True))
    assert "opus" in result.stderr
    assert 'model_reasoning_effort="high"' in result.stderr
    assert "tools.web_search=true" in result.stderr
    assert "--ignore-user-config" in result.stderr


def test_umgebung_wird_gefiltert(tmp_path, monkeypatch) -> None:
    """Ein geerbtes OPENAI_BASE_URL lenkte den Lauf sonst still um."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://umgeleitet.example")
    monkeypatch.setenv("CODEX_HOME", "/soll/ueberschrieben/werden")
    # 'env' liegt nicht im PATH des Stubs; gemeldet wird ueber die Shell.
    zeigen = ('printf "%s\\n" "OPENAI_BASE_URL=$OPENAI_BASE_URL" '
              '"CODEX_HOME=$CODEX_HOME" >&2\n')
    result = codex_lauf(tmp_path, monkeypatch,
                        zeigen + antwort_schreiben('{"unit_id": "x"}'))
    assert "OPENAI_BASE_URL=\n" in result.stderr
    assert "/soll/ueberschrieben/werden" not in result.stderr
    assert "codex-home" in result.stderr


def test_positivliste_uebernimmt_nur_gesetzte_werte(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/pfad")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    env = gs.clean_env({"CODEX_HOME": "/tmp/home"})
    assert env["PATH"] == "/pfad"
    assert env["CODEX_HOME"] == "/tmp/home"
    assert "OPENAI_BASE_URL" not in env


@pytest.mark.parametrize("name", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
])
def test_anmeldung_erreicht_den_unterprozess(tmp_path, monkeypatch, name) -> None:
    """Ohne diese Variablen scheiterte eine tokenbasierte Anmeldung.

    Geprueft wird am Kindprozess und nicht an ``clean_env`` allein: eine
    Entkopplung der beiden bliebe sonst unentdeckt.
    """
    monkeypatch.setenv(name, "geheim-" + name.lower())
    zeigen = f'printf "%s\\n" "{name}=${name}" >&2\n'
    result = codex_lauf(tmp_path, monkeypatch,
                        zeigen + antwort_schreiben('{"unit_id": "x"}'))
    assert f"{name}=geheim-{name.lower()}" in result.stderr


def test_verify_nennt_die_unicodevariante(sandbox, taxonomy, capsys) -> None:
    """Ein Gedankenstrich statt eines Bindestrichs sieht im Blatt gleich aus."""
    pfad = workbook_vorbereiten(sandbox)
    echt = next(value for value in sorted(taxonomy.ids) if "-" in value)
    zelle_setzen(pfad, 2, "industry_id", echt.replace("-", "‐", 1))
    capsys.readouterr()

    assert lauf("verify", "--source", "claude") == 2
    ausgabe = capsys.readouterr().out
    assert "GS_INDUSTRY_UNKNOWN" in ausgabe
    assert "Unicodeform" in ausgabe
    assert repr(echt) in ausgabe


# -- Schalter fuer den Webzugang ------------------------------------------
def kommandozeile(monkeypatch, **abweichung) -> list[str]:
    """Faengt die Kommandozeile ab, die ``run_claude`` aufbauen wuerde."""
    gesehen: list[str] = []

    def falle(command, **kwargs):
        gesehen.extend(command)
        return gs.EngineResult(answer=None, exit_code=0, duration_s=0.0,
                               timed_out=False, stdout="", stderr="")

    monkeypatch.setattr(gs, "_spawn", falle)
    task = gs.load_task("house--energy-and-commerce--energy")
    gs.run_claude(task, dataclasses.replace(config("claude"), **abweichung),
                  Path("/tmp"))
    return gesehen


def test_webzugang_steht_ohne_flag_offen(monkeypatch) -> None:
    command = kommandozeile(monkeypatch)
    assert command[command.index("--tools") + 1] == "WebSearch,WebFetch"


def test_web_access_off_nimmt_claude_alle_werkzeuge(monkeypatch) -> None:
    """Der leere Wert heisst 'kein einziges Werkzeug'.

    Ein weggelassenes ``--tools`` gaebe dem Agenten stattdessen den vollen
    Satz samt Bash und Editierwerkzeugen.
    """
    command = kommandozeile(monkeypatch, web_access=False)
    assert command[command.index("--tools") + 1] == ""


def test_web_access_off_schaltet_codex_die_suche_ab(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(codex_stub(tmp_path, antwort_schreiben(
        '{"unit_id": "x"}', echo=True))))
    monkeypatch.delenv(gs.FAKE_DIR_ENV, raising=False)
    workdir = tmp_path / "arbeit"
    workdir.mkdir()
    result = gs.run_codex(gs.load_task("house--energy-and-commerce--energy"),
                          dataclasses.replace(config("codex"), web_access=False),
                          workdir)
    assert "tools.web_search=false" in result.stderr
    assert "tools.web_search=true" not in result.stderr


def test_kein_quellenbefund_bei_geschlossenem_zugang(paar, taxonomy) -> None:
    """Ohne Webzugang ist die leere Quellenliste die Folge der Einstellung.

    Der Befund verlangt eine Logpruefung; bei abgeschaltetem Zugang stuende er
    an allen 22 Units und verdeckte die echten Befunde.
    """
    task, record = paar
    record["answer"]["sources"] = []
    record["web_access"] = False
    assert "GS_WEBSEARCH_OFF" not in codes(task, record, taxonomy)
    record["web_access"] = True
    assert "GS_WEBSEARCH_OFF" in codes(task, record, taxonomy)


def test_flag_erreicht_die_konfiguration() -> None:
    """Parser und Aufloesung, der Weg vor jedem Enginelauf."""
    parser = gs.build_parser()
    offen = gs.engine_config(parser.parse_args(["run", "--engine", "claude"]),
                             gs.load_label_settings())
    assert offen.web_access is True
    zu = gs.engine_config(
        parser.parse_args(["run", "--engine", "claude", "--web-access-off"]),
        gs.load_label_settings())
    assert zu.web_access is False


def test_preflight_reicht_den_zugang_an_den_kurzlauf_durch(monkeypatch, capsys) -> None:
    """Der Preflight laeuft mit eigenem Deckel - aber nicht mit eigenem Zugang."""
    gesehen: list[gs.EngineConfig] = []

    def faenger(engine):
        def runner(probe, config, workdir):
            gesehen.append(config)
            return gs.EngineResult(answer={"antwort": "bereit"}, exit_code=0,
                                   duration_s=0.1, timed_out=False,
                                   stdout='"effort":"high"', stderr="")
        return runner

    monkeypatch.setattr(gs, "engine_runner", faenger)
    monkeypatch.setattr(gs, "assert_cli_available", lambda engine: "")
    assert lauf("preflight", "--engine", "claude", "--web-access-off") == 0
    capsys.readouterr()
    assert gesehen[0].web_access is False


def test_trockenlauf_dokumentiert_den_geschlossenen_zugang(sandbox, capsys) -> None:
    """Vom Flag bis in die Provenienz: Antwortdatei und info-Tabelle."""
    unit_id = "house--energy-and-commerce--energy"
    assert lauf("run", "--engine", "claude", "--web-access-off",
                "--unit-id", unit_id) == 0
    record = json.loads(gs.answer_path("claude", unit_id).read_text(encoding="utf-8"))
    assert record["web_access"] is False

    lauf("collect", "--engine", "claude")
    capsys.readouterr()
    infos = {row["unit_id"]: row for row in
             gs.read_csv(gs.info_path("claude"), gs.INFO_COLUMNS, was="Provenienz")}
    assert infos[unit_id]["web_access"] == "nein"

    # Derselbe Satz laesst sich nicht mit offenem Zugang fortsetzen.
    assert lauf("run", "--engine", "claude", "--unit-id", unit_id) == 2
    assert "GS_RESUME_MISMATCH" in capsys.readouterr().out


def test_unlesbarer_web_access_wert_wird_gemeldet(paar, taxonomy) -> None:
    """'false' als Text waere sonst wahr und stuende zugleich falsch in info."""
    task, record = paar
    record["answer"]["sources"] = []
    record["web_access"] = "false"
    found = gs.check_answer(task, record, taxonomy)
    codes_ = {p.code for p in found.problems}
    assert "GS_ANSWER_FORMAT" in codes_
    # Im Zweifel wird die fehlende Quellenliste gemeldet, nicht unterdrueckt.
    assert "GS_WEBSEARCH_OFF" in codes_
    assert found.info["web_access"] == ""


def test_resume_trennt_recherchierte_von_nicht_recherchierten_antworten(paar) -> None:
    """Ein halb recherchierter Satz waere genau der stille Mischstand."""
    task, record = paar
    record.update(prompt_delivery=gs.PROMPT_DELIVERY["claude"], web_access=True)
    offen = config("claude")
    assert gs.resume_problem(offen, task, record) is None
    found = gs.resume_problem(dataclasses.replace(offen, web_access=False),
                              task, record)
    assert found is not None and found.code == "GS_RESUME_MISMATCH"
    assert "web_access" in found.was

    # Antworten aus der Zeit vor dem Schalter entstanden mit offenem Zugang.
    del record["web_access"]
    assert gs.resume_problem(offen, task, record) is None
    assert gs.resume_problem(dataclasses.replace(offen, web_access=False),
                             task, record) is not None

    # Ein unlesbarer Wert gilt als Abweichung: die Unit wird nicht uebersprungen.
    record["web_access"] = 0
    assert gs.resume_problem(offen, task, record) is not None


# -- Zaehlung im Merge -----------------------------------------------------
def test_merge_zaehlt_beidseitig_offene_units_nicht_als_einig() -> None:
    """Fehlen von Widerspruch ist keine Zustimmung.

    Zwei leere Haende ergeben dieselbe (leere) Entscheidung und dieselbe
    (leere) ID-Menge; ohne eigenen Zweig zaehlte eine nirgends gelabelte Unit
    als einig und die Quote im Bericht behauptete Konsens ueber eine Luecke.
    """
    assert gs._merge_quote(1, 1, 22, 20) == (
        "1 von 2 verglichenen Units einig, 1 strittig. "
        "20 von 22 Units sind in keinem Lauf gelabelt.")
    assert gs._merge_quote(21, 1, 22, 0) == \
        "21 von 22 verglichenen Units einig, 1 strittig."


def test_merge_zaehlt_nur_verglichene_units(sandbox, taxonomy, capsys) -> None:
    """Der Mischfall: zwei belegte Units neben zwanzig Luecken.

    Genau hier log die alte Zaehlung - die beidseitig offenen Units hatten
    dieselbe leere Entscheidung und galten als einig.
    """
    einig_unit = "house--energy-and-commerce--energy"
    strittig_unit = "house--ways-and-means--health"
    beide = ("--unit-id", einig_unit, "--unit-id", strittig_unit)
    assert lauf("run", "--engine", "claude", *beide) == 0
    lauf("collect", "--engine", "claude")

    fake = gs.fake_dir()
    assert fake is not None
    pfad = fake / f"{strittig_unit}.json"
    payload = json.loads(pfad.read_text(encoding="utf-8"))
    payload["assignments"][0]["industry_id"] = sorted(taxonomy.ids)[1]
    pfad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert lauf("run", "--engine", "codex", *beide) == 0
    lauf("collect", "--engine", "codex")
    capsys.readouterr()

    assert lauf("merge", "--base", "claude", "--against", "codex") == 2
    ausgabe = capsys.readouterr().out
    assert "1 von 2 verglichenen Units einig, 1 strittig." in ausgabe
    assert "20 von 22 Units sind in keinem Lauf gelabelt." in ausgabe
    bericht = gs.MERGE_REPORT.read_text(encoding="utf-8")
    assert f"## {strittig_unit}" in bericht
    assert f"## {einig_unit}" not in bericht
    assert f"in keinem der beiden Laeufe: {einig_unit}" not in bericht


def test_merge_meldet_luecken_als_befund(sandbox, capsys) -> None:
    """Ein Vergleich ueber unvollstaendigem Material ist kein sauberer Lauf."""
    for engine in ("claude", "codex"):
        gs.write_csv(gs.labels_path(engine), gs.LABEL_COLUMNS, [])
        gs.write_csv(gs.notes_path(engine), gs.NOTE_COLUMNS, [])
        gs.write_csv(gs.info_path(engine), gs.INFO_COLUMNS, [])
    assert lauf("merge", "--base", "claude", "--against", "codex") == 2
    ausgabe = capsys.readouterr().out
    assert "0 von 0 verglichenen Units einig" in ausgabe
    assert "in keinem der beiden Laeufe gelabelt" in ausgabe
    bericht = gs.MERGE_REPORT.read_text(encoding="utf-8")
    assert "in keinem der beiden Laeufe:" in bericht


# -- Sammelkommandos -------------------------------------------------------
def test_label_fuehrt_eine_engine_bis_zur_reviewmappe(sandbox, capsys) -> None:
    """Ein Aufruf statt fuenf; ohne --compared entfaellt der Vergleich."""
    assert lauf("label", "--engine", "claude") == 0
    ausgabe = capsys.readouterr().out
    assert "beide Laeufe vergleichen" not in ausgabe
    assert gs.WORKBOOK_PATH.exists()
    assert not gs.labels_path("merged").exists()
    assert "finish --engine claude" in ausgabe


def test_label_compared_vergleicht_beide_engines(sandbox, capsys) -> None:
    assert lauf("label", "--compared") == 0
    ausgabe = capsys.readouterr().out
    assert "beide Laeufe vergleichen" in ausgabe
    assert gs.labels_path("merged").exists()
    assert gs.MERGE_REPORT.exists()
    assert "finish --compared" in ausgabe


def test_label_und_compared_schliessen_einander_aus(capsys) -> None:
    with pytest.raises(SystemExit):
        lauf("label", "--engine", "claude", "--compared")


def test_finish_haelt_bei_befunden_an(sandbox, monkeypatch, capsys) -> None:
    """Der Vertrag ist die Bedingung der Uebernahme, nicht ihr Nachtrag."""
    assert lauf("label", "--engine", "claude") == 0
    book = load_workbook(gs.WORKBOOK_PATH)
    book[gs.SHEET_LABELS]["C2"] = "9"
    book.save(gs.WORKBOOK_PATH)
    gerufen: list[str] = []
    monkeypatch.setattr(gs, "_matcher",
                        lambda schritt, eval_id: gerufen.append(schritt) or 0)
    capsys.readouterr()

    assert lauf("finish", "--engine", "claude", "--eval-id", "eval-x") == 2
    assert gerufen == []
    assert "Das Gold-Set entsteht nicht" in capsys.readouterr().out


def test_finish_eroeffnet_die_revision_nur_einmal(sandbox, monkeypatch, capsys) -> None:
    # Die Revision liegt in der Sandbox: ein Test schreibt nicht ins echte
    # Datenverzeichnis.
    manifest = sandbox / "evaluations" / "eval-x" / gs.EXPORT_MANIFEST
    monkeypatch.setattr(gs, "export_manifest_path", lambda eval_id: manifest)
    assert lauf("label", "--engine", "claude") == 0
    gerufen: list[str] = []
    monkeypatch.setattr(gs, "_matcher",
                        lambda schritt, eval_id: gerufen.append(schritt) or 0)
    capsys.readouterr()

    assert lauf("finish", "--engine", "claude", "--eval-id", "eval-x") == 0
    assert gerufen == ["export", "import"]

    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")
    gerufen.clear()
    assert lauf("finish", "--engine", "claude", "--eval-id", "eval-x") == 0
    assert gerufen == ["import"]


def test_finish_reicht_den_fehlercode_des_matchers_durch(sandbox, monkeypatch,
                                                         capsys) -> None:
    """Exit 7 heisst abgelehnter Import; das darf nicht zu 2 verflachen."""
    assert lauf("label", "--engine", "claude") == 0
    monkeypatch.setattr(gs, "_matcher",
                        lambda schritt, eval_id: 0 if schritt == "export" else 7)
    capsys.readouterr()
    assert lauf("finish", "--engine", "claude", "--eval-id", "eval-x") == 7


def test_label_reicht_modell_und_denktiefe_durch(sandbox, monkeypatch, capsys) -> None:
    """Die Wahl gilt fuer den Preflight und fuer jeden Unitlauf."""
    gesehen: list[gs.EngineConfig] = []
    echt = gs.run_fake

    def merken(root, task, config):
        gesehen.append(config)
        return echt(root, task, config)

    monkeypatch.setattr(gs, "run_fake", merken)
    assert lauf("label", "--engine", "claude", "--model", "claude-opus-5",
                "--effort", "xhigh") == 0
    capsys.readouterr()
    assert {config.model for config in gesehen} == {"claude-opus-5"}
    assert {config.effort for config in gesehen} == {"xhigh"}
    # Preflight plus 22 Units.
    assert len(gesehen) == 23


# -- Fullrun ---------------------------------------------------------------
def test_use_fullrun_paths_legt_alles_unter_die_fullrun_wurzel(monkeypatch,
                                                               tmp_path) -> None:
    """Ein Fullrun darf keine Datei der Gold-Set-Ablage anfassen."""
    monkeypatch.setattr(gs, "FULLRUN_ROOT", tmp_path / "fullrun")
    vorher = {name: getattr(gs, name) for name in UMGESCHALTETE_PFADE}

    gs.use_fullrun_paths()

    for name in UMGESCHALTETE_PFADE:
        neu = getattr(gs, name)
        assert neu != vorher[name]
        assert (tmp_path / "fullrun") in neu.parents


def test_fullrun_units_deckt_den_ganzen_korpus_ab() -> None:
    """Alle Units, sortiert, fortlaufend nummeriert - und mehr als die Probe."""
    corpus = gs.load_corpus_units()
    units = gs.fullrun_units(corpus)

    assert [item.unit_id for item in units] == sorted(corpus)
    assert [item.nr for item in units] == list(range(1, len(corpus) + 1))
    assert len(units) > len(gs.load_sample())
    # Die Eingabebasis kommt aus dem Korpus und nicht aus einer zweiten Liste.
    assert all(item.input_basis == corpus[item.unit_id].input_basis.value
               for item in units)


def test_label_units_ohne_flag_bleibt_die_stichprobe(sample) -> None:
    assert gs.label_units(gs._teilargs()) == sample


# -- Warteschleife bei Ratenlimit ------------------------------------------
def _config(engine: str = "claude") -> gs.EngineConfig:
    return gs.EngineConfig(engine=engine, model="opus", effort="high",
                           timeout_seconds=60)


def _fehlschlag(text: str) -> gs.EngineResult:
    return gs.EngineResult(answer=None, exit_code=1, duration_s=1.0,
                           timed_out=False, stdout="", stderr=text)


def _erfolg() -> gs.EngineResult:
    return gs.EngineResult(answer={"unit_id": "x"}, exit_code=0, duration_s=1.0,
                           timed_out=False, stdout="", stderr="")


RATE_LIMIT_TEXT = '{"status":429} You\'ve hit your usage limit.'


def test_ratenlimit_wird_erkannt() -> None:
    """Beide Formulierungen der CLIs; ein Erfolg ist nie ein Ratenlimit."""
    assert gs.rate_limited(_fehlschlag(RATE_LIMIT_TEXT), _config())
    assert gs.rate_limited(_fehlschlag("Error: rate_limit_exceeded"), _config("codex"))
    assert not gs.rate_limited(_fehlschlag("some other failure"), _config())
    assert not gs.rate_limited(_erfolg(), _config())


def test_ratenlimit_im_zeitdeckel_wird_erkannt() -> None:
    """Ein 429 bleibt eines, auch wenn der Unitdeckel darueber reisst.

    Die Claude-CLI wiederholt ein Ratenlimit zuerst selbst und wartet dabei
    die gemeldete Verzoegerung ab; genau dieser Fall darf nicht als blosser
    Zeitueberlauf durchgehen, sonst laeuft der Fullrun an seinem Zweck vorbei.
    """
    gedeckelt = gs.EngineResult(answer=None, exit_code=1, duration_s=1800.0,
                                timed_out=True, stdout="", stderr=RATE_LIMIT_TEXT)
    assert gs.rate_limited(gedeckelt, _config())

    ohne_grund = gs.EngineResult(answer=None, exit_code=1, duration_s=1800.0,
                                 timed_out=True, stdout="", stderr="still busy")
    assert not gs.rate_limited(ohne_grund, _config())


def test_plattenfehler_ist_kein_ratenlimit() -> None:
    """'Disk quota exceeded' darf keine endlose Warteschleife ausloesen."""
    assert not gs.rate_limited(_fehlschlag("write failed: Disk quota exceeded"),
                               _config())
    # Die Kontingentmeldung der API bleibt dagegen erkannt.
    assert gs.rate_limited(_fehlschlag("Error code: insufficient_quota"),
                           _config())


def test_fullrun_wartet_das_ratenlimit_aus(monkeypatch, capsys) -> None:
    """Zweimal Limit, dann Antwort: die Unit gilt am Ende als gelabelt."""
    geschlafen: list[int] = []
    monkeypatch.setattr(gs.time, "sleep", geschlafen.append)
    antworten = [_fehlschlag(RATE_LIMIT_TEXT), _fehlschlag(RATE_LIMIT_TEXT),
                 _erfolg()]

    result, gewartet = gs.run_until_quota(
        lambda task, config, workdir: antworten.pop(0), {}, _config(), wait=True)

    assert result.answer is not None
    assert geschlafen == [gs.RATE_LIMIT_WAIT_SECONDS] * 2
    assert gewartet == 2 * gs.RATE_LIMIT_WAIT_SECONDS
    assert "Ratenlimit erreicht (Versuch 2)" in capsys.readouterr().out


def test_ohne_fullrun_wird_nicht_gewartet(monkeypatch) -> None:
    """Der Gold-Set-Satz soll abbrechen statt stundenlang stillzustehen."""
    monkeypatch.setattr(gs.time, "sleep",
                        lambda _: pytest.fail("es darf nicht gewartet werden"))
    result, gewartet = gs.run_until_quota(
        lambda task, config, workdir: _fehlschlag(RATE_LIMIT_TEXT), {},
        _config(), wait=False)
    assert result.answer is None
    assert gewartet == 0


def test_anderer_fehler_wartet_auch_im_fullrun_nicht(monkeypatch) -> None:
    """Ein unbekanntes Modell wird durch Warten nicht bekannter."""
    monkeypatch.setattr(gs.time, "sleep",
                        lambda _: pytest.fail("es darf nicht gewartet werden"))
    result, gewartet = gs.run_until_quota(
        lambda task, config, workdir: _fehlschlag("unknown model"), {},
        _config(), wait=True)
    assert result.answer is None
    assert gewartet == 0


@pytest.fixture()
def fullrun_sandbox(tmp_path, monkeypatch, taxonomy, sample):
    """Die Fullrun-Ablage in tmp_path, mit der Stichprobe als Laufmenge.

    Der echte Fullrun umfasst 225 Units; hier laufen die 22 der Stichprobe.
    Geprueft wird die Betriebsart - Ablage, Deckel, Warteschleife -, nicht die
    Menge: ein Testlauf ueber 225 Aufgabenpakete kostete Minuten und wiese
    nichts nach, was 'test_fullrun_units_deckt_den_ganzen_korpus_ab' nicht
    schon zeigt.
    """
    monkeypatch.setattr(gs, "FULLRUN_ROOT", tmp_path / "fullrun")
    monkeypatch.setattr(gs, "fullrun_units", lambda corpus: sample)

    industry = sorted(taxonomy.ids)[0]
    fake = tmp_path / "antworten"
    fake.mkdir()
    for item in sample:
        task = gs.load_task(item.unit_id)
        (fake / f"{item.unit_id}.json").write_text(
            json.dumps(build_answer(task, industry), ensure_ascii=False),
            encoding="utf-8")
    monkeypatch.setenv(gs.FAKE_DIR_ENV, str(fake))
    gs.use_fullrun_paths()
    return tmp_path


def test_fullrun_schreibt_nur_in_die_eigene_ablage(fullrun_sandbox, capsys) -> None:
    """Die Referenz bleibt unberuehrt; alles Neue liegt unter fullrun/."""
    goldset_stand = sorted(p.name for p in gs.GOLDSET_ROOT.iterdir())

    assert lauf("label", "--fullrun", "--engine", "claude") == 0
    capsys.readouterr()

    assert (fullrun_sandbox / "fullrun" / "fullrun_review.xlsx").exists()
    assert (fullrun_sandbox / "fullrun" / "tasks" / "index.json").exists()
    assert sorted(p.name for p in gs.GOLDSET_ROOT.iterdir()) == goldset_stand


def test_fullrun_kennt_keinen_gesamtdeckel(fullrun_sandbox, monkeypatch,
                                           capsys) -> None:
    """Ohne Flag schneidet der Deckel ab, mit Flag laeuft der Satz durch."""
    assert lauf("prepare", "--fullrun") == 0
    stand = {"t": 0.0}
    # Jeder Blick auf die Uhr springt eine Stunde weiter: der Gesamtdeckel
    # waere nach der ersten Unit erschoepft.
    monkeypatch.setattr(gs.time, "monotonic",
                        lambda: stand.__setitem__("t", stand["t"] + 3600) or stand["t"])
    capsys.readouterr()

    assert lauf("run", "--fullrun", "--engine", "claude") == 0
    ausgabe = capsys.readouterr().out
    assert "ohne Gesamtdeckel (Fullrun)" in ausgabe
    assert "Gesamtdeckel von" not in ausgabe


def test_ohne_fullrun_greift_der_gesamtdeckel(sandbox, monkeypatch, capsys) -> None:
    stand = {"t": 0.0}
    monkeypatch.setattr(gs.time, "monotonic",
                        lambda: stand.__setitem__("t", stand["t"] + 3600) or stand["t"])
    assert lauf("run", "--engine", "claude", "--total-timeout", "3600") == 2
    assert "Gesamtdeckel von 3600 s erreicht" in capsys.readouterr().out


def test_run_wartet_im_fullrun_und_labelt_danach(fullrun_sandbox, monkeypatch,
                                                 capsys) -> None:
    """Ein Limit bei der ersten Unit haelt den Satz an, statt ihn zu beenden."""
    assert lauf("prepare", "--fullrun") == 0
    capsys.readouterr()
    geschlafen: list[int] = []
    monkeypatch.setattr(gs.time, "sleep", geschlafen.append)
    echt = gs.run_fake
    zustand = {"limit": True}

    def einmal_limit(root, task, config):
        if zustand["limit"]:
            zustand["limit"] = False
            return _fehlschlag(RATE_LIMIT_TEXT)
        return echt(root, task, config)

    monkeypatch.setattr(gs, "run_fake", einmal_limit)
    assert lauf("run", "--fullrun", "--engine", "claude") == 0
    ausgabe = capsys.readouterr().out
    assert geschlafen == [gs.RATE_LIMIT_WAIT_SECONDS]
    assert "10 min auf das Abo gewartet" in ausgabe
    assert "GS_RATE_LIMITED" not in ausgabe


def test_ratenlimit_ohne_fullrun_bricht_den_satz_ab(sandbox, monkeypatch,
                                                    capsys) -> None:
    monkeypatch.setattr(gs, "run_fake",
                        lambda root, task, config: _fehlschlag(RATE_LIMIT_TEXT))
    assert lauf("run", "--engine", "claude") == 2
    ausgabe = capsys.readouterr().out
    assert "GS_RATE_LIMITED" in ausgabe
    assert "bricht ab" in ausgabe


def test_finish_fullrun_ruft_das_exportmodul(fullrun_sandbox, monkeypatch,
                                             capsys) -> None:
    """Der Fullrun endet im Mapping, nicht in einer Evaluationsrevision."""
    assert lauf("label", "--fullrun", "--engine", "claude") == 0
    gerufen: list[tuple[str, str]] = []
    monkeypatch.setattr(gs, "_fullrun_import",
                        lambda review_id, source, test=False: gerufen.append(
                            (review_id, source, test)) or 0)
    monkeypatch.setattr(gs, "_matcher",
                        lambda schritt, eval_id: pytest.fail(
                            "der Fullrun geht nicht ueber 'evaluate'"))
    capsys.readouterr()

    assert lauf("finish", "--fullrun", "--engine", "claude",
                "--review-id", "map-x") == 0
    assert gerufen == [("map-x", "claude", False)]


def test_review_id_und_eval_id_gehoeren_zu_je_einer_betriebsart(capsys) -> None:
    """Der falsche Zielparameter ist ein Bedienfehler, kein stiller Umweg."""
    with pytest.raises(SystemExit):
        gs.main(["finish", "--review-id", "map-x"])
    assert "--review-id gilt nur mit --fullrun" in capsys.readouterr().err
    vorher = {name: getattr(gs, name) for name in UMGESCHALTETE_PFADE}
    with pytest.raises(SystemExit):
        gs.main(["finish", "--fullrun", "--eval-id", "eval-x"])
    assert "--eval-id gilt nur ohne --fullrun" in capsys.readouterr().err
    # Der abgewiesene Aufruf laesst die Ablage, wie sie war: haette er
    # umgeschaltet, schriebe der naechste Aufruf desselben Prozesses in die
    # Fullrun-Ablage, ohne dass jemand das verlangt hat.
    assert {name: getattr(gs, name) for name in UMGESCHALTETE_PFADE} == vorher


# -- Testlauf --------------------------------------------------------------
def test_use_fullrun_paths_kennt_die_testablage(monkeypatch, tmp_path) -> None:
    """Der Testlauf bekommt eine eigene Wurzel und eine eigene Mappe."""
    gs.use_fullrun_paths(tmp_path / "fullrun_test")

    for name in UMGESCHALTETE_PFADE:
        assert (tmp_path / "fullrun_test") in getattr(gs, name).parents
    assert gs.WORKBOOK_PATH.name == "fullrun_test_review.xlsx"
    assert gs.TEST_SELECTION_PATH.name == gs.TEST_SELECTION_FILENAME


def test_ziehung_nimmt_fuenf_units_und_haelt_sie_fest(monkeypatch,
                                                      tmp_path) -> None:
    """Einmal gezogen, danach gilt die Datei - sonst liefe jeder Schritt anders."""
    monkeypatch.setattr(gs, "TEST_SELECTION_PATH", tmp_path / "test_units.json")
    corpus = gs.load_corpus_units()

    erste = gs.test_units(corpus)

    assert len(erste) == gs.TEST_UNIT_COUNT
    assert {item.unit_id for item in erste} <= set(corpus)
    assert [item.nr for item in erste] == list(range(1, gs.TEST_UNIT_COUNT + 1))
    assert [item.unit_id for item in erste] == sorted(item.unit_id for item in erste)
    assert all(item.input_basis == corpus[item.unit_id].input_basis.value
               for item in erste)

    # Ein zweites Ziehen waere ein zweiter Satz Units; die Datei verhindert es.
    monkeypatch.setattr(gs.random, "sample",
                        lambda *args, **kwargs: pytest.fail("zweite Ziehung"))
    assert gs.test_units(corpus) == erste


def test_ziehung_ohne_saat(monkeypatch, tmp_path) -> None:
    """Gezogen wird aus dem ganzen Korpus, nicht aus seinem Anfang."""
    gesehen: list[list[str]] = []
    monkeypatch.setattr(gs, "TEST_SELECTION_PATH", tmp_path / "test_units.json")
    echt = gs.random.sample
    monkeypatch.setattr(gs.random, "sample",
                        lambda menge, k: gesehen.append(list(menge)) or echt(menge, k))
    corpus = gs.load_corpus_units()

    gs.test_units(corpus)

    assert gesehen == [sorted(corpus)]


@pytest.fixture()
def test_sandbox(tmp_path, monkeypatch, taxonomy, sample):
    """Die Testlauf-Ablage in tmp_path, mit festgeschriebener Ziehung.

    Gezogen wird hier nicht - das prueft
    'test_ziehung_nimmt_fuenf_units_und_haelt_sie_fest'. Diese Sandbox braucht
    Units, zu denen es vorbereitete Antworten gibt, und nimmt dafuer die ersten
    fuenf der Stichprobe.
    """
    wurzel = tmp_path / "fullrun_test"
    monkeypatch.setattr(gs, "FULLRUN_TEST_ROOT", wurzel)
    gezogen = [item.unit_id for item in sample[:gs.TEST_UNIT_COUNT]]

    industry = sorted(taxonomy.ids)[0]
    fake = tmp_path / "antworten"
    fake.mkdir()
    for unit_id in gezogen:
        task = gs.load_task(unit_id)
        (fake / f"{unit_id}.json").write_text(
            json.dumps(build_answer(task, industry), ensure_ascii=False),
            encoding="utf-8")
    monkeypatch.setenv(gs.FAKE_DIR_ENV, str(fake))

    gs.use_fullrun_paths(wurzel)
    gs.write_json(gs.TEST_SELECTION_PATH, {
        "selection_format": gs.TEST_SELECTION_FORMAT,
        "unit_count": len(gezogen),
        "unit_ids": sorted(gezogen),
        "drawn_at": gezogen_vor(timedelta(0)),
    })
    return tmp_path


def gezogen_vor(alter: timedelta) -> str:
    """Ziehungszeitpunkt relativ zu jetzt, wie ``draw_test_units`` ihn schreibt.

    Relativ, nicht fest: ein eingetragenes Datum waere ab dem Folgetag aelter
    als die Haltbarkeit, und die Tests pruefen dann die Aufraeumung statt der
    Sache, um die es ihnen geht.
    """
    return (datetime.now(timezone.utc) - alter).isoformat(timespec="seconds")


def test_testlauf_geht_denselben_weg_und_bleibt_in_der_eigenen_ablage(
        test_sandbox, capsys) -> None:
    """Ganze Kette bis zur Mappe; Gold-Set und Fullrun bleiben unberuehrt."""
    goldset_stand = sorted(p.name for p in gs.GOLDSET_ROOT.iterdir())
    fullrun_stand = (sorted(p.name for p in gs.FULLRUN_ROOT.iterdir())
                     if gs.FULLRUN_ROOT.exists() else None)

    assert lauf("label", "--test-fullrun", "--engine", "claude") == 0
    ausgabe = capsys.readouterr().out

    assert "Testlauf: 5 zufaellig gezogene Units" in ausgabe
    mappe = test_sandbox / "fullrun_test" / "fullrun_test_review.xlsx"
    assert mappe.exists()
    assert (test_sandbox / "fullrun_test" / "tasks" / "index.json").exists()
    labels = load_workbook(mappe)[gs.SHEET_LABELS]
    assert labels.max_row == gs.TEST_UNIT_COUNT + 1
    assert sorted(p.name for p in gs.GOLDSET_ROOT.iterdir()) == goldset_stand
    assert (sorted(p.name for p in gs.FULLRUN_ROOT.iterdir())
            if gs.FULLRUN_ROOT.exists() else None) == fullrun_stand


def test_testlauf_labelt_genau_die_gezogenen_units(test_sandbox, capsys) -> None:
    assert lauf("prepare", "--test-fullrun") == 0
    capsys.readouterr()
    gezogen = set(gs.load_test_selection())

    assert {pfad.stem for pfad in gs.TASK_ROOT.glob("*.json")} == gezogen | {"index"}
    assert gs.read_json(gs.TASK_ROOT / "index.json")["unit_count"] == \
        gs.TEST_UNIT_COUNT


def test_kaputte_ziehung_haelt_den_testlauf_an(test_sandbox, capsys) -> None:
    """Eine unlesbare Auswahl faellt auf, statt still eine neue zu ziehen."""
    gs.TEST_SELECTION_PATH.write_text("[]", encoding="utf-8")

    assert lauf("prepare", "--test-fullrun") == 1
    assert "GS_TEST_SELECTION" in capsys.readouterr().err


def test_ziehung_aus_einem_aelteren_korpus_haelt_den_testlauf_an(
        test_sandbox, capsys) -> None:
    gs.write_json(gs.TEST_SELECTION_PATH, {
        "selection_format": gs.TEST_SELECTION_FORMAT,
        "unit_count": 1,
        "unit_ids": ["house--nicht-mehr-vorhanden"],
        "drawn_at": gezogen_vor(timedelta(0)),
    })

    assert lauf("prepare", "--test-fullrun") == 1
    fehler = capsys.readouterr().err
    assert "GS_TEST_SELECTION" in fehler
    assert "house--nicht-mehr-vorhanden" in fehler



def test_eine_abgelaufene_testablage_raeumt_sich_selbst_weg(
        test_sandbox, capsys) -> None:
    """Nach einem Tag beginnt der Testlauf neu, statt an sich selbst zu haengen."""
    assert lauf("prepare", "--test-fullrun") == 0
    alte_ziehung = set(gs.load_test_selection())
    (gs.RUN_ROOT / "claude").mkdir(parents=True, exist_ok=True)
    (gs.RUN_ROOT / "claude" / "irgendwas.json").write_text("{}", encoding="utf-8")
    auswahl = gs.read_json(gs.TEST_SELECTION_PATH)
    auswahl["drawn_at"] = gezogen_vor(gs.TEST_MAX_AGE + timedelta(minutes=1))
    gs.write_json(gs.TEST_SELECTION_PATH, auswahl)
    capsys.readouterr()

    assert lauf("prepare", "--test-fullrun") == 0
    ausgabe = capsys.readouterr().out

    assert "Testablage verworfen" in ausgabe
    assert not (gs.RUN_ROOT / "claude").exists()
    assert gs.load_test_selection() is not None
    assert len(gs.load_test_selection()) == gs.TEST_UNIT_COUNT
    assert {pfad.stem for pfad in gs.TASK_ROOT.glob("*.json")} == \
        set(gs.load_test_selection()) | {"index"}
    assert alte_ziehung is not None


def test_eine_frische_testablage_bleibt_liegen(test_sandbox, capsys) -> None:
    """Ein unterbrochener Lauf wird am selben Tag fortgesetzt, nicht neu bezahlt."""
    assert lauf("prepare", "--test-fullrun") == 0
    gezogen = gs.load_test_selection()
    auswahl = gs.read_json(gs.TEST_SELECTION_PATH)
    auswahl["drawn_at"] = gezogen_vor(gs.TEST_MAX_AGE - timedelta(minutes=1))
    gs.write_json(gs.TEST_SELECTION_PATH, auswahl)
    capsys.readouterr()

    assert lauf("prepare", "--test-fullrun") == 0

    assert "Testablage verworfen" not in capsys.readouterr().out
    assert gs.load_test_selection() == gezogen


def test_eine_ziehung_ohne_zeitpunkt_wird_nicht_weggeraeumt(
        test_sandbox, capsys) -> None:
    """Geloescht wird nur, was sich selbst als alt ausweist."""
    auswahl = gs.read_json(gs.TEST_SELECTION_PATH)
    auswahl.pop("drawn_at")
    gs.write_json(gs.TEST_SELECTION_PATH, auswahl)
    capsys.readouterr()

    assert lauf("prepare", "--test-fullrun") == 0

    assert "Testablage verworfen" not in capsys.readouterr().out
    assert gs.TEST_SELECTION_PATH.exists()


def test_fullrun_und_testlauf_schliessen_einander_aus(capsys) -> None:
    with pytest.raises(SystemExit):
        gs.main(["prepare", "--fullrun", "--test-fullrun"])
    assert "not allowed with argument" in capsys.readouterr().err


def test_finish_testlauf_reicht_den_schalter_an_das_exportmodul(
        test_sandbox, monkeypatch, capsys) -> None:
    """Ohne den Schalter laese das Exportmodul die Mappe des Fullruns."""
    assert lauf("label", "--test-fullrun", "--engine", "claude") == 0
    gerufen: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(gs, "_fullrun_import",
                        lambda review_id, source, test=False: gerufen.append(
                            (review_id, source, test)) or 0)
    capsys.readouterr()

    assert lauf("finish", "--test-fullrun", "--engine", "claude",
                "--review-id", "map-test") == 0
    assert gerufen == [("map-test", "claude", True)]
    assert "'scope': 'test-fullrun'" in capsys.readouterr().out


# -- Taxonomie-Refresh vor prepare ----------------------------------------
class _Drift:
    def __init__(self, blocking: bool) -> None:
        self.blocking = blocking
        self.added = ("neu",) if blocking else ()
        self.removed = ()
        self.renamed = ()
        self.resectored = ()


class _Snapshotergebnis:
    def __init__(self, *, industries: int = 145, drift: _Drift | None = None) -> None:
        self.industries = industries
        self.drift = drift


def _refresh_args(**overrides) -> argparse.Namespace:
    werte = {"no_taxonomy_refresh": False}
    werte.update(overrides)
    return argparse.Namespace(**werte)


@pytest.fixture(autouse=True)
def kein_netzabruf(monkeypatch):
    """Kein Test ruft yfinance an.

    ``prepare`` baut die Taxonomie frisch; faellt eine Wache aus, liefe der
    echte Abruf minutenlang und der Test haenge, statt einen Befund zu zeigen.
    Wer den Refresh prueft, ersetzt diese Stelle selbst.
    """
    def netzabruf(*args, **kwargs):
        raise AssertionError("Netzabruf im Test")

    monkeypatch.setattr(gs.taxonomy_snapshot, "refresh_baseline", netzabruf)


@pytest.fixture
def taxonomie_sandbox(tmp_path, monkeypatch):
    """Refresh gegen eine Ablage, die kein echtes Artefakt beruehrt."""
    monkeypatch.setattr(gs, "RUN_ROOT", tmp_path / "runs")
    baseline = tmp_path / "taxonomy" / "approved_baseline.json"
    monkeypatch.setattr(gs.taxonomy_snapshot, "baseline_path", lambda: baseline)
    monkeypatch.setattr(gs.taxonomy_snapshot, "archived_run_id", lambda: "tax-alt")
    monkeypatch.setattr(
        gs.taxonomy_snapshot, "archive_path",
        lambda run_id: tmp_path / "taxonomy" / "archiv" / f"approved_baseline_{run_id}.json")
    return tmp_path


def _antwort_ablegen(sandbox: Path) -> None:
    ziel = sandbox / "runs" / "claude"
    ziel.mkdir(parents=True, exist_ok=True)
    (ziel / "house--rules.json").write_text("{}", encoding="utf-8")


def test_refresh_bleibt_bei_begonnenem_lauf_stehen(taxonomie_sandbox) -> None:
    """Ein neuer Snapshot entwertete die schon bezahlten Antworten."""
    _antwort_ablegen(taxonomie_sandbox)
    with pytest.raises(gs.GoldsetBlocked) as blocked:
        gs.refresh_taxonomy(_refresh_args())
    assert blocked.value.problem.code == "GS_TAXONOMY_RUN_ACTIVE"
    assert "--no-taxonomy-refresh" in blocked.value.problem.abhilfe


def test_refresh_uebersprungen_ruehrt_die_taxonomie_nicht_an(
        taxonomie_sandbox, monkeypatch, capsys) -> None:
    def nie_aufrufen(*args, **kwargs):  # pragma: no cover - darf nicht laufen
        raise AssertionError("Abruf trotz --no-taxonomy-refresh")

    monkeypatch.setattr(gs.taxonomy_snapshot, "refresh_baseline", nie_aufrufen)
    _antwort_ablegen(taxonomie_sandbox)
    gs.refresh_taxonomy(_refresh_args(no_taxonomy_refresh=True))
    assert "uebersprungen" in capsys.readouterr().out


def test_refresh_meldet_freigabe_und_archiv(
        taxonomie_sandbox, monkeypatch, capsys) -> None:
    baseline = taxonomie_sandbox / "taxonomy" / "approved_baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        gs.taxonomy_snapshot, "refresh_baseline",
        lambda collector, client, *, run_id: (_Snapshotergebnis(), "a" * 64))

    gs.refresh_taxonomy(_refresh_args())

    ausgabe = capsys.readouterr().out
    assert "145 Industries freigegeben" in ausgabe
    assert "approved_baseline_tax-alt.json" in ausgabe


def test_refresh_ohne_vorgaenger_meldet_kein_archiv(
        taxonomie_sandbox, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        gs.taxonomy_snapshot, "refresh_baseline",
        lambda collector, client, *, run_id: (_Snapshotergebnis(), "b" * 64))

    gs.refresh_taxonomy(_refresh_args())

    assert "Archiv:" not in capsys.readouterr().out


def test_identitaetsdrift_haelt_prepare_am_gate_an(
        taxonomie_sandbox, monkeypatch) -> None:
    monkeypatch.setattr(
        gs.taxonomy_snapshot, "refresh_baseline",
        lambda collector, client, *, run_id: (
            _Snapshotergebnis(drift=_Drift(blocking=True)), None))

    with pytest.raises(gs.GoldsetBlocked) as blocked:
        gs.refresh_taxonomy(_refresh_args())

    problem = blocked.value.problem
    assert problem.code == "GS_TAXONOMY_REFRESH"
    assert "driftet" in problem.was
    assert "snapshot approve" in problem.abhilfe


def test_gescheiterter_abruf_nennt_den_befundcode(
        taxonomie_sandbox, monkeypatch) -> None:
    from committee_matcher.errors import ErrorCollector, Stage

    def abruf_scheitert(collector, client, *, run_id):
        collector.blocker("TAX_FETCH_FAILED", Stage.TAXONOMY, run_id,
                          "Abruf gescheitert.", "Spaeter erneut versuchen.")
        return _Snapshotergebnis(industries=0), None

    monkeypatch.setattr(gs.taxonomy_snapshot, "refresh_baseline", abruf_scheitert)

    with pytest.raises(gs.GoldsetBlocked) as blocked:
        gs.refresh_taxonomy(_refresh_args())

    assert "TAX_FETCH_FAILED" in blocked.value.problem.was


# -- Gleichzeitige Laeufe (--combined) -------------------------------------
def test_combined_ausserhalb_des_bereichs_blockiert(sandbox, capsys) -> None:
    """0 und 6 sind keine Zahl gleichzeitiger Aufrufe, sondern ein Vertipper."""
    for wert in ("0", str(gs.MAX_COMBINED + 1), "-2"):
        assert lauf("run", "--engine", "claude", "--combined", wert) == 1
        assert "GS_COMBINED_RANGE" in capsys.readouterr().err


def test_combined_eins_geht_nicht_durch_den_pool(sandbox, monkeypatch,
                                                 capsys) -> None:
    """Ohne Angabe und bei 1 bleibt der Weg der sequenzielle.

    Nicht kosmetisch: der Pool ist die einzige Stelle, an der Reihenfolge und
    Ausgabe des bisherigen Laufs sich aendern koennen. Wer ihn nicht anfordert,
    soll ihn auch nicht bekommen.
    """
    monkeypatch.setattr(gs, "ThreadPoolExecutor",
                        lambda *a, **k: pytest.fail("kein Pool bei combined=1"))
    assert lauf("run", "--engine", "claude") == 0
    assert lauf("run", "--engine", "claude", "--combined", "1",
                "--force") == 0
    ausgabe = capsys.readouterr().out
    # Der sequenzielle Weg meldet den Beginn einer Unit vor ihrem Ergebnis.
    assert "laeuft ..." in ausgabe


def test_combined_labelt_jede_unit_genau_einmal(sandbox, sample,
                                                monkeypatch, capsys) -> None:
    """Drei gleichzeitig: dieselbe Menge, keine doppelt, keine ausgelassen."""
    gerufen: list[str] = []
    sperre = threading.Lock()
    echt = gs.run_fake

    def zaehlend(root, task, config):
        if task.get("unit_id"):
            with sperre:
                gerufen.append(task["unit_id"])
        return echt(root, task, config)

    monkeypatch.setattr(gs, "run_fake", zaehlend)
    assert lauf("run", "--engine", "claude", "--combined", "3") == 0
    capsys.readouterr()

    erwartet = sorted(item.unit_id for item in sample)
    assert sorted(gerufen) == erwartet
    assert sorted(p.stem for p in (gs.RUN_ROOT / "claude").glob("*.json")) \
        == erwartet


def test_combined_bleibt_mit_collect_vertraeglich(sandbox) -> None:
    """Parallel erzeugte Antworten sind dieselben Antworten."""
    assert lauf("run", "--engine", "claude", "--combined", "4") == 0
    assert lauf("collect", "--engine", "claude") == 0


def test_abofenster_laesst_nur_einen_stellvertreter_zu() -> None:
    """Das Tor ist genau einmal zu vergeben; der Zweite wartet nur mit."""
    tor = gs.Abofenster()
    assert tor.uebernimm() is True
    assert tor.uebernimm() is False
    tor.gib_frei()
    assert tor.uebernimm() is True


def test_am_ratenlimit_wartet_nur_ein_arbeiter(monkeypatch) -> None:
    """Fuenf Arbeiter im Limit ergeben einen Wartetakt, nicht fuenf.

    Das ist der Kern des Parallelbetriebs: ohne das gemeinsame Tor liefe jeder
    Arbeiter fuer sich in denselben Zehnminutentakt und klopfte zu fuenft an
    ein Fenster, das ohnehin zu ist.
    """
    tor = gs.Abofenster()
    sperre = threading.Lock()
    schlafend = 0
    hoechstens_zugleich = 0
    # Nach dem ersten Wartetakt antwortet die CLI wieder allen.
    frei = threading.Event()

    def schlafe(sekunden):
        nonlocal schlafend, hoechstens_zugleich
        with sperre:
            schlafend += 1
            hoechstens_zugleich = max(hoechstens_zugleich, schlafend)
        frei.set()
        with sperre:
            schlafend -= 1

    monkeypatch.setattr(gs.time, "sleep", schlafe)

    def runner(task, config, workdir):
        return _erfolg() if frei.is_set() else _fehlschlag(RATE_LIMIT_TEXT)

    with ThreadPoolExecutor(max_workers=5) as pool:
        ergebnisse = [f.result() for f in [
            pool.submit(gs.run_until_quota, runner, {}, _config(),
                        wait=True, tor=tor)
            for _ in range(5)]]

    assert all(result.answer is not None for result, _ in ergebnisse)
    # Die eigentliche Zusicherung: nie warten zwei nebeneinander. Wie viele
    # Takte insgesamt anfallen, haengt am Zufall der Threadverschraenkung -
    # ein Aufruf, der nach der Freigabe erneut ins Limit laeuft, wartet zu
    # Recht ein zweites Mal.
    assert hoechstens_zugleich == 1


def test_das_tor_wird_auch_nach_einem_fehler_freigegeben(monkeypatch) -> None:
    """Ein Abbruch im Stellvertreter darf die uebrigen nicht aussperren.

    Der erste Aufruf laeuft ins Limit - erst dadurch wird dieser Arbeiter zum
    Stellvertreter und das Tor tatsaechlich geschlossen. Der zweite wirft.
    """
    tor = gs.Abofenster()
    monkeypatch.setattr(gs.time, "sleep", lambda _: None)
    aufrufe = []

    def runner(task, config, workdir):
        aufrufe.append(1)
        if len(aufrufe) == 1:
            return _fehlschlag(RATE_LIMIT_TEXT)
        raise RuntimeError("die CLI verschwindet mitten im Lauf")

    with pytest.raises(RuntimeError):
        gs.run_until_quota(runner, {}, _config(), wait=True, tor=tor)
    assert len(aufrufe) == 2, "das Tor wurde nie uebernommen"
    # Waere es zu geblieben, blockierte der naechste Arbeiter fuer immer.
    assert tor.uebernimm() is True


def test_systemischer_fehler_stoppt_das_nachruecken(sandbox, sample,
                                                    monkeypatch,
                                                    capsys) -> None:
    """Ein unbekanntes Modell trifft jeden Aufruf; es wird nicht nachgerueckt."""
    gerufen: list[str] = []
    sperre = threading.Lock()

    def scheitert(root, task, config):
        if task.get("unit_id"):
            with sperre:
                gerufen.append(task["unit_id"])
        return _fehlschlag("unknown model")

    monkeypatch.setattr(gs, "run_fake", scheitert)
    assert lauf("run", "--engine", "claude", "--combined", "2") == 2
    ausgabe = capsys.readouterr().out
    assert "bricht ab" in ausgabe
    # Harte Schranke statt eines Ungefaehr: das Stop-Flag faellt im Arbeiter,
    # bevor er zurueckkehrt. Hinter der Pruefung koennen also hoechstens die
    # zwei stehen, die gleichzeitig laufen duerfen - egal wie der Scheduler
    # die Threads verschraenkt.
    assert len(gerufen) <= 2 < len(sample)
    assert "Offen:" in ausgabe


def test_eine_exception_im_arbeiter_stoppt_die_warteschlange(sandbox, sample,
                                                             monkeypatch,
                                                             capsys) -> None:
    """Der teuerste Fehlerfall: der Executor darf nicht weiterbezahlen.

    Ohne Stop-Flag liefe die restliche Warteschlange beim Verlassen des
    ``with`` einfach ab - bei 225 Units waeren das 224 bezahlte Aufrufe fuer
    ein Ergebnis, das niemand mehr entgegennimmt.
    """
    gerufen: list[str] = []
    sperre = threading.Lock()

    def platzt(root, task, config):
        if task.get("unit_id"):
            with sperre:
                gerufen.append(task["unit_id"])
        raise RuntimeError("die CLI verschwindet mitten im Satz")

    monkeypatch.setattr(gs, "run_fake", platzt)
    with pytest.raises(RuntimeError):
        lauf("run", "--engine", "claude", "--combined", "2")
    capsys.readouterr()
    assert len(gerufen) <= 2 < len(sample)


def test_strg_c_bezahlt_keine_weiteren_units(sandbox, sample, monkeypatch,
                                             capsys) -> None:
    """Strg-C mitten im Parallellauf beendet ihn, statt ihn abzuarbeiten."""
    gerufen: list[str] = []
    sperre = threading.Lock()

    def abgebrochen(root, task, config):
        if task.get("unit_id"):
            with sperre:
                gerufen.append(task["unit_id"])
        raise KeyboardInterrupt

    monkeypatch.setattr(gs, "run_fake", abgebrochen)
    with pytest.raises(KeyboardInterrupt):
        lauf("run", "--engine", "claude", "--combined", "3")
    capsys.readouterr()
    assert len(gerufen) <= 3 < len(sample)


def test_doppelte_unit_id_laeuft_nur_einmal(sandbox, monkeypatch,
                                            capsys) -> None:
    """Zweimal dieselbe ID waeren sonst zwei Arbeiter an einer Antwortdatei."""
    gerufen: list[str] = []
    sperre = threading.Lock()
    echt = gs.run_fake

    def zaehlend(root, task, config):
        if task.get("unit_id"):
            with sperre:
                gerufen.append(task["unit_id"])
        return echt(root, task, config)

    monkeypatch.setattr(gs, "run_fake", zaehlend)
    unit = gs.load_sample()[0].unit_id
    assert lauf("run", "--engine", "claude", "--combined", "2",
                "--unit-id", unit, "--unit-id", unit) == 0
    capsys.readouterr()
    assert gerufen == [unit]


def test_der_parallele_lauf_meldet_jede_unit_beim_start(sandbox, sample,
                                                        capsys) -> None:
    """Sonst ist nicht erkennbar, welche Units gerade laufen."""
    assert lauf("run", "--engine", "claude", "--combined", "3") == 0
    ausgabe = capsys.readouterr().out
    for item in sample:
        assert f"{item.unit_id}: laeuft ..." in ausgabe


def test_der_gesamtdeckel_meldet_sich_nur_einmal(sandbox, monkeypatch,
                                                 capsys) -> None:
    """N Arbeiter stellen ihn im selben Moment fest, melden ihn aber einmal."""
    # Ein erschoepfter Deckel ohne echte Wartezeit: die Restzeit meldet, dass
    # nichts mehr uebrig ist. Ueber Flags ginge es nicht - die Settingpruefung
    # laesst einen so kleinen Deckel zu Recht nicht zu.
    monkeypatch.setattr(gs, "restzeit", lambda *_: 0)
    assert lauf("run", "--engine", "claude", "--combined", "3") == 2
    ausgabe = capsys.readouterr().out
    assert ausgabe.count("Gesamtdeckel von") == 1
    assert "Offen:" in ausgabe


def test_label_reicht_combined_an_den_lauf_durch(sandbox, capsys) -> None:
    """Das Flag steht an 'label', wirken muss es in 'run'."""
    assert lauf("label", "--engine", "claude", "--combined", "2") == 0
    assert "2 gleichzeitig" in capsys.readouterr().out


def test_label_prueft_combined_vor_dem_ersten_aufruf(sandbox, monkeypatch,
                                                     capsys) -> None:
    """Ein Vertipper darf nicht erst nach prepare und Preflight auffallen."""
    monkeypatch.setattr(gs, "command_prepare",
                        lambda args: pytest.fail("prepare lief trotz Blocker"))
    assert lauf("label", "--engine", "claude", "--combined", "9") == 1
    assert "GS_COMBINED_RANGE" in capsys.readouterr().err
