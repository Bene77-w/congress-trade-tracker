"""Snapshot, Katalog, Content-Hash und Driftgate der Taxonomie (Plan 2.4).

Der Snapshot ist **all-or-nothing**: erst ein vollstaendiger, kollisionsfreier
Abruf wird persistiert. Ein halber Snapshot koennte Mappings speisen, die eine
Industry nie gesehen haben - dieser Fehler waere spaeter nicht mehr erkennbar.
Ein abgebrochener Abruf hinterlaesst deshalb nur einen run-lokalen Checkpoint,
aus dem ``snapshot resume`` fortsetzt.

``taxonomy-content-v1`` ist der versionierte Inhaltsvertrag: SHA-256 ueber die
kanonische JSON-Serialisierung der sortierten Industries mit genau den fuenf
erlaubten Feldern. Abrufzeiten, Run- und Snapshot-IDs gehen nicht ein - zwei
Abrufe derselben Taxonomie ergeben denselben Hash.

Freigabelogik:

- ohne freigegebene Baseline stoppt der Lauf mit Grund ``no_baseline``;
- relevante Drift (Industry-IDs, Namen, Sektorzuordnung) stoppt ebenfalls;
- beides fuehrt in ``awaiting_taxonomy_approval`` (Exit 5), nie in eine stille
  Uebernahme;
- reine Description-Aenderungen werden protokolliert und blockieren nicht; ihre
  endgueltige Freigabepolicy legt Gate C fest;
- ``snapshot approve`` ist an dieselbe ``run_id`` **und** denselben
  ``taxonomy_content_hash`` gebunden.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .errors import ErrorCollector, Stage
from .models import IndustryRecord, TaxonomySnapshot
from .status import ExitCode, WorkflowState
from .taxonomy_source import (
    INDUSTRY_FIELDS,
    IndustryRow,
    YFinanceClient,
    fetch_taxonomy,
)

#: Version des Inhaltsvertrags; jede Aenderung an Feldmenge oder
#: Serialisierung braucht eine neue Version.
TAXONOMY_CONTENT_VERSION = "taxonomy-content-v1"

#: Ablageorte unterhalb der Artefaktwurzel.
RUNS_DIR = "runs"
TAXONOMY_DIR = "taxonomy"
CHECKPOINT_FILENAME = "taxonomy_checkpoint.json"
SNAPSHOT_FILENAME = "taxonomy_snapshot.json"
CATALOG_FILENAME = "taxonomy_catalog.md"
BASELINE_FILENAME = "approved_baseline.json"

#: Gruende, die in ``awaiting_taxonomy_approval`` fuehren.
REASON_NO_BASELINE = "no_baseline"
REASON_DRIFT = "drift"


def checkpoint_path(run_id: str) -> Path:
    return paths.artifact_path(RUNS_DIR, run_id, CHECKPOINT_FILENAME)


def snapshot_path(run_id: str) -> Path:
    return paths.artifact_path(RUNS_DIR, run_id, SNAPSHOT_FILENAME)


def catalog_path(run_id: str) -> Path:
    return paths.artifact_path(RUNS_DIR, run_id, CATALOG_FILENAME)


def baseline_path() -> Path:
    return paths.artifact_path(TAXONOMY_DIR, BASELINE_FILENAME)


# ---------------------------------------------------------------------------
# Inhaltsvertrag
# ---------------------------------------------------------------------------

def content_records(rows: "tuple[IndustryRow, ...] | list[IndustryRow]") -> list[dict[str, str]]:
    """Kanonische Datensatzliste: sortiert, auf die Allowlist reduziert."""
    return [row.as_record() for row in sorted(rows, key=lambda r: r.industry_id)]


def hash_records(records: "list[dict[str, str]]") -> str:
    """SHA-256 ueber bereits kanonische Datensaetze."""
    payload = {
        "version": TAXONOMY_CONTENT_VERSION,
        "industries": sorted(records, key=lambda r: r["industry_id"]),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def content_hash(rows: "tuple[IndustryRow, ...] | list[IndustryRow]") -> str:
    """SHA-256 des Taxonomieinhalts gemaess ``taxonomy-content-v1``."""
    return hash_records(content_records(rows))


def strict_records(payload: object) -> list[dict[str, str]] | None:
    """Liest eine persistierte Industryliste strikt; ``None`` = Vertragsbruch.

    Streng heisst: exakt die fuenf erlaubten Felder, alle nichtleere Strings,
    keine doppelte ID. Ein Fremdfeld wird nicht stillschweigend ignoriert -
    stuende eine Boersengroesse in einem Artefakt, waere der Adapterrand
    umgangen worden, und genau das soll auffallen.
    """
    if not isinstance(payload, list):
        return None
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict) or set(entry) != set(INDUSTRY_FIELDS):
            return None
        values: dict[str, str] = {}
        for key in INDUSTRY_FIELDS:
            value = entry[key]
            if not isinstance(value, str) or not value.strip():
                return None
            values[key] = value
        if values["industry_id"] in seen:
            return None
        seen.add(values["industry_id"])
        records.append(values)
    return records


def build_catalog(rows: "tuple[IndustryRow, ...] | list[IndustryRow]") -> str:
    """Nach Sektoren gruppierter Markdown-Katalog des Snapshots."""
    by_sector: dict[tuple[str, str], list[IndustryRow]] = {}
    for row in rows:
        by_sector.setdefault((row.sector_id, row.sector_name), []).append(row)

    lines = [
        "# yfinance-Industry-Katalog",
        "",
        f"Inhaltsvertrag: `{TAXONOMY_CONTENT_VERSION}` · "
        f"Content-Hash: `{content_hash(rows)}`",
        "",
        f"{len(rows)} Industries in {len(by_sector)} Sektoren. Boersengroessen "
        "sind bewusst nicht enthalten.",
        "",
    ]
    for (sector_id, sector_name), items in sorted(by_sector.items()):
        lines.append(f"## {sector_name} (`{sector_id}`)")
        lines.append("")
        for row in sorted(items, key=lambda r: r.industry_id):
            lines.append(f"- **{row.industry_name}** (`{row.industry_id}`): {row.description}")
        lines.append("")
    return "\n".join(lines)


def validate_rows(collector: ErrorCollector, rows: "tuple[IndustryRow, ...] | list[IndustryRow]") -> bool:
    """Prueft Vollstaendigkeit und Kollisionsfreiheit vor der Persistenz."""
    if not rows:
        collector.blocker(
            "TAX_SNAPSHOT_EMPTY", Stage.TAXONOMY, "snapshot",
            "Snapshot enthaelt keine Industry.",
            "Abruf wiederholen; ein leerer Snapshot darf keine Mappings speisen.",
        )
        return False

    seen: dict[str, IndustryRow] = {}
    ok = True
    for row in rows:
        for field_name in INDUSTRY_FIELDS:
            if not str(getattr(row, field_name, "")).strip():
                collector.blocker(
                    "TAX_FIELD_MISSING", Stage.TAXONOMY, row.industry_id or "?",
                    f"Feld {field_name!r} ist leer.",
                    "Abruf wiederholen; unvollstaendige Industries blockieren.",
                )
                ok = False
        previous = seen.get(row.industry_id)
        if previous is not None:
            ok = False
            if previous.as_record() != row.as_record():
                collector.blocker(
                    "TAX_ID_COLLISION", Stage.TAXONOMY, row.industry_id,
                    f"Industry-ID kommt in {previous.sector_id} und "
                    f"{row.sector_id} mit unterschiedlichen Daten vor.",
                    "Abruf wiederholen; kollidierende IDs blockieren den Snapshot.",
                )
            else:
                collector.blocker(
                    "TAX_ID_DUPLICATE", Stage.TAXONOMY, row.industry_id,
                    "Industry-ID kommt mehrfach vor.",
                    "Abruf wiederholen; doppelte IDs blockieren den Snapshot.",
                )
            continue
        seen[row.industry_id] = row
    return ok


def to_model(rows: "tuple[IndustryRow, ...] | list[IndustryRow]", *, snapshot_id: str,
             yfinance_version: str) -> TaxonomySnapshot:
    """Baut das validierte Snapshotmodell (Boersengroessen sind hier bereits weg)."""
    return TaxonomySnapshot(
        snapshot_id=snapshot_id,
        yfinance_version=yfinance_version,
        taxonomy_content_hash=content_hash(rows),
        industries=tuple(
            IndustryRecord(**record) for record in content_records(rows)
        ),
    )


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    """Run-lokaler Fortschritt eines Abrufs.

    Der Checkpoint ist bewusst kein Snapshot: er wird nie freigegeben und nie
    an Modellcalls uebergeben. Er existiert nur, damit ein abgebrochener Abruf
    fortsetzbar bleibt.
    """

    run_id: str
    rows: dict[str, IndustryRow] = field(default_factory=dict)

    def as_payload(self) -> dict:
        return {
            "run_id": self.run_id,
            "content_version": TAXONOMY_CONTENT_VERSION,
            "industries": [row.as_record() for row in
                           sorted(self.rows.values(), key=lambda r: r.industry_id)],
        }

    def write(self) -> Path:
        target = paths.artifact_path(RUNS_DIR, self.run_id, CHECKPOINT_FILENAME,
                                     create_parents=True)
        return paths.write_text_atomic(
            target, json.dumps(self.as_payload(), ensure_ascii=False, indent=2) + "\n"
        )


def load_checkpoint(collector: ErrorCollector, run_id: str) -> Checkpoint:
    """Liest den Checkpoint eines Runs; fehlt er, beginnt der Abruf von vorn."""
    target = paths.artifact_path(RUNS_DIR, run_id, CHECKPOINT_FILENAME)
    if not target.exists():
        return Checkpoint(run_id=run_id)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        collector.warning(
            "TAX_CHECKPOINT_MALFORMED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Checkpoint ist kein gueltiges JSON.",
            "Abruf beginnt von vorn; der Checkpoint wird ueberschrieben.",
        )
        return Checkpoint(run_id=run_id)
    if not isinstance(data, dict):
        collector.warning(
            "TAX_CHECKPOINT_MALFORMED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Checkpoint ist kein JSON-Objekt.",
            "Abruf beginnt von vorn; der Checkpoint wird ueberschrieben.",
        )
        return Checkpoint(run_id=run_id)
    if data.get("content_version") != TAXONOMY_CONTENT_VERSION:
        collector.warning(
            "TAX_CHECKPOINT_VERSION", Stage.TAXONOMY, paths.relative_to_base(target),
            f"Checkpoint traegt {data.get('content_version')!r} statt "
            f"{TAXONOMY_CONTENT_VERSION!r}.",
            "Abruf beginnt von vorn; ein fremder Inhaltsvertrag wird nicht gemischt.",
        )
        return Checkpoint(run_id=run_id)
    if data.get("run_id") != run_id:
        # Ein Checkpoint gehoert genau einem Run. Waere er uebertragbar, koennte
        # ein Resume Beschreibungen aus einem fremden Lauf uebernehmen, ohne
        # dass der Snapshot das noch erkennen laesst.
        collector.warning(
            "TAX_CHECKPOINT_FOREIGN_RUN", Stage.TAXONOMY, paths.relative_to_base(target),
            f"Checkpoint gehoert zu Run {data.get('run_id')!r}, nicht zu {run_id!r}.",
            "Abruf beginnt von vorn; Checkpoints sind nicht zwischen Runs uebertragbar.",
        )
        return Checkpoint(run_id=run_id)
    records = strict_records(data.get("industries"))
    if records is None:
        collector.warning(
            "TAX_CHECKPOINT_MALFORMED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Checkpointeintraege verletzen den Inhaltsvertrag.",
            "Abruf beginnt von vorn; der Checkpoint wird ueberschrieben.",
        )
        return Checkpoint(run_id=run_id)
    rows = {
        record["industry_id"]: IndustryRow(**record) for record in records
    }
    return Checkpoint(run_id=run_id, rows=rows)


# ---------------------------------------------------------------------------
# Baseline und Drift
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftReport:
    """Vergleich eines Snapshots mit der freigegebenen Baseline."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    renamed: tuple[str, ...] = ()
    resectored: tuple[str, ...] = ()
    described: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        """Nur Identitaetsdrift blockiert; Beschreibungen werden protokolliert."""
        return bool(self.added or self.removed or self.renamed or self.resectored)

    @property
    def any_change(self) -> bool:
        return self.blocking or bool(self.described)


