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
#: Abschlussmarker: erst er belegt einen vollstaendigen Artefaktsatz.
INDEX_FILENAME = "artifacts_manifest.json"

#: Versionen der Artefaktvertraege; sie wandern in die Kopfzeile ihres Berichts
#: beziehungsweise in das JSON.
COST_REPORT_VERSION = "cost-report-v1"
ARTIFACT_INDEX_VERSION = "artifact-index-v1"

#: Spaltenvertraege. Reihenfolge und Schreibweise sind Teil des Artefakts und
#: durch Golden Tests gebunden - nachgelagerte Werkzeuge lesen nach Namen, der
#: Review liest nach Position.
ASSIGNMENT_COLUMNS: tuple[str, ...] = (
    "run_id", "revision_id", "unit_id", "chamber", "committee_name", "unit_name",
    "unit_kind", "input_basis", "mapping_input_hash", "decision",
    "broad_scope_exception", "industry_id", "industry_name", "sector_id",
    "sector_name", "confidence", "evidence_excerpt", "rationale_short",
    "assignment_source", "broad_scope_reason", "call_id", "provider",
    "generation_id", "model", "taxonomy_content_hash", "snapshot_id",
    "prompt_id", "prompt_version", "prompt_sha256", "schema_name",
    "schema_sha256",
)
UNIT_COVERAGE_COLUMNS: tuple[str, ...] = (
    "run_id", "revision_id", "unit_id", "chamber", "committee_name", "unit_name",
    "unit_kind", "status", "assignment_count", "no_mapping_reason",
    "broad_scope_exception", "broad_scope_reason", "call_id", "provider",
    "generation_id", "violations",
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
    roster_coverage: ArtifactFile

    def files(self) -> tuple[ArtifactFile, ...]:
        return (self.assignments, self.unit_coverage, self.boundary_conflicts,
                self.cost_report, self.roster_coverage)

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
        decided = record.status in (UnitStatus.MAPPED, UnitStatus.NO_MAPPING)
        # Beide Richtungen pruefen: ein entschiedener Status braucht sein
        # passendes Ergebnis, ein unentschiedener darf keines tragen. Sonst
        # entstuenden Zuordnungszeilen zu einer Unit, die die Coverage im selben
        # Lauf als ``failed`` oder ``not_decided`` ausweist.
        if decided and not record.reusable:
            raise _blocked(
                "ART_STATE_INCONSISTENT", unit_id,
                f"Status {record.status.value!r} passt nicht zum gespeicherten "
                "Ergebnis der Unit.",
                "Laufzustand pruefen; ein Artefakt darf keinen Widerspruch "
                "zwischen Status und Modellurteil festschreiben.")
        if not decided and record.result is not None:
            raise _blocked(
                "ART_STATE_INCONSISTENT", unit_id,
                f"Status {record.status.value!r} traegt trotzdem ein "
                "Modellergebnis.",
                "Laufzustand pruefen; eine unentschiedene Unit darf keine "
                "Zuordnung erzeugen.")
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


def _joined(values: Sequence[str], obj: str) -> str:
    """Verbindet Mehrfachwerte; ein Wert mit dem Trennzeichen blockiert.

    Ein maskiertes Trennzeichen waere die einzige Alternative - und eine stille
    Aenderung des Werts. Fail closed ist hier billiger: in den betroffenen
    Feldern (Fehlercodes, Unit-IDs) kommt ``|`` nach heutigem Vertrag nicht vor.
    """
    for value in values:
        if CELL_SEPARATOR in value:
            raise _blocked(
                "ART_SEPARATOR_IN_VALUE", obj,
                f"Der Wert {value!r} enthaelt das Zellentrennzeichen "
                f"{CELL_SEPARATOR!r}.",
                "Wert oder Trennzeichen anpassen; eine maskierte Zelle waere "
                "beim Zurueckleseneine stille Aenderung.")
    return CELL_SEPARATOR.join(values)


def _required_hash(unit: MatcherUnit) -> str:
    if not unit.mapping_input_hash:
        raise _blocked(
            "ART_PROVENANCE_INCOMPLETE", unit.unit_id,
            "Die Unit traegt keinen Inputhash.",
            "Units ueber units.py bilden; ohne Inputhash ist eine Zeile spaeter "
            "keiner Eingabefassung zuzuordnen.")
    return unit.mapping_input_hash


def _call_provenance(record: workflow.UnitRecord) -> tuple[str, str, str]:
    """Herkunft eines entschiedenen Ergebnisses; sie ist Pflicht.

    Bei freier Providerwahl beantwortet erst sie, welcher Anbieter unter welcher
    Generation-ID diese Zeile erzeugt hat. Eine leere Spalte waere im Abgleich
    mit dem Provider wertlos.
    """
    if not record.call_id:
        raise _blocked(
            "ART_PROVENANCE_INCOMPLETE", record.unit_id,
            "Das Ergebnis der Unit nennt keinen Call.",
            "Laufzustand mit Callprovenienz schreiben (call_id, provider, "
            "generation_id); sie gehoert zum Urteil, nicht zum Lauf.")
    return record.call_id, record.provider or "", record.generation_id or ""


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
        call_id, provider_name, generation_id = _call_provenance(record)
        input_hash = _required_hash(unit)
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
                "mapping_input_hash": input_hash,
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
                "broad_scope_reason": result.broad_scope_reason or "",
                "call_id": call_id,
                "provider": provider_name,
                "generation_id": generation_id,
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
            "broad_scope_reason": (result.broad_scope_reason or "") if result else "",
            "call_id": (record.call_id or "") if record else "",
            "provider": (record.provider or "") if record else "",
            "generation_id": (record.generation_id or "") if record else "",
            "violations": _joined(record.violations, row.unit_id) if record else "",
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
            "unit_ids": _joined(conflict.unit_ids, conflict.family_id),
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

    Details und Summenzaehler muessen zusammenpassen. Mehr Detaileintraege als
    gezaehlte Calls hiesse, dass Bericht und Kostenwache verschiedene Laeufe
    beschreiben - dann ist keine der beiden Zahlen belastbar.
    """
    if audited_calls > guard.calls or len(unknown) > guard.unknown_calls:
        raise _blocked(
            "ART_COST_INCONSISTENT", run_id,
            f"Der Bericht nennt {audited_calls} auditierte und {len(unknown)} "
            f"offene Calls, die Kostenwache zaehlt {guard.calls} beziehungsweise "
            f"{guard.unknown_calls}.",
            "Kostenwache und Auditspur desselben Laufs uebergeben; ein "
            "gemischter Stand macht beide Zahlen wertlos.")
    return {
        "version": COST_REPORT_VERSION,
        "run_id": run_id,
        "audited_calls": audited_calls,
        **guard.report(),
        "unknown_calls_detail": [call.as_payload() for call in unknown],
    }


def _write_json_artifact(run_id: str, filename: str,
                         payload: Mapping, rows: int) -> ArtifactFile:
    target = workflow.mapping_path(run_id, filename, create_parents=True)
    paths.write_text_atomic(
        target, json.dumps(dict(payload), ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n")
    return ArtifactFile(path=target, rows=rows)


def write_cost_report(run_id: str, guard: workflow.CostGuard,
                      unknown: Sequence[workflow.UnknownCall] = (),
                      audited_calls: int = 0) -> ArtifactFile:
    payload = cost_payload(run_id, guard, unknown, audited_calls)
    return _write_json_artifact(run_id, COST_REPORT_FILENAME, payload, len(unknown))


#: Alle Ziele eines vollstaendigen Satzes; die Reihenfolge ist die des Schreibens.
ARTIFACT_FILENAMES: tuple[str, ...] = (
    ASSIGNMENTS_FILENAME, UNIT_COVERAGE_FILENAME, BOUNDARY_FILENAME,
    COST_REPORT_FILENAME, ROSTER_COVERAGE_FILENAME, INDEX_FILENAME,
)


def _assert_revision_free(run_id: str) -> None:
    """Eine bestehende Ergebnisrevision wird nie ueberschrieben.

    Runrevisionen sind unveraenderlich (Plan 2.8). Ein zweiter Schreibvorgang
    unter derselben Run-ID wuerde den bereits gesichteten Stand ersetzen, ohne
    dass die Ersetzung irgendwo sichtbar waere.
    """
    for filename in ARTIFACT_FILENAMES:
        target = workflow.mapping_path(run_id, filename)
        if target.exists():
            raise _blocked(
                "ART_REVISION_EXISTS", paths.relative_to_base(target),
                "Diese Runrevision traegt bereits Ergebnisartefakte.",
                "Neue --run-id verwenden oder die vorhandene Revision nach "
                "Sichtung entfernen; ueberschrieben wird sie nicht.")


def _rollback(written: Sequence[ArtifactFile]) -> None:
    """Entfernt die schon geschriebenen Dateien eines gescheiterten Satzes."""
    for entry in written:
        try:
            entry.path.unlink(missing_ok=True)
        except OSError:
            # Der fehlende Abschlussmarker weist den Satz ohnehin als
            # unvollstaendig aus; mehr ist hier nicht zu erreichen.
            pass


def artifact_index(run_id: str, written: Sequence[ArtifactFile]) -> dict[str, object]:
    """Abschlussmarker: er entsteht als letzte Datei des Satzes.

    Erst sein Vorhandensein belegt, dass alle Artefakte vollstaendig geschrieben
    wurden. Ein Prozessabbruch mitten in der Reihe hinterlaesst ihn nicht, und
    jeder Leser erkennt den Satz daran als unbrauchbar.
    """
    return {
        "version": ARTIFACT_INDEX_VERSION,
        "run_id": run_id,
        "files": [{"name": entry.path.name, "rows": entry.rows}
                  for entry in written],
    }


def is_complete(run_id: str) -> bool:
    """Traegt der Lauf einen vollstaendigen, abgeschlossenen Artefaktsatz?"""
    target = workflow.mapping_path(run_id, INDEX_FILENAME)
    if not target.exists():
        return False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict) or data.get("version") != ARTIFACT_INDEX_VERSION:
        return False
    if data.get("run_id") != run_id:
        return False
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        return False
    return all(workflow.mapping_path(run_id, entry["name"]).exists()
               for entry in entries
               if isinstance(entry, dict) and isinstance(entry.get("name"), str))


def _checked_roster(rows: Sequence[RosterCoverageRow]) -> Sequence[RosterCoverageRow]:
    """Die Roster-Coverage ist Pflicht und muss je Vorkommen eindeutig sein.

    Vollstaendig ist sie, weil ``roster_coverage.build_coverage`` sie aus dem
    Roster erzeugt; hier wird nur ausgeschlossen, dass eine leere oder in sich
    doppelte Menge als Coverage durchgeht.
    """
    if not rows:
        raise _blocked(
            "ART_ROSTER_MISSING", "roster_coverage",
            "Der Lauf uebergibt keine Roster-Coverage.",
            "Coverage ueber roster_coverage.build_coverage erzeugen; Plan 2.8 "
            "fuehrt sie als Pflichtartefakt.")
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.chamber, row.committee_name, row.unit_name)
        if key in seen:
            raise _blocked(
                "ART_ROSTER_DUPLICATE", CELL_SEPARATOR.join(key),
                "Dasselbe Rostervorkommen erscheint mehrfach.",
                "Coverage pruefen; jedes Vorkommen steht genau einmal.")
        seen.add(key)
    return rows


def write_run_artifacts(*, run_id: str, units: Iterable[MatcherUnit],
                        state: workflow.RunState, snapshot: TaxonomySnapshot,
                        manifest: Mapping, guard: workflow.CostGuard,
                        roster: Iterable[RosterCoverageRow],
                        unknown: Sequence[workflow.UnknownCall] = (),
                        audited_calls: int = 0) -> ArtifactSet:
    """Schreibt alle Pflichtartefakte eines Laufs (Plan 2.8).

    Jede Pruefung und jede Zeile entsteht vor dem ersten Schreibvorgang.
    Scheitert danach ein Schreibvorgang, werden die bereits geschriebenen
    Dateien entfernt; ein Prozessabbruch dazwischen laesst den Abschlussmarker
    fehlen. Ein halber Satz ist so in beiden Faellen als solcher erkennbar.
    """
    prov = provenance(manifest)
    if prov.run_id != run_id:
        raise _blocked(
            "ART_RUN_MISMATCH", run_id,
            f"Das Manifest gehoert zu Run {prov.run_id!r}.",
            "Artefakte nur aus dem Manifest desselben Laufs schreiben.")
    if state.run_id != run_id:
        raise _blocked(
            "ART_RUN_MISMATCH", run_id,
            f"Der Laufzustand gehoert zu Run {state.run_id!r}.",
            "Zustand desselben Laufs laden; gleiche Unit-IDs machen fremde "
            "Ergebnisse sonst unsichtbar.")
    if prov.snapshot_id != snapshot.snapshot_id:
        raise _blocked(
            "ART_SNAPSHOT_MISMATCH", run_id,
            f"Das Manifest bindet an Snapshot {prov.snapshot_id!r}, uebergeben "
            f"wurde {snapshot.snapshot_id!r}.",
            "Snapshot des Runs laden; inhaltsgleiche Snapshots anderer "
            "Revisionen sind nicht derselbe Kontrollinput.")
    if prov.taxonomy_content_hash != snapshot.taxonomy_content_hash:
        raise _blocked(
            "ART_SNAPSHOT_MISMATCH", run_id,
            "Der uebergebene Snapshot ist nicht der eingefrorene Snapshot des Laufs.",
            "Snapshot des Runs laden; Industry- und Sektornamen muessen aus "
            "genau der Taxonomie stammen, gegen die das Modell entschieden hat.")
    units = tuple(units)
    expected_count = manifest.get("unit_count")
    if expected_count != len(units):
        raise _blocked(
            "ART_UNIT_COUNT_MISMATCH", run_id,
            f"Das Manifest erwartet {expected_count!r} Units, uebergeben wurden "
            f"{len(units)}.",
            "Vollstaendige Unitmenge des Laufs uebergeben; eine Teilmenge waere "
            "in der Coverage nicht von Vollstaendigkeit zu unterscheiden.")
    _assert_revision_free(run_id)

    assignments = assignment_rows(units, state, snapshot, prov)
    coverage = _coverage_payload(units, state, run_id)
    conflicts = boundary_rows(units, state, snapshot, run_id)
    roster_payload = roster_rows(_checked_roster(tuple(roster)), run_id)
    cost = cost_payload(run_id, guard, unknown, audited_calls)

    done: list[ArtifactFile] = []
    try:
        assignments_file = _write_csv(run_id, ASSIGNMENTS_FILENAME,
                                      ASSIGNMENT_COLUMNS, assignments)
        done.append(assignments_file)
        coverage_file = _write_csv(run_id, UNIT_COVERAGE_FILENAME,
                                   UNIT_COVERAGE_COLUMNS, coverage)
        done.append(coverage_file)
        conflicts_file = _write_csv(run_id, BOUNDARY_FILENAME, BOUNDARY_COLUMNS,
                                    conflicts)
        done.append(conflicts_file)
        cost_file = _write_json_artifact(run_id, COST_REPORT_FILENAME, cost,
                                         len(unknown))
        done.append(cost_file)
        roster_file = _write_csv(run_id, ROSTER_COVERAGE_FILENAME,
                                 ROSTER_COVERAGE_COLUMNS, roster_payload)
        done.append(roster_file)
        index = artifact_index(run_id, done)
        _write_json_artifact(run_id, INDEX_FILENAME, index, len(done))
    except Exception:
        _rollback([*done, ArtifactFile(workflow.mapping_path(run_id, INDEX_FILENAME), 0)])
        raise
    return ArtifactSet(
        assignments=assignments_file,
        unit_coverage=coverage_file,
        boundary_conflicts=conflicts_file,
        cost_report=cost_file,
        roster_coverage=roster_file,
    )
