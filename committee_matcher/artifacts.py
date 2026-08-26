"""Ergebnisartefakte eines Mappinglaufs (Plan 2.8, P7b).

Dieses Modul ist die einzige Stelle, die Laufergebnisse in Dateien giesst. Es
entscheidet nichts fachlich: es liest den Laufzustand, loest Identitaeten gegen
Snapshot und Runmanifest auf und schreibt.

Zwei Regeln bestimmen den Zuschnitt:

- **Nie still kuerzen.** Jede erwartete Unit erscheint in der Coverage genau
  einmal, auch unentschieden oder gescheitert. Ein Zustandseintrag ohne
  erwartete Unit, eine unbekannte Industry oder ein Zustand, der seinem eigenen
  Ergebnis widerspricht, blockiert das Schreiben - eine Datei, die weniger
  zeigt als der Lauf hergab, waere schlimmer als keine.
- **Kein Fremdwert als Provenienz.** Industry- und Sektornamen stammen aus dem
  Snapshot, Modell- und Promptangaben aus dem Runmanifest. Das Modell nennt nur
  die ``industry_id``; alles andere wird hier aufgeloest.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import paths, validation, workflow
from .errors import Finding, MatcherError, Severity, Stage
from .models import MappingUnitResult, MatcherUnit, RosterCoverageRow, TaxonomySnapshot, UnitCoverageRow
from .status import AssignmentSource, ExitCode, MappingDecision, UnitStatus

#: Dateinamen im Mappingordner des Runs.
ASSIGNMENTS_FILENAME = "assignments_long.csv"
UNIT_COVERAGE_FILENAME = "unit_coverage.csv"
BOUNDARY_FILENAME = "boundary_conflicts.csv"
ROSTER_COVERAGE_FILENAME = "roster_coverage.csv"
COST_REPORT_FILENAME = "cost_report.json"

#: Versionen der Artefaktvertraege; sie wandern in die Kopfzeile ihres Berichts
#: beziehungsweise in das JSON.
COST_REPORT_VERSION = "cost-report-v1"

#: Spaltenvertraege. Reihenfolge und Schreibweise sind Teil des Artefakts und
#: durch Golden Tests gebunden - nachgelagerte Werkzeuge lesen nach Namen, der
#: Review liest nach Position.
ASSIGNMENT_COLUMNS: tuple[str, ...] = (
    "run_id", "revision_id", "unit_id", "chamber", "committee_name", "unit_name",
    "unit_kind", "input_basis", "mapping_input_hash", "decision",
    "broad_scope_exception", "industry_id", "industry_name", "sector_id",
    "sector_name", "confidence", "evidence_excerpt", "rationale_short",
    "assignment_source", "model", "taxonomy_content_hash", "snapshot_id",
    "prompt_id", "prompt_version", "prompt_sha256", "schema_name",
    "schema_sha256",
)
UNIT_COVERAGE_COLUMNS: tuple[str, ...] = (
    "run_id", "revision_id", "unit_id", "chamber", "committee_name", "unit_name",
    "unit_kind", "status", "assignment_count", "no_mapping_reason",
    "broad_scope_exception", "violations",
)
BOUNDARY_COLUMNS: tuple[str, ...] = (
    "run_id", "family_id", "industry_id", "industry_name", "unit_count", "unit_ids",
)
ROSTER_COVERAGE_COLUMNS: tuple[str, ...] = (
    "run_id", "chamber", "committee_name", "unit_name", "coverage_status",
    "unit_id", "match_basis", "hint", "fuzzy_suggestion",
)

#: Trennt mehrere Werte innerhalb einer Zelle. Kein Komma und kein Semikolon:
#: beide kommen in Ausschussnamen vor und wuerden beim Zurueckleseneine
#: Mehrdeutigkeit erzeugen.
CELL_SEPARATOR = "|"

#: Der Mappingprompt bestimmt das Ergebnis; der Reparaturprompt korrigiert nur
#: die Form. Als Provenienz einer Zuordnung zaehlt deshalb der Mappingprompt.
PROVENANCE_PROMPT_ID = "mapping_system"


class ArtifactError(MatcherError):
    """Die Laufdaten tragen das Artefakt nicht; es wird nicht geschrieben."""


def _blocked(code: str, obj: str, message: str, hint: str,
             exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> ArtifactError:
    return ArtifactError([Finding(
        severity=Severity.BLOCKER,
        code=code,
        stage=Stage.WORKFLOW,
        obj=obj,
        message=message,
        hint=hint,
        exit_code=exit_code,
    )], f"{code}: {obj}")


@dataclass(frozen=True)
class ArtifactFile:
    """Eine geschriebene Datei samt Umfang - Grundlage des Terminalberichts."""

    path: Path
    rows: int

    @property
    def relative(self) -> str:
        return paths.relative_to_base(self.path)


@dataclass(frozen=True)
class ArtifactSet:
    """Die Artefakte eines Laufs in der Reihenfolge ihrer Entstehung."""

    assignments: ArtifactFile
    unit_coverage: ArtifactFile
    boundary_conflicts: ArtifactFile
    cost_report: ArtifactFile
    roster_coverage: ArtifactFile | None = None

    def files(self) -> tuple[ArtifactFile, ...]:
        entries = (self.assignments, self.unit_coverage, self.boundary_conflicts,
                   self.cost_report, self.roster_coverage)
        return tuple(entry for entry in entries if entry is not None)

    def report_paths(self) -> tuple[str, ...]:
        return tuple(entry.relative for entry in self.files())


# ---------------------------------------------------------------------------
# Provenienz aus dem Runmanifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Provenance:
    """Die Laufbedingungen, die jede Zuordnungszeile mitfuehrt."""

    run_id: str
    snapshot_id: str
    model: str
    taxonomy_content_hash: str
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    schema_name: str
    schema_sha256: str


def provenance(manifest: Mapping) -> Provenance:
    """Liest die Provenienz aus dem Runmanifest.

    Fehlt ein Feld, wird das Artefakt nicht geschrieben. Eine Zuordnung ohne
    belegbare Herkunft waere im Review nicht nachvollziehbar und im
    Wiederholungsfall nicht zuzuordnen - eine leere Spalte wuerde diesen Mangel
    nur verstecken.
    """
    def required(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise _blocked(
                "ART_PROVENANCE_INCOMPLETE", field,
                f"Das Runmanifest traegt kein verwendbares Feld {field!r}.",
                "Lauf mit vollstaendigem Manifest wiederholen; die Artefakte "
                "muessen Modell, Prompt und Schema belegen koennen.")
        return value

    schema = manifest.get("schema")
    schema = schema if isinstance(schema, Mapping) else {}
    entries = manifest.get("prompts")
    entries = entries if isinstance(entries, Sequence) else ()
    prompt: Mapping = {}
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("prompt_id") == PROVENANCE_PROMPT_ID:
            prompt = entry
            break
    if not prompt:
        raise _blocked(
            "ART_PROVENANCE_INCOMPLETE", PROVENANCE_PROMPT_ID,
            f"Das Runmanifest nennt keinen Prompt {PROVENANCE_PROMPT_ID!r}.",
            "Lauf mit vollstaendigem Manifest wiederholen; der Mappingprompt "
            "ist die Provenienz jeder Zuordnung.")
    return Provenance(
        run_id=required(manifest.get("run_id"), "run_id"),
        snapshot_id=required(manifest.get("snapshot_id"), "snapshot_id"),
        model=required(manifest.get("model"), "model"),
        taxonomy_content_hash=required(manifest.get("taxonomy_content_hash"),
                                       "taxonomy_content_hash"),
        prompt_id=required(prompt.get("prompt_id"), "prompts[].prompt_id"),
        prompt_version=required(prompt.get("prompt_version"), "prompts[].prompt_version"),
        prompt_sha256=required(prompt.get("prompt_sha256"), "prompts[].prompt_sha256"),
        schema_name=required(schema.get("schema_name"), "schema.schema_name"),
        schema_sha256=required(schema.get("schema_sha256"), "schema.schema_sha256"),
    )


# ---------------------------------------------------------------------------
# Zustand gegen Sollmenge pruefen
# ---------------------------------------------------------------------------

def _expected(units: Iterable[MatcherUnit]) -> dict[str, MatcherUnit]:
    index: dict[str, MatcherUnit] = {}
    for unit in units:
        if unit.unit_id in index:
            raise _blocked(
                "ART_DUPLICATE_UNIT", unit.unit_id,
                "Die erwartete Unitmenge enthaelt dieselbe Unit mehrfach.",
                "Unitbildung pruefen; jede Unit erscheint in der Coverage genau "
                "einmal.")
        index[unit.unit_id] = unit
    return index


def _checked_records(expected: Mapping[str, MatcherUnit],
                     state: workflow.RunState) -> dict[str, workflow.UnitRecord]:
    """Nimmt nur Zustandseintraege an, die zur Sollmenge passen und stimmig sind."""
    for unit_id, record in state.units.items():
        if unit_id not in expected:
            raise _blocked(
                "ART_UNEXPECTED_UNIT", unit_id,
                "Der Laufzustand kennt eine Unit, die dieser Lauf nicht erwartet.",
                "Run-ID und Unitauswahl pruefen; ein fremder Zustandseintrag "
                "darf nicht in die Artefakte geraten.")
        if record.status in (UnitStatus.MAPPED, UnitStatus.NO_MAPPING) and not record.reusable:
            raise _blocked(
                "ART_STATE_INCONSISTENT", unit_id,
                f"Status {record.status.value!r} passt nicht zum gespeicherten "
                "Ergebnis der Unit.",
                "Laufzustand pruefen; ein Artefakt darf keinen Widerspruch "
                "zwischen Status und Modellurteil festschreiben.")
    return dict(state.units)


def _industry(snapshot: TaxonomySnapshot, industry_id: str, unit_id: str):
    for record in snapshot.industries:
        if record.industry_id == industry_id:
            return record
    raise _blocked(
        "ART_UNKNOWN_INDUSTRY", f"{unit_id}/{industry_id}",
        "Die zugeordnete Industry steht nicht im Snapshot des Laufs.",
        "Snapshotbindung des Laufs pruefen; Industry- und Sektornamen werden "
        "nie aus dem Modellergebnis uebernommen.")


# ---------------------------------------------------------------------------
# Zeilen
# ---------------------------------------------------------------------------

def _flag(value: bool) -> str:
    return "true" if value else "false"


def assignment_rows(units: Iterable[MatcherUnit], state: workflow.RunState,
                    snapshot: TaxonomySnapshot,
                    prov: Provenance) -> tuple[dict[str, object], ...]:
    """Eine Zeile je Unit-Industry-Zuordnung, nach Unit und Industry sortiert.

    ``no_mapping`` erzeugt keine Zeile - es gibt keine Zuordnung. Diese Units
    stehen vollzaehlig in der Unit-Coverage.
    """
    expected = _expected(units)
    records = _checked_records(expected, state)
    rows: list[dict[str, object]] = []
    for unit_id in sorted(records):
        record = records[unit_id]
        result = record.result
        if result is None or result.decision is not MappingDecision.MAPPED:
            continue
        unit = expected[unit_id]
        for assignment in sorted(result.assignments, key=lambda item: item.industry_id):
            industry = _industry(snapshot, assignment.industry_id, unit_id)
            rows.append({
                "run_id": prov.run_id,
                "revision_id": prov.run_id,
                "unit_id": unit_id,
                "chamber": unit.chamber,
                "committee_name": unit.committee_name,
                "unit_name": unit.unit_name,
                "unit_kind": unit.kind.value,
                "input_basis": unit.input_basis.value,
                "mapping_input_hash": unit.mapping_input_hash or "",
                "decision": result.decision.value,
                "broad_scope_exception": _flag(result.broad_scope_exception),
                "industry_id": industry.industry_id,
                "industry_name": industry.industry_name,
                "sector_id": industry.sector_id,
                "sector_name": industry.sector_name,
                "confidence": assignment.confidence,
                "evidence_excerpt": assignment.evidence_excerpt,
                "rationale_short": assignment.rationale_short,
                "assignment_source": AssignmentSource.MODEL.value,
                "model": prov.model,
                "taxonomy_content_hash": prov.taxonomy_content_hash,
                "snapshot_id": prov.snapshot_id,
                "prompt_id": prov.prompt_id,
                "prompt_version": prov.prompt_version,
                "prompt_sha256": prov.prompt_sha256,
                "schema_name": prov.schema_name,
                "schema_sha256": prov.schema_sha256,
            })
    return tuple(rows)


def coverage_rows(units: Iterable[MatcherUnit],
                  state: workflow.RunState) -> tuple[UnitCoverageRow, ...]:
    """Fachlicher Stand je erwarteter Unit - genau eine Zeile, nie weniger.

    Eine Unit ohne Zustandseintrag ist ``not_decided``. Das ist kein
    Mappingurteil, sondern die Aussage, dass dieser Lauf sie nicht entschieden
    hat.
    """
    expected = _expected(units)
    records = _checked_records(expected, state)
    return tuple(
        UnitCoverageRow(
            unit_id=unit_id,
            status=records[unit_id].status if unit_id in records else UnitStatus.NOT_DECIDED,
        )
        for unit_id in sorted(expected)
    )


def _coverage_payload(units: Iterable[MatcherUnit], state: workflow.RunState,
                      run_id: str) -> tuple[dict[str, object], ...]:
    expected = _expected(units)
    rows: list[dict[str, object]] = []
    for row in coverage_rows(units, state):
        unit = expected[row.unit_id]
        record = state.units.get(row.unit_id)
        result: MappingUnitResult | None = record.result if record else None
        rows.append({
            "run_id": run_id,
            "revision_id": run_id,
            "unit_id": row.unit_id,
            "chamber": unit.chamber,
            "committee_name": unit.committee_name,
            "unit_name": unit.unit_name,
            "unit_kind": unit.kind.value,
            "status": row.status.value,
            "assignment_count": len(result.assignments) if result else 0,
            "no_mapping_reason": (result.no_mapping_reason or "") if result else "",
            "broad_scope_exception": _flag(bool(result and result.broad_scope_exception)),
            "violations": CELL_SEPARATOR.join(record.violations) if record else "",
        })
    return tuple(rows)


def boundary_rows(units: Iterable[MatcherUnit], state: workflow.RunState,
                  snapshot: TaxonomySnapshot, run_id: str) -> tuple[dict[str, object], ...]:
    """Gleiche Industry in Geschwister-Units; Reviewposten, kein Fehler."""
    expected = _expected(units)
    records = _checked_records(expected, state)
    results = [records[unit_id].result for unit_id in sorted(records)
               if records[unit_id].result is not None]
    conflicts = validation.find_boundary_conflicts(tuple(expected.values()), results)
    rows: list[dict[str, object]] = []
    for conflict in conflicts:
        industry = _industry(snapshot, conflict.industry_id, conflict.family_id)
        rows.append({
            "run_id": run_id,
            "family_id": conflict.family_id,
            "industry_id": conflict.industry_id,
            "industry_name": industry.industry_name,
            "unit_count": len(conflict.unit_ids),
            "unit_ids": CELL_SEPARATOR.join(conflict.unit_ids),
        })
    return tuple(rows)


def roster_rows(rows: Iterable[RosterCoverageRow],
                run_id: str) -> tuple[dict[str, object], ...]:
    return tuple({
        "run_id": run_id,
        "chamber": row.chamber,
        "committee_name": row.committee_name,
        "unit_name": row.unit_name,
        "coverage_status": row.coverage_status.value,
        "unit_id": row.unit_id or "",
        "match_basis": row.match_basis.value if row.match_basis else "",
        "hint": row.hint or "",
        "fuzzy_suggestion": row.fuzzy_suggestion or "",
    } for row in rows)


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def render_csv(columns: Sequence[str], rows: Sequence[Mapping[str, object]]) -> str:
    """Baut den CSV-Text; unbekannte Spalten sind ein Programmierfehler.

    ``\\n`` als Zeilenende und UTF-8 halten die Datei plattformgleich; die
    Kopfzeile steht auch dann, wenn keine Zeile folgt - eine leere Datei waere
    von einem abgebrochenen Lauf nicht zu unterscheiden.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n",
                            extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))
    return buffer.getvalue()


