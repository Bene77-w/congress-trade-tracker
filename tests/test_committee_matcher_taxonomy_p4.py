"""Tests fuer P4 des Committee Matchers V2: yfinance-Adapter und Snapshot.

Alle Tests sind netzwerkfrei: yfinance wird durch ein Testdouble ersetzt, das
den im Contract-Spike festgestellten Rueckgabeformen entspricht (DataFrame-
artige Zeilen mit ``name``/``symbol``/``market weight``, Overview-Dict mit
``description`` plus Boersengroessen). Genau daran wird geprueft, dass die
Groessenfelder den Adapter nie verlassen.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import paths, taxonomy_snapshot, taxonomy_source  # noqa: E402
from committee_matcher.errors import ErrorCollector, Severity, Stage  # noqa: E402
from committee_matcher.settings_view import load_settings  # noqa: E402
from committee_matcher.status import ExitCode, WorkflowState  # noqa: E402
from committee_matcher.taxonomy_source import IndustryRow, SectorSeed  # noqa: E402

OFFLINE = replace(load_settings().offline, yfinance_request_pause_s=0.0)


def codes(collector: ErrorCollector, severity: Severity | None = None) -> set[str]:
    return {f.code for f in collector.findings
            if severity is None or f.severity is severity}


class FakeClient:
    """Testdouble mit derselben Schnittstelle wie ``YFinanceClient``."""

    checkpoint_every = 2

    def __init__(self, sectors: dict[str, list[dict]], *, fail_on: str | None = None):
        self.sectors = sectors
        self.fail_on = fail_on
        self.described: list[str] = []

    def version(self) -> str:
        return "1.3.0-fake"

    def sector_seeds(self) -> tuple[SectorSeed, ...]:
        return tuple(
            SectorSeed(sector_id=key, sector_name=key.replace("-", " ").title())
            for key in sorted(self.sectors)
        )

    def industries(self, seed: SectorSeed) -> tuple[IndustryRow, ...]:
        rows = []
        for record in self.sectors[seed.sector_id]:
            payload = dict(record)
            payload.update(
                industry_id=record["key"],
                industry_name=record["name"],
                sector_id=seed.sector_id,
                sector_name=seed.sector_name,
            )
            rows.append(IndustryRow.from_payload(payload))
        return tuple(rows)

    def description(self, industry_id: str) -> str:
        if industry_id == self.fail_on:
            # Wie der echte Client: erschoepfte Retries werden zum Befund.
            raise taxonomy_source.TaxonomyFetchError(
                [
                    ErrorCollector().blocker(
                        "TAX_FETCH_FAILED", Stage.TAXONOMY, industry_id,
                        "Abruf fehlgeschlagen.", "Spaeter fortsetzen.",
                    )
                ],
                "Abbruch mitten im Abruf",
            )
        self.described.append(industry_id)
        for records in self.sectors.values():
            for record in records:
                if record["key"] == industry_id:
                    return record.get("description", "")
        return ""


def sectors(**overrides) -> dict[str, list[dict]]:
    data = {
        "energy": [
            {"key": "oil-gas-midstream", "name": "Oil & Gas Midstream",
             "symbol": "^YH1", "market weight": 0.12,
             "description": "Pipelines und Speicherung."},
            {"key": "uranium", "name": "Uranium", "symbol": "^YH2",
             "market weight": 0.01, "description": "Uranabbau und -aufbereitung."},
        ],
        "technology": [
            {"key": "semiconductors", "name": "Semiconductors", "symbol": "^YH3",
             "market weight": 0.38, "description": "Entwurf und Fertigung von Chips."},
        ],
    }
    data.update(overrides)
    return data


@pytest.fixture
def artifact_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Adapterrand: Boersengroessen
# ---------------------------------------------------------------------------

def test_allowlist_and_discarded_fields_are_disjoint():
    assert not set(taxonomy_source.INDUSTRY_FIELDS) & set(taxonomy_source.DISCARDED_FIELDS)


def test_market_size_fields_never_leave_the_adapter():
    row = IndustryRow.from_payload({
        "industry_id": "semiconductors", "industry_name": "Semiconductors",
        "sector_id": "technology", "sector_name": "Technology",
        "description": "Chips.", "market_cap": "10693998804992",
        "market weight": 0.38, "companies_count": "75", "employee_count": "1296254",
        "symbol": "^YH31130020", "message_board_id": "INDEXYH31130020",
    })
    assert set(row.as_record()) == set(taxonomy_source.INDUSTRY_FIELDS)
    assert "10693998804992" not in json.dumps(row.as_record())


def test_fetch_result_carries_only_allowed_fields():
    collector = ErrorCollector()
    rows = taxonomy_source.fetch_taxonomy(collector, FakeClient(sectors()))
    assert collector.findings == []
    assert len(rows) == 3
    for row in rows:
        assert set(row.as_record()) == set(taxonomy_source.INDUSTRY_FIELDS)


def test_retries_are_finite_and_end_in_a_finding():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("Yahoo antwortet nicht")

    client = taxonomy_source.YFinanceClient(replace(OFFLINE, yfinance_max_retries=2),
                                            sleep=lambda _s: None)
    with pytest.raises(taxonomy_source.TaxonomyFetchError) as excinfo:
        client._call("sector energy", boom)
    assert calls["n"] == 3  # ein Versuch plus zwei Retries
    assert [f.code for f in excinfo.value.findings] == ["TAX_FETCH_FAILED"]


def test_request_pause_is_applied_between_calls():
    pauses: list[float] = []
    client = taxonomy_source.YFinanceClient(
        replace(OFFLINE, yfinance_request_pause_s=1.5), sleep=pauses.append
    )
    client._call("sector energy", lambda: "ok")
    assert pauses == [1.5]


def test_empty_sector_and_missing_description_block():
    collector = ErrorCollector()
    data = sectors()
    data["technology"] = []
    taxonomy_source.fetch_taxonomy(collector, FakeClient(data))
    assert "TAX_SECTOR_EMPTY" in codes(collector)

    collector = ErrorCollector()
    data = sectors()
    data["technology"][0]["description"] = ""
    taxonomy_source.fetch_taxonomy(collector, FakeClient(data))
    assert "TAX_DESCRIPTION_MISSING" in codes(collector)


# ---------------------------------------------------------------------------
# Inhaltsvertrag
# ---------------------------------------------------------------------------

def rows_of(data: dict[str, list[dict]]) -> tuple[IndustryRow, ...]:
    return taxonomy_source.fetch_taxonomy(ErrorCollector(), FakeClient(data))


def test_content_hash_is_order_independent_and_content_sensitive():
    base = rows_of(sectors())
    shuffled = tuple(reversed(base))
    assert taxonomy_snapshot.content_hash(base) == taxonomy_snapshot.content_hash(shuffled)

    changed = sectors()
    changed["technology"][0]["description"] = "Andere Beschreibung."
    assert taxonomy_snapshot.content_hash(base) != taxonomy_snapshot.content_hash(rows_of(changed))


def test_content_hash_is_frozen_for_a_known_input():
    import hashlib

    rows = rows_of(sectors())
    payload = {
        "version": taxonomy_snapshot.TAXONOMY_CONTENT_VERSION,
        "industries": taxonomy_snapshot.content_records(rows),
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert taxonomy_snapshot.content_hash(rows) == expected


def test_catalog_groups_by_sector_and_names_the_hash():
    rows = rows_of(sectors())
    catalog = taxonomy_snapshot.build_catalog(rows)
    assert "## Energy (`energy`)" in catalog
    assert "## Technology (`technology`)" in catalog
    assert taxonomy_snapshot.content_hash(rows) in catalog
    assert "market" not in catalog.lower().split("Boersengroessen")[0]
    assert catalog.index("## Energy") < catalog.index("## Technology")


def test_validation_blocks_duplicates_and_gaps():
    collector = ErrorCollector()
    row = IndustryRow("a", "A", "s", "S", "Text.")
    assert taxonomy_snapshot.validate_rows(collector, (row, row)) is False
    assert "TAX_ID_DUPLICATE" in codes(collector)

    collector = ErrorCollector()
    other = IndustryRow("a", "A", "andere", "Andere", "Text.")
    assert taxonomy_snapshot.validate_rows(collector, (row, other)) is False
    assert "TAX_ID_COLLISION" in codes(collector)

    collector = ErrorCollector()
    assert taxonomy_snapshot.validate_rows(collector, ()) is False
    assert "TAX_SNAPSHOT_EMPTY" in codes(collector)

    collector = ErrorCollector()
    assert taxonomy_snapshot.validate_rows(collector, (IndustryRow("a", "A", "s", "S", " "),)) is False
    assert "TAX_FIELD_MISSING" in codes(collector)


# ---------------------------------------------------------------------------
# Snapshot, Checkpoint, Freigabe, Drift
# ---------------------------------------------------------------------------

def test_first_run_without_baseline_stops_with_no_baseline(artifact_sandbox):
    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(
        collector, FakeClient(sectors()), run_id="run-1"
    )
    assert result.state is WorkflowState.AWAITING_TAXONOMY_APPROVAL
    assert result.reason == taxonomy_snapshot.REASON_NO_BASELINE
    assert collector.exit_code() is ExitCode.AWAITING_TAXONOMY_APPROVAL
    assert (artifact_sandbox / "runs" / "run-1" / "taxonomy_snapshot.json").exists()
    assert (artifact_sandbox / "runs" / "run-1" / "taxonomy_catalog.md").exists()


def test_approval_binds_run_and_content(artifact_sandbox):
    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(collector, FakeClient(sectors()), run_id="run-1")

    collector = ErrorCollector()
    digest = taxonomy_snapshot.approve_snapshot(collector, "run-1")
    assert digest == result.content_hash
    baseline = json.loads((artifact_sandbox / "taxonomy" / "approved_baseline.json").read_text("utf-8"))
    assert baseline["approved_run_id"] == "run-1"
    assert baseline["taxonomy_content_hash"] == digest

    collector = ErrorCollector()
    assert taxonomy_snapshot.approve_snapshot(collector, "run-unbekannt") is None
    assert "TAX_SNAPSHOT_MISSING" in codes(collector)


def test_tampered_snapshot_cannot_be_approved(artifact_sandbox):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    target = artifact_sandbox / "runs" / "run-1" / "taxonomy_snapshot.json"
    data = json.loads(target.read_text("utf-8"))
    data["industries"][0]["description"] = "Nachtraeglich veraendert."
    target.write_text(json.dumps(data), encoding="utf-8")

    collector = ErrorCollector()
    assert taxonomy_snapshot.approve_snapshot(collector, "run-1") is None
    assert "TAX_SNAPSHOT_TAMPERED" in codes(collector)


def test_unchanged_taxonomy_passes_after_approval(artifact_sandbox):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    taxonomy_snapshot.approve_snapshot(ErrorCollector(), "run-1")

    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(collector, FakeClient(sectors()), run_id="run-2")
    assert result.state is WorkflowState.COMPLETED
    assert collector.findings == []
    assert result.drift is not None and not result.drift.any_change


def _move_uranium_to_technology(data: dict[str, list[dict]]) -> None:
    """Sektorwechsel einer bestehenden Industry ohne leeren Sector."""
    moved = next(r for r in data["energy"] if r["key"] == "uranium")
    data["energy"] = [r for r in data["energy"] if r["key"] != "uranium"]
    data["technology"].append(moved)


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d["energy"].append(
            {"key": "solar", "name": "Solar", "description": "Solarmodule."}), "TAX_DRIFT_ADDED"),
        (lambda d: d["energy"].pop(), "TAX_DRIFT_REMOVED"),
        (lambda d: d["energy"][0].update(name="Oil and Gas Midstream"), "TAX_DRIFT_RENAMED"),
        (_move_uranium_to_technology, "TAX_DRIFT_SECTOR"),
    ],
)
def test_identity_drift_stops_in_awaiting_approval(artifact_sandbox, mutate, code):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    taxonomy_snapshot.approve_snapshot(ErrorCollector(), "run-1")

    data = sectors()
    mutate(data)
    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(collector, FakeClient(data), run_id="run-2")
    assert code in codes(collector)
    assert result.state is WorkflowState.AWAITING_TAXONOMY_APPROVAL
    assert result.reason == taxonomy_snapshot.REASON_DRIFT
    assert collector.exit_code() is ExitCode.AWAITING_TAXONOMY_APPROVAL


def test_description_drift_only_warns(artifact_sandbox):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    taxonomy_snapshot.approve_snapshot(ErrorCollector(), "run-1")

    data = sectors()
    data["technology"][0]["description"] = "Chips, neu formuliert."
    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(collector, FakeClient(data), run_id="run-2")
    assert codes(collector) == {"TAX_DRIFT_DESCRIPTION"}
    assert not collector.has_blockers()
    assert result.state is WorkflowState.COMPLETED
    assert collector.exit_code() is ExitCode.SUCCESS_WITH_WARNINGS


def test_broken_baseline_blocks_instead_of_being_ignored(artifact_sandbox):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    taxonomy_snapshot.approve_snapshot(ErrorCollector(), "run-1")
    target = artifact_sandbox / "taxonomy" / "approved_baseline.json"

    target.write_text("kein json", encoding="utf-8")
    collector = ErrorCollector()
    taxonomy_snapshot.build_snapshot(collector, FakeClient(sectors()), run_id="run-2")
    assert "TAX_BASELINE_MALFORMED" in codes(collector)

    data = {"content_version": "taxonomy-content-v0", "industries": []}
    target.write_text(json.dumps(data), encoding="utf-8")
    collector = ErrorCollector()
    taxonomy_snapshot.build_snapshot(collector, FakeClient(sectors()), run_id="run-3")
    assert "TAX_BASELINE_VERSION" in codes(collector)

    data = {"content_version": taxonomy_snapshot.TAXONOMY_CONTENT_VERSION, "industries": []}
    target.write_text(json.dumps(data), encoding="utf-8")
    collector = ErrorCollector()
    taxonomy_snapshot.build_snapshot(collector, FakeClient(sectors()), run_id="run-4")
    assert "TAX_BASELINE_EMPTY" in codes(collector)


def test_aborted_fetch_leaves_only_a_checkpoint(artifact_sandbox):
    client = FakeClient(sectors(), fail_on="semiconductors")
    collector = ErrorCollector()
    with pytest.raises(taxonomy_source.TaxonomyFetchError):
        taxonomy_snapshot.build_snapshot(collector, client, run_id="run-1")
    run_dir = artifact_sandbox / "runs" / "run-1"
    assert (run_dir / "taxonomy_checkpoint.json").exists()
    assert not (run_dir / "taxonomy_snapshot.json").exists()


def test_resume_reuses_the_checkpoint_and_completes(artifact_sandbox):
    client = FakeClient(sectors(), fail_on="semiconductors")
    with pytest.raises(taxonomy_source.TaxonomyFetchError):
        taxonomy_snapshot.build_snapshot(ErrorCollector(), client, run_id="run-1")
    fetched_before = list(client.described)
    assert fetched_before  # der Abbruch kam nach den ersten Industries

    healthy = FakeClient(sectors())
    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(collector, healthy, run_id="run-1", resume=True)
    assert result.industries == 3
    assert set(fetched_before) & set(healthy.described) == set()  # nichts doppelt abgerufen
    assert (artifact_sandbox / "runs" / "run-1" / "taxonomy_snapshot.json").exists()


def test_checkpoint_of_a_foreign_version_is_not_mixed_in(artifact_sandbox):
    taxonomy_snapshot.Checkpoint(run_id="run-1").write()
    target = artifact_sandbox / "runs" / "run-1" / "taxonomy_checkpoint.json"
    data = json.loads(target.read_text("utf-8"))
    data["content_version"] = "taxonomy-content-v0"
    target.write_text(json.dumps(data), encoding="utf-8")

    collector = ErrorCollector()
    checkpoint = taxonomy_snapshot.load_checkpoint(collector, "run-1")
    assert checkpoint.rows == {}
    assert "TAX_CHECKPOINT_VERSION" in codes(collector, Severity.WARNUNG)


def test_foreign_run_checkpoint_is_not_adopted(artifact_sandbox):
    """Ein kopierter Checkpoint darf keinen fremden Run fortsetzen."""
    checkpoint = taxonomy_snapshot.Checkpoint(
        run_id="run-1",
        rows={"uranium": IndustryRow("uranium", "Uranium", "energy", "Energy", "Alt.")},
    )
    checkpoint.write()
    source = artifact_sandbox / "runs" / "run-1" / "taxonomy_checkpoint.json"
    target = artifact_sandbox / "runs" / "run-2" / "taxonomy_checkpoint.json"
    target.parent.mkdir(parents=True)
    target.write_text(source.read_text("utf-8"), encoding="utf-8")

    collector = ErrorCollector()
    loaded = taxonomy_snapshot.load_checkpoint(collector, "run-2")
    assert loaded.rows == {}
    assert "TAX_CHECKPOINT_FOREIGN_RUN" in codes(collector, Severity.WARNUNG)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("description"), id="feld-fehlt"),
        pytest.param(lambda r: r.update(market_cap="1e12"), id="fremdfeld"),
        pytest.param(lambda r: r.update(industry_name="  "), id="leerer-wert"),
        pytest.param(lambda r: r.update(sector_id=7), id="falscher-typ"),
    ],
)
def test_checkpoint_with_broken_records_starts_over(artifact_sandbox, mutate):
    """Ein strukturell kaputter Checkpoint wird verworfen, nicht teilweise gelesen."""
    taxonomy_snapshot.Checkpoint(
        run_id="run-1",
        rows={"uranium": IndustryRow("uranium", "Uranium", "energy", "Energy", "Text.")},
    ).write()
    target = artifact_sandbox / "runs" / "run-1" / "taxonomy_checkpoint.json"
    data = json.loads(target.read_text("utf-8"))
    mutate(data["industries"][0])
    target.write_text(json.dumps(data), encoding="utf-8")

    collector = ErrorCollector()
    loaded = taxonomy_snapshot.load_checkpoint(collector, "run-1")
    assert loaded.rows == {}
    assert "TAX_CHECKPOINT_MALFORMED" in codes(collector, Severity.WARNUNG)


def test_copied_snapshot_cannot_be_approved_under_another_run(artifact_sandbox):
    """Freigabe ist an den Run gebunden, nicht nur an einen stimmigen Hash."""
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    source = artifact_sandbox / "runs" / "run-1" / "taxonomy_snapshot.json"
    target = artifact_sandbox / "runs" / "run-2" / "taxonomy_snapshot.json"
    target.parent.mkdir(parents=True)
    target.write_text(source.read_text("utf-8"), encoding="utf-8")

    collector = ErrorCollector()
    assert taxonomy_snapshot.approve_snapshot(collector, "run-2") is None
    assert "TAX_SNAPSHOT_RUN_MISMATCH" in codes(collector)
    assert not (artifact_sandbox / "taxonomy" / "approved_baseline.json").exists()


@pytest.mark.parametrize(
    "mutate, code",
    [
        pytest.param(lambda d: d.update(content_version="taxonomy-content-v0"),
                     "TAX_SNAPSHOT_VERSION", id="fremde-version"),
        pytest.param(lambda d: d.pop("yfinance_version"),
                     "TAX_SNAPSHOT_TAMPERED", id="ohne-yfinance-version"),
        pytest.param(lambda d: d["industries"][0].update(market_cap="1e12"),
                     "TAX_SNAPSHOT_TAMPERED", id="boersengroesse-eingeschleust"),
    ],
)
def test_snapshot_violating_the_contract_cannot_be_approved(artifact_sandbox, mutate, code):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    target = artifact_sandbox / "runs" / "run-1" / "taxonomy_snapshot.json"
    data = json.loads(target.read_text("utf-8"))
    mutate(data)
    target.write_text(json.dumps(data), encoding="utf-8")

    collector = ErrorCollector()
    assert taxonomy_snapshot.approve_snapshot(collector, "run-1") is None
    assert code in codes(collector)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["industries"][0].update(description="Umgeschrieben."),
                     id="beschreibung-am-hash-vorbei"),
        pytest.param(lambda d: d["industries"][0].update(industry_name="Anders"),
                     id="name-am-hash-vorbei"),
        pytest.param(lambda d: d["industries"][0].update(market_cap="1e12"),
                     id="fremdfeld"),
        pytest.param(lambda d: d.pop("approved_run_id"), id="ohne-freigabe-run"),
    ],
)
def test_baseline_edited_past_its_hash_blocks(artifact_sandbox, mutate):
    """Die Baseline ist Bezugspunkt der Drift - ihr Hash wird nachgerechnet."""
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    taxonomy_snapshot.approve_snapshot(ErrorCollector(), "run-1")
    target = artifact_sandbox / "taxonomy" / "approved_baseline.json"
    data = json.loads(target.read_text("utf-8"))
    mutate(data)
    target.write_text(json.dumps(data), encoding="utf-8")

    collector = ErrorCollector()
    result = taxonomy_snapshot.build_snapshot(collector, FakeClient(sectors()), run_id="run-2")
    assert "TAX_BASELINE_TAMPERED" in codes(collector)
    assert result.state is WorkflowState.FAILED


def test_resume_takes_identity_live_and_only_the_description_from_the_checkpoint(artifact_sandbox):
    """Zwischen Abbruch und Resume umbenannte Industries fallen nicht durchs Raster."""
    client = FakeClient(sectors(), fail_on="semiconductors")
    with pytest.raises(taxonomy_source.TaxonomyFetchError):
        taxonomy_snapshot.build_snapshot(ErrorCollector(), client, run_id="run-1")

    renamed = sectors()
    next(r for r in renamed["energy"] if r["key"] == "uranium")["name"] = "Uranium Mining"
    healthy = FakeClient(renamed)
    result = taxonomy_snapshot.build_snapshot(
        ErrorCollector(), healthy, run_id="run-1", resume=True
    )
    snapshot = json.loads(
        (artifact_sandbox / "runs" / "run-1" / "taxonomy_snapshot.json").read_text("utf-8")
    )
    entry = next(r for r in snapshot["industries"] if r["industry_id"] == "uranium")
    assert entry["industry_name"] == "Uranium Mining"       # Identitaet frisch
    assert entry["description"] == "Uranabbau und -aufbereitung."  # Beschreibung gecacht
    assert "uranium" not in healthy.described               # nicht erneut abgerufen
    assert result.industries == 3


def test_empty_overview_is_retried_before_it_blocks(monkeypatch):
    """Eine leere Overview ist eine Drosselung und faellt in die Retrylogik."""
    calls = {"n": 0}

    class FakeIndustry:
        def __init__(self, key: str) -> None:
            self.key = key

        @property
        def overview(self) -> dict:
            calls["n"] += 1
            if calls["n"] < 3:
                return {}
            return {"description": "Endlich da.", "market_cap": 1e12}

    module = type(sys)("yfinance")
    module.Industry = FakeIndustry
    monkeypatch.setitem(sys.modules, "yfinance", module)

    client = taxonomy_source.YFinanceClient(
        replace(OFFLINE, yfinance_max_retries=3), sleep=lambda _s: None
    )
    assert client.description("uranium") == "Endlich da."
    assert calls["n"] == 3


def test_snapshot_writes_stay_inside_the_artifact_root(artifact_sandbox):
    taxonomy_snapshot.build_snapshot(ErrorCollector(), FakeClient(sectors()), run_id="run-1")
    written = [p for p in artifact_sandbox.rglob("*") if p.is_file()]
    assert written
    for path in written:
        assert paths.is_within(artifact_sandbox, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_snapshot_paths_are_bound_to_a_run(artifact_sandbox, monkeypatch, capsys):
    from committee_matcher import cli

    monkeypatch.setattr(taxonomy_source, "YFinanceClient", lambda settings: FakeClient(sectors()))
    assert cli.main(["snapshot", "build", "--run-id", "run-1"]) == int(
        ExitCode.AWAITING_TAXONOMY_APPROVAL
    )
    assert "no_baseline" in capsys.readouterr().out

    assert cli.main(["snapshot", "approve", "--run-id", "run-1"]) == int(ExitCode.SUCCESS)
    assert cli.main(["snapshot", "build", "--run-id", "run-2"]) == int(ExitCode.SUCCESS)

    assert cli.main(["snapshot", "resume"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    assert "CLI_RUN_ID_REQUIRED" in capsys.readouterr().out
    assert cli.main(["snapshot", "approve"]) == int(ExitCode.PREFLIGHT_BLOCKED)
    capsys.readouterr()


def test_cli_rejects_a_malformed_run_id(artifact_sandbox, monkeypatch, capsys):
    from committee_matcher import cli

    monkeypatch.setattr(taxonomy_source, "YFinanceClient", lambda settings: FakeClient(sectors()))
    assert cli.main(["snapshot", "build", "--run-id", "../ausbruch"]) == int(
        ExitCode.PREFLIGHT_BLOCKED
    )
    assert "CLI_RUN_ID_INVALID" in capsys.readouterr().out
