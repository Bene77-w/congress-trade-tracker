"""Tests der Evaluationsrevision und von 'evaluate export' (P9a).

Netzwerkfrei: der yfinance-Rand ist durch ``FakeYFinance`` ersetzt, der
Providerrand ist verboten - ``evaluate export`` ruft kein Modell auf. Corpus,
Artefaktwurzel und Settings liegen unter ``tmp_path``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import cli, evaluation, input_corpus, paths  # noqa: E402
from committee_matcher import taxonomy_snapshot, workflow  # noqa: E402
from committee_matcher.errors import ErrorCollector, MatcherError  # noqa: E402
from committee_matcher.settings_view import SettingsBlocked, load_settings  # noqa: E402
from committee_matcher.status import ExitCode, WorkflowState  # noqa: E402
from committee_matcher.taxonomy_source import IndustryRow, SectorSeed  # noqa: E402

from tests.test_committee_matcher_input_units import build_corpus, make_markdown  # noqa: E402
from tests.test_committee_matcher_run_cli_p7 import (  # noqa: E402
    INDUSTRIES, settings_module, write_baseline,
)

EVAL_ID = "eval-p9a"

#: Die drei Units des Testcorpus; die Stichprobe zieht zwei davon.
SAMPLE_ROWS = [
    {
        "nr": 1,
        "unit_id": "house--testing--livestock",
        "chamber": "house",
        "committee_name": "Committee on Testing",
        "unit_name": "Subcommittee on Livestock",
        "input_basis": "jurisdiction_text",
        "focus": "darf nie in ein Aufgabenpaket gelangen",
    },
    {
        "nr": 2,
        "unit_id": "house--testing--nutrition",
        "chamber": "house",
        "committee_name": "Committee on Testing",
        "unit_name": "Subcommittee on Nutrition",
        "input_basis": "jurisdiction_text",
        "focus": "zweite Unit der Stichprobe",
    },
]


class FakeYFinance:
    """Testdouble mit der Schnittstelle des ``YFinanceClient``.

    Zaehlt seine Abrufe: mehrere Tests pruefen, dass der Netzabruf gar nicht
    erst stattfindet, wenn eine billige Pruefung den Export schon verhindert.
    """

    checkpoint_every = 10

    def __init__(self, industries=INDUSTRIES):
        self.industries_data = list(industries)
        self.calls = 0

    def version(self) -> str:
        return "0.2.99"

    def sector_seeds(self) -> tuple[SectorSeed, ...]:
        seeds = {row["sector_id"]: row["sector_name"] for row in self.industries_data}
        return tuple(SectorSeed(sector_id=key, sector_name=name)
                     for key, name in sorted(seeds.items()))

    def industries(self, seed: SectorSeed) -> tuple[IndustryRow, ...]:
        self.calls += 1
        return tuple(
            IndustryRow.from_payload(dict(row))
            for row in self.industries_data if row["sector_id"] == seed.sector_id
        )

    def description(self, industry_id: str) -> str:
        for row in self.industries_data:
            if row["industry_id"] == industry_id:
                return row.get("description", "")
        return ""


def write_sample(rows=None, *, version=evaluation.SAMPLE_VERSION,
                 expected: int | None = None) -> Path:
    payload = {
        "sample_version": version,
        "source": "tests",
        "expected_unit_count": len(rows if rows is not None else SAMPLE_ROWS)
        if expected is None else expected,
        "units": list(rows if rows is not None else SAMPLE_ROWS),
    }
    target = evaluation.sample_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return target


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Corpus, Artefaktwurzel, Settings, Baseline und ein gefakter Netzrand."""
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path / "artifacts")
    root = build_corpus(tmp_path, {
        "house_testing.md": make_markdown(
            sections=("Subcommittee on Livestock", "Subcommittee on Nutrition")),
    })
    original = input_corpus.load_documents
    monkeypatch.setattr(
        cli.input_corpus, "load_documents",
        lambda collector, offline, input_root=root: original(collector, offline, root),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(settings_module()))

    def forbidden(*args, **kwargs):
        raise AssertionError("'evaluate export' ruft kein Modell auf")

    monkeypatch.setattr(cli.provider.OpenRouterProvider, "create", forbidden)

    client = FakeYFinance()
    monkeypatch.setattr(cli.taxonomy_source, "YFinanceClient",
                        lambda offline, reporter=None: client)
    write_baseline()
    write_sample()
    return SimpleNamespace(root=root, tmp_path=tmp_path, monkeypatch=monkeypatch,
                           client=client)


