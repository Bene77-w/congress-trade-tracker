"""P7a: Runzustand, Auditspur, Kostenwache und Cache/Resume.

Alle Tests sind netzwerkfrei und schreiben ausschliesslich in eine umgeleitete
Artefaktwurzel.
"""
from __future__ import annotations

import inspect
import json

import pytest

from committee_matcher import mapping, paths, provider, workflow
from committee_matcher.errors import ErrorCollector, Stage
from committee_matcher.models import (Assignment, IndustryRecord, MappingUnitResult,
                                      MatcherUnit, TaxonomySnapshot)
from committee_matcher.prompts import SystemPrompt
from committee_matcher.settings_view import (MatcherSettings, OfflineSettings,
                                             ProviderSettings)
from committee_matcher.status import ExitCode, UnitStatus, WorkflowState

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture()
def artifact_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    return tmp_path


# -- Bausteine --------------------------------------------------------------
def _unit(unit_id="house--banking--capital-markets", *, input_hash=HASH_A) -> MatcherUnit:
    return MatcherUnit(
        unit_id=unit_id,
        kind="subcommittee",
        chamber="house",
        committee_name="Committee on Financial Services",
        unit_name="Subcommittee on Capital Markets",
        input_basis="jurisdiction_text",
        source_path="data/committee_matcher_inputs/house/financial_services.md",
        evidence_text="jurisdiction over securities markets",
        parent_unit_id="house--banking",
        parent_committee_name="Committee on Financial Services",
        mapping_input_hash=input_hash,
    )


def _prompt(prompt_id="mapping_system", sha=HASH_A) -> SystemPrompt:
    return SystemPrompt(prompt_id=prompt_id, version="mapping-system-v2",
                        schema_version="mapping-contract-2.5", text="Regeltext.",
                        sha256=sha, path="data/config/committee_matcher_prompts/x.md")


def _offline(**overrides) -> OfflineSettings:
    values = dict(
        max_file_bytes=512_000, max_frontmatter_bytes=20_000, min_evidence_chars=40,
        yfinance_request_pause_s=0.4, yfinance_max_retries=3,
        yfinance_checkpoint_every=25, batch_size=3, workflow_retries=2,
        max_repair_attempts=1, chars_per_token=3.0, cost_guard=True,
    )
    values.update(overrides)
    return OfflineSettings(**values)


def _provider_settings(**overrides) -> ProviderSettings:
    values = dict(
        model="deepseek/deepseek-v4-flash-0731", data_collection="deny", zdr=True,
        max_price_usd_per_mtok={"prompt": 0.30, "completion": 0.90},
        endpoint_context_tokens=64_000, max_output_tokens=4_000,
        reasoning_reserve_tokens=2_000, request_timeout_s=90.0,
        quantizations=("fp8",),
    )
    values.update(overrides)
    return ProviderSettings(**values)


def _settings(**overrides) -> MatcherSettings:
    return MatcherSettings(
        offline=_offline(**overrides.pop("offline", {})),
        provider=_provider_settings(**overrides.pop("provider", {})),
        budgets={"run": 2.0, "evaluation": None, "qualification": None},
        provider_findings=(),
    )


def _request(call_id="call-1", user_content="Daten", max_output_tokens=4_000):
    return provider.StructuredRequest(
        call_id=call_id, system_prompt="Regeltext.", user_content=user_content,
        schema_name=mapping.SCHEMA_NAME, json_schema=mapping.batch_schema(),
        max_output_tokens=max_output_tokens)


def _response(call_id="call-1", *, cost=0.0004, generation_id="gen-1", payload=None):
    return provider.StructuredResponse(
        call_id=call_id, payload=payload if payload is not None else {"units": []},
        model="deepseek/deepseek-v4-flash-0731", provider="Anbieter A",
        prompt_tokens=1_200, completion_tokens=300, cost_usd=cost,
        finish_reason="stop", generation_id=generation_id)


def _result(unit_id="house--banking--capital-markets", industry="industry-0"):
    return MappingUnitResult(
        unit_id=unit_id, decision="mapped",
        assignments=[Assignment(industry_id=industry, confidence=3,
                                evidence_excerpt="jurisdiction over securities markets",
                                rationale_short="Zustaendigkeit fuer Wertpapiermaerkte.")])