def _baseline_document(collector: ErrorCollector) -> dict | None:
    """Liest die freigegebene Baseline vollstaendig geprueft; ``None`` = unbrauchbar.

    Rueckgabe ist das Rohdokument, nicht nur die Industryliste: Freigaberun und
    yfinance-Version gehoeren zur Provenienz und werden von ``approved_snapshot``
    gebraucht.
    """
    target = baseline_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        collector.blocker(
            "TAX_BASELINE_MALFORMED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Freigegebene Baseline ist kein gueltiges JSON.",
            "Baseline pruefen und bewusst neu freigeben.",
        )
        return None
    if not isinstance(data, dict):
        collector.blocker(
            "TAX_BASELINE_MALFORMED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Freigegebene Baseline ist kein JSON-Objekt.",
            "Baseline pruefen und bewusst neu freigeben.",
        )
        return None
    if data.get("content_version") != TAXONOMY_CONTENT_VERSION:
        collector.blocker(
            "TAX_BASELINE_VERSION", Stage.TAXONOMY, paths.relative_to_base(target),
            f"Baseline traegt {data.get('content_version')!r} statt "
            f"{TAXONOMY_CONTENT_VERSION!r}.",
            "Inhaltsvertrag und Baseline gemeinsam versionieren.",
        )
        return None
    entries = strict_records(data.get("industries"))
    if entries is None:
        collector.blocker(
            "TAX_BASELINE_TAMPERED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Baselineeintraege verletzen den Inhaltsvertrag.",
            "Baseline pruefen und bewusst neu freigeben.",
        )
        return None
    if not entries:
        collector.blocker(
            "TAX_BASELINE_EMPTY", Stage.TAXONOMY, paths.relative_to_base(target),
            "Freigegebene Baseline enthaelt keine Industry.",
            "Baseline bewusst neu freigeben.",
        )
        return None
    # Die Baseline ist der Bezugspunkt der Drifterkennung. Wird sie am Hash
    # vorbei veraendert - etwa eine Beschreibung angepasst -, wuerde genau die
    # Aenderung unsichtbar, die der Vergleich melden soll. Deshalb wird ihr
    # Hash nachgerechnet statt geglaubt.
    digest = hash_records(entries)
    if digest != data.get("taxonomy_content_hash"):
        collector.blocker(
            "TAX_BASELINE_TAMPERED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Content-Hash passt nicht zum Baselineinhalt.",
            "Baseline pruefen und bewusst neu freigeben.",
        )
        return None
    approved_run = data.get("approved_run_id")
    if not isinstance(approved_run, str) or not approved_run.strip():
        collector.blocker(
            "TAX_BASELINE_TAMPERED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Baseline nennt keinen freigegebenen Run.",
            "Baseline pruefen und bewusst neu freigeben.",
        )
        return None
    version = data.get("yfinance_version")
    if not isinstance(version, str) or not version.strip():
        # Die Version gehoert zur Provenienz jedes Mappingurteils; fehlt sie,
        # ist nicht mehr belegbar, welcher Katalogstand entschieden hat.
        collector.blocker(
            "TAX_BASELINE_TAMPERED", Stage.TAXONOMY, paths.relative_to_base(target),
            "Baseline nennt keine yfinance-Version.",
            "Baseline pruefen und bewusst neu freigeben.",
        )
        return None
    return data


