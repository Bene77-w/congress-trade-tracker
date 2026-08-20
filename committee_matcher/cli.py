"""CLI-Skelett des Committee Matchers V2.

Plan 2.7 legt die CLI-Familien und die Exit-Codes fest. Dieses Modul haelt
beides zusammen: es kennt alle Familien bereits vollstaendig, fuehrt den
Offline-Preflight der Settings aus, blockiert jeden Providerpfad, solange
Gate B offen ist, und gibt in jedem Fall den Terminalbericht aus.

Stages, die erst in spaeteren Phasen entstehen, melden ``CLI_STAGE_NOT_IMPLEMENTED``
und beenden mit Exit-Code 4 (Preflight blockiert). Das ist bewusst kein neuer
Exit-Code: die Codeliste ist Vertrag und wird nicht erweitert.

Es gibt weiterhin kein ``promote``, ``finalize``, ``research`` oder LLM-``review``.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Sequence

from . import SCAFFOLD_VERSION
from . import input_corpus, paths, roster_coverage, units as unit_module
from .errors import ErrorCollector, MatcherError, Severity, Stage, print_report
from .settings_view import MatcherSettings, SettingsBlocked, load_settings
from .status import CoverageStatus, ExitCode, InputBasis

PROGRAM = "committee_matcher"

#: Ablage des strukturierten Fehlerberichts je Kommando. Plan 2.7 verlangt die
#: Persistenz fuer jeden CLI-Lauf; ab P7 tritt die Run-Revision an die Stelle
#: des Kommandonamens, der Schreibweg bleibt derselbe.
REPORT_DIR = "reports"
REPORT_FILENAME = "errors.json"


@dataclass(frozen=True)
class Command:
    """Eine CLI-Aktion mit ihrer Herkunftsphase."""

    family: str
    action: str | None
    stage: Stage
    phase: str
    provider: bool
    help: str

    @property
    def name(self) -> str:
        return f"{self.family} {self.action}" if self.action else self.family


#: Vollstaendige Familienliste aus Plan 2.7. ``phase`` nennt die Phase, in der
#: die Aktion implementiert wird; ``provider`` markiert Providerpfade.
COMMANDS: tuple[Command, ...] = (
    Command("input", "validate", Stage.INPUT, "P3", False,
            "Corpusmanifest, Parser, Unitbildung und 225-ID-Sollmenge pruefen"),
    Command("roster", "validate", Stage.ROSTER, "P3", False,
            "Roster-Coverage inklusive Joint-Cross-Chamber-Regel pruefen"),
    Command("snapshot", "build", Stage.TAXONOMY, "P4", False,
            "frischen yfinance-Industry-Snapshot erzeugen"),
    Command("snapshot", "approve", Stage.TAXONOMY, "P4", False,
            "Snapshot bzw. erkannte Drift freigeben (run- und snapshotgebunden)"),
    Command("snapshot", "resume", Stage.TAXONOMY, "P4", False,
            "abgebrochenen Snapshotabruf aus dem run-lokalen Checkpoint fortsetzen"),
    Command("run", "plan", Stage.WORKFLOW, "P7", True,
            "Entwicklungsrun planen inklusive Worst-Case-Kostenschaetzung"),
    Command("run", "execute", Stage.WORKFLOW, "P7", True,
            "geplanten Entwicklungsrun ausfuehren"),
    Command("run", "reconcile", Stage.WORKFLOW, "P7", True,
            "outcome_unknown ueber die OpenRouter-Generation-ID klaeren"),
    Command("review", "export", Stage.REVIEW, "P8", False,
            "Review-XLSX aus einer Runrevision exportieren"),
    Command("review", "import", Stage.REVIEW, "P8", False,
            "bearbeitete Review-XLSX validiert zurueckimportieren"),
    Command("evaluate", "goldset", Stage.EVALUATION, "P9", True,
            "Entwicklungs-Gold-Set auswerten"),
    Command("qualify", "holdout", Stage.EVALUATION, "P10", True,
            "unabhaengiges Holdout qualifizieren"),
    Command("full-run", None, Stage.WORKFLOW, "P11", True,
            "qualifizierten, filterlosen Full-Run ueber die Unit-Sollmenge starten"),
    Command("report", None, Stage.ARTIFACTS, "P7", False,
            "Fehler-, Kosten- und Coverage-Bericht einer Revision ausgeben"),
)

#: Zielangaben, die ausschliesslich Entwicklungsruns erlauben (Plan 2.6).
DEVELOPMENT_TARGET_FLAGS = ("--chamber", "--committee", "--unit-id")


class _Parser(argparse.ArgumentParser):
    """Argparse mit vertragskonformem Exit-Code.

    Der Default waere ``2``; ``2`` bedeutet im Matcher aber 'erfolgreich mit
    Warnungen'. Ein Bedienfehler ist ein blockierter Preflight.
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: Fehler: {message}", file=sys.stderr)
        raise SystemExit(int(ExitCode.PREFLIGHT_BLOCKED))


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROGRAM,
        description=(
            "Committee Matcher V2 - providerfreier Kern "
            f"({SCAFFOLD_VERSION}). Providerpfade bleiben bis Gate B gesperrt."
        ),
    )
    parser.add_argument("--version", action="version", version=SCAFFOLD_VERSION)
    families = parser.add_subparsers(dest="family", required=True, metavar="FAMILIE")

    grouped: dict[str, list[Command]] = {}
    for command in COMMANDS:
        grouped.setdefault(command.family, []).append(command)

    for family, commands in grouped.items():
        head = commands[0]
        if head.action is None:
            sub = families.add_parser(family, help=head.help, description=head.help)
            _add_common_flags(sub, head)
            continue
        sub = families.add_parser(family, help=f"{family}-Familie")
        actions = sub.add_subparsers(dest="action", required=True, metavar="AKTION")
        for command in commands:
            action_parser = actions.add_parser(
                command.action, help=command.help, description=command.help
            )
            _add_common_flags(action_parser, command)
    return parser