def export_argv(eval_id: str = EVAL_ID) -> list[str]:
    return ["evaluate", "export", "--eval-id", eval_id]


# ---------------------------------------------------------------------------
# evaluate export - Regelweg
# ---------------------------------------------------------------------------

def test_export_freezes_the_revision_and_hands_over(sandbox, capsys):
    assert cli.main(export_argv()) == int(ExitCode.SUCCESS)
    manifest = evaluation.load_export_manifest(EVAL_ID)
    assert manifest is not None
    assert manifest["unit_count"] == len(SAMPLE_ROWS)
    assert manifest["selection"]["unit_ids"] == sorted(
        row["unit_id"] for row in SAMPLE_ROWS)
    # Der Zustand steht im Manifest selbst; die Revision entsteht damit in
    # einem einzigen atomaren Schreibvorgang.
    assert manifest["state"] == WorkflowState.AWAITING_DEVELOPMENT_GOLDSET.value
    assert sorted(path.name for path in
                  evaluation.evaluation_path(EVAL_ID).iterdir()) == [
        evaluation.EXPORT_MANIFEST_FILENAME]
    out = capsys.readouterr().out
    assert "[START] evaluation | evaluation=eval-p9a | action=export" in out
    assert "awaiting_development_goldset" in out


def test_export_binds_every_condition_that_decides_the_result(sandbox):
    cli.main(export_argv())
    manifest = evaluation.load_export_manifest(EVAL_ID)
    baseline_hash = taxonomy_snapshot.hash_records(INDUSTRIES)
    # Gebunden wird der freigegebene Stand: gegen genau ihn laeuft spaeter
    # auch der Prueflauf. Der frische Abruf ist die Gegenprobe, nicht die
    # Quelle der Bindung.
    assert manifest["snapshot_id"] == "run-taxonomy"
    assert manifest["taxonomy_content_hash"] == baseline_hash
    assert manifest["input_manifest_hash"]
    assert manifest["cache_key_version"] == workflow.CACHE_KEY_VERSION
    assert manifest["schema"]["schema_sha256"]
    assert [entry["prompt_id"] for entry in manifest["prompts"]] == [
        "mapping_system", "repair_system"]
    assert manifest["budget_usd"] == 5.0
    assert set(manifest["mapping_input_hashes"]) == {
        row["unit_id"] for row in SAMPLE_ROWS}
    assert all(manifest["mapping_input_hashes"].values())


def test_the_variant_matrix_replaces_the_configured_batch_size(sandbox):
    cli.main(export_argv())
    manifest = evaluation.load_export_manifest(EVAL_ID)
    # Die Batchgroesse ist in der Evaluation die Variante; bliebe der
    # Settingswert im Manifest stehen, widerspraeche er jeder Variante ausser
    # einer und die Bindungspruefung des Prueflaufs schluege grundlos fehl.
    assert "batch_size" not in manifest["batching"]
    assert manifest["variants"] == {
        "batch_sizes": [1, 3], "repetitions": 3, "max_repetitions": 5}