def load_baseline(collector: ErrorCollector) -> dict[str, dict[str, str]] | None:
    """Freigegebene Baseline als Karte Industry-ID -> Datensatz."""
    data = _baseline_document(collector)
    if data is None:
        return None
    return {record["industry_id"]: record
            for record in strict_records(data.get("industries")) or []}


def approved_snapshot(collector: ErrorCollector) -> TaxonomySnapshot | None:
    """Die freigegebene Taxonomie als Kontrollinput eines Mappinglaufs.

    Gemappt wird ausschliesslich gegen einen freigegebenen Stand: der Snapshot
    eines Runs ist erst nach ``snapshot approve`` verbindlich. Fehlt die
    Freigabe, blockiert der Lauf mit Exit-Code 5 statt gegen eine ungepruefte
    Taxonomie zu entscheiden.

    ``snapshot_id`` ist der freigegebene Run - genau die Revision, gegen die das
    Modell entschieden hat, und damit die Provenienz jeder Artefaktzeile.
    """
    if not baseline_path().exists():
        collector.blocker(
            "TAX_BASELINE_MISSING", Stage.TAXONOMY,
            paths.relative_to_base(baseline_path()),
            "Es ist keine Taxonomie freigegeben.",
            "'snapshot build --run-id <revision>' und danach "
            "'snapshot approve --run-id <revision>' ausfuehren.",
            exit_code=ExitCode.AWAITING_TAXONOMY_APPROVAL,
        )
        return None
    data = _baseline_document(collector)
    if data is None:
        return None
    records = strict_records(data.get("industries")) or []
    rows = tuple(IndustryRow(**record) for record in records)
    return to_model(
        rows,
        snapshot_id=str(data["approved_run_id"]),
        yfinance_version=str(data["yfinance_version"]),
    )


