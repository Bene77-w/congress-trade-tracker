"""Tests fuer das providerfreie Geruest (P2) des Committee Matchers V2.

Gepinnt werden die Vertraege aus ``organisation/committee_matcher_v2_implementierungsplan.md``:
Exit-Code-Matrix und Workflowzustaende (2.6/2.7), Terminalbericht (2.7),
Pfadwache und Schreibschutz (2.1/2.8), typisierte Settingssicht inklusive
Providerblockade (2.6) sowie die formalen Mappingregeln (2.5).

Alle Tests sind netzwerkfrei und schreiben ausschliesslich unter ``tmp_path``.
Der Inputroot und die Produktionspfade werden nur read-only beruehrt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pydantic
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import SCAFFOLD_VERSION, cli, errors, models, paths, settings_view, status  # noqa: E402
from committee_matcher.errors import ErrorCollector, Severity, Stage  # noqa: E402
from committee_matcher.settings_view import SettingsBlocked, load_settings  # noqa: E402
from committee_matcher.status import (  # noqa: E402
    CoverageStatus,
    ExitCode,
    InputBasis,
    MappingDecision,
    MatchBasis,
    UnitKind,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Status, Exit-Codes und Zustaende (Plan 2.6/2.7)
# ---------------------------------------------------------------------------

def test_exit_code_matrix_is_frozen():
    """Die Exit-Codes sind Vertrag; 1 bleibt bewusst unbelegt."""
    assert {code.name: int(code) for code in ExitCode} == {
        "SUCCESS": 0,
        "SUCCESS_WITH_WARNINGS": 2,
        "FAILED_MAPPING_UNITS": 3,
        "PREFLIGHT_BLOCKED": 4,
        "AWAITING_TAXONOMY_APPROVAL": 5,
        "COST_BLOCKED": 6,
        "REVIEW_IMPORT_REJECTED": 7,
        "OUTCOME_UNKNOWN": 8,
        "PROVIDER_ERROR": 9,
        "INTERNAL_ERROR": 10,
        "USER_ABORT": 130,
    }
    assert 1 not in {int(code) for code in ExitCode}


def test_workflow_states_cover_the_required_minimum():
    required = {
        "pending", "running", "awaiting_taxonomy_approval",
        "awaiting_development_goldset", "awaiting_holdout",
        "awaiting_manual_review", "failed", "outcome_unknown", "completed",
    }
    assert {state.value for state in WorkflowState} == required


def test_outcome_unknown_is_never_a_mapping_verdict():
    assert status.is_mapping_verdict(WorkflowState.COMPLETED) is True
    assert status.is_mapping_verdict(WorkflowState.OUTCOME_UNKNOWN) is False
    assert status.exit_code_for_state(WorkflowState.OUTCOME_UNKNOWN) is ExitCode.OUTCOME_UNKNOWN


def test_every_workflow_state_has_an_exit_code():
    for state in WorkflowState:
        assert status.exit_code_for_state(state) in set(ExitCode)


def test_worst_exit_code_follows_the_documented_priority():
    assert status.worst_exit_code([]) is ExitCode.SUCCESS
    assert status.worst_exit_code(
        [ExitCode.SUCCESS_WITH_WARNINGS, ExitCode.COST_BLOCKED]
    ) is ExitCode.COST_BLOCKED
    assert status.worst_exit_code(
        [ExitCode.COST_BLOCKED, ExitCode.INTERNAL_ERROR]
    ) is ExitCode.INTERNAL_ERROR


# ---------------------------------------------------------------------------
# Fehlercollector und Terminalbericht (Plan 2.7)
# ---------------------------------------------------------------------------

def test_collector_exit_codes_by_severity():
    empty = ErrorCollector()
    assert empty.exit_code() is ExitCode.SUCCESS

    warned = ErrorCollector()
    warned.warning("W", Stage.INPUT, "obj", "m", "h")
    assert warned.exit_code() is ExitCode.SUCCESS_WITH_WARNINGS

    blocked = ErrorCollector()
    blocked.blocker("B", Stage.INPUT, "obj", "m", "h")
    assert blocked.exit_code() is ExitCode.PREFLIGHT_BLOCKED


def test_explicit_blocker_exit_code_wins_over_the_default():
    collector = ErrorCollector()
    collector.blocker("B1", Stage.INPUT, "obj", "m", "h")
    collector.blocker("B2", Stage.COST, "budget", "m", "h", exit_code=ExitCode.COST_BLOCKED)
    assert collector.exit_code() is ExitCode.COST_BLOCKED


def test_info_findings_do_not_change_the_exit_code():
    collector = ErrorCollector()
    collector.info("I", Stage.INPUT, "obj", "m", "h")
    assert collector.exit_code() is ExitCode.SUCCESS


def test_report_is_rendered_without_any_finding():
    text = errors.render_report(ErrorCollector(), headline="Leerlauf")
    assert "Befunde: keine" in text
    assert "Kosten: 0.00 USD (providerfreie Phase, keine Modellaufrufe)" in text
    assert "Exit-Code: 0 (success)" in text


def test_report_groups_by_severity_and_stage_and_caps_objects():
    collector = ErrorCollector()
    for index in range(7):
        collector.blocker("MANY", Stage.INPUT, f"datei{index}.md", "msg", "hint")
    collector.warning("ONE", Stage.TAXONOMY, "industry", "wmsg", "whint")
    text = errors.render_report(collector, headline="Bericht")

    assert "[BLOCKER] 7" in text and "[WARNUNG] 1" in text
    assert "Stage input" in text and "Stage taxonomy" in text
    assert "(+2 weitere)" in text
    assert "datei5.md" not in text
    assert text.index("[BLOCKER]") < text.index("[WARNUNG]")


def test_report_shows_cost_and_artifacts_when_given():
    collector = ErrorCollector()
    text = errors.render_report(
        collector,
        headline="Lauf",
        artifacts=["data/committee_matcher_v2/runs/r1/manifest.json"],
        cost_usd=0.1234,
    )
    assert "Kosten: 0.1234 USD" in text
    assert "runs/r1/manifest.json" in text


def test_findings_persist_as_structured_records(tmp_path):
    collector = ErrorCollector()
    collector.blocker("B", Stage.COST, "budget", "m", "h", line=7, exit_code=ExitCode.COST_BLOCKED)
    target = collector.write_json(tmp_path / "errors" / "report.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["findings"] == [
        {
            "severity": "BLOCKER", "code": "B", "stage": "cost", "object": "budget",
            "line": 7, "message": "m", "hint": "h", "exit_code": 6,
        }
    ]


def test_raise_if_blocked_carries_exit_code():
    collector = ErrorCollector()
    collector.blocker("B", Stage.PROVIDER, "call", "m", "h", exit_code=ExitCode.PROVIDER_ERROR)
    with pytest.raises(errors.MatcherError) as excinfo:
        collector.raise_if_blocked()
    assert excinfo.value.exit_code is ExitCode.PROVIDER_ERROR


# ---------------------------------------------------------------------------
# Pfadwache (Plan 2.1/2.8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "parts,code",
    [
        ((".." , "etc"), "PATH_TRAVERSAL"),
        (("runs/../../etc",), "PATH_TRAVERSAL"),
        (("/etc/passwd",), "PATH_ABSOLUTE"),
    ],
)
def test_resolve_within_blocks_escaping_paths(parts, code):
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.resolve_within(paths.ARTIFACT_ROOT, *parts)
    assert excinfo.value.findings[0].code == code
    assert excinfo.value.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_resolve_within_blocks_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.resolve_within(root, "link", "secret.txt")
    assert excinfo.value.findings[0].code == "PATH_ESCAPE"


def test_resolve_within_accepts_a_regular_relative_path():
    target = paths.resolve_within(paths.ARTIFACT_ROOT, "runs", "r1", "manifest.json")
    assert paths.relative_to_base(target) == "data/committee_matcher_v2/runs/r1/manifest.json"


def test_resolve_within_can_require_existence(tmp_path):
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.resolve_within(tmp_path, "fehlt.txt", must_exist=True)
    assert excinfo.value.findings[0].code == "PATH_MISSING"


def test_input_root_is_read_only_for_the_matcher():
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.assert_writable(paths.INPUT_ROOT / "md" / "senate_finance.md")
    assert excinfo.value.findings[0].code == "PATH_INPUT_READONLY"


def test_protected_production_paths_block_every_write():
    protected = sorted(paths.protected_paths())
    assert protected, "Schutzbaseline liefert keine Pfade"
    for target in protected:
        with pytest.raises(paths.PathGuardError) as excinfo:
            paths.assert_writable(target)
        assert excinfo.value.findings[0].code == "PATH_PROTECTED"


def test_protected_baseline_matches_the_p0_artifact():
    stored = {paths.relative_to_base(p) for p in paths.protected_paths()}
    assert "data/mappings/subcommittee_sector_industry_mapping.csv" in stored
    assert len(stored) == 9


def test_write_outside_the_artifact_root_is_denied(tmp_path):
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.assert_writable(tmp_path / "beliebig.json")
    assert excinfo.value.findings[0].code == "PATH_WRITE_DENIED"


def test_read_outside_the_known_roots_is_denied(tmp_path):
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.assert_readable(tmp_path / "fremd.csv")
    assert excinfo.value.findings[0].code == "PATH_READ_DENIED"


def test_missing_protection_baseline_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PROTECTED_BASELINE_PATH", tmp_path / "fehlt.yaml")
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.assert_writable(paths.ARTIFACT_ROOT / "runs" / "r1.json")
    assert excinfo.value.findings[0].code == "PATH_BASELINE_MISSING"


def test_malformed_protection_baseline_fails_closed(tmp_path, monkeypatch):
    broken = tmp_path / "broken.yaml"
    broken.write_text("paths: [\n", encoding="utf-8")
    monkeypatch.setattr(paths, "PROTECTED_BASELINE_PATH", broken)
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.assert_writable(paths.ARTIFACT_ROOT / "runs" / "r1.json")
    assert excinfo.value.findings[0].code == "PATH_BASELINE_MALFORMED"


def test_baseline_without_path_field_fails_closed(tmp_path, monkeypatch):
    broken = tmp_path / "incomplete.yaml"
    broken.write_text("paths:\n- bytes: 1\n", encoding="utf-8")
    monkeypatch.setattr(paths, "PROTECTED_BASELINE_PATH", broken)
    with pytest.raises(paths.PathGuardError) as excinfo:
        paths.assert_writable(paths.ARTIFACT_ROOT / "runs" / "r1.json")
    assert excinfo.value.findings[0].code == "PATH_BASELINE_MALFORMED"


def test_artifact_path_does_not_create_directories_by_default():
    target = paths.artifact_path("runs", "unused-run", "manifest.json")
    assert not target.parent.exists()
    assert paths.is_within(paths.ARTIFACT_ROOT, target)


# ---------------------------------------------------------------------------
# Settingssicht (Plan 2.6)
# ---------------------------------------------------------------------------

def _offline_module(**overrides):
    values = {
        "COMMITTEE_MATCHER_MAX_FILE_BYTES": 512_000,
        "COMMITTEE_MATCHER_MAX_FRONTMATTER_BYTES": 32_000,
        "COMMITTEE_MATCHER_YFINANCE_REQUEST_PAUSE_S": 1.5,
        "COMMITTEE_MATCHER_YFINANCE_MAX_RETRIES": 3,
        "COMMITTEE_MATCHER_YFINANCE_CHECKPOINT_EVERY": 10,
        "COMMITTEE_MATCHER_BATCH_SIZE": 3,
        "COMMITTEE_MATCHER_WORKFLOW_RETRIES": 2,
        "COMMITTEE_MATCHER_MODEL": None,
        "COMMITTEE_MATCHER_PROVIDER_SLUG": None,
        "COMMITTEE_MATCHER_DATA_COLLECTION": None,
        "COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK": None,
        "COMMITTEE_MATCHER_ENDPOINT_CONTEXT_TOKENS": None,
        "COMMITTEE_MATCHER_MAX_OUTPUT_TOKENS": None,
        "COMMITTEE_MATCHER_REASONING_RESERVE_TOKENS": None,
        "COMMITTEE_MATCHER_REQUEST_TIMEOUT_S": None,
        "COMMITTEE_MATCHER_MAX_RUN_COST_USD": None,
        "COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD": None,
        "COMMITTEE_MATCHER_MAX_QUALIFICATION_COST_USD": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _provider_module(**overrides):
    values = dict(
        COMMITTEE_MATCHER_MODEL="anbieter/modell",
        COMMITTEE_MATCHER_PROVIDER_SLUG="anbieter/endpoint",
        COMMITTEE_MATCHER_DATA_COLLECTION="deny",
        COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK={"prompt": 3.0, "completion": 15.0},
        COMMITTEE_MATCHER_ENDPOINT_CONTEXT_TOKENS=200_000,
        COMMITTEE_MATCHER_MAX_OUTPUT_TOKENS=8_000,
        COMMITTEE_MATCHER_REASONING_RESERVE_TOKENS=4_000,
        COMMITTEE_MATCHER_REQUEST_TIMEOUT_S=120.0,
        COMMITTEE_MATCHER_MAX_RUN_COST_USD=5.0,
    )
    values.update(overrides)
    return _offline_module(**values)


def test_real_settings_are_valid_offline_and_provider_free():
    loaded = load_settings()
    assert loaded.offline.batch_size in settings_view.ALLOWED_BATCH_SIZES
    assert loaded.provider_ready is False, "Providersettings duerfen vor Gate B nicht gesetzt sein"


def test_offline_settings_are_typed_and_bounded():
    loaded = load_settings(_offline_module())
    assert loaded.offline.max_file_bytes == 512_000
    assert loaded.offline.yfinance_request_pause_s == pytest.approx(1.5)


@pytest.mark.parametrize(
    "override,code",
    [
        ({"COMMITTEE_MATCHER_BATCH_SIZE": 2}, "CFG_INVALID"),
        ({"COMMITTEE_MATCHER_BATCH_SIZE": "3"}, "CFG_INVALID"),
        ({"COMMITTEE_MATCHER_WORKFLOW_RETRIES": -1}, "CFG_INVALID"),
        ({"COMMITTEE_MATCHER_YFINANCE_REQUEST_PAUSE_S": True}, "CFG_INVALID"),
        ({"COMMITTEE_MATCHER_MAX_FILE_BYTES": 10}, "CFG_INVALID"),
    ],
)
def test_invalid_offline_settings_block_every_run(override, code):
    with pytest.raises(SettingsBlocked) as excinfo:
        load_settings(_offline_module(**override))
    assert excinfo.value.findings[0].code == code
    assert excinfo.value.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_missing_offline_setting_is_named_explicitly():
    module = _offline_module()
    del module.COMMITTEE_MATCHER_BATCH_SIZE
    with pytest.raises(SettingsBlocked) as excinfo:
        load_settings(module)
    finding = excinfo.value.findings[0]
    assert finding.code == "CFG_MISSING"
    assert finding.obj == "COMMITTEE_MATCHER_BATCH_SIZE"


def test_unset_provider_settings_block_the_provider_path():
    loaded = load_settings(_offline_module())
    assert loaded.provider_ready is False
    with pytest.raises(SettingsBlocked) as excinfo:
        loaded.require_provider()
    codes = {f.code for f in excinfo.value.findings}
    objects = {f.obj for f in excinfo.value.findings}
    assert codes == {"CFG_PROVIDER_UNSET"}
    assert "COMMITTEE_MATCHER_MODEL" in objects
    assert excinfo.value.exit_code is ExitCode.PREFLIGHT_BLOCKED


def test_complete_provider_settings_unlock_the_provider_path():
    loaded = load_settings(_provider_module())
    provider = loaded.require_provider()
    assert provider.model == "anbieter/modell"
    assert provider.allow_fallbacks is False
    assert provider.require_parameters is True
    assert provider.sdk_retries == 0
    assert provider.max_concurrent_calls == 1


@pytest.mark.parametrize(
    "override",
    [
        {"COMMITTEE_MATCHER_DATA_COLLECTION": "vielleicht"},
        {"COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK": {"prompt": 3.0}},
        {"COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK": {"prompt": 3.0, "completion": 0}},
        {"COMMITTEE_MATCHER_REQUEST_TIMEOUT_S": 0.5},
    ],
)
def test_invalid_provider_settings_keep_the_provider_path_closed(override):
    loaded = load_settings(_provider_module(**override))
    assert loaded.provider_ready is False
    with pytest.raises(SettingsBlocked):
        loaded.require_provider()


def test_context_window_must_leave_room_for_prompt_and_taxonomy():
    loaded = load_settings(_provider_module(
        COMMITTEE_MATCHER_ENDPOINT_CONTEXT_TOKENS=10_000,
        COMMITTEE_MATCHER_MAX_OUTPUT_TOKENS=8_000,
        COMMITTEE_MATCHER_REASONING_RESERVE_TOKENS=4_000,
    ))
    assert loaded.provider_ready is False
    codes = {f.code for f in loaded.provider_findings}
    assert "CFG_CONTEXT_RESERVE" in codes


def test_budgets_block_with_the_cost_exit_code():
    loaded = load_settings(_offline_module())
    with pytest.raises(SettingsBlocked) as excinfo:
        loaded.require_budget("run")
    assert excinfo.value.findings[0].code == "CFG_BUDGET_UNSET"
    assert excinfo.value.exit_code is ExitCode.COST_BLOCKED


def test_set_budget_is_returned_typed():
    loaded = load_settings(_provider_module())
    assert loaded.require_budget("run") == pytest.approx(5.0)
    with pytest.raises(SettingsBlocked):
        loaded.require_budget("evaluation")
    with pytest.raises(KeyError):
        loaded.require_budget("unbekannt")


def test_budget_kinds_match_the_plan():
    assert set(settings_view.BUDGET_SETTINGS) == {"run", "evaluation", "qualification"}


# ---------------------------------------------------------------------------
# Datenvertraege (Plan 2.2-2.5)
# ---------------------------------------------------------------------------

def _assignment(index: int = 1, confidence: int = 3) -> models.Assignment:
    return models.Assignment(
        industry_id=f"industry-{index}",
        confidence=confidence,
        evidence_excerpt=f"evidence {index}",
        rationale_short=f"rationale {index}",
    )


def test_mapped_unit_with_two_assignments_is_the_normal_case():
    result = models.MappingUnitResult(
        unit_id="u1",
        decision=MappingDecision.MAPPED,
        assignments=(_assignment(1), _assignment(2)),
    )
    assert len(result.assignments) == 2
    assert result.broad_scope_exception is False


def test_no_mapping_requires_a_reason_and_stays_empty():
    result = models.MappingUnitResult(
        unit_id="u1",
        decision=MappingDecision.NO_MAPPING,
        no_mapping_reason="keine unmittelbare Zustaendigkeit",
    )
    assert result.assignments == ()

    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(unit_id="u1", decision=MappingDecision.NO_MAPPING)
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.NO_MAPPING,
            no_mapping_reason="grund", assignments=(_assignment(),),
        )
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.NO_MAPPING,
            no_mapping_reason="grund", broad_scope_exception=True,
        )


def test_mapped_without_assignment_is_invalid():
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(unit_id="u1", decision=MappingDecision.MAPPED)


def test_three_to_five_assignments_require_the_broad_scope_exception():
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.MAPPED,
            assignments=tuple(_assignment(i) for i in range(3)),
        )
    ok = models.MappingUnitResult(
        unit_id="u1", decision=MappingDecision.MAPPED,
        broad_scope_exception=True,
        broad_scope_reason="drei getrennte Kernzustaendigkeiten",
        assignments=tuple(_assignment(i) for i in range(3)),
    )
    assert len(ok.assignments) == 3


def test_broad_scope_requires_confidence_evidence_and_rationale_per_industry():
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.MAPPED,
            broad_scope_exception=True, broad_scope_reason="grund",
            assignments=(_assignment(1), _assignment(2), _assignment(3, confidence=2)),
        )
    doppelte_evidence = (
        models.Assignment(industry_id="a", confidence=3, evidence_excerpt="x", rationale_short="r1"),
        models.Assignment(industry_id="b", confidence=3, evidence_excerpt="x", rationale_short="r2"),
        models.Assignment(industry_id="c", confidence=3, evidence_excerpt="y", rationale_short="r3"),
    )
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.MAPPED,
            broad_scope_exception=True, broad_scope_reason="grund",
            assignments=doppelte_evidence,
        )


def test_more_than_five_assignments_is_always_invalid():
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.MAPPED,
            broad_scope_exception=True, broad_scope_reason="grund",
            assignments=tuple(_assignment(i) for i in range(6)),
        )


def test_broad_scope_flag_without_enough_assignments_is_invalid():
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.MAPPED,
            broad_scope_exception=True, broad_scope_reason="grund",
            assignments=(_assignment(1), _assignment(2)),
        )


def test_duplicate_industry_id_within_a_unit_is_invalid():
    with pytest.raises(pydantic.ValidationError):
        models.MappingUnitResult(
            unit_id="u1", decision=MappingDecision.MAPPED,
            assignments=(_assignment(1), _assignment(1)),
        )


@pytest.mark.parametrize("confidence", [0, 5, -1])
def test_confidence_outside_one_to_four_is_invalid(confidence):
    with pytest.raises(pydantic.ValidationError):
        models.Assignment(
            industry_id="i", confidence=confidence,
            evidence_excerpt="e", rationale_short="r",
        )


@pytest.mark.parametrize("field", ["evidence_excerpt", "rationale_short"])
def test_blank_evidence_or_rationale_is_invalid(field):
    payload = {"industry_id": "i", "confidence": 2, "evidence_excerpt": "e", "rationale_short": "r"}
    payload[field] = "   "
    with pytest.raises(pydantic.ValidationError):
        models.Assignment(**payload)


def test_batch_holds_one_to_three_units_each_exactly_once():
    unit = models.MappingUnitResult(
        unit_id="u1", decision=MappingDecision.NO_MAPPING, no_mapping_reason="grund")
    assert models.MappingBatchResult(requested_batch_id="b1", units=(unit,)).unit_ids() == {"u1"}
    with pytest.raises(pydantic.ValidationError):
        models.MappingBatchResult(requested_batch_id="b1", units=())
    with pytest.raises(pydantic.ValidationError):
        models.MappingBatchResult(requested_batch_id="b1", units=(unit, unit))
    with pytest.raises(pydantic.ValidationError):
        models.MappingBatchResult(
            requested_batch_id="b1",
            units=tuple(
                models.MappingUnitResult(
                    unit_id=f"u{i}", decision=MappingDecision.NO_MAPPING,
                    no_mapping_reason="grund")
                for i in range(4)
            ),
        )


def test_industry_record_rejects_market_size_fields():
    with pytest.raises(pydantic.ValidationError):
        models.IndustryRecord(
            industry_id="i", industry_name="n", sector_id="s",
            sector_name="sn", description="d", market_cap=1_000,
        )
    with pytest.raises(pydantic.ValidationError):
        models.IndustryRecord(
            industry_id="i", industry_name="n", sector_id="s",
            sector_name="sn", description="d", market_weight=0.1,
        )


def test_snapshot_rejects_empty_and_duplicated_industries():
    record = models.IndustryRecord(
        industry_id="i", industry_name="n", sector_id="s", sector_name="sn", description="d")
    digest = "0" * 64
    with pytest.raises(pydantic.ValidationError):
        models.TaxonomySnapshot(
            snapshot_id="s1", yfinance_version="1.3.0",
            taxonomy_content_hash=digest, industries=(),
        )
    with pytest.raises(pydantic.ValidationError):
        models.TaxonomySnapshot(
            snapshot_id="s1", yfinance_version="1.3.0",
            taxonomy_content_hash=digest, industries=(record, record),
        )
    snapshot = models.TaxonomySnapshot(
        snapshot_id="s1", yfinance_version="1.3.0",
        taxonomy_content_hash=digest, industries=(record,),
    )
    assert snapshot.industry_ids() == {"i"}


def test_models_are_frozen():
    record = models.IndustryRecord(
        industry_id="i", industry_name="n", sector_id="s", sector_name="sn", description="d")
    with pytest.raises(pydantic.ValidationError):
        record.industry_id = "andere"


def test_unit_kind_rules():
    sub = models.MatcherUnit(
        unit_id="house-x-sub", kind=UnitKind.SUBCOMMITTEE, chamber="house",
        committee_name="C", unit_name="S", input_basis=InputBasis.NAME_ONLY,
        source_path="data/committee_matcher_inputs/md/house_x.md",
        parent_unit_id="house-x",
    )
    assert sub.parent_unit_id == "house-x"

    with pytest.raises(pydantic.ValidationError):
        models.MatcherUnit(
            unit_id="house-x-sub", kind=UnitKind.SUBCOMMITTEE, chamber="house",
            committee_name="C", unit_name="S", input_basis=InputBasis.JURISDICTION_TEXT,
            source_path="p",
        )
    with pytest.raises(pydantic.ValidationError):
        models.MatcherUnit(
            unit_id="house-x", kind=UnitKind.COMMITTEE_DIRECT, chamber="house",
            committee_name="C", unit_name="C", input_basis=InputBasis.NAME_ONLY,
            source_path="p",
        )
    with pytest.raises(pydantic.ValidationError):
        models.MatcherUnit(
            unit_id="haus x", kind=UnitKind.COMMITTEE_DIRECT, chamber="house",
            committee_name="C", unit_name="C", input_basis=InputBasis.JURISDICTION_TEXT,
            source_path="p",
        )


def test_committee_input_basis_is_always_jurisdiction_text():
    spec = models.UnitInputBasisSpec(subcommittees={"S": InputBasis.NAME_ONLY})
    assert spec.committee is InputBasis.JURISDICTION_TEXT
    with pytest.raises(pydantic.ValidationError):
        models.UnitInputBasisSpec(committee=InputBasis.NAME_ONLY)


@pytest.mark.parametrize(
    "payload",
    [
        {"coverage_status": CoverageStatus.MAPPED, "unit_id": None, "match_basis": MatchBasis.EXACT},
        {"coverage_status": CoverageStatus.MAPPED, "unit_id": "u", "match_basis": MatchBasis.MANUAL_EXCLUSION},
        {"coverage_status": CoverageStatus.EXCLUDED, "unit_id": "u", "match_basis": MatchBasis.MANUAL_EXCLUSION},
        {"coverage_status": CoverageStatus.EXCLUDED, "unit_id": None, "match_basis": MatchBasis.EXACT},
        {"coverage_status": CoverageStatus.MISSING, "unit_id": "u", "match_basis": None, "hint": "h"},
        {"coverage_status": CoverageStatus.MISSING, "unit_id": None, "match_basis": None},
    ],
)
def test_roster_coverage_null_rules(payload):
    base = {"chamber": "house", "committee_name": "C", "unit_name": "U"}
    with pytest.raises(pydantic.ValidationError):
        models.RosterCoverageRow(**base, **payload)


def test_roster_coverage_accepts_the_three_valid_shapes():
    base = {"chamber": "house", "committee_name": "C", "unit_name": "U"}
    assert models.RosterCoverageRow(
        **base, coverage_status=CoverageStatus.MAPPED, unit_id="u",
        match_basis=MatchBasis.ALIAS).unit_id == "u"
    assert models.RosterCoverageRow(
        **base, coverage_status=CoverageStatus.EXCLUDED,
        match_basis=MatchBasis.MANUAL_EXCLUSION).unit_id is None
    assert models.RosterCoverageRow(
        **base, coverage_status=CoverageStatus.MISSING,
        hint="Alias oder Ausschluss in den Rosterregeln ergaenzen").hint


def test_corpus_file_entry_rejects_traversal_and_bad_digests():
    with pytest.raises(pydantic.ValidationError):
        models.CorpusFileEntry(path="../geheim.md", role="mapping_input", bytes=1, sha256="a" * 64)
    with pytest.raises(pydantic.ValidationError):
        models.CorpusFileEntry(path="md/x.md", role="mapping_input", bytes=1, sha256="XYZ")
    entry = models.CorpusFileEntry(path="md/x.md", role="mapping_input", bytes=1, sha256="a" * 64)
    assert entry.role.value == "mapping_input"


# ---------------------------------------------------------------------------
# CLI-Skelett (Plan 2.7)
# ---------------------------------------------------------------------------

def test_cli_families_match_the_plan():
    families = {command.family for command in cli.COMMANDS}
    assert families == {
        "input", "roster", "snapshot", "run", "review", "evaluate",
        "qualify", "full-run", "report",
    }
    forbidden = {"promote", "finalize", "research"}
    assert not families & forbidden
    assert not any(command.action == "review" and command.family == "run" for command in cli.COMMANDS)


@pytest.mark.parametrize("command", cli.COMMANDS, ids=lambda c: c.name.replace(" ", "-"))
def test_every_command_is_reachable_and_blocks_with_exit_four(command, capsys):
    argv = [command.family] + ([command.action] if command.action else [])
    assert cli.main(argv) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert command.name in out
    expected = "CLI_PROVIDER_GATE_CLOSED" if command.provider else "CLI_STAGE_NOT_IMPLEMENTED"
    assert expected in out


def test_provider_families_name_the_missing_settings(capsys):
    assert cli.main(["run", "execute"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "CFG_PROVIDER_UNSET" in out
    assert "COMMITTEE_MATCHER_MODEL" in out
    assert "Gate B" in out


def test_provider_free_families_do_not_mention_the_provider_gate(capsys):
    assert cli.main(["input", "validate"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "CLI_PROVIDER_GATE_CLOSED" not in out
    assert "Phase P3" in out


def test_report_is_printed_for_every_command(capsys):
    cli.main(["snapshot", "build"])
    out = capsys.readouterr().out
    assert "Kosten: 0.00 USD" in out
    assert "Exit-Code: 4" in out


def test_unknown_command_uses_the_contract_exit_code(capsys):
    assert cli.main(["nichtvorhanden"]) == int(ExitCode.PREFLIGHT_BLOCKED)


def test_help_exits_zero(capsys):
    assert cli.main(["--help"]) == 0
    assert "Gate B" in capsys.readouterr().out


def test_version_reports_the_scaffold_version(capsys):
    assert cli.main(["--version"]) == 0
    assert SCAFFOLD_VERSION in capsys.readouterr().out


def test_development_target_flags_only_exist_for_development_runs():
    parser = cli.build_parser()
    namespace = parser.parse_args(["run", "execute", "--chamber", "house"])
    assert namespace.chamber == "house"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["full-run", "--chamber", "house"])
    assert excinfo.value.code == int(ExitCode.PREFLIGHT_BLOCKED)


def test_unexpected_error_maps_to_exit_ten(monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(cli, "run_command", explode)
    assert cli.main(["input", "validate"]) == int(ExitCode.INTERNAL_ERROR)
    assert "CLI_INTERNAL_ERROR" in capsys.readouterr().out


def test_user_abort_maps_to_exit_130(monkeypatch, capsys):
    def abort(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_command", abort)
    assert cli.main(["input", "validate"]) == int(ExitCode.USER_ABORT)
    assert "CLI_ABORTED" in capsys.readouterr().out


def test_broken_settings_block_before_any_stage(monkeypatch, capsys):
    def blocked():
        raise SettingsBlocked(
            [errors.Finding(
                severity=Severity.BLOCKER, code="CFG_INVALID", stage=Stage.CONFIG,
                obj="COMMITTEE_MATCHER_BATCH_SIZE", message="m", hint="h",
                exit_code=ExitCode.PREFLIGHT_BLOCKED)],
        )

    monkeypatch.setattr(cli, "load_settings", blocked)
    assert cli.main(["input", "validate"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "CFG_INVALID" in out
    assert "Settings ungueltig" in out


# ---------------------------------------------------------------------------
# Geruestinvarianten
# ---------------------------------------------------------------------------

def test_importing_the_package_pulls_no_provider_or_network_dependency():
    import subprocess

    code = (
        "import sys, committee_matcher, committee_matcher.cli, committee_matcher.models;"
        "heavy=[m for m in ('yfinance','openrouter','requests','httpx','openai') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""


def test_p2_modules_exist_with_clear_ownership():
    package = ROOT / "committee_matcher"
    implemented = {"errors.py", "status.py", "paths.py", "settings_view.py", "models.py", "cli.py"}
    present = {p.name for p in package.glob("*.py")}
    assert implemented <= present
    ownership = (package / "__init__.py").read_text(encoding="utf-8")
    for module in sorted(implemented):
        assert f"``{module[:-3]}``" in ownership


def test_pyyaml_stays_pinned():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "PyYAML==" in requirements