def test_the_manifest_carries_neither_prompt_text_nor_credentials(sandbox, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "geheim-testschluessel")
    cli.main(export_argv())
    raw = evaluation.evaluation_path(
        EVAL_ID, evaluation.EXPORT_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert "geheim-testschluessel" not in raw
    assert "api_key" not in raw.lower()
    # Der Prompt ist ueber seinen Hash belegt; sein Text gehoert nicht in ein
    # versioniertes Artefakt.
    assert "Regeltext" not in raw
    for entry in json.loads(raw)["prompts"]:
        assert entry["prompt_sha256"]
        assert "text" not in entry


def test_the_focus_of_the_sample_never_reaches_the_manifest(sandbox):
    cli.main(export_argv())
    raw = evaluation.evaluation_path(
        EVAL_ID, evaluation.EXPORT_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert "Aufgabenpaket" not in raw
    assert "focus" not in raw


# ---------------------------------------------------------------------------
# Nichtueberschreibbarkeit
# ---------------------------------------------------------------------------

def test_a_second_export_under_the_same_id_changes_nothing(sandbox, capsys):
    assert cli.main(export_argv()) == int(ExitCode.SUCCESS)
    before = evaluation.evaluation_path(
        EVAL_ID, evaluation.EXPORT_MANIFEST_FILENAME).read_text(encoding="utf-8")
    calls = sandbox.client.calls
    capsys.readouterr()

    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_REVISION_TAKEN" in capsys.readouterr().out
    after = evaluation.evaluation_path(
        EVAL_ID, evaluation.EXPORT_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert after == before
    # Der abgelehnte Export ruft yfinance gar nicht erst ab.
    assert sandbox.client.calls == calls


def test_an_id_already_used_as_a_run_revision_is_refused(sandbox, capsys):
    workflow.write_manifest(EVAL_ID, {"version": workflow.MANIFEST_VERSION,
                                      "run_id": EVAL_ID})
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    out = capsys.readouterr().out
    assert "EVAL_REVISION_TAKEN" in out
    assert evaluation.load_export_manifest(EVAL_ID) is None


# ---------------------------------------------------------------------------
# Bedienung und Stichprobe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["export", "import", "run"])
def test_no_evaluation_action_runs_without_an_eval_id(sandbox, capsys, action):
    assert cli.main(["evaluate", action]) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "--eval-id" in capsys.readouterr().err
    assert sandbox.client.calls == 0


def test_an_unusable_eval_id_refuses_to_run(sandbox, capsys):
    assert cli.main(export_argv("eval/../fremd")) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "CLI_EVAL_ID_INVALID" in capsys.readouterr().out


def test_a_missing_sample_blocks_before_the_network_call(sandbox, capsys):
    evaluation.sample_path().unlink()
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_SAMPLE_MISSING" in capsys.readouterr().out
    assert sandbox.client.calls == 0


def test_a_shortened_sample_blocks(sandbox, capsys):
    write_sample(SAMPLE_ROWS[:1], expected=len(SAMPLE_ROWS))
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_SAMPLE_COUNT" in capsys.readouterr().out
    assert evaluation.load_export_manifest(EVAL_ID) is None


def test_a_sample_unit_outside_the_target_set_blocks(sandbox, capsys):
    rows = [dict(SAMPLE_ROWS[0], unit_id="house--gibt-es-nicht"), SAMPLE_ROWS[1]]
    write_sample(rows)
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_SAMPLE_UNIT_UNKNOWN" in capsys.readouterr().out


def test_a_renamed_unit_blocks_instead_of_measuring_something_else(sandbox, capsys):
    rows = [dict(SAMPLE_ROWS[0], unit_name="Subcommittee on Something Else"),
            SAMPLE_ROWS[1]]
    write_sample(rows)
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_SAMPLE_UNIT_DRIFT" in capsys.readouterr().out
    assert evaluation.load_export_manifest(EVAL_ID) is None


def test_a_duplicate_sample_unit_blocks(sandbox, capsys):
    write_sample([SAMPLE_ROWS[0], dict(SAMPLE_ROWS[1], **{
        "unit_id": SAMPLE_ROWS[0]["unit_id"],
        "unit_name": SAMPLE_ROWS[0]["unit_name"]})])
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_SAMPLE_DUPLICATE" in capsys.readouterr().out


def test_a_foreign_sample_version_blocks(sandbox, capsys):
    write_sample(version="goldset-sample-v99")
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "EVAL_SAMPLE_CONTRACT" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Taxonomiestand
# ---------------------------------------------------------------------------

def test_taxonomy_drift_waits_instead_of_freezing_another_state(sandbox, capsys):
    drifted = [dict(INDUSTRIES[0]), dict(INDUSTRIES[1], industry_name="Umbenannt")]
    sandbox.monkeypatch.setattr(
        cli.taxonomy_source, "YFinanceClient",
        lambda offline, reporter=None: FakeYFinance(drifted))
    assert cli.main(export_argv()) == int(ExitCode.AWAITING_TAXONOMY_APPROVAL)
    assert evaluation.load_export_manifest(EVAL_ID) is None
    out = capsys.readouterr().out
    assert "TAX_DRIFT_RENAMED" in out
    assert "[WAIT] evaluation | evaluation=eval-p9a | awaiting_taxonomy_approval" in out


def test_a_changed_description_alone_still_blocks_the_export(sandbox, capsys):
    # Reine Beschreibungsdrift laeuft im Snapshotpfad als Warnung durch. Fuer
    # die Evaluation entscheidet sie mit: der Beschreibungstext steht im
    # Prompt, und ein abweichender Content-Hash liesse den spaeteren
    # Prueflauf gegen einen anderen Stand messen.
    drifted = [dict(INDUSTRIES[0], description="Anderer Beschreibungstext."),
               dict(INDUSTRIES[1])]
    sandbox.monkeypatch.setattr(
        cli.taxonomy_source, "YFinanceClient",
        lambda offline, reporter=None: FakeYFinance(drifted))
    assert cli.main(export_argv()) == int(ExitCode.AWAITING_TAXONOMY_APPROVAL)
    assert evaluation.load_export_manifest(EVAL_ID) is None
    out = capsys.readouterr().out
    assert "EVAL_TAXONOMY_DRIFT" in out
    assert "[WAIT] evaluation | evaluation=eval-p9a | awaiting_taxonomy_approval" in out


def test_a_missing_baseline_waits_before_the_network_call(sandbox, capsys):
    taxonomy_snapshot.baseline_path().unlink()
    assert cli.main(export_argv()) == int(ExitCode.AWAITING_TAXONOMY_APPROVAL)
    assert evaluation.load_export_manifest(EVAL_ID) is None
    out = capsys.readouterr().out
    assert "TAX_BASELINE_MISSING" in out
    assert "[WAIT] evaluation" in out
    # Ohne freigegebenen Stand gibt es nichts zu bestaetigen; der Abruf
    # unterbleibt.
    assert sandbox.client.calls == 0


# ---------------------------------------------------------------------------
# Modulvertrag
# ---------------------------------------------------------------------------

def test_the_export_manifest_is_bound_to_its_own_revision(sandbox):
    cli.main(export_argv())
    assert evaluation.load_export_manifest("eine-andere-revision") is None
    target = evaluation.evaluation_path(EVAL_ID, evaluation.EXPORT_MANIFEST_FILENAME)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["version"] = "evaluation-export-v0"
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert evaluation.load_export_manifest(EVAL_ID) is None


def test_an_unreadable_sample_is_reported_not_swallowed(sandbox):
    evaluation.sample_path().write_text("{kein json", encoding="utf-8")
    collector = ErrorCollector()
    assert evaluation.load_sample(collector) is None
    assert [finding.code for finding in collector.blockers] == ["EVAL_SAMPLE_UNREADABLE"]


def test_a_sample_row_without_head_fields_blocks(sandbox):
    rows = [dict(SAMPLE_ROWS[0]), dict(SAMPLE_ROWS[1])]
    rows[1].pop("committee_name")
    write_sample(rows)
    collector = ErrorCollector()
    assert evaluation.load_sample(collector) is None
    assert any(finding.code == "EVAL_SAMPLE_CONTRACT"
               for finding in collector.blockers)


def test_an_unbound_unit_blocks_the_selection(sandbox):
    sample = evaluation.load_sample(ErrorCollector())
    assert sample is not None
    collector = ErrorCollector()
    corpus = input_corpus.load_documents(
        collector, load_settings(settings_module()).offline, sandbox.root)
    units = cli.unit_module.build_units(collector, corpus.documents)
    stripped = [unit.model_copy(update={"mapping_input_hash": None})
                for unit in units]
    assert evaluation.select_units(collector, stripped, sample) is None
    assert any(finding.code == "EVAL_UNIT_UNBOUND"
               for finding in collector.blockers)


# ---------------------------------------------------------------------------
# Bindung an den Prueflauf
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("batch_size", [1, 3])
def test_both_variants_of_a_run_bind_to_the_revision(sandbox, batch_size):
    """Beide Varianten muessen dieselbe Revision treffen.

    Die Batchgroesse ist in der Evaluation die Variante; wuerde sie in die
    Bindung eingehen, waere hoechstens eine der beiden Varianten zulaessig.
    """
    assert cli.main(export_argv()) == int(ExitCode.SUCCESS)
    manifest = evaluation.load_export_manifest(EVAL_ID)
    sandbox.monkeypatch.setattr(
        cli, "load_settings",
        lambda: load_settings(settings_module(
            COMMITTEE_MATCHER_BATCH_SIZE=batch_size)))
    run_id = f"{EVAL_ID}-b{batch_size}"
    argv = ["run", "plan", "--run-id", run_id]
    for unit_id in manifest["selection"]["unit_ids"]:
        argv += ["--unit-id", unit_id]
    assert cli.main(argv) in (int(ExitCode.SUCCESS),
                              int(ExitCode.SUCCESS_WITH_WARNINGS))
    run_manifest = workflow.load_manifest(run_id)
    assert run_manifest is not None
    assert evaluation.binding_conflicts(manifest, run_manifest) == ()
    assert evaluation.variant_allowed(manifest, batch_size)


def test_a_batch_size_outside_the_matrix_is_not_allowed(sandbox):
    cli.main(export_argv())
    manifest = evaluation.load_export_manifest(EVAL_ID)
    assert not evaluation.variant_allowed(manifest, 2)


def test_a_different_model_is_a_binding_conflict(sandbox):
    cli.main(export_argv())
    manifest = evaluation.load_export_manifest(EVAL_ID)
    foreign = dict(manifest, model="anderer/anbieter",
                   taxonomy_content_hash="0" * 64)
    assert evaluation.binding_conflicts(manifest, foreign) == (
        "taxonomy_content_hash", "model")


def test_other_batching_values_remain_bound(sandbox):
    cli.main(export_argv())
    manifest = evaluation.load_export_manifest(EVAL_ID)
    changed = dict(manifest,
                   batching=dict(manifest["batching"], workflow_retries=99))
    assert evaluation.binding_conflicts(manifest, changed) == ("batching",)


# ---------------------------------------------------------------------------
# Settings und Statusvertrag
# ---------------------------------------------------------------------------

def test_a_float_batch_size_is_refused(sandbox):
    with pytest.raises(SettingsBlocked) as excinfo:
        load_settings(settings_module(
            COMMITTEE_MATCHER_EVALUATION_BATCH_SIZES=(1.0, 3)))
    assert any(finding.obj.endswith("EVALUATION_BATCH_SIZES")
               for finding in excinfo.value.findings)


def test_a_duplicate_batch_size_is_refused(sandbox):
    with pytest.raises(SettingsBlocked):
        load_settings(settings_module(
            COMMITTEE_MATCHER_EVALUATION_BATCH_SIZES=(3, 3)))


def test_an_aborting_module_still_closes_its_start(sandbox, capsys):
    """Auch ein Abbruch als Ausnahme schliesst den ``START`` des Vorgangs."""
    def explode(collector):
        raise MatcherError(
            [ErrorCollector().blocker("PRM_UNREADABLE", cli.Stage.PROVIDER,
                                      "mapping_system", "Prompt unlesbar.",
                                      "Datei pruefen.")],
            "Prompt unlesbar")

    sandbox.monkeypatch.setattr(cli.prompts, "load_all", explode)
    assert cli.main(export_argv()) == int(ExitCode.PREFLIGHT_BLOCKED)
    captured = capsys.readouterr()
    assert "[START] evaluation | evaluation=eval-p9a | action=export" in captured.out
    assert "[ERROR] evaluation | evaluation=eval-p9a | code=PRM_UNREADABLE" in captured.err