def _write_csv(run_id: str, filename: str, columns: Sequence[str],
               rows: Sequence[Mapping[str, object]]) -> ArtifactFile:
    target = workflow.mapping_path(run_id, filename, create_parents=True)
    paths.write_text_atomic(target, render_csv(columns, rows))
    return ArtifactFile(path=target, rows=len(rows))


def write_assignments(run_id: str, units: Iterable[MatcherUnit],
                      state: workflow.RunState, snapshot: TaxonomySnapshot,
                      prov: Provenance) -> ArtifactFile:
    return _write_csv(run_id, ASSIGNMENTS_FILENAME, ASSIGNMENT_COLUMNS,
                      assignment_rows(units, state, snapshot, prov))


def write_unit_coverage(run_id: str, units: Iterable[MatcherUnit],
                        state: workflow.RunState) -> ArtifactFile:
    return _write_csv(run_id, UNIT_COVERAGE_FILENAME, UNIT_COVERAGE_COLUMNS,
                      _coverage_payload(units, state, run_id))


def write_boundary_conflicts(run_id: str, units: Iterable[MatcherUnit],
                             state: workflow.RunState,
                             snapshot: TaxonomySnapshot) -> ArtifactFile:
    return _write_csv(run_id, BOUNDARY_FILENAME, BOUNDARY_COLUMNS,
                      boundary_rows(units, state, snapshot, run_id))