def _add_common_flags(parser: argparse.ArgumentParser, command: Command) -> None:
    parser.add_argument(
        "--run-id",
        help="bestehende Run-Revision; Snapshotfreigabe und Resume sind daran gebunden",
    )
    if command.family in ("run", "evaluate"):
        parser.add_argument("--chamber", help="nur Entwicklungsruns: Kammerfilter")
        parser.add_argument("--committee", help="nur Entwicklungsruns: Committee-Filter")
        parser.add_argument(
            "--unit-id", action="append", default=[],
            help="nur Entwicklungsruns: einzelne Unit-ID (mehrfach zulaessig)",
        )


def _lookup(namespace: argparse.Namespace) -> Command:
    family = namespace.family
    action = getattr(namespace, "action", None)
    for command in COMMANDS:
        if command.family == family and command.action == action:
            return command
    raise KeyError(f"unbekannte Aktion: {family} {action}")


def _preflight(command: Command, collector: ErrorCollector) -> MatcherSettings | None:
    """Prueft Settings; Providerpfade zusaetzlich gegen das offene Gate B."""
    try:
        settings = load_settings()
    except SettingsBlocked as exc:
        collector.extend(exc.findings)
        return None
    if command.provider:
        try:
            settings.require_provider()
        except SettingsBlocked as exc:
            collector.extend(exc.findings)
    return settings


