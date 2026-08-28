"""Tests des Gold-Set-Labelwerkzeugs (scripts/committee_matcher_goldset_label.py).

Netzwerk- und CLI-frei: der Trockenlauf ueber ``GOLDSET_LABEL_FAKE_DIR``
ersetzt beide Engines durch vorbereitete Antwortdateien. Geprueft wird die
ganze Kette - Antwort, Sammeln, Reviewdatei, Rueckpruefung - und je Fehlercode
der Fall, der ihn ausloest.

Die Eingaben sind die echten Aufgabenpakete des Repos; ausgetauscht werden nur
die Schreibpfade. Ein eigener Miniaturkorpus wuerde genau die Bindung an
Taxonomie, Vertrag und Korpus nicht pruefen, die das Werkzeug herstellen soll.
"""
from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))
import committee_matcher_goldset_label as gs  # noqa: E402

SCRIPT_PATH = ROOT / "scripts" / "committee_matcher_goldset_label.py"


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
    assert lauf("verify") == 0

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

    assert lauf("merge", "--base", "claude", "--against", "codex") == 2
    bericht = gs.MERGE_REPORT.read_text(encoding="utf-8")
    assert "0 von 22 Units einig" in bericht
    assert f"nur codex: {zweite}" in bericht

    notizen = gs.notes_path("merged").read_text(encoding="utf-8")
    assert f"nur codex: {zweite}" in notizen


def test_merge_gegen_sich_selbst_blockiert(sandbox) -> None:
    assert lauf("merge", "--base", "claude", "--against", "claude") == 1


def test_collect_ohne_lauf_meldet_fehlende_units(sandbox, capsys) -> None:
    assert lauf("collect", "--engine", "claude") == 2
    assert "GS_UNIT_MISSING" in capsys.readouterr().out


def test_to_xlsx_ohne_sammlung_blockiert(sandbox, capsys) -> None:
    assert lauf("to-xlsx", "--source", "claude") == 1
    assert "GS_LABELS_MISSING" in capsys.readouterr().err


def test_verify_ohne_datei_blockiert(sandbox, capsys) -> None:
    assert lauf("verify") == 1
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


def test_zu_viele_zuordnungen(paar, taxonomy) -> None:
    task, record = paar
    vorlage = record["answer"]["assignments"][0]
    record["answer"]["assignments"] = [
        {**vorlage, "industry_id": name}
        for name in sorted(taxonomy.ids)[:gs.REGULAR_ASSIGNMENT_LIMIT + 1]]
    assert "GS_COUNT_EXCEEDED" in codes(task, record, taxonomy)


def test_breite_zustaendigkeit_erlaubt_mehr(paar, taxonomy) -> None:
    task, record = paar
    vorlage = record["answer"]["assignments"][0]
    record["answer"]["broad_scope_exception"] = True
    record["answer"]["broad_scope_reason"] = "Die Zustaendigkeit ist breit."
    record["answer"]["assignments"] = [
        {**vorlage, "industry_id": name}
        for name in sorted(taxonomy.ids)[:gs.BROAD_SCOPE_ASSIGNMENT_LIMIT]]
    assert "GS_COUNT_EXCEEDED" not in codes(task, record, taxonomy)


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
    fremd = (eltern["binding"]["evidence_text"] or "")[:60]
    if fremd and fremd.lower() not in (kind["binding"]["evidence_text"] or "").lower():
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

    assert lauf("verify") == 2
    assert "GS_INDUSTRY_CASE" in capsys.readouterr().out

    assert lauf("verify", "--fix-case") == 0
    book = load_workbook(pfad)
    assert book[gs.SHEET_LABELS].cell(row=2, column=2).value == echt


def test_verify_meldet_unbekannte_industry(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "industry_id", "gibt-es-nicht")
    capsys.readouterr()
    assert lauf("verify") == 2
    assert "GS_INDUSTRY_UNKNOWN" in capsys.readouterr().out


def test_verify_meldet_formelzelle(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "rationale_short", "=SUM(1;2)")
    capsys.readouterr()
    assert lauf("verify") == 2
    assert "GS_XLSX_FORMULA" in capsys.readouterr().out


def test_verify_meldet_fehlende_unit(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    book[gs.SHEET_LABELS].delete_rows(2)
    book.save(pfad)
    capsys.readouterr()
    assert lauf("verify") == 2
    assert "GS_UNIT_MISSING" in capsys.readouterr().out


def test_verify_meldet_widerspruch(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    zelle_setzen(pfad, 2, "no_mapping_reason", "keine Beruehrung")
    capsys.readouterr()
    assert lauf("verify") == 2
    assert "GS_DECISION_CONFLICT" in capsys.readouterr().out


def test_verify_meldet_fremdes_blattbild(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    del book[gs.SHEET_INFO]
    book.save(pfad)
    capsys.readouterr()
    assert lauf("verify") == 1
    assert "GS_XLSX_LAYOUT" in capsys.readouterr().err


def test_verify_meldet_fremde_spalte(sandbox, capsys) -> None:
    pfad = workbook_vorbereiten(sandbox)
    book = load_workbook(pfad)
    book[gs.SHEET_LABELS].cell(row=1, column=2, value="industry")
    book.save(pfad)
    capsys.readouterr()
    assert lauf("verify") == 1
    assert "GS_XLSX_LAYOUT" in capsys.readouterr().err


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


def test_unbekannte_ursache_ist_kein_schemafehler() -> None:
    found = gs.classify_failure("claude", ergebnis(stderr="segfault"), config())
    assert found.code == "GS_RUN_FAILED"


def test_mehrdeutige_antwort_wird_erkannt() -> None:
    found = gs.classify_failure(
        "codex", ergebnis(stderr="[mehrdeutig] die Abschlussdatei enthaelt 2"),
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


def test_codex_effort_gilt_als_unbestaetigt() -> None:
    assert gs.effort_confirmation("claude") == "bestaetigt"
    assert gs.effort_confirmation("codex") == "unbestaetigt"


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