def compare_with_baseline(
    rows: "tuple[IndustryRow, ...] | list[IndustryRow]",
    baseline: dict[str, dict[str, str]],
) -> DriftReport:
    """Stellt Identitaets- und Beschreibungsdrift getrennt fest."""
    current = {row.industry_id: row.as_record() for row in rows}
    added = tuple(sorted(set(current) - set(baseline)))
    removed = tuple(sorted(set(baseline) - set(current)))
    renamed: list[str] = []
    resectored: list[str] = []
    described: list[str] = []
    for industry_id in sorted(set(current) & set(baseline)):
        now, before = current[industry_id], baseline[industry_id]
        if now["industry_name"] != before.get("industry_name"):
            renamed.append(industry_id)
        if (now["sector_id"], now["sector_name"]) != (
            before.get("sector_id"), before.get("sector_name")
        ):
            resectored.append(industry_id)
        if now["description"] != before.get("description"):
            described.append(industry_id)
    return DriftReport(
        added=added, removed=removed, renamed=tuple(renamed),
        resectored=tuple(resectored), described=tuple(described),
    )


def report_drift(collector: ErrorCollector, drift: DriftReport) -> None:
    """Meldet Drift befundgenau: Identitaet blockiert, Beschreibung warnt."""
    for code, obj_list, message in (
        ("TAX_DRIFT_ADDED", drift.added, "Industry ist neu gegenueber der Baseline."),
        ("TAX_DRIFT_REMOVED", drift.removed, "Industry der Baseline fehlt im Snapshot."),
        ("TAX_DRIFT_RENAMED", drift.renamed, "Industry-Name weicht von der Baseline ab."),
        ("TAX_DRIFT_SECTOR", drift.resectored, "Sektorzuordnung weicht von der Baseline ab."),
    ):
        for industry_id in obj_list:
            collector.blocker(
                code, Stage.TAXONOMY, industry_id, message,
                "Drift pruefen und mit 'snapshot approve --run-id ...' bewusst "
                "freigeben.",
                exit_code=ExitCode.AWAITING_TAXONOMY_APPROVAL,
            )
    for industry_id in drift.described:
        collector.warning(
            "TAX_DRIFT_DESCRIPTION", Stage.TAXONOMY, industry_id,
            "Beschreibung weicht von der Baseline ab.",
            "Protokolliert; die Freigabepolicy fuer Beschreibungen legt Gate C fest.",
        )


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotResult:
    """Ergebnis eines Snapshotlaufs."""

    run_id: str
    state: WorkflowState
    reason: str | None
    content_hash: str | None
    industries: int
    artifacts: tuple[str, ...]
    drift: DriftReport | None = None