def _slug(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")


def _persist(collector: ErrorCollector, command_name: str) -> tuple[list[str], object]:
    """Schreibt den strukturierten Fehlerbericht ueber die Pfadwache.

    Scheitert das Schreiben, bricht der Lauf nicht ab: der Terminalbericht
    erscheint trotzdem und nennt den Fehlschlag als Warnung.
    """
    try:
        target = paths.artifact_path(REPORT_DIR, _slug(command_name), REPORT_FILENAME)
        collector.write_json(target)
        return [paths.relative_to_base(target)], None
    except (MatcherError, OSError) as exc:
        finding = ErrorCollector().add(
            Severity.WARNUNG,
            "CLI_REPORT_NOT_PERSISTED",
            Stage.ARTIFACTS,
            f"{REPORT_DIR}/{_slug(command_name)}/{REPORT_FILENAME}",
            f"Fehlerbericht konnte nicht persistiert werden: {exc}",
            "Schreibrecht unter data/committee_matcher_v2/ pruefen.",
        )
        return [], finding


def _write_artifact(collector: ErrorCollector, command_name: str, filename: str,
                    payload: object) -> list[str]:
    """Schreibt ein Diagnoseartefakt neben den Fehlerbericht."""
    try:
        target = paths.artifact_path(REPORT_DIR, _slug(command_name), filename)
        paths.write_text_atomic(
            target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        return [paths.relative_to_base(target)]
    except (MatcherError, OSError) as exc:
        collector.warning(
            "CLI_ARTIFACT_NOT_WRITTEN", Stage.ARTIFACTS, filename,
            f"Artefakt konnte nicht geschrieben werden: {exc}",
            "Schreibrecht unter data/committee_matcher_v2/ pruefen.",
        )
        return []


def handle_input_validate(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
) -> tuple[list[str], list[str]]:
    """Corpus laden, Units bilden, Sollmenge pruefen (Plan 2.1/2.2)."""
    result = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, result.documents)
    matches_baseline = unit_module.check_against_baseline(collector, units)

    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.kind.value] = counts.get(unit.kind.value, 0) + 1
    name_only = sum(
        1 for unit in units if unit.input_basis is not InputBasis.JURISDICTION_TEXT
    )
    artifacts = _write_artifact(
        collector, command.name, "units.json",
        {
            "unit_id_version": unit_module.UNIT_ID_VERSION,
            "mapping_input_version": unit_module.MAPPING_INPUT_VERSION,
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "kind": unit.kind.value,
                    "chamber": unit.chamber,
                    "committee_name": unit.committee_name,
                    "unit_name": unit.unit_name,
                    "input_basis": unit.input_basis.value,
                    "source_path": unit.source_path,
                    "parent_unit_id": unit.parent_unit_id,
                    "mapping_input_hash": unit.mapping_input_hash,
                    "source_bytes_sha256": unit.source_bytes_sha256,
                }
                for unit in units
            ],
        },
    )
    summary = [
        f"Manifest: {result.manifest_files} Dateien geprueft, "
        f"{len(result.documents)} Mapping-Inputs geladen",
        "Units:    "
        + " + ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        + f" = {len(units)}",
        f"Basis:    {name_only} name_only",
        f"Sollmenge: {'exakt deckungsgleich' if matches_baseline else 'abweichend'} "
        f"({unit_module.UNIT_ID_VERSION})",
    ]
    return summary, artifacts


def handle_roster_validate(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
) -> tuple[list[str], list[str]]:
    """Roster-Coverage gegen die Unit-Sollmenge pruefen (Plan 2.3)."""
    result = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, result.documents)
    rows = roster_coverage.run_coverage(collector, units)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.coverage_status.value] = counts.get(row.coverage_status.value, 0) + 1
    for row in rows:
        if row.coverage_status is CoverageStatus.MISSING:
            collector.warning(
                "ROS_UNIT_MISSING", Stage.ROSTER,
                f"{row.chamber}: {row.unit_name}",
                "Rostervorkommen hat keine Unit.",
                row.hint or "Alias oder Ausschluss festlegen.",
            )
    covered = {row.unit_id for row in rows if row.unit_id}
    without_roster = sorted(unit.unit_id for unit in units if unit.unit_id not in covered)
    artifacts = _write_artifact(
        collector, command.name, "roster_coverage.json",
        {
            "rows": [row.model_dump(mode="json") for row in rows],
            "units_without_roster_occurrence": without_roster,
        },
    )
    summary = [
        f"Roster:   {len(rows)} Vorkommen aus {len(roster_coverage.roster_paths())} Kammern",
        "Coverage: "
        + " | ".join(f"{status}={count}" for status, count in sorted(counts.items())),
        f"Units ohne Rostervorkommen: {len(without_roster)}",
    ]
    return summary, artifacts