class _Recorder:
    """Provider, der Aufrufe mitschreibt und vorbereitete Antworten liefert."""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[str] = []

    def complete(self, request):
        self.seen.append(request.call_id)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# -- Cache-Key --------------------------------------------------------------
def test_the_same_facts_yield_the_same_key():
    keys = {workflow.unit_cache_key(
        _unit(), taxonomy_content_hash=HASH_B, prompt=_prompt(),
        model="m", inference={"max_output_tokens": 4_000}) for _ in range(3)}
    assert len(keys) == 1


@pytest.mark.parametrize("change", [
    {"unit": _unit(input_hash=HASH_B)},
    {"taxonomy_content_hash": "c" * 64},
    {"prompt": _prompt(sha="d" * 64)},
    {"model": "anderes/modell"},
    {"inference": {"max_output_tokens": 8_000}},
])
def test_every_contract_relevant_change_moves_the_key(change):
    base = dict(unit=_unit(), taxonomy_content_hash=HASH_B, prompt=_prompt(),
                model="m", inference={"max_output_tokens": 4_000})
    changed = {**base, **change}
    unit = changed.pop("unit")
    original = base.pop("unit")
    assert (workflow.unit_cache_key(unit, **changed)
            != workflow.unit_cache_key(original, **base))


def test_volatile_provenance_cannot_enter_the_key():
    """Sonst waere jeder neue Run ein vollstaendiger Cache-Miss.

    Geprueft wird die Signatur: was der Schluessel nicht annimmt, kann ihn auch
    nicht versehentlich verschieben.
    """
    names = set(inspect.signature(workflow.cache_key).parameters)
    assert not {name for name in names
                if any(word in name for word in ("run", "snapshot", "call",
                                                 "endpoint", "provider",
                                                 "evaluation", "attempt"))}


def test_a_unit_without_input_hash_has_no_key():
    unit = _unit()
    bare = unit.model_copy(update={"mapping_input_hash": None})
    with pytest.raises(ValueError):
        workflow.unit_cache_key(bare, taxonomy_content_hash=HASH_B,
                                prompt=_prompt(), model="m", inference={})


def test_the_schema_hash_reacts_to_the_schema():
    other = dict(mapping.batch_schema())
    other["title"] = "abweichend"
    assert workflow.schema_hash(mapping.batch_schema()) != workflow.schema_hash(other)


def test_inference_fingerprint_covers_the_declared_fields():
    fingerprint = workflow.inference_fingerprint(_provider_settings())
    assert set(fingerprint) == set(workflow.CACHE_INFERENCE_FIELDS)


# -- Kosten -----------------------------------------------------------------
def test_worst_case_uses_the_price_ceiling():
    rates = workflow.CostRates(prompt=1.0, completion=2.0)
    assert rates.worst_case_usd(1_000_000, 1_000_000) == pytest.approx(3.0)


def test_the_ceiling_comes_from_the_provider_settings():
    rates = workflow.CostRates.from_settings(_provider_settings())
    assert (rates.prompt, rates.completion) == (0.30, 0.90)


def test_output_is_priced_at_the_full_token_limit():
    """Weniger kann der Endpoint liefern, mehr nicht - der Deckel ist die Grenze."""
    rates = workflow.CostRates(prompt=0.0, completion=1.0)
    cost = workflow.request_worst_case_usd(_request(max_output_tokens=10_000),
                                           rates, _offline())
    assert cost == pytest.approx(10_000 / 1_000_000)


def test_a_call_within_budget_passes():
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    assert guard.check(0.5, "call-1") is None


def test_a_call_over_budget_is_blocked_with_exit_six():
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    blocker = guard.check(1.5, "call-1")
    assert blocker is not None
    assert blocker.code == "COST_BUDGET_EXCEEDED"
    assert blocker.exit_code is ExitCode.COST_BLOCKED
    assert blocker.stage is Stage.COST


def test_a_disabled_guard_still_keeps_the_books():
    """Der Schalter nimmt das Blockieren, nicht die Buchfuehrung."""
    guard = workflow.CostGuard(budget_usd=0.001, rates=workflow.CostRates(1.0, 1.0),
                               enabled=False)
    assert guard.check(99.0, "call-1") is None
    guard.settle(99.0, _response(cost=0.5))
    assert guard.spent_usd == pytest.approx(0.5)
    assert guard.report()["guard_enabled"] is False