def build_snapshot(
    collector: ErrorCollector,
    client: YFinanceClient,
    *,
    run_id: str,
    resume: bool = False,
) -> SnapshotResult:
    """Ruft die Taxonomie ab, prueft sie und persistiert sie all-or-nothing."""
    checkpoint = load_checkpoint(collector, run_id) if resume else Checkpoint(run_id=run_id)
    known = dict(checkpoint.rows)

    def remember(row: IndustryRow) -> None:
        checkpoint.rows[row.industry_id] = row
        if len(checkpoint.rows) % _checkpoint_every(client) == 0:
            checkpoint.write()

    try:
        rows = fetch_taxonomy(collector, client, known=known, on_progress=remember)
    finally:
        # Auch ein abgebrochener Abruf hinterlaesst seinen Fortschritt - sonst
        # beginnt 'snapshot resume' unnoetig von vorn.
        checkpoint.write()

    if collector.has_blockers() or not validate_rows(collector, rows):
        return SnapshotResult(
            run_id=run_id, state=WorkflowState.FAILED, reason="incomplete_fetch",
            content_hash=None, industries=len(rows), artifacts=(),
        )

    digest = content_hash(rows)
    artifacts: list[str] = []
    snapshot = to_model(
        rows, snapshot_id=run_id, yfinance_version=client.version()
    )
    target = paths.artifact_path(RUNS_DIR, run_id, SNAPSHOT_FILENAME, create_parents=True)
    paths.write_text_atomic(
        target,
        json.dumps(
            {
                "content_version": TAXONOMY_CONTENT_VERSION,
                **snapshot.model_dump(mode="json"),
            },
            ensure_ascii=False, indent=2,
        ) + "\n",
    )
    artifacts.append(paths.relative_to_base(target))
    catalog = paths.artifact_path(RUNS_DIR, run_id, CATALOG_FILENAME, create_parents=True)
    paths.write_text_atomic(catalog, build_catalog(rows))
    artifacts.append(paths.relative_to_base(catalog))

    baseline = load_baseline(collector)
    if collector.has_blockers():
        return SnapshotResult(
            run_id=run_id, state=WorkflowState.FAILED, reason="baseline_unreadable",
            content_hash=digest, industries=len(rows), artifacts=tuple(artifacts),
        )
    if baseline is None:
        collector.blocker(
            "TAX_NO_BASELINE", Stage.TAXONOMY, run_id,
            "Es gibt keine freigegebene Taxonomie-Baseline.",
            f"Snapshot pruefen und mit 'snapshot approve --run-id {run_id}' "
            "erstmalig freigeben.",
            exit_code=ExitCode.AWAITING_TAXONOMY_APPROVAL,
        )
        return SnapshotResult(
            run_id=run_id, state=WorkflowState.AWAITING_TAXONOMY_APPROVAL,
            reason=REASON_NO_BASELINE, content_hash=digest,
            industries=len(rows), artifacts=tuple(artifacts),
        )

    drift = compare_with_baseline(rows, baseline)
    report_drift(collector, drift)
    if drift.blocking:
        return SnapshotResult(
            run_id=run_id, state=WorkflowState.AWAITING_TAXONOMY_APPROVAL,
            reason=REASON_DRIFT, content_hash=digest, industries=len(rows),
            artifacts=tuple(artifacts), drift=drift,
        )
    return SnapshotResult(
        run_id=run_id, state=WorkflowState.COMPLETED, reason=None,
        content_hash=digest, industries=len(rows), artifacts=tuple(artifacts),
        drift=drift,
    )


