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
import sys
from dataclasses import dataclass
from typing import Sequence

from . import SCAFFOLD_VERSION
from .errors import ErrorCollector, MatcherError, Stage, print_report
from .settings_view import MatcherSettings, SettingsBlocked, load_settings
from .status import ExitCode

PROGRAM = "committee_matcher"


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


def run_command(command: Command, namespace: argparse.Namespace) -> int:
    collector = ErrorCollector()
    settings = _preflight(command, collector)

    if command.provider and collector.has_blockers():
        collector.blocker(
            "CLI_PROVIDER_GATE_CLOSED", Stage.PROVIDER, command.name,
            "Providerpfad ist gesperrt, solange Gate B nicht abgeschlossen ist.",
            "Providerfreie Familien input, roster, snapshot, review und report nutzen.",
        )
    else:
        collector.blocker(
            "CLI_STAGE_NOT_IMPLEMENTED", command.stage, command.name,
            f"Die Stage '{command.name}' entsteht in Phase {command.phase}.",
            f"Phase {command.phase} implementieren; das Geruest steht bereit.",
        )

    summary = [
        f"Kommando: {command.name}",
        f"Phase:    {command.phase}",
        f"Settings: offline geprueft"
        + (
            f" | Provider {'bereit' if settings.provider_ready else 'unset (Gate B offen)'}"
            if settings is not None
            else " | Settings ungueltig"
        ),
    ]
    print_report(
        collector,
        headline=f"Committee Matcher V2 - {command.name}",
        summary_lines=summary,
    )
    return int(collector.exit_code())


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:  # --help liefert 0, Bedienfehler 4
        return int(exc.code or 0)

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