def test_reported_costs_are_booked_as_spent():
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    guard.settle(0.9, _response(cost=0.25))
    assert guard.spent_usd == pytest.approx(0.25)
    assert guard.reserved_usd == 0.0
    assert guard.unknown_calls == 0


def test_an_unreported_cost_reserves_the_worst_case():
    """Ein Call ohne Kostenmeldung ist nicht kostenlos, nur ungeklaert."""
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    guard.settle(0.4, _response(cost=None))
    assert guard.spent_usd == 0.0
    assert guard.reserved_usd == pytest.approx(0.4)
    assert guard.unknown_calls == 1
    assert guard.remaining_usd == pytest.approx(0.6)


def test_reservations_count_against_the_budget():
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    guard.settle(0.8, _response(cost=None))
    assert guard.check(0.5, "call-2") is not None


def test_the_plan_includes_the_allowed_repairs():
    rates = workflow.CostRates(prompt=0.0, completion=1.0)
    requests = [_request(max_output_tokens=1_000_000)]
    once = workflow.plan_cost_usd(requests, rates, _offline(max_repair_attempts=1))
    never = workflow.plan_cost_usd(requests, rates, _offline(max_repair_attempts=0))
    assert once == pytest.approx(2 * never)


# -- Auditspur --------------------------------------------------------------
def test_the_audit_record_holds_provenance_and_payload():
    record = workflow.audit_record(_request(), _response(payload={"units": [1]}))
    assert record["generation_id"] == "gen-1"
    assert record["payload"] == {"units": [1]}
    assert record["version"] == workflow.CALL_AUDIT_VERSION


def test_the_audit_record_carries_no_prompt_and_no_data():
    """Prompt und Nutzdaten liegen versioniert vor; hier waeren sie nur Ballast."""
    record = workflow.audit_record(_request(user_content="GEHEIMER INPUT"), _response())
    text = json.dumps(record, ensure_ascii=False)
    assert "GEHEIMER INPUT" not in text
    assert "Regeltext" not in text


def test_a_response_is_on_disk_before_it_is_validated(artifact_sandbox):
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    audited = workflow.AuditedProvider(
        inner=_Recorder([_response()]), run_id="run-1", guard=guard, offline=_offline())
    audited.complete(_request())
    target = workflow.call_path("run-1", "call-1")
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["call_id"] == "call-1"


def test_a_call_over_budget_is_never_sent(artifact_sandbox):
    inner = _Recorder([_response()])
    guard = workflow.CostGuard(budget_usd=0.0, rates=workflow.CostRates(1.0, 1.0))
    audited = workflow.AuditedProvider(inner=inner, run_id="run-1", guard=guard,
                                       offline=_offline())
    with pytest.raises(workflow.CostBlocked):
        audited.complete(_request())
    assert inner.seen == []
    assert audited.blockers[0].exit_code is ExitCode.COST_BLOCKED


def test_a_failed_call_is_booked_as_reserved(artifact_sandbox):
    """Ob der Provider ihn abgerechnet hat, ist von hier aus nicht feststellbar."""
    error = provider.ProviderCallError(
        [ErrorCollector().blocker("PROV_CALL_TIMEOUT", Stage.PROVIDER, "call-1",
                                  "Zeitueberschreitung.", "Spaeter abgleichen.")],
        retryable=True)
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    audited = workflow.AuditedProvider(inner=_Recorder([error]), run_id="run-1",
                                       guard=guard, offline=_offline())
    with pytest.raises(provider.ProviderCallError):
        audited.complete(_request())
    assert guard.unknown_calls == 1
    assert guard.reserved_usd > 0.0
    assert not workflow.call_path("run-1", "call-1").exists()


def test_every_call_gets_its_own_audit_file(artifact_sandbox):
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(1.0, 1.0))
    audited = workflow.AuditedProvider(
        inner=_Recorder([_response("call-1"), _response("call-1-repair-1")]),
        run_id="run-1", guard=guard, offline=_offline())
    audited.complete(_request("call-1"))
    audited.complete(_request("call-1-repair-1"))
    assert audited.audited == ["call-1", "call-1-repair-1"]
    assert workflow.call_path("run-1", "call-1-repair-1").exists()