def _checkpoint_every(client: YFinanceClient) -> int:
    """Schrittweite des Checkpoints; ein Testdouble darf sie weglassen."""
    return max(1, int(getattr(client, "checkpoint_every", 10) or 1))


def approve_snapshot(collector: ErrorCollector, run_id: str) -> str | None:
    """Gibt den Snapshot eines Runs als neue Baseline frei.

    Freigabe ist an ``run_id`` **und** Inhalt gebunden: freigegeben wird genau
    der Snapshot, der unter diesem Run persistiert wurde. Ein zwischenzeitlich
    veraenderter Snapshot faellt ueber den Content-Hash auf.
    """
    target = paths.artifact_path(RUNS_DIR, run_id, SNAPSHOT_FILENAME)
    if not target.exists():
        collector.blocker(
            "TAX_SNAPSHOT_MISSING", Stage.TAXONOMY, run_id,
            "Zu diesem Run existiert kein Snapshot.",
            "Zuerst 'snapshot build' ausfuehren.",
        )
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        collector.blocker(
            "TAX_SNAPSHOT_MALFORMED", Stage.TAXONOMY, run_id,
            "Snapshot ist kein gueltiges JSON.",
            "Snapshot neu erzeugen.",
        )
        return None

    if not isinstance(data, dict):
        collector.blocker(
            "TAX_SNAPSHOT_MALFORMED", Stage.TAXONOMY, run_id,
            "Snapshot ist kein JSON-Objekt.",
            "Snapshot neu erzeugen.",
        )
        return None
    if data.get("content_version") != TAXONOMY_CONTENT_VERSION:
        collector.blocker(
            "TAX_SNAPSHOT_VERSION", Stage.TAXONOMY, run_id,
            f"Snapshot traegt {data.get('content_version')!r} statt "
            f"{TAXONOMY_CONTENT_VERSION!r}.",
            "Inhaltsvertrag und Snapshot gemeinsam versionieren.",
        )
        return None
    if data.get("snapshot_id") != run_id:
        # Freigegeben wird der Snapshot dieses Runs. Ohne diese Pruefung liesse
        # sich ein in ein anderes Runverzeichnis kopierter Snapshot mit intaktem
        # Hash als Ergebnis dieses Runs freigeben.
        collector.blocker(
            "TAX_SNAPSHOT_RUN_MISMATCH", Stage.TAXONOMY, run_id,
            f"Snapshot ist an Run {data.get('snapshot_id')!r} gebunden.",
            "Snapshot des angegebenen Runs freigeben oder neu erzeugen.",
        )
        return None
    version = data.get("yfinance_version")
    if not isinstance(version, str) or not version.strip():
        collector.blocker(
            "TAX_SNAPSHOT_TAMPERED", Stage.TAXONOMY, run_id,
            "Snapshot nennt keine yfinance-Version.",
            "Snapshot neu erzeugen; freigegeben wird nur unveraenderter Inhalt.",
        )
        return None

    records = strict_records(data.get("industries"))
    if records is None:
        collector.blocker(
            "TAX_SNAPSHOT_TAMPERED", Stage.TAXONOMY, run_id,
            "Snapshoteintraege verletzen den Inhaltsvertrag.",
            "Snapshot neu erzeugen; freigegeben wird nur unveraenderter Inhalt.",
        )
        return None
    rows = tuple(IndustryRow(**record) for record in records)
    if not validate_rows(collector, rows):
        return None
    digest = content_hash(rows)
    if digest != data.get("taxonomy_content_hash"):
        collector.blocker(
            "TAX_SNAPSHOT_TAMPERED", Stage.TAXONOMY, run_id,
            "Content-Hash passt nicht zum Snapshotinhalt.",
            "Snapshot neu erzeugen; freigegeben wird nur unveraenderter Inhalt.",
        )
        return None

    baseline = paths.artifact_path(TAXONOMY_DIR, BASELINE_FILENAME, create_parents=True)
    paths.write_text_atomic(
        baseline,
        json.dumps(
            {
                "content_version": TAXONOMY_CONTENT_VERSION,
                "approved_run_id": run_id,
                "taxonomy_content_hash": digest,
                "yfinance_version": version,
                "industries": content_records(rows),
            },
            ensure_ascii=False, indent=2,
        ) + "\n",
    )
    return digest