def write_roster_coverage(run_id: str,
                          rows: Iterable[RosterCoverageRow]) -> ArtifactFile:
    return _write_csv(run_id, ROSTER_COVERAGE_FILENAME, ROSTER_COVERAGE_COLUMNS,
                      roster_rows(rows, run_id))


def cost_payload(run_id: str, guard: workflow.CostGuard,
                 unknown: Sequence[workflow.UnknownCall] = (),
                 audited_calls: int = 0) -> dict[str, object]:
    """Kostenstand des Laufs samt der Calls, deren Ausgang offen blieb.

    Reservierte Kosten stehen getrennt neben den gemeldeten: nur so ist im
    Bericht sichtbar, welcher Teil der Summe eine Schaetzung des schlimmsten
    Falls ist und erst durch die Reconciliation verschwindet.
    """
    return {
        "version": COST_REPORT_VERSION,
        "run_id": run_id,
        "audited_calls": audited_calls,
        **guard.report(),
        "unknown_calls_detail": [call.as_payload() for call in unknown],
    }


def write_cost_report(run_id: str, guard: workflow.CostGuard,
                      unknown: Sequence[workflow.UnknownCall] = (),
                      audited_calls: int = 0) -> ArtifactFile:
    target = workflow.mapping_path(run_id, COST_REPORT_FILENAME, create_parents=True)
    payload = cost_payload(run_id, guard, unknown, audited_calls)
    paths.write_text_atomic(
        target, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return ArtifactFile(path=target, rows=len(payload["unknown_calls_detail"]))


def write_run_artifacts(*, run_id: str, units: Iterable[MatcherUnit],
                        state: workflow.RunState, snapshot: TaxonomySnapshot,
                        manifest: Mapping, guard: workflow.CostGuard,
                        unknown: Sequence[workflow.UnknownCall] = (),
                        audited_calls: int = 0,
                        roster: Iterable[RosterCoverageRow] | None = None) -> ArtifactSet:
    """Schreibt alle Pflichtartefakte eines Laufs (Plan 2.8).

    Die Pruefungen laufen vor dem ersten Schreibvorgang: entweder steht der
    vollstaendige Satz auf der Platte oder keine Datei. Ein halber Satz waere im
    Review nicht als unvollstaendig erkennbar.
    """
    prov = provenance(manifest)
    if prov.run_id != run_id:
        raise _blocked(
            "ART_RUN_MISMATCH", run_id,
            f"Das Manifest gehoert zu Run {prov.run_id!r}.",
            "Artefakte nur aus dem Manifest desselben Laufs schreiben.")
    if prov.taxonomy_content_hash != snapshot.taxonomy_content_hash:
        raise _blocked(
            "ART_SNAPSHOT_MISMATCH", run_id,
            "Der uebergebene Snapshot ist nicht der eingefrorene Snapshot des Laufs.",
            "Snapshot des Runs laden; Industry- und Sektornamen muessen aus "
            "genau der Taxonomie stammen, gegen die das Modell entschieden hat.")
    units = tuple(units)
    assignments = assignment_rows(units, state, snapshot, prov)
    coverage = _coverage_payload(units, state, run_id)
    conflicts = boundary_rows(units, state, snapshot, run_id)
    roster_payload = roster_rows(tuple(roster), run_id) if roster is not None else None

    written = ArtifactSet(
        assignments=_write_csv(run_id, ASSIGNMENTS_FILENAME, ASSIGNMENT_COLUMNS,
                               assignments),
        unit_coverage=_write_csv(run_id, UNIT_COVERAGE_FILENAME,
                                 UNIT_COVERAGE_COLUMNS, coverage),
        boundary_conflicts=_write_csv(run_id, BOUNDARY_FILENAME, BOUNDARY_COLUMNS,
                                      conflicts),
        cost_report=write_cost_report(run_id, guard, unknown, audited_calls),
        roster_coverage=(
            _write_csv(run_id, ROSTER_COVERAGE_FILENAME, ROSTER_COVERAGE_COLUMNS,
                       roster_payload) if roster_payload is not None else None),
    )
    return written