def test_an_audit_path_stays_inside_the_run(artifact_sandbox):
    with pytest.raises(paths.PathGuardError):
        workflow.call_path("run-1", "../../escape")


# -- Runzustand -------------------------------------------------------------
def test_a_written_state_reads_back_identically(artifact_sandbox):
    state = workflow.RunState(run_id="run-1", state=WorkflowState.RUNNING)
    state.record(workflow.UnitRecord(
        unit_id="u-1", status=UnitStatus.MAPPED, cache_key=HASH_A, result=_result("u-1")))
    state.write()
    loaded = workflow.load_state(ErrorCollector(), "run-1")
    assert loaded.state is WorkflowState.RUNNING
    assert loaded.units["u-1"].result.assignments[0].industry_id == "industry-0"


def test_a_missing_state_is_a_fresh_run(artifact_sandbox):
    collector = ErrorCollector()
    assert workflow.load_state(collector, "run-1").units == {}
    assert collector.findings == []


@pytest.mark.parametrize("text", [
    "kein json",
    '["liste"]',
    '{"version": "run-state-v0", "run_id": "run-1", "workflow_state": "running", "units": []}',
    '{"version": "run-state-v1", "run_id": "fremd", "workflow_state": "running", "units": []}',
    '{"version": "run-state-v1", "run_id": "run-1", "workflow_state": "erfunden", "units": []}',
    '{"version": "run-state-v1", "run_id": "run-1", "workflow_state": "running", "units": {}}',
])
def test_an_unusable_state_restarts_the_run_with_a_warning(artifact_sandbox, text):
    paths.write_text_atomic(workflow.state_path("run-1", create_parents=True), text)
    collector = ErrorCollector()
    state = workflow.load_state(collector, "run-1")
    assert state.units == {}
    assert [f.code for f in collector.findings] == ["RUN_STATE_MALFORMED"]


def test_a_single_bad_unit_discards_the_whole_state(artifact_sandbox):
    """Sonst wuerden einzelne Units aus einer kaputten Datei geglaubt."""
    good = workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                               cache_key=HASH_A, result=_result("u-1")).as_payload()
    payload = {"version": workflow.STATE_VERSION, "run_id": "run-1",
               "workflow_state": "running",
               "units": [good, {"unit_id": "u-2", "status": "erfunden",
                                "cache_key": HASH_A}]}
    paths.write_text_atomic(workflow.state_path("run-1", create_parents=True),
                            json.dumps(payload))
    state = workflow.load_state(ErrorCollector(), "run-1")
    assert state.units == {}


def test_a_result_belonging_to_another_unit_is_rejected(artifact_sandbox):
    payload = {"version": workflow.STATE_VERSION, "run_id": "run-1",
               "workflow_state": "running",
               "units": [{"unit_id": "u-1", "status": "mapped", "cache_key": HASH_A,
                          "result": _result("u-2").model_dump(mode="json"),
                          "violations": []}]}
    paths.write_text_atomic(workflow.state_path("run-1", create_parents=True),
                            json.dumps(payload))
    assert workflow.load_state(ErrorCollector(), "run-1").units == {}


def test_only_a_decided_and_checked_unit_is_reusable():
    decided = workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                                  cache_key=HASH_A, result=_result("u-1"))
    failed = workflow.UnitRecord(unit_id="u-2", status=UnitStatus.FAILED,
                                 cache_key=HASH_A, violations=("MAP_UNKNOWN_INDUSTRY",))
    empty = workflow.UnitRecord(unit_id="u-3", status=UnitStatus.MAPPED, cache_key=HASH_A)
    assert decided.reusable
    assert not failed.reusable
    assert not empty.reusable


def test_a_changed_key_invalidates_the_cached_unit():
    state = workflow.RunState(run_id="run-1")
    state.record(workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                                     cache_key=HASH_A, result=_result("u-1")))
    assert state.cached("u-1", HASH_A) is not None
    assert state.cached("u-1", HASH_B) is None