#: Bereits implementierte Aktionen; alles andere meldet die Zielphase.
HANDLERS = {
    "input validate": handle_input_validate,
    "roster validate": handle_roster_validate,
}


def run_command(command: Command, namespace: argparse.Namespace) -> int:
    collector = ErrorCollector()
    settings = _preflight(command, collector)
    handler = HANDLERS.get(command.name)
    stage_summary: list[str] = []
    stage_artifacts: list[str] = []

    if command.provider and collector.has_blockers():
        collector.blocker(
            "CLI_PROVIDER_GATE_CLOSED", Stage.PROVIDER, command.name,
            "Providerpfad ist gesperrt, solange Gate B nicht abgeschlossen ist.",
            "Providerfreie Familien input, roster, snapshot, review und report nutzen.",
        )
    elif handler is None or settings is None:
        if settings is not None:
            collector.blocker(
                "CLI_STAGE_NOT_IMPLEMENTED", command.stage, command.name,
                f"Die Stage '{command.name}' entsteht in Phase {command.phase}.",
                f"Phase {command.phase} implementieren; das Geruest steht bereit.",
            )
    else:
        try:
            stage_summary, stage_artifacts = handler(
                command, namespace, collector, settings
            )
        except MatcherError as exc:
            collector.extend(exc.findings)

    artifacts, persist_finding = _persist(collector, command.name)
    if persist_finding is not None:
        collector.extend([persist_finding])
    artifacts = stage_artifacts + artifacts

    summary = [
        f"Kommando: {command.name}",
        f"Phase:    {command.phase}",
        f"Settings: offline geprueft"
        + (
            f" | Provider {'bereit' if settings.provider_ready else 'unset (Gate B offen)'}"
            if settings is not None
            else " | Settings ungueltig"
        ),
    ] + stage_summary
    print_report(
        collector,
        headline=f"Committee Matcher V2 - {command.name}",
        summary_lines=summary,
        artifacts=artifacts,
    )
    return int(collector.exit_code())


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:  # --help und --version liefern 0
        code = int(exc.code or 0)
        if code == 0:
            return 0
        collector = ErrorCollector()
        collector.blocker(
            "CLI_USAGE_ERROR", Stage.CLI, " ".join(argv or sys.argv[1:]) or PROGRAM,
            "Aufruf entspricht keiner bekannten CLI-Familie.",
            "python -m committee_matcher --help zeigt alle Familien und Aktionen.",
        )
        artifacts, persist_finding = _persist(collector, "usage")
        if persist_finding is not None:
            collector.extend([persist_finding])
        print_report(
            collector,
            headline="Committee Matcher V2 - Aufruf ungueltig",
            artifacts=artifacts,
        )
        return int(collector.exit_code())

    try:
        command = _lookup(namespace)
        return run_command(command, namespace)
    except KeyboardInterrupt:
        collector = ErrorCollector()
        collector.blocker(
            "CLI_ABORTED", Stage.CLI, PROGRAM,
            "Lauf durch Benutzerabbruch beendet.",
            "Lauf erneut starten; bereits geschriebene Artefakte bleiben unveraendert.",
            exit_code=ExitCode.USER_ABORT,
        )
        print_report(collector, headline="Committee Matcher V2 - Abbruch")
        return int(ExitCode.USER_ABORT)
    except MatcherError as exc:
        collector = ErrorCollector()
        collector.extend(exc.findings)
        print_report(collector, headline="Committee Matcher V2 - blockiert")
        return int(collector.exit_code())
    except Exception as exc:  # noqa: BLE001 - letzte Sicherung, Exit-Code 10
        collector = ErrorCollector()
        collector.blocker(
            "CLI_INTERNAL_ERROR", Stage.CLI, PROGRAM,
            f"Unerwarteter interner Fehler: {exc.__class__.__name__}: {exc}",
            "Traceback pruefen; dieser Pfad ist immer ein Implementierungsfehler.",
            exit_code=ExitCode.INTERNAL_ERROR,
        )
        print_report(collector, headline="Committee Matcher V2 - interner Fehler")
        return int(ExitCode.INTERNAL_ERROR)
