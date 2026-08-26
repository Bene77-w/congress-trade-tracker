"""P7b: Ergebnisartefakte des Mappinglaufs (Long-CSV, Coverage, Berichte).

Alle Tests sind netzwerkfrei und schreiben ausschliesslich in eine umgeleitete
Artefaktwurzel. Geprueft wird vor allem, dass kein Artefakt weniger zeigt als
der Lauf hergab und dass keine Identitaet aus dem Modellergebnis stammt.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from committee_matcher import artifacts, paths, workflow
from committee_matcher.models import (Assignment, IndustryRecord, MappingUnitResult,
                                      MatcherUnit, RosterCoverageRow, TaxonomySnapshot)
from committee_matcher.prompts import SystemPrompt
from committee_matcher.settings_view import (MatcherSettings, OfflineSettings,
                                             ProviderSettings)
from committee_matcher.status import ExitCode, UnitStatus

HASH_A = "a" * 64
HASH_B = "b" * 64
CONTENT_HASH = "c" * 64
RUN_ID = "run-2026-08-27"


@pytest.fixture()
def artifact_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    return tmp_path


# -- Bausteine --------------------------------------------------------------
def _unit(unit_id="house--banking--capital-markets", *, parent="house--banking",
          unit_name="Subcommittee on Capital Markets") -> MatcherUnit:
    return MatcherUnit(
        unit_id=unit_id,
        kind="subcommittee" if parent else "committee_direct",
        chamber="house",
        committee_name="Committee on Financial Services",
        unit_name=unit_name,
        input_basis="jurisdiction_text",
        source_path="data/committee_matcher_inputs/house/financial_services.md",
        evidence_text="jurisdiction over securities markets and exchanges",
        parent_unit_id=parent,
        parent_committee_name="Committee on Financial Services" if parent else None,
        mapping_input_hash=HASH_A,
    )


def _snapshot(industry_ids=("industry-0", "industry-1")) -> TaxonomySnapshot:
    return TaxonomySnapshot(
        snapshot_id=RUN_ID,
        yfinance_version="1.3.0",
        taxonomy_content_hash=CONTENT_HASH,
        industries=tuple(
            IndustryRecord(industry_id=key, industry_name=f"Name {key}",
                           sector_id=f"sector-{position}",
                           sector_name=f"Sektor {position}",
                           description="Beschreibung als Mappinggrundlage.")
            for position, key in enumerate(industry_ids)),
    )


def _result(unit_id, *industry_ids, decision="mapped", reason=None) -> MappingUnitResult:
    return MappingUnitResult(
        unit_id=unit_id,
        decision=decision,
        no_mapping_reason=reason,
        assignments=tuple(
            Assignment(industry_id=key, confidence=3,
                       evidence_excerpt="jurisdiction over securities markets",
                       rationale_short="Zustaendigkeit fuer Wertpapiermaerkte.")
            for key in industry_ids),
    )


def _state(*records: workflow.UnitRecord) -> workflow.RunState:
    state = workflow.RunState(run_id=RUN_ID)
    for record in records:
        state.record(record)
    return state


def _mapped(unit_id, *industry_ids, call_id="batch-1", provider="Anbieter A",
            generation_id="gen-1") -> workflow.UnitRecord:
    return workflow.UnitRecord(unit_id=unit_id, status=UnitStatus.MAPPED,
                               cache_key=HASH_B, result=_result(unit_id, *industry_ids),
                               call_id=call_id, provider=provider,
                               generation_id=generation_id)


def _roster(unit_id="house--banking--capital-markets",
            unit_name="Subcommittee on Capital Markets") -> RosterCoverageRow:
    return RosterCoverageRow(chamber="house",
                             committee_name="Committee on Financial Services",
                             unit_name=unit_name, coverage_status="mapped",
                             unit_id=unit_id, match_basis="exact")


def _offline(**overrides) -> OfflineSettings:
    values = dict(
        max_file_bytes=512_000, max_frontmatter_bytes=20_000, min_evidence_chars=40,
        yfinance_request_pause_s=0.4, yfinance_max_retries=3,
        yfinance_checkpoint_every=25, batch_size=3, workflow_retries=2,
        max_repair_attempts=1, chars_per_token=3.0, cost_guard=True,
    )
    values.update(overrides)
    return OfflineSettings(**values)


def _provider_settings() -> ProviderSettings:
    return ProviderSettings(
        model="deepseek/deepseek-v4-flash-0731", data_collection="deny", zdr=True,
        max_price_usd_per_mtok={"prompt": 0.30, "completion": 0.90},
        endpoint_context_tokens=64_000, max_output_tokens=4_000,
        reasoning_reserve_tokens=2_000, request_timeout_s=90.0,
        quantizations=("fp8",))


def _prompt(prompt_id="mapping_system", sha=HASH_A) -> SystemPrompt:
    return SystemPrompt(prompt_id=prompt_id, version="mapping-system-v2",
                        schema_version="mapping-contract-2.5", text="Regeltext.",
                        sha256=sha, path="data/config/committee_matcher_prompts/x.md")


def _manifest(**overrides) -> dict:
    manifest = workflow.build_manifest(
        run_id=RUN_ID,
        settings=MatcherSettings(offline=_offline(), provider=_provider_settings(),
                                 budgets={"run": 2.0, "evaluation": None,
                                          "qualification": None},
                                 provider_findings=()),
        provider_settings=_provider_settings(),
        prompts_used=[_prompt("repair_system", HASH_B), _prompt()],
        taxonomy_content_hash=CONTENT_HASH,
        snapshot_id=RUN_ID,
        input_manifest_hash=HASH_B,
        unit_count=1,
        budget_usd=2.0,
    )
    manifest.update(overrides)
    return manifest


def _guard() -> workflow.CostGuard:
    return workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(0.3, 0.9))


def _read(target) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(target.read_text(encoding="utf-8"))))


def _write_all(units, state, snapshot=None, **overrides) -> artifacts.ArtifactSet:
    units = list(units)
    values = dict(run_id=RUN_ID, units=units, state=state,
                  snapshot=snapshot if snapshot is not None else _snapshot(),
                  manifest=_manifest(unit_count=len(units)), guard=_guard(),
                  roster=[_roster()])
    values.update(overrides)
    return artifacts.write_run_artifacts(**values)


# -- Provenienz -------------------------------------------------------------
def test_the_provenance_comes_from_the_run_manifest():
    """Kopplung an die echte Manifestform, nicht an ein Testdouble."""
    prov = artifacts.provenance(_manifest())
    assert prov.run_id == RUN_ID
    assert prov.model == "deepseek/deepseek-v4-flash-0731"
    assert prov.taxonomy_content_hash == CONTENT_HASH
    assert prov.schema_name == "MappingBatchResult"
    assert len(prov.schema_sha256) == 64


def test_the_mapping_prompt_is_the_provenance_not_the_repair_prompt():
    """Der Reparaturprompt korrigiert die Form, er entscheidet nichts fachlich."""
    prov = artifacts.provenance(_manifest())
    assert prov.prompt_id == "mapping_system"
    assert prov.prompt_sha256 == HASH_A


@pytest.mark.parametrize("field", ["run_id", "snapshot_id", "model",
                                   "taxonomy_content_hash"])
def test_a_missing_manifest_field_blocks_the_artifact(field):
    """Eine leere Provenienzspalte wuerde den Mangel nur verstecken."""
    with pytest.raises(artifacts.ArtifactError) as exc:
        artifacts.provenance(_manifest(**{field: None}))
    assert exc.value.findings[0].code == "ART_PROVENANCE_INCOMPLETE"
    assert exc.value.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_a_manifest_without_the_mapping_prompt_blocks_the_artifact():
    with pytest.raises(artifacts.ArtifactError) as exc:
        artifacts.provenance(_manifest(prompts=[_prompt("repair_system").manifest_entry()]))
    assert exc.value.findings[0].code == "ART_PROVENANCE_INCOMPLETE"


# -- Long-CSV ---------------------------------------------------------------
#: Unabhaengige Spezifikation der Long-CSV. Bewusst als Literal und nicht als
#: Verweis auf die Produktionskonstante: sonst wuerde eine geloeschte
#: Pflichtspalte den Test einfach mitaendern.
EXPECTED_ASSIGNMENT_HEADER = (
    "run_id,revision_id,unit_id,chamber,committee_name,unit_name,unit_kind,"
    "input_basis,mapping_input_hash,decision,broad_scope_exception,industry_id,"
    "industry_name,sector_id,sector_name,confidence,evidence_excerpt,"
    "rationale_short,assignment_source,broad_scope_reason,call_id,provider,"
    "generation_id,model,taxonomy_content_hash,snapshot_id,prompt_id,"
    "prompt_version,prompt_sha256,schema_name,schema_sha256")

EXPECTED_COVERAGE_HEADER = (
    "run_id,revision_id,unit_id,chamber,committee_name,unit_name,unit_kind,"
    "status,assignment_count,no_mapping_reason,broad_scope_exception,"
    "broad_scope_reason,call_id,provider,generation_id,violations")


def test_the_long_csv_keeps_its_column_contract(artifact_sandbox):
    """Golden Test: nachgelagerte Werkzeuge lesen nach Name, der Review nach Position."""
    written = _write_all([_unit()], _state(_mapped(_unit().unit_id, "industry-0")))
    header = written.assignments.path.read_text(encoding="utf-8").splitlines()[0]
    assert header == EXPECTED_ASSIGNMENT_HEADER


def test_the_coverage_csv_keeps_its_column_contract(artifact_sandbox):
    written = _write_all([_unit()], _state(_mapped(_unit().unit_id, "industry-0")))
    header = written.unit_coverage.path.read_text(encoding="utf-8").splitlines()[0]
    assert header == EXPECTED_COVERAGE_HEADER


def test_a_row_names_the_call_that_produced_it(artifact_sandbox):
    """Bei freier Providerwahl beantwortet erst das, wer diese Zeile erzeugt hat."""
    unit = _unit()
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    row = _read(written.assignments.path)[0]
    assert (row["call_id"], row["provider"], row["generation_id"]) == (
        "batch-1", "Anbieter A", "gen-1")
    assert row["mapping_input_hash"] == HASH_A


def test_a_result_without_call_provenance_blocks_the_artifact(artifact_sandbox):
    unit = _unit()
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0", call_id=None)))
    assert exc.value.findings[0].code == "ART_PROVENANCE_INCOMPLETE"


def test_a_unit_without_input_hash_blocks_the_artifact(artifact_sandbox):
    """Ohne Inputhash ist eine Zeile keiner Eingabefassung zuzuordnen."""
    unit = _unit().model_copy(update={"mapping_input_hash": None})
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    assert exc.value.findings[0].code == "ART_PROVENANCE_INCOMPLETE"


def test_one_row_per_unit_industry_pair(artifact_sandbox):
    unit = _unit()
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-1", "industry-0")))
    rows = _read(written.assignments.path)
    assert [row["industry_id"] for row in rows] == ["industry-0", "industry-1"]
    assert written.assignments.rows == 2


def test_industry_and_sector_names_come_from_the_snapshot(artifact_sandbox):
    """Das Modell nennt nur die ID; jede weitere Identitaet wird hier aufgeloest."""
    unit = _unit()
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-1")))
    row = _read(written.assignments.path)[0]
    assert row["industry_name"] == "Name industry-1"
    assert row["sector_id"] == "sector-1"
    assert row["sector_name"] == "Sektor 1"


def test_an_industry_outside_the_snapshot_blocks_the_artifact(artifact_sandbox):
    unit = _unit()
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-9")))
    assert exc.value.findings[0].code == "ART_UNKNOWN_INDUSTRY"
    assert not workflow.mapping_path(RUN_ID, artifacts.ASSIGNMENTS_FILENAME).exists()


def test_a_no_mapping_unit_produces_no_assignment_row(artifact_sandbox):
    """Es gibt keine Zuordnung - die Unit steht vollzaehlig in der Coverage."""
    unit = _unit()
    record = workflow.UnitRecord(
        unit_id=unit.unit_id, status=UnitStatus.NO_MAPPING, cache_key=HASH_B,
        result=_result(unit.unit_id, decision="no_mapping",
                       reason="Keine Industry beruehrt die Zustaendigkeit."),
        call_id="batch-1", provider="Anbieter A", generation_id="gen-1")
    written = _write_all([unit], _state(record))
    assert _read(written.assignments.path) == []
    coverage = _read(written.unit_coverage.path)
    assert [row["status"] for row in coverage] == ["no_mapping"]
    assert coverage[0]["no_mapping_reason"].startswith("Keine Industry")


def test_evidence_survives_the_csv_unchanged(artifact_sandbox):
    """Nie still korrigieren: Trennzeichen und Umbrueche bleiben im Zitat."""
    unit = _unit()
    excerpt = 'jurisdiction over "securities", commodities\nand exchanges'
    result = MappingUnitResult(
        unit_id=unit.unit_id, decision="mapped",
        assignments=(Assignment(industry_id="industry-0", confidence=2,
                                evidence_excerpt=excerpt,
                                rationale_short="Zustaendigkeit fuer Maerkte."),))
    state = _state(workflow.UnitRecord(unit_id=unit.unit_id, status=UnitStatus.MAPPED,
                                       cache_key=HASH_B, result=result,
                                       call_id="batch-1", provider="Anbieter A",
                                       generation_id="gen-1"))
    written = _write_all([unit], state)
    assert _read(written.assignments.path)[0]["evidence_excerpt"] == excerpt


# -- Unit-Coverage ----------------------------------------------------------
def test_every_expected_unit_appears_exactly_once(artifact_sandbox):
    units = [_unit(), _unit("house--banking--housing", unit_name="Subcommittee on Housing")]
    written = _write_all(units, _state(_mapped(units[0].unit_id, "industry-0")))
    rows = _read(written.unit_coverage.path)
    assert [row["unit_id"] for row in rows] == sorted(u.unit_id for u in units)
    assert len(rows) == len(units)


def test_a_unit_the_run_never_decided_is_not_decided(artifact_sandbox):
    """``not_decided`` ist kein Mappingurteil, sondern eine Aussage ueber den Lauf."""
    unit = _unit()
    written = _write_all([unit], _state())
    row = _read(written.unit_coverage.path)[0]
    assert row["status"] == "not_decided"
    assert row["assignment_count"] == "0"
    assert row["violations"] == ""


def test_a_failed_unit_shows_its_violation_codes(artifact_sandbox):
    unit = _unit()
    record = workflow.UnitRecord(unit_id=unit.unit_id, status=UnitStatus.FAILED,
                                 cache_key=HASH_B,
                                 violations=("MAP_EVIDENCE_UNSUPPORTED", "MAP_SCHEMA"))
    written = _write_all([unit], _state(record))
    row = _read(written.unit_coverage.path)[0]
    assert row["status"] == "failed"
    assert row["violations"] == "MAP_EVIDENCE_UNSUPPORTED|MAP_SCHEMA"


def test_a_state_entry_outside_the_expected_units_blocks_the_artifact(artifact_sandbox):
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([_unit()], _state(_mapped("senate--fremd", "industry-0")))
    assert exc.value.findings[0].code == "ART_UNEXPECTED_UNIT"


def test_a_state_contradicting_its_own_result_blocks_the_artifact(artifact_sandbox):
    """Status ``mapped`` mit Entscheidung ``no_mapping`` darf nichts festschreiben."""
    unit = _unit()
    record = workflow.UnitRecord(
        unit_id=unit.unit_id, status=UnitStatus.MAPPED, cache_key=HASH_B,
        result=_result(unit.unit_id, decision="no_mapping", reason="Kein Bezug."),
        call_id="batch-1")
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(record))
    assert exc.value.findings[0].code == "ART_STATE_INCONSISTENT"


def test_a_duplicate_expected_unit_blocks_the_artifact(artifact_sandbox):
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([_unit(), _unit()], _state())
    assert exc.value.findings[0].code == "ART_DUPLICATE_UNIT"


# -- Boundary-Konflikte -----------------------------------------------------
def test_the_same_industry_in_sibling_units_becomes_a_review_item(artifact_sandbox):
    units = [_unit(), _unit("house--banking--housing", unit_name="Subcommittee on Housing")]
    state = _state(_mapped(units[0].unit_id, "industry-0"),
                   _mapped(units[1].unit_id, "industry-0"))
    written = _write_all(units, state)
    row = _read(written.boundary_conflicts.path)[0]
    assert row["family_id"] == "house--banking"
    assert row["industry_id"] == "industry-0"
    assert row["industry_name"] == "Name industry-0"
    assert row["unit_ids"] == "|".join(sorted(u.unit_id for u in units))
    assert row["unit_count"] == "2"


def test_a_run_without_conflicts_still_writes_the_header(artifact_sandbox):
    """Eine leere Datei waere von einem abgebrochenen Lauf nicht zu unterscheiden."""
    unit = _unit()
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    text = written.boundary_conflicts.path.read_text(encoding="utf-8")
    assert text.splitlines() == [",".join(artifacts.BOUNDARY_COLUMNS)]
    assert written.boundary_conflicts.rows == 0


# -- Kostenbericht ----------------------------------------------------------
def test_the_cost_report_separates_reported_from_reserved(artifact_sandbox):
    """Nur so ist sichtbar, welcher Teil der Summe eine Worst-Case-Schaetzung ist."""
    guard = _guard()
    guard.settle(0.5, cost_usd=0.002)
    guard.settle(0.4, None)
    unknown = [workflow.UnknownCall(call_id="call-1", generation_id="gen-1",
                                    reserved_usd=0.4, reason="PROV_CALL_FAILED")]
    written = _write_all([_unit()], _state(), guard=guard, unknown=unknown,
                         audited_calls=1)
    payload = json.loads(written.cost_report.path.read_text(encoding="utf-8"))
    assert payload["version"] == artifacts.COST_REPORT_VERSION
    assert payload["spent_usd"] == pytest.approx(0.002)
    assert payload["reserved_usd"] == pytest.approx(0.4)
    assert payload["committed_usd"] == pytest.approx(0.402)
    assert payload["audited_calls"] == 1
    assert payload["unknown_calls_detail"][0]["generation_id"] == "gen-1"


# -- Gesamtsatz -------------------------------------------------------------
def test_a_complete_run_writes_the_whole_artifact_set(artifact_sandbox):
    unit = _unit()
    roster = [RosterCoverageRow(chamber="house",
                                committee_name="Committee on Financial Services",
                                unit_name="Subcommittee on Capital Markets",
                                coverage_status="mapped", unit_id=unit.unit_id,
                                match_basis="exact")]
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")),
                         roster=roster)
    assert len(written.files()) == 5
    assert all(entry.path.exists() for entry in written.files())
    assert _read(written.roster_coverage.path)[0]["match_basis"] == "exact"
    assert written.report_paths() == tuple(entry.relative for entry in written.files())
    assert [Path(path).name for path in written.report_paths()] == [
        artifacts.ASSIGNMENTS_FILENAME, artifacts.UNIT_COVERAGE_FILENAME,
        artifacts.BOUNDARY_FILENAME, artifacts.COST_REPORT_FILENAME,
        artifacts.ROSTER_COVERAGE_FILENAME]


def test_a_cost_report_beyond_the_guards_own_count_blocks_the_artifact(artifact_sandbox):
    """Bericht und Kostenwache muessen denselben Lauf beschreiben."""
    guard = _guard()
    guard.settle(0.5, cost_usd=0.002)
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([_unit()], _state(), guard=guard, audited_calls=5)
    assert exc.value.findings[0].code == "ART_COST_INCONSISTENT"


def test_a_run_without_roster_coverage_blocks_the_artifact(artifact_sandbox):
    """Plan 2.8 fuehrt die Roster-Coverage als Pflichtartefakt."""
    unit = _unit()
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")), roster=[])
    assert exc.value.findings[0].code == "ART_ROSTER_MISSING"


def test_a_duplicated_roster_occurrence_blocks_the_artifact(artifact_sandbox):
    unit = _unit()
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")),
                   roster=[_roster(), _roster()])
    assert exc.value.findings[0].code == "ART_ROSTER_DUPLICATE"


def test_a_foreign_manifest_writes_nothing(artifact_sandbox):
    unit = _unit()
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")),
                   manifest=_manifest(run_id="run-fremd"))
    assert exc.value.findings[0].code == "ART_RUN_MISMATCH"
    assert not workflow.mapping_path(RUN_ID, artifacts.UNIT_COVERAGE_FILENAME).exists()


def test_a_foreign_snapshot_writes_nothing(artifact_sandbox):
    """Die Namen muessen aus der Taxonomie stammen, gegen die entschieden wurde."""
    unit = _unit()
    other = TaxonomySnapshot(snapshot_id=RUN_ID, yfinance_version="1.3.0",
                             taxonomy_content_hash=HASH_B,
                             industries=_snapshot().industries)
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")), snapshot=other)
    assert exc.value.findings[0].code == "ART_SNAPSHOT_MISMATCH"
    assert not workflow.mapping_path(RUN_ID, artifacts.ASSIGNMENTS_FILENAME).exists()


def test_a_state_from_another_run_writes_nothing(artifact_sandbox):
    """Gleiche Unit-IDs wuerden fremde Ergebnisse sonst unsichtbar uebernehmen."""
    unit = _unit()
    state = workflow.RunState(run_id="run-fremd")
    state.record(_mapped(unit.unit_id, "industry-0"))
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], state)
    assert exc.value.findings[0].code == "ART_RUN_MISMATCH"
    assert not workflow.mapping_path(RUN_ID, artifacts.ASSIGNMENTS_FILENAME).exists()


def test_a_snapshot_of_another_revision_writes_nothing(artifact_sandbox):
    """Inhaltsgleich ist nicht derselbe Kontrollinput."""
    unit = _unit()
    other = TaxonomySnapshot(snapshot_id="run-fremd", yfinance_version="1.3.0",
                             taxonomy_content_hash=CONTENT_HASH,
                             industries=_snapshot().industries)
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")), snapshot=other)
    assert exc.value.findings[0].code == "ART_SNAPSHOT_MISMATCH"


def test_a_partial_unit_set_writes_nothing(artifact_sandbox):
    """Eine Teilmenge waere in der Coverage nicht von Vollstaendigkeit zu unterscheiden."""
    unit = _unit()
    with pytest.raises(artifacts.ArtifactError) as exc:
        artifacts.write_run_artifacts(
            run_id=RUN_ID, units=[unit], state=_state(_mapped(unit.unit_id, "industry-0")),
            snapshot=_snapshot(), manifest=_manifest(unit_count=2), guard=_guard(),
            roster=[_roster()])
    assert exc.value.findings[0].code == "ART_UNIT_COUNT_MISMATCH"


def test_an_existing_revision_is_never_overwritten(artifact_sandbox):
    """Runrevisionen sind unveraenderlich; ein Ersetzen waere nirgends sichtbar."""
    unit = _unit()
    state = _state(_mapped(unit.unit_id, "industry-0"))
    _write_all([unit], state)
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], state)
    assert exc.value.findings[0].code == "ART_REVISION_EXISTS"


def test_a_complete_set_carries_its_completion_marker(artifact_sandbox):
    unit = _unit()
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    assert artifacts.is_complete(RUN_ID) is True
    index = json.loads(
        workflow.mapping_path(RUN_ID, artifacts.INDEX_FILENAME).read_text(encoding="utf-8"))
    assert [entry["name"] for entry in index["files"]] == [
        entry.path.name for entry in written.files()]


@pytest.mark.parametrize("failing_index", [0, 1, 2, 3, 4, 5])
def test_a_failed_write_leaves_no_half_set(artifact_sandbox, monkeypatch, failing_index):
    """Fault Injection an jeder Schreibstelle: entweder alles oder nichts."""
    unit = _unit()
    original = paths.write_text_atomic
    calls = {"n": 0}

    def _guarded(target, text):
        if calls["n"] == failing_index:
            calls["n"] += 1
            raise OSError("Platte voll")
        calls["n"] += 1
        return original(target, text)

    monkeypatch.setattr(paths, "write_text_atomic", _guarded)
    with pytest.raises(OSError):
        _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    folder = workflow.mapping_path(RUN_ID)
    remaining = sorted(entry.name for entry in folder.iterdir()) if folder.exists() else []
    assert remaining == []
    assert artifacts.is_complete(RUN_ID) is False


def test_an_incomplete_set_is_recognisable_without_the_marker(artifact_sandbox):
    """Ein Prozessabbruch laesst den Marker fehlen - genau daran haengt der Leser."""
    unit = _unit()
    _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    workflow.mapping_path(RUN_ID, artifacts.INDEX_FILENAME).unlink()
    assert artifacts.is_complete(RUN_ID) is False


def test_a_not_decided_unit_carrying_a_result_blocks_the_artifact(artifact_sandbox):
    """Sonst entstuenden Zeilen zu einer Unit, die die Coverage als offen ausweist."""
    unit = _unit()
    record = workflow.UnitRecord(
        unit_id=unit.unit_id, status=UnitStatus.FAILED, cache_key=HASH_B,
        result=_result(unit.unit_id, "industry-0"), call_id="batch-1")
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(record))
    assert exc.value.findings[0].code == "ART_STATE_INCONSISTENT"


def test_a_value_containing_the_cell_separator_blocks_the_artifact(artifact_sandbox):
    """Maskieren waere eine stille Aenderung des Werts."""
    unit = _unit()
    record = workflow.UnitRecord(unit_id=unit.unit_id, status=UnitStatus.FAILED,
                                 cache_key=HASH_B, violations=("MAP_A|B",))
    with pytest.raises(artifacts.ArtifactError) as exc:
        _write_all([unit], _state(record))
    assert exc.value.findings[0].code == "ART_SEPARATOR_IN_VALUE"


def test_artifacts_stay_inside_the_run_directory(artifact_sandbox):
    unit = _unit()
    written = _write_all([unit], _state(_mapped(unit.unit_id, "industry-0")))
    for entry in written.files():
        assert entry.path.parent == workflow.mapping_path(RUN_ID)