def test_pending_lists_exactly_the_open_units():
    state = workflow.RunState(run_id="run-1")
    state.record(workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                                     cache_key=HASH_A, result=_result("u-1")))
    state.record(workflow.UnitRecord(unit_id="u-2", status=UnitStatus.FAILED,
                                     cache_key=HASH_A))
    units = [_unit("u-1"), _unit("u-2"), _unit("u-3")]
    keys = {"u-1": HASH_A, "u-2": HASH_A, "u-3": HASH_A}
    assert [u.unit_id for u in state.pending(units, keys)] == ["u-2", "u-3"]


def test_a_unit_without_key_always_runs():
    state = workflow.RunState(run_id="run-1")
    state.record(workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                                     cache_key=HASH_A, result=_result("u-1")))
    assert [u.unit_id for u in state.pending([_unit("u-1")], {})] == ["u-1"]


def test_counts_cover_every_unit_status():
    state = workflow.RunState(run_id="run-1")
    state.record(workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                                     cache_key=HASH_A, result=_result("u-1")))
    counts = state.counts()
    assert set(counts) == {status.value for status in UnitStatus}
    assert counts["mapped"] == 1 and counts["failed"] == 0


def test_workflow_state_and_unit_status_stay_separate(artifact_sandbox):
    """`outcome_unknown` ist ein Laufzustand, nie ein Urteil ueber eine Unit."""
    state = workflow.RunState(run_id="run-1", state=WorkflowState.OUTCOME_UNKNOWN)
    state.record(workflow.UnitRecord(unit_id="u-1", status=UnitStatus.MAPPED,
                                     cache_key=HASH_A, result=_result("u-1")))
    state.write()
    loaded = workflow.load_state(ErrorCollector(), "run-1")
    assert loaded.state is WorkflowState.OUTCOME_UNKNOWN
    assert loaded.units["u-1"].status is UnitStatus.MAPPED


# -- Runmanifest ------------------------------------------------------------
def _manifest(**overrides) -> dict:
    values = dict(run_id="run-1", settings=_settings(),
                  provider_settings=_provider_settings(), prompts_used=[_prompt()],
                  taxonomy_content_hash=HASH_B, snapshot_id="snap-1",
                  input_manifest_hash=HASH_A, unit_count=225, budget_usd=2.0)
    values.update(overrides)
    return workflow.build_manifest(**values)


def test_the_manifest_freezes_the_run_conditions():
    manifest = _manifest()
    assert manifest["model"] == "deepseek/deepseek-v4-flash-0731"
    assert manifest["routing"]["allow_fallbacks"] is True
    assert manifest["routing"]["zdr"] is True
    assert manifest["prompts"][0]["prompt_sha256"] == HASH_A
    assert manifest["schema"]["schema_name"] == mapping.SCHEMA_NAME


def test_the_manifest_holds_no_secret_and_no_prompt_text():
    text = json.dumps(_manifest(), ensure_ascii=False)
    assert "Regeltext" not in text
    assert "api_key" not in text.lower()


def test_a_written_manifest_reads_back(artifact_sandbox):
    workflow.write_manifest("run-1", _manifest())
    assert workflow.load_manifest("run-1")["unit_count"] == 225


def test_a_manifest_of_another_version_is_not_read(artifact_sandbox):
    payload = _manifest()
    payload["version"] = "run-manifest-v0"
    paths.write_text_atomic(workflow.manifest_path("run-1", create_parents=True),
                            json.dumps(payload))
    assert workflow.load_manifest("run-1") is None


def test_an_unchanged_manifest_permits_a_resume():
    assert workflow.resume_conflicts(_manifest(), _manifest()) == ()


@pytest.mark.parametrize("change,expected", [
    ({"taxonomy_content_hash": "c" * 64}, "taxonomy_content_hash"),
    ({"input_manifest_hash": "c" * 64}, "input_manifest_hash"),
    ({"prompts_used": [_prompt(sha="c" * 64)]}, "prompts"),
    ({"provider_settings": _provider_settings(model="anderes/modell")}, "model"),
    ({"settings": _settings(offline={"batch_size": 1})}, "batching"),
])
def test_a_changed_condition_forbids_a_resume(change, expected):
    conflicts = workflow.resume_conflicts(_manifest(), _manifest(**change))
    assert expected in conflicts


def test_budget_and_snapshot_id_do_not_forbid_a_resume():
    """Sonst wuerde ein Lauf bezahlte Ergebnisse aus formalen Gruenden wegwerfen."""
    changed = _manifest(budget_usd=99.0, snapshot_id="snap-2")
    assert workflow.resume_conflicts(_manifest(), changed) == ()


def test_a_drift_blocker_names_the_fields_and_blocks_preflight():
    blocker = workflow.resume_blocker("run-1", ("model", "prompts"))
    assert blocker.exit_code is ExitCode.PREFLIGHT_BLOCKED
    assert "model" in blocker.message and "prompts" in blocker.message


# -- Ungeklaerte Calls ------------------------------------------------------
def test_a_call_with_generation_id_can_be_reconciled():
    call = workflow.UnknownCall("call-1", "gen-1", 0.001, "Zeitueberschreitung")
    assert call.reconcilable
    assert call.as_payload()["generation_id"] == "gen-1"


def test_a_call_without_generation_id_stays_open():
    assert not workflow.UnknownCall("call-1", None, 0.001, "Abbruch").reconcilable


def test_unknown_calls_exit_with_eight_and_warn_about_double_billing():
    finding = workflow.outcome_unknown_finding(
        [workflow.UnknownCall("call-1", None, 0.001, "Abbruch")])
    assert finding.exit_code is ExitCode.OUTCOME_UNKNOWN
    assert "doppelt" in finding.hint
    assert "nicht abgleichbar" in finding.hint


# -- Zusammenspiel mit dem Mappingkern --------------------------------------
def _batch_payload(batch, unit_id):
    return {"requested_batch_id": batch.batch_id,
            "units": [{"unit_id": unit_id, "decision": "mapped",
                       "no_mapping_reason": None, "broad_scope_exception": False,
                       "broad_scope_reason": None,
                       "assignments": [{
                           "industry_id": "industry-0", "confidence": 3,
                           "evidence_excerpt": "jurisdiction over securities markets",
                           "rationale_short": "Zustaendigkeit fuer Wertpapiermaerkte."}]}]}


def _snapshot() -> TaxonomySnapshot:
    return TaxonomySnapshot(
        snapshot_id="snap-1", yfinance_version="0.2.65",
        taxonomy_content_hash=HASH_B,
        industries=[IndustryRecord(
            industry_id="industry-0", industry_name="Banks",
            sector_id="financial-services", sector_name="Financial Services",
            description="Banks provide lending and securities services.")])


def test_the_mapping_core_runs_unchanged_behind_the_audit(artifact_sandbox):
    """Der Dekorator sitzt am Providerrand; ``mapping.py`` merkt davon nichts."""
    unit = _unit("u-1")
    batch = mapping.MappingBatch(mapping.batch_id_for(["u-1"]), (unit,))
    guard = workflow.CostGuard(budget_usd=1.0, rates=workflow.CostRates(0.3, 0.9))
    audited = workflow.AuditedProvider(
        inner=_Recorder([_response(f"{batch.batch_id}-call", payload=_batch_payload(batch, "u-1"))]),
        run_id="run-1", guard=guard, offline=_offline())
    outcome = mapping.map_batch(
        audited, batch, _snapshot(), mapping_prompt=_prompt(),
        repair_prompt=_prompt("repair_system"), offline=_offline(),
        provider_settings=_provider_settings())
    assert outcome.ok
    assert len(audited.audited) == 1
    assert guard.spent_usd > 0.0


def test_an_exhausted_budget_stops_the_batch_before_the_call(artifact_sandbox):
    unit = _unit("u-1")
    batch = mapping.MappingBatch(mapping.batch_id_for(["u-1"]), (unit,))
    inner = _Recorder([])
    audited = workflow.AuditedProvider(
        inner=inner, run_id="run-1",
        guard=workflow.CostGuard(budget_usd=0.0, rates=workflow.CostRates(0.3, 0.9)),
        offline=_offline())
    with pytest.raises(workflow.CostBlocked):
        mapping.map_batch(audited, batch, _snapshot(), mapping_prompt=_prompt(),
                          repair_prompt=_prompt("repair_system"), offline=_offline(),
                          provider_settings=_provider_settings())
    assert inner.seen == []
