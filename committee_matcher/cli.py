"""CLI-Skelett des Committee Matchers V2.

Plan 2.7 legt die CLI-Familien und die Exit-Codes fest. Dieses Modul haelt
beides zusammen: es kennt alle Familien bereits vollstaendig, fuehrt den
Offline-Preflight der Settings aus, blockiert jeden Providerpfad mit
unvollstaendigen Providersettings und gibt in jedem Fall den Terminalbericht
aus.

Jedes registrierte Kommando hat einen Handler. Bleibt eines ohne, meldet der
Lauf ``CLI_STAGE_NOT_IMPLEMENTED`` und beendet mit Exit-Code 4 (Preflight
blockiert). Das ist bewusst kein neuer Exit-Code: die Codeliste ist Vertrag und
wird nicht erweitert.

Nicht gebaut werden Evaluationslauf, Holdout-Qualifikation und Auswertungs-
bericht; die Begruendung steht in ``AUFGEGEBENER_PLANTEIL.md``. Es gibt
weiterhin kein ``promote``, ``finalize``, ``research`` oder LLM-``review``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from . import SCAFFOLD_VERSION
from . import (
    artifacts,
    evaluation as evaluation_module,
    input_corpus,
    mapping,
    paths,
    prompts,
    provider,
    review as review_module,
    roster_coverage,
    taxonomy_snapshot,
    taxonomy_source,
    units as unit_module,
    validation,
    workflow,
)
from .errors import (
    ErrorCollector,
    MatcherError,
    Severity,
    Stage,
    StatusKind,
    TerminalReporter,
    print_report,
)
from .models import MatcherUnit, TaxonomySnapshot
from .settings_view import MatcherSettings, SettingsBlocked, load_settings
from .status import (
    CoverageStatus,
    ExitCode,
    InputBasis,
    MappingDecision,
    UnitStatus,
    WorkflowState,
)

PROGRAM = "committee_matcher"

#: Ablage des strukturierten Fehlerberichts je Kommando. Plan 2.7 verlangt die
#: Persistenz fuer jeden CLI-Lauf; ab P7 tritt die Run-Revision an die Stelle
#: des Kommandonamens, der Schreibweg bleibt derselbe.
REPORT_DIR = "reports"
REPORT_FILENAME = "errors.json"

#: Zulaessige Form einer Run-Revision.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Command:
    """Eine CLI-Aktion mit ihrer Herkunftsphase."""

    family: str
    action: str | None
    stage: Stage
    phase: str
    provider: bool
    help: str
    #: True, wenn die Aktion einen externen Dienst abruft (yfinance oder
    #: den oeffentlichen OpenRouter-Katalog). Sagt nichts ueber Kosten aus.
    network: bool = False
    #: True, wenn die Aktion ohne bestehende Run-Revision sinnlos waere
    #: (Freigabe und Resume sind an genau einen Run gebunden).
    requires_run_id: bool = False

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
            "frischen yfinance-Industry-Snapshot erzeugen", network=True),
    Command("snapshot", "approve", Stage.TAXONOMY, "P4", False,
            "Snapshot bzw. erkannte Drift freigeben (run- und snapshotgebunden)",
            requires_run_id=True),
    Command("snapshot", "resume", Stage.TAXONOMY, "P4", False,
            "abgebrochenen Snapshotabruf aus dem run-lokalen Checkpoint fortsetzen",
            network=True, requires_run_id=True),
    Command("provider", "check", Stage.PROVIDER, "P5", True,
            "Endpointverfuegbarkeit der Providerkonfiguration vorab pruefen "
            "(kostenlos, kein Modellaufruf)", network=True),
    Command("provider", "smoke", Stage.PROVIDER, "P5", True,
            "Contract-Smoke: ein einzelner kostenpflichtiger Minimalcall prueft "
            "Auth, Structured Output, Usage/Kosten und das tatsaechliche Routing",
            network=True),
    Command("run", "plan", Stage.WORKFLOW, "P7", True,
            "Entwicklungsrun planen inklusive Worst-Case-Kostenschaetzung "
            "(kein Modellaufruf)", requires_run_id=True),
    Command("run", "execute", Stage.WORKFLOW, "P7", True,
            "geplanten Entwicklungsrun ausfuehren (kostenpflichtig)",
            network=True, requires_run_id=True),
    Command("run", "reconcile", Stage.WORKFLOW, "P7", True,
            "outcome_unknown ueber die OpenRouter-Generation-ID klaeren",
            network=True, requires_run_id=True),
    Command("review", "export", Stage.REVIEW, "P7", False,
            "Review-XLSX aus einer abgeschlossenen Runrevision exportieren",
            requires_run_id=True),
    Command("review", "import", Stage.REVIEW, "P7", False,
            "bearbeitete Review-XLSX validiert zurueckimportieren",
            requires_run_id=True),
    Command("evaluate", "export", Stage.EVALUATION, "P9", True,
            "Evaluationsrevision eroeffnen: frischer Taxonomieabruf, Stichprobe, "
            "Inputs, Prompts und Variantenmatrix einfrieren (kein Modellaufruf)",
            network=True),
    Command("evaluate", "import", Stage.EVALUATION, "P9", True,
            "gelabeltes Entwicklungs-Gold-Set validiert in die Revision uebernehmen"),
)

#: Zielangaben eines Runs. ``--all-units`` nimmt die gesamte Sollmenge; die
#: uebrigen drei schneiden sie zu.
RUN_TARGET_FLAGS = ("--all-units", "--chamber", "--committee", "--unit-id")


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
            "Committee Matcher V2 "
            f"({SCAFFOLD_VERSION}). Providerpfade rufen erst ab P5 ein Modell "
            "auf; 'provider check' prueft die Endpointverfuegbarkeit vorab "
            "kostenlos gegen den OpenRouter-Katalog."
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
    if command.family == "review":
        parser.add_argument(
            "--review-id",
            help="Reviewrevision; ohne Angabe 'review-<run-id>'",
        )
        parser.add_argument(
            "--workbook",
            help="bearbeitete XLSX; ohne Angabe die Datei der Reviewrevision",
        )
    if command.family == "evaluate":
        # Die Evaluationsmenge steht in der Stichprobendatei; eine Zielangabe
        # auf der Kommandozeile wuerde das Gold-Set stillschweigend kuerzen.
        parser.add_argument(
            "--eval-id", required=True,
            help="Evaluationsrevision; Export, Labelimport und Prueflauf sind "
                 "daran gebunden",
        )
        parser.add_argument(
            "--labels",
            help="gelabelte Gold-Set-Datei (.xlsx oder .csv); ohne Angabe die "
                 "Arbeitsdatei des Labelwerkzeugs",
        )
    if command.family == "run":
        parser.add_argument(
            "--all-units", action="store_true",
            help="die gesamte Unit-Sollmenge mappen (kostenpflichtig; "
                 "'run plan' nennt vorher den Preis)",
        )
        parser.add_argument("--chamber", help="Kammerfilter")
        parser.add_argument("--committee", help="Committee-Filter")
        parser.add_argument(
            "--unit-id", action="append", default=[],
            help="einzelne Unit-ID (mehrfach zulaessig)",
        )


def _lookup(namespace: argparse.Namespace) -> Command:
    family = namespace.family
    action = getattr(namespace, "action", None)
    for command in COMMANDS:
        if command.family == family and command.action == action:
            return command
    raise KeyError(f"unbekannte Aktion: {family} {action}")


def _preflight(command: Command, collector: ErrorCollector) -> MatcherSettings | None:
    """Prueft Settings; Providerpfade zusaetzlich auf vollstaendige Providerwerte."""
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


def _report_segments(command_name: str, revision: str | None,
                     family: str | None = None) -> tuple[str, ...]:
    """Ablageort der Diagnose; mit Revision an den Vorgang gebunden.

    Ohne Bindung ueberschreibt der naechste Aufruf desselben Kommandos den
    Bericht des vorigen Laufs - genau die Datei, die einen abgebrochenen Lauf
    erklaert. Evaluationen liegen dabei bei ihrer eigenen Revision und nicht
    unter ``runs``.
    """
    if revision and family == "evaluate":
        return (evaluation_module.EVALUATIONS_DIR, revision, REPORT_DIR,
                _slug(command_name))
    if revision:
        return ("runs", revision, REPORT_DIR, _slug(command_name))
    return (REPORT_DIR, _slug(command_name))


def _persist(collector: ErrorCollector, command_name: str,
             run_id: str | None = None,
             family: str | None = None) -> tuple[list[str], object]:
    """Schreibt den strukturierten Fehlerbericht ueber die Pfadwache.

    Scheitert das Schreiben, bricht der Lauf nicht ab: der Terminalbericht
    erscheint trotzdem und nennt den Fehlschlag als Warnung.
    """
    segments = _report_segments(command_name, run_id, family)
    try:
        target = paths.artifact_path(*segments, REPORT_FILENAME)
        collector.write_json(target)
        return [paths.relative_to_base(target)], None
    except (MatcherError, OSError) as exc:
        finding = ErrorCollector().add(
            Severity.WARNUNG,
            "CLI_REPORT_NOT_PERSISTED",
            Stage.ARTIFACTS,
            "/".join(segments) + f"/{REPORT_FILENAME}",
            f"Fehlerbericht konnte nicht persistiert werden: {exc}",
            "Schreibrecht unter data/committee_matcher_v2/ pruefen.",
        )
        return [], finding


def _write_artifact(collector: ErrorCollector, command_name: str, filename: str,
                    payload: object, run_id: str | None = None) -> list[str]:
    """Schreibt ein Diagnoseartefakt neben den Fehlerbericht."""
    try:
        target = paths.artifact_path(
            *_report_segments(command_name, run_id), filename)
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
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Corpus laden, Units bilden, Sollmenge pruefen (Plan 2.1/2.2)."""
    reporter.start(Stage.INPUT, action="validate")
    result = input_corpus.load_documents(collector, settings.offline)
    reporter.info(Stage.INPUT, files=result.manifest_files,
                  documents=len(result.documents))
    units = unit_module.build_units(collector, result.documents)
    matches_baseline = unit_module.check_against_baseline(collector, units)
    reporter.emit(
        StatusKind.OK if matches_baseline else StatusKind.ERROR,
        Stage.INPUT, units=len(units),
        baseline="deckungsgleich" if matches_baseline else "abweichend")

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
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Roster-Coverage gegen die Unit-Sollmenge pruefen (Plan 2.3)."""
    reporter.start(Stage.ROSTER, action="validate")
    result = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, result.documents)
    rows = roster_coverage.run_coverage(collector, units)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.coverage_status.value] = counts.get(row.coverage_status.value, 0) + 1
    without_roster = roster_coverage.report_gaps(collector, units, rows)
    reporter.emit(
        StatusKind.WARN if without_roster else StatusKind.OK,
        Stage.ROSTER, occurrences=len(rows), units=len(units),
        without_roster=len(without_roster))
    artifacts = _write_artifact(
        collector, command.name, "roster_coverage.json",
        {
            "rows": [row.model_dump(mode="json") for row in rows],
            "units_without_roster_occurrence": list(without_roster),
        },
    )
    summary = [
        f"Roster:   {len(rows)} Vorkommen aus {len(roster_coverage.roster_paths())} Kammern",
        "Coverage: "
        + " | ".join(f"{status}={count}" for status, count in sorted(counts.items())),
        f"Units ohne Rostervorkommen: {len(without_roster)}",
    ]
    return summary, artifacts


def _run_id(namespace: argparse.Namespace) -> str:
    """Liefert die Run-Revision; ohne Angabe entsteht eine neue aus der Uhrzeit.

    Die Revision ist bewusst zeitbasiert und damit sortierbar. P7 uebernimmt die
    Verwaltung der Runs; bis dahin genuegt eine eindeutige, stabile Kennung, an
    die Snapshot, Checkpoint und Freigabe gebunden sind.
    """
    given = getattr(namespace, "run_id", None)
    if given:
        candidate = str(given).strip()
        if not RUN_ID_RE.match(candidate):
            raise MatcherError(
                [
                    ErrorCollector().blocker(
                        "CLI_RUN_ID_INVALID", Stage.CLI, candidate,
                        "Run-ID enthaelt unzulaessige Zeichen.",
                        "Nur Buchstaben, Ziffern, '-' und '_' verwenden.",
                    )
                ],
                "Run-ID ungueltig",
            )
        return candidate
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")


def _missing_run_id(
    command: Command, namespace: argparse.Namespace, collector: ErrorCollector
) -> bool:
    """Meldet die fehlende Run-Revision fuer run-gebundene Aktionen."""
    if not command.requires_run_id or getattr(namespace, "run_id", None):
        return False
    collector.blocker(
        "CLI_RUN_ID_REQUIRED", command.stage, command.name,
        f"'{command.name}' ist an genau eine Run-Revision gebunden.",
        "Lauf mit --run-id <revision> wiederholen.",
    )
    return True


def _snapshot_summary(result: taxonomy_snapshot.SnapshotResult) -> list[str]:
    lines = [
        f"Run:      {result.run_id}",
        f"Zustand:  {result.state.value}"
        + (f" ({result.reason})" if result.reason else ""),
        f"Snapshot: {result.industries} Industries"
        + (f" | Content-Hash {result.content_hash[:12]}" if result.content_hash else ""),
    ]
    drift = result.drift
    if drift is not None and drift.any_change:
        lines.append(
            "Drift:    "
            + " | ".join(
                f"{label}={len(items)}"
                for label, items in (
                    ("neu", drift.added), ("entfallen", drift.removed),
                    ("umbenannt", drift.renamed), ("Sektor", drift.resectored),
                    ("Beschreibung", drift.described),
                )
                if items
            )
        )
    elif drift is not None:
        lines.append("Drift:    keine")
    return lines


def _snapshot_run(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter, *, resume: bool,
) -> tuple[list[str], list[str]]:
    """Gemeinsamer Ablauf von 'snapshot build' und 'snapshot resume' (Plan 2.4)."""
    if _missing_run_id(command, namespace, collector):
        return [], []
    run_id = _run_id(namespace)
    client = taxonomy_source.YFinanceClient(settings.offline, reporter=reporter)
    result = taxonomy_snapshot.build_snapshot(
        collector, client, run_id=run_id, resume=resume, reporter=reporter
    )
    return _snapshot_summary(result), list(result.artifacts)


def handle_snapshot_build(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Frischen Snapshot abrufen, pruefen und all-or-nothing persistieren."""
    return _snapshot_run(command, namespace, collector, settings, reporter,
                         resume=False)


def handle_snapshot_resume(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Abgebrochenen Abruf aus dem run-lokalen Checkpoint fortsetzen."""
    return _snapshot_run(command, namespace, collector, settings, reporter,
                         resume=True)


def handle_snapshot_approve(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Snapshot eines Runs als neue Taxonomie-Baseline freigeben."""
    if _missing_run_id(command, namespace, collector):
        return [], []
    run_id = _run_id(namespace)
    reporter.start(Stage.TAXONOMY, run=run_id, action="approve")
    digest = taxonomy_snapshot.approve_snapshot(collector, run_id)
    if digest is None:
        reporter.error(Stage.TAXONOMY, run=run_id, approval="abgelehnt")
        return [f"Run:      {run_id}", "Freigabe: abgelehnt"], []
    reporter.ok(Stage.TAXONOMY, run=run_id, approval="erteilt",
                content_hash=digest[:12])
    return (
        [
            f"Run:      {run_id}",
            f"Freigabe: erteilt (Content-Hash {digest[:12]})",
        ],
        [paths.relative_to_base(taxonomy_snapshot.baseline_path())],
    )


def handle_provider_check(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Prueft vor dem ersten Call, ob ein zulaessiger Endpoint existiert."""
    provider_settings = settings.require_provider()
    report = provider.check_routing(collector, provider_settings)
    lines = [
        f"Modell:   {provider_settings.model}",
        f"Filter:   structured_outputs, quantizations="
        f"{list(provider_settings.quantizations or ()) or 'keine'}, "
        f"max_price={provider_settings.max_price_usd_per_mtok}, "
        f"zdr={provider_settings.zdr}",
    ]
    if report is None:
        lines.append("Endpoints: keiner zulaessig")
        return lines, []
    cheapest = report.cheapest
    lines.append(
        f"Endpoints: {len(report.eligible)} von {report.total} zulaessig"
        + (f" | guenstigster {cheapest.tag} "
           f"({cheapest.prompt_usd_per_mtok:.3f}/"
           f"{cheapest.completion_usd_per_mtok:.3f} USD pro Mtok)"
           if cheapest else "")
    )
    lines.append(
        "Hinweis:  ZDR ist im Katalog nicht ausgewiesen und wird erst beim "
        "ersten Call geprueft."
    )
    lines.extend(_prompt_lines(collector))
    return lines, []


def _prompt_lines(collector: ErrorCollector) -> list[str]:
    """Provenienz der Systemprompts; Befunde landen im selben Bericht."""
    loaded = prompts.load_all(collector)
    if loaded is None:
        return ["Prompts:  nicht ladbar (siehe Befunde)"]
    return [
        f"Prompts:  {name} {item.version} ({item.sha256[:12]})"
        for name, item in sorted(loaded.items())
    ]


#: Minimalschema des Contract-Smokes. Bewusst winzig: geprueft wird der
#: Vertrag, nicht die Fachlogik - jedes zusaetzliche Feld kostet Tokens.
SMOKE_ECHO = "contract-smoke"
SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "echo": {"type": "string"},
    },
    "required": ["ok", "echo"],
    "additionalProperties": False,
}


def _smoke_schema_violations(payload: dict) -> list[str]:
    """Prueft die Antwort gegen SMOKE_SCHEMA - ohne Zusatzabhaengigkeit.

    Der Smoke soll belegen, dass der geroutete Endpoint striktes JSON-Schema
    tatsaechlich einhaelt. Ein beliebiges JSON-Objekt durchzulassen wuerde
    genau diesen Nachweis vortaeuschen.
    """
    problems = []
    unexpected = sorted(set(payload) - set(SMOKE_SCHEMA["properties"]))
    if unexpected:
        problems.append(f"unerwartete Felder: {', '.join(unexpected)}")
    missing = sorted(set(SMOKE_SCHEMA["required"]) - set(payload))
    if missing:
        problems.append(f"fehlende Pflichtfelder: {', '.join(missing)}")
    if "ok" in payload and not isinstance(payload["ok"], bool):
        problems.append(f"ok ist {type(payload['ok']).__name__}, erwartet boolean")
    if "echo" in payload and not isinstance(payload["echo"], str):
        problems.append(f"echo ist {type(payload['echo']).__name__}, erwartet string")
    if payload.get("ok") is False:
        problems.append("ok ist false, erwartet true")
    if isinstance(payload.get("echo"), str) and payload["echo"] != SMOKE_ECHO:
        problems.append(f"echo ist {payload['echo']!r}, erwartet {SMOKE_ECHO!r}")
    return problems


def handle_provider_smoke(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Setzt genau einen kostenpflichtigen Minimalcall ab (Plan 2.6).

    Der Smoke bestaetigt vor jedem groesseren Lauf, was der kostenlose
    Preflight nicht sehen kann: Auth, echte Structured-Output-Faehigkeit des
    gerouteten Endpoints, gemeldete Kosten und die ZDR-Vertraeglichkeit.
    """
    provider_settings = settings.require_provider()
    loaded = prompts.load_all(collector)
    adapter = provider.OpenRouterProvider.create(collector, provider_settings)
    if adapter is None or loaded is None:
        return ["Smoke:    nicht ausgefuehrt (Vorbedingung blockiert)"], []

    request = provider.StructuredRequest(
        call_id="contract-smoke",
        system_prompt="Antworte ausschliesslich mit dem geforderten JSON-Objekt.",
        user_content=f'Setze ok=true und echo="{SMOKE_ECHO}".',
        schema_name="ContractSmoke",
        json_schema=SMOKE_SCHEMA,
        max_output_tokens=64,
    )
    try:
        response = adapter.complete(request)
    except provider.ProviderCallError as exc:
        collector.extend(exc.findings)
        if exc.routing_suspected:
            provider.routing_failure_finding(
                collector, provider_settings, exc.findings[0].message)
        return ["Smoke:    fehlgeschlagen (siehe Befunde)"], []

    violations = _smoke_schema_violations(response.payload)
    if violations:
        collector.blocker(
            "PROV_SCHEMA_VIOLATED", Stage.PROVIDER, response.call_id,
            "Antwort haelt das erzwungene JSON-Schema nicht ein: "
            + "; ".join(violations) + ".",
            "Der geroutete Endpoint unterstuetzt Structured Output nur "
            "nominell. Quantisierungs- oder Preisfilter enger fassen, damit "
            "ein anderer Endpoint greift, oder Modell wechseln "
            "(COMMITTEE_MATCHER_MODEL).",
            exit_code=ExitCode.PROVIDER_ERROR,
        )
        return [
            f"Smoke:    Schema verletzt ({len(violations)} Abweichungen)",
            f"Routing:  {response.provider or 'unbekannt'} | Modell {response.model}",
            f"Payload:  {response.payload}",
        ], [], response.cost_usd

    cost = ("unbekannt (cost_unknown)" if response.cost_usd is None
            else f"{response.cost_usd:.6f} USD")
    return [
        f"Smoke:    Antwort gueltig, Schema eingehalten",
        f"Routing:  {response.provider or 'unbekannt'} | Modell {response.model}",
        f"Tokens:   {response.prompt_tokens} Prompt / "
        f"{response.completion_tokens} Completion | Kosten {cost}",
        f"Payload:  {response.payload}",
        f"Generation: {response.generation_id or 'unbekannt'}",
    ], [], response.cost_usd


# ---------------------------------------------------------------------------
# Entwicklungsrun (P7c)
# ---------------------------------------------------------------------------

def _run_blocker(collector: ErrorCollector, code: str, obj: str, message: str,
                 hint: str, *, exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> None:
    collector.blocker(code, Stage.WORKFLOW, obj, message, hint, exit_code=exit_code)


def _selection(namespace: argparse.Namespace,
               frozen: Mapping | None = None) -> dict[str, object]:
    """Die Zielangabe des Laufs in der Form, in der sie im Manifest steht.

    Ohne Zielflags gilt die im Plan eingefrorene Auswahl: ``run execute``
    fuehrt genau den Lauf aus, der geplant wurde, statt eine erneute Eingabe zu
    verlangen. Eine abweichende Angabe faellt spaeter als Manifestdrift auf.
    """
    given = {
        "all_units": bool(getattr(namespace, "all_units", False)),
        "chamber": getattr(namespace, "chamber", None) or None,
        "committee": getattr(namespace, "committee", None) or None,
        "unit_ids": sorted(set(getattr(namespace, "unit_id", []) or [])),
    }
    if any(given.values()) or not isinstance(frozen, Mapping):
        return given
    return {
        "all_units": bool(frozen.get("all_units")),
        "chamber": frozen.get("chamber") or None,
        "committee": frozen.get("committee") or None,
        "unit_ids": sorted(frozen.get("unit_ids") or []),
    }


def _targets(collector: ErrorCollector, units: Sequence[MatcherUnit],
             selection: Mapping[str, object]) -> tuple[MatcherUnit, ...] | None:
    """Waehlt die Units des Runs aus.

    Ohne Zielangabe laeuft nichts. Der Lauf ueber die gesamte Sollmenge ist der
    teuerste des Werkzeugs und wird deshalb ausdruecklich mit ``--all-units``
    verlangt, statt sich aus fehlenden Filtern zu ergeben.
    """
    all_units = bool(selection.get("all_units"))
    chamber = selection.get("chamber")
    committee = selection.get("committee")
    unit_ids = tuple(selection.get("unit_ids") or ())
    if not (all_units or chamber or committee or unit_ids):
        _run_blocker(
            collector, "CLI_RUN_TARGET_REQUIRED", "run",
            "Ein Run braucht eine Zielangabe.",
            "--all-units fuer die gesamte Sollmenge setzen oder mit --chamber, "
            "--committee bzw. --unit-id zuschneiden.",
        )
        return None
    if all_units and (chamber or committee or unit_ids):
        # Sonst hiesse ``--all-units`` im Manifest etwas anderes als im Lauf:
        # die Coverage wiese eine Teilmenge als vollstaendig aus.
        _run_blocker(
            collector, "CLI_RUN_TARGET_CONFLICT", "run",
            "--all-units schliesst jeden Filter aus.",
            "Entweder --all-units oder --chamber/--committee/--unit-id "
            "angeben, nicht beides.",
        )
        return None
    if all_units:
        return tuple(units)

    def matches(unit: MatcherUnit) -> bool:
        if chamber and unit.chamber.casefold() != str(chamber).casefold():
            return False
        if committee and unit.committee_name.casefold() != str(committee).casefold():
            return False
        if unit_ids and unit.unit_id not in unit_ids:
            return False
        return True

    selected = tuple(unit for unit in units if matches(unit))
    known = {unit.unit_id for unit in units}
    unknown = [unit_id for unit_id in unit_ids if unit_id not in known]
    if unknown:
        _run_blocker(
            collector, "CLI_RUN_TARGET_UNKNOWN", ", ".join(sorted(unknown)),
            "Diese Unit-IDs kommen in der Sollmenge nicht vor.",
            "Unit-IDs aus 'input validate' uebernehmen; eine stillschweigend "
            "kleinere Zielmenge waere in der Coverage nicht erkennbar.",
        )
        return None
    if not selected:
        _run_blocker(
            collector, "CLI_RUN_TARGET_EMPTY",
            f"chamber={chamber or '-'} committee={committee or '-'}",
            "Die Zielangabe trifft keine einzige Unit.",
            "Kammer- und Committee-Namen exakt wie im Corpus schreiben; "
            "'input validate' listet sie auf.",
        )
        return None
    return selected


@dataclass(frozen=True)
class RunPreparation:
    """Alles, was Plan und Ausfuehrung eines Runs gemeinsam brauchen.

    Sie entsteht in beiden Aktionen auf demselben Weg. Nur so ist die
    Kostenschaetzung aus ``run plan`` fuer ``run execute`` verbindlich: ein
    zweiter Bauweg koennte andere Batches und damit andere Kosten ergeben.
    """

    run_id: str
    units: tuple[MatcherUnit, ...]
    targets: tuple[MatcherUnit, ...]
    snapshot: TaxonomySnapshot
    mapping_prompt: prompts.SystemPrompt
    repair_prompt: prompts.SystemPrompt
    manifest: dict
    batches: tuple[mapping.MappingBatch, ...]
    requests: tuple[provider.StructuredRequest, ...]
    plan_usd: float
    budget_usd: float
    provider_settings: object
    selection: dict[str, object]
    input_manifest_hash: str


def _prepare_run(command: Command, namespace: argparse.Namespace,
                 collector: ErrorCollector, settings: MatcherSettings,
                 reporter: TerminalReporter, run_id: str,
                 frozen: Mapping | None = None) -> RunPreparation | None:
    """Baut den vollstaendigen Laufplan; kein Providercall, kein Schreibvorgang.

    Gibt ``None`` zurueck, sobald ein Schritt blockiert; die Befunde stehen dann
    im Collector und tragen ihren eigenen Exit-Code.
    """
    provider_settings = settings.require_provider()
    budget_usd = settings.require_budget("run")

    corpus = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, corpus.documents)
    if collector.has_blockers():
        return None
    if not corpus.manifest_sha256:
        _run_blocker(
            collector, "RUN_INPUT_UNBOUND", "corpus_manifest.yaml",
            "Der Inputstand ist nicht hashbar; ein Lauf waere an keinen "
            "Corpus gebunden.",
            "Corpusmanifest wiederherstellen; 'input validate' zeigt den Stand.",
        )
        return None

    selection = _selection(namespace, (frozen or {}).get("selection"))
    targets = _targets(collector, units, selection)
    if targets is None:
        return None
    reporter.ok(Stage.INPUT, run=run_id, units=len(units), targets=len(targets))

    snapshot = taxonomy_snapshot.approved_snapshot(collector)
    if snapshot is None:
        reporter.wait(Stage.TAXONOMY, run=run_id, awaiting_taxonomy_approval=None)
        return None
    reporter.ok(Stage.TAXONOMY, snapshot=snapshot.snapshot_id,
                industries=len(snapshot.industries))

    loaded = prompts.load_all(collector)
    if loaded is None:
        return None
    mapping_prompt = loaded["mapping_system"]
    repair_prompt = loaded["repair_system"]

    batches = mapping.plan_batches(
        collector, targets, snapshot, mapping_prompt, settings.offline,
        provider_settings)
    if batches is None:
        return None
    requests = tuple(
        mapping.build_request(batch, snapshot, mapping_prompt, provider_settings)
        for batch in batches
    )
    rates = workflow.CostRates.from_settings(provider_settings)
    plan_usd = workflow.plan_cost_usd(requests, rates, settings.offline)
    reporter.ok(Stage.MAPPING, run=run_id, batches=len(batches),
                units=len(targets))

    manifest = workflow.build_manifest(
        run_id=run_id,
        settings=settings,
        provider_settings=provider_settings,
        prompts_used=(mapping_prompt, repair_prompt),
        taxonomy_content_hash=snapshot.taxonomy_content_hash,
        snapshot_id=snapshot.snapshot_id,
        input_manifest_hash=corpus.manifest_sha256,
        unit_count=len(targets),
        budget_usd=budget_usd,
        selection=selection,
    )
    return RunPreparation(
        run_id=run_id, units=tuple(units), targets=targets, snapshot=snapshot,
        mapping_prompt=mapping_prompt, repair_prompt=repair_prompt,
        manifest=manifest, batches=tuple(batches), requests=requests,
        plan_usd=plan_usd, budget_usd=budget_usd,
        provider_settings=provider_settings, selection=selection,
        input_manifest_hash=corpus.manifest_sha256,
    )


def _manifest_conflict(collector: ErrorCollector, run_id: str,
                       manifest: Mapping) -> bool:
    """Prueft ein vorhandenes Manifest gegen den aktuellen Stand.

    Ein zweiter Plan unter derselben Revision ist erlaubt, solange er dieselben
    ergebnisbestimmenden Bedingungen hat. Weicht eine davon ab, ist es ein
    anderer Lauf und bekommt eine eigene Revision.
    """
    unusable = workflow.manifest_unusable(run_id)
    if unusable is not None:
        # Ein unbrauchbares Manifest zu ueberschreiben wuerde einen daneben
        # liegenden Laufzustand mit bezahlten Ergebnissen unter neue
        # Bedingungen stellen: der Resume liefe gegen ein Manifest, das die
        # Ergebnisse nie erzeugt hat.
        _run_blocker(
            collector, "RUN_MANIFEST_UNUSABLE", run_id,
            f"Unter dieser Revision liegt bereits ein Manifest, das {unusable}.",
            "Datei sichten und die Revision entweder bereinigen oder eine neue "
            "--run-id verwenden; ueberschrieben wird sie nie.")
        return True
    previous = workflow.load_manifest(run_id)
    if previous is None:
        return False
    conflicts = workflow.resume_conflicts(previous, manifest)
    if not conflicts:
        return False
    collector.extend([workflow.resume_blocker(run_id, conflicts)])
    return True


def _assert_revision_open(collector: ErrorCollector, run_id: str) -> bool:
    """Eine abgeschlossene Revision wird nie ueberschrieben (Plan 2.8)."""
    if not artifacts.is_complete(run_id):
        return True
    _run_blocker(
        collector, "RUN_REVISION_COMPLETE", run_id,
        "Diese Revision ist bereits vollstaendig abgeschlossen.",
        "Neue --run-id verwenden; abgeschlossene Revisionen bleiben "
        "unveraendert.",
    )
    return False


def _cost_lines(*, budget_usd: float, plan_usd: float, guard_enabled: bool) -> list[str]:
    return [
        f"Kosten:   Worst Case {plan_usd:.6f} USD von {budget_usd:.6f} USD Budget"
        + ("" if guard_enabled else " | Kostenwache abgeschaltet"),
    ]


def handle_run_plan(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Plant einen Entwicklungsrun und friert die Bedingungen ein (Plan 2.6).

    Der Plan ist providerfrei: er bildet Batches, rechnet den Worst Case und
    schreibt das Runmanifest. Erst ``run execute`` sendet einen Call.
    """
    if _missing_run_id(command, namespace, collector):
        return [], []
    run_id = _run_id(namespace)
    reporter.start(Stage.WORKFLOW, run=run_id, action="plan")
    if not _assert_revision_open(collector, run_id):
        return [f"Run:      {run_id}", "Plan:     abgelehnt (Revision abgeschlossen)"], []

    prepared = _prepare_run(command, namespace, collector, settings, reporter, run_id)
    if prepared is None:
        reporter.error(Stage.WORKFLOW, run=run_id, action="plan", blocked=None)
        return [f"Run:      {run_id}", "Plan:     blockiert (siehe Befunde)"], []

    if _manifest_conflict(collector, run_id, prepared.manifest):
        reporter.error(Stage.WORKFLOW, run=run_id, code="RUN_MANIFEST_DRIFT")
        return [f"Run:      {run_id}", "Plan:     abgelehnt (andere Bedingungen)"], []

    guard = workflow.CostGuard(
        budget_usd=prepared.budget_usd,
        rates=workflow.CostRates.from_settings(prepared.provider_settings),
        enabled=settings.offline.cost_guard,
    )
    blocker = guard.check(prepared.plan_usd, run_id)
    if blocker is not None:
        collector.extend([blocker])
        reporter.error(Stage.COST, run=run_id, code="COST_BUDGET_EXCEEDED",
                       plan_usd=prepared.plan_usd, budget_usd=prepared.budget_usd)
        return [
            f"Run:      {run_id}",
            f"Umfang:   {len(prepared.targets)} Units in {len(prepared.batches)} Batches",
            *_cost_lines(budget_usd=prepared.budget_usd, plan_usd=prepared.plan_usd,
                         guard_enabled=guard.enabled),
            "Plan:     abgelehnt (Budget)",
        ], []

    manifest_file = workflow.write_manifest(run_id, prepared.manifest)
    reporter.ok(Stage.COST, run=run_id, plan_usd=prepared.plan_usd,
                budget_usd=prepared.budget_usd, guard=guard.enabled)
    reporter.ok(Stage.WORKFLOW, run=run_id, action="plan",
                manifest=manifest_file.name)
    return [
        f"Run:      {run_id}",
        f"Auswahl:  chamber={prepared.selection['chamber'] or '-'} | "
        f"committee={prepared.selection['committee'] or '-'} | "
        f"unit_ids={len(prepared.selection['unit_ids'])}",
        f"Umfang:   {len(prepared.targets)} von {len(prepared.units)} Units "
        f"in {len(prepared.batches)} Batches",
        f"Taxonomie: Snapshot {prepared.snapshot.snapshot_id} | "
        f"{len(prepared.snapshot.industries)} Industries",
        *_cost_lines(budget_usd=prepared.budget_usd, plan_usd=prepared.plan_usd,
                     guard_enabled=guard.enabled),
        "Naechster Schritt: 'run execute --run-id " + run_id + "' sendet "
        "kostenpflichtige Calls.",
    ], [paths.relative_to_base(manifest_file)]


def _roster(collector: ErrorCollector, units: Sequence[MatcherUnit],
            ) -> tuple[tuple, int]:
    """Roster-Coverage samt der Zahl der gelesenen Rostervorkommen.

    Die Zahl kommt aus den Rostereintraegen, nicht aus den Coveragezeilen:
    sonst waere die Vollzaehligkeitspruefung in ``artifacts`` tautologisch.
    """
    rules = roster_coverage.load_rules(collector)
    entries: list = []
    for chamber, path in sorted(roster_coverage.roster_paths().items()):
        entries.extend(roster_coverage.read_roster(collector, chamber, path))
    rows = roster_coverage.build_coverage(collector, units, entries, rules)
    return rows, len(entries)


def _unit_keys(targets: Sequence[MatcherUnit], prepared: RunPreparation,
               ) -> dict[str, str]:
    """Cache-Key je Unit; er entscheidet ueber Wiederverwendung beim Resume."""
    inference = workflow.inference_fingerprint(prepared.provider_settings)
    return {
        unit.unit_id: workflow.unit_cache_key(
            unit,
            taxonomy_content_hash=prepared.snapshot.taxonomy_content_hash,
            prompt=prepared.mapping_prompt,
            model=prepared.provider_settings.model,
            inference=inference,
        )
        for unit in targets
    }


#: Grund, aus dem ein Batch ohne Ergebnis blieb; er unterscheidet den
#: geordneten Abbruch des Laufs vom Fehlschlag eines einzelnen Batches.
_STOP_COST = "cost"
_STOP_PROVIDER = "provider"


def _run_batch(collector: ErrorCollector, audited: workflow.AuditedProvider,
               batch: mapping.MappingBatch, prepared: RunPreparation,
               settings: MatcherSettings, reporter: TerminalReporter,
               ) -> tuple[mapping.BatchOutcome | None, str | None]:
    """Fuehrt einen Batch aus; liefert Ergebnis und einen etwaigen Abbruchgrund.

    Wiederholt wird ausschliesslich ein Call, den der Provider nachweislich vor
    der Ausfuehrung abgelehnt hat. Bei offenem Ausgang wird nie wiederholt: der
    Call koennte gelaufen und abgerechnet worden sein (Plan 2.6).
    """
    reused = _reuse_paid_answer(audited.run_id, batch, prepared, reporter)
    if reused is not None:
        return reused, None
    attempts = 1 + max(0, settings.offline.workflow_retries)
    # Ein Batch, der in diesem Lauf schon einmal gesendet wurde, bekommt einen
    # eigenen Durchlaufpraefix: seine alte Call-ID gehoert zu einem Call, der
    # bezahlt worden sein kann.
    base = workflow.free_call_prefix(audited.run_id, batch.batch_id)
    for attempt in range(attempts):
        prefix = base if attempt == 0 else f"{base}retry{attempt}-"
        booked_before = audited.guard.calls
        try:
            return mapping.map_batch(
                audited, batch, prepared.snapshot,
                mapping_prompt=prepared.mapping_prompt,
                repair_prompt=prepared.repair_prompt,
                offline=settings.offline,
                provider_settings=prepared.provider_settings,
                call_prefix=prefix,
                reporter=reporter,
            ), None
        except workflow.CostBlocked as exc:
            collector.extend(exc.findings)
            reporter.error(Stage.COST, batch=batch.batch_id,
                           code="COST_BUDGET_EXCEEDED")
            return None, _STOP_COST
        except workflow.CallIdReused as exc:
            collector.extend(exc.findings)
            reporter.error(Stage.WORKFLOW, batch=batch.batch_id,
                           code="CALL_ID_REUSED")
            return None, _STOP_PROVIDER
        except provider.ProviderCallError as exc:
            last = attempt == attempts - 1
            # Wiederholt wird der ganze Batch. Das ist nur zulaessig, solange er
            # nichts gekostet hat: scheitert erst ein Reparaturcall, waere der
            # bereits bezahlte Erstcall sonst ein zweites Mal faellig.
            free_of_charge = audited.guard.calls == booked_before
            retryable = exc.retryable and not exc.outcome_unknown and free_of_charge
            if retryable and not last:
                reporter.warn(Stage.PROVIDER, batch=batch.batch_id,
                              retry=f"{attempt + 1}/{attempts - 1}",
                              reason=exc.findings[0].code if exc.findings else "unbekannt")
                continue
            collector.extend(exc.findings)
            if exc.routing_suspected:
                provider.routing_failure_finding(
                    collector, prepared.provider_settings, exc.findings[0].message)
            reporter.error(Stage.PROVIDER, batch=batch.batch_id,
                           code=exc.findings[0].code if exc.findings else "PROV_CALL_FAILED",
                           outcome_unknown=exc.outcome_unknown)
            # Ein nicht wiederholbarer Providerfehler beendet den Lauf: die
            # naechsten Batches liefen in denselben Fehler und kosteten erneut.
            return None, _STOP_PROVIDER
    return None, _STOP_PROVIDER


def _reuse_paid_answer(run_id: str, batch: mapping.MappingBatch,
                       prepared: RunPreparation, reporter: TerminalReporter,
                       ) -> mapping.BatchOutcome | None:
    """Nimmt eine bezahlte Antwort auf, die der Laufzustand verloren hat.

    Bricht der Prozess zwischen Auditpersistenz und Zustandspersistenz ab, ist
    der Batch bezahlt und seine Antwort liegt auf der Platte - offen ist nur
    der Zustandseintrag. Ihn durch einen zweiten Call zu ersetzen hiesse,
    denselben Batch zweimal zu bezahlen.

    Wiederverwendet wird ausschliesslich ein Erstcall, dessen Antwort die
    formale Pruefung vollstaendig besteht. Eine Antwort, die damals repariert
    werden musste, laeuft erneut: ihre Reparaturkette wieder aufzunehmen waere
    ein zweiter Zustandsautomat fuer einen seltenen Fall.
    """
    data = workflow.load_audit(run_id, batch.batch_id)
    if data is None:
        return None
    response = workflow.audit_response(data)
    if response is None:
        reporter.warn(Stage.MAPPING, batch=batch.batch_id, audit_unusable=None,
                      reason="Provenienz unvollstaendig")
        return None
    outcome = validation.validate_payload(
        response.payload, batch, prepared.snapshot)
    if not outcome.ok:
        reporter.warn(Stage.MAPPING, batch=batch.batch_id, audit_unusable=None,
                      reason="Antwort besteht die Pruefung nicht",
                      violations=len(outcome.violations))
        return None
    request = mapping.build_request(
        batch, prepared.snapshot, prepared.mapping_prompt,
        prepared.provider_settings)
    reporter.info(Stage.MAPPING, batch=batch.batch_id, reused=None,
                  source="audit", cost_usd=response.cost_usd or 0.0)
    return mapping.BatchOutcome(
        batch=batch, result=outcome.result, violations=(),
        calls=(mapping.CallRecord("mapping", request, response, ()),),
        unit_calls={unit_id: batch.batch_id for unit_id in batch.unit_ids})


def _report_abort(reporter: TerminalReporter, batch: mapping.MappingBatch,
                  guard: workflow.CostGuard, reason: str | None) -> None:
    """Schliesst einen abgebrochenen Batch ab (Statusplan S4).

    Ohne diese Meldung bliebe der ``START`` des Batches ohne Gegenstueck, und
    seine Units verschwaenden aus der Ausgabe, obwohl sie offen sind. Offen
    heisst nicht gescheitert: der Batch laeuft im naechsten Durchlauf erneut.

    Offen sind alle Units des Batches. Ein Batch entsteht ausschliesslich aus
    ``pending``, und ein abgebrochener Batch hat nichts entschieden - ein Blick
    in den Laufzustand wuerde dagegen die bereits gescheiterten Units eines
    frueheren Durchlaufs faelschlich als erledigt zaehlen.
    """
    reporter.error(Stage.MAPPING, batch=batch.batch_id, aborted=None,
                   reason=reason or "unbekannt", open_units=len(batch.units),
                   cost_usd=guard.spent_usd)
    for unit in batch.units:
        reporter.warn(Stage.MAPPING, unit=unit.unit_id, not_decided=None,
                      reason=reason or "unbekannt")


def _record_outcome(state: workflow.RunState, outcome: mapping.BatchOutcome,
                    keys: Mapping[str, str], reporter: TerminalReporter) -> None:
    """Schreibt das Batchergebnis in den Laufzustand.

    Ohne vollstaendige Callprovenienz gilt eine Unit als fehlgeschlagen statt
    als entschieden: ein Urteil, das sich nicht auf einen Call zurueckfuehren
    laesst, ist beim Provider nicht abgleichbar. Die Antwort selbst bleibt in
    der Auditdatei erhalten.
    """
    by_call = {record.request.call_id: record for record in outcome.calls}
    if outcome.result is None:
        reasons = tuple(violation.rendered() for violation in outcome.violations)
        for unit in outcome.batch.units:
            state.record(workflow.UnitRecord(
                unit_id=unit.unit_id, status=UnitStatus.FAILED,
                cache_key=keys.get(unit.unit_id, ""), violations=reasons))
            reporter.error(Stage.MAPPING, unit=unit.unit_id,
                           code="RUN_UNIT_INVALID", reasons=len(reasons))
        return

    for result in outcome.result.units:
        call_id = outcome.unit_calls.get(result.unit_id)
        record = by_call.get(call_id or "")
        response = record.response if record is not None else None
        missing = [name for name, value in (
            ("call_id", call_id),
            ("provider", getattr(response, "provider", None)),
            ("generation_id", getattr(response, "generation_id", None)),
        ) if not value]
        if missing:
            state.record(workflow.UnitRecord(
                unit_id=result.unit_id, status=UnitStatus.FAILED,
                cache_key=keys.get(result.unit_id, ""),
                violations=(f"Callprovenienz unvollstaendig: {', '.join(missing)}",)))
            reporter.error(Stage.MAPPING, unit=result.unit_id,
                           code="RUN_PROVENANCE_MISSING")
            continue
        state.record(workflow.UnitRecord(
            unit_id=result.unit_id,
            status=workflow.STATUS_FOR_DECISION[result.decision],
            cache_key=keys.get(result.unit_id, ""),
            result=result,
            call_id=call_id,
            provider=response.provider,
            generation_id=response.generation_id,
            from_repair=record.kind == "repair",
        ))
        if result.decision is MappingDecision.NO_MAPPING:
            reporter.info(Stage.MAPPING, unit=result.unit_id, no_mapping=None,
                          reason=result.no_mapping_reason or "-")
        else:
            reporter.ok(Stage.MAPPING, unit=result.unit_id,
                        industries=len(result.assignments))


def _report_committees(reporter: TerminalReporter, targets: Sequence,
                       state: workflow.RunState) -> None:
    """Eine Zusammenfassung je Committee; Subcommittees zaehlen zum Parent."""
    groups: dict[str, list] = {}
    for unit in targets:
        groups.setdefault(unit.parent_unit_id or unit.unit_id, []).append(unit)
    for committee_id, members in sorted(groups.items()):
        counts = {UnitStatus.MAPPED: 0, UnitStatus.NO_MAPPING: 0, UnitStatus.FAILED: 0}
        not_decided = 0
        industries: set[str] = set()
        for unit in members:
            record = state.units.get(unit.unit_id)
            if record is None:
                # Nie gelaufen ist nicht gescheitert; der naechste Durchlauf
                # holt die Unit nach.
                not_decided += 1
                continue
            if record.status in counts:
                counts[record.status] += 1
            for assignment in (record.result.assignments if record.result else ()):
                industries.add(assignment.industry_id)
        reporter.emit(
            StatusKind.WARN if counts[UnitStatus.FAILED] or not_decided
            else StatusKind.INFO,
            Stage.MAPPING, committee=committee_id, units=len(members),
            mapped=counts[UnitStatus.MAPPED],
            no_mapping=counts[UnitStatus.NO_MAPPING],
            failed=counts[UnitStatus.FAILED], not_decided=not_decided,
            industries=len(industries))


def _report_run_abort(collector: ErrorCollector, reporter: TerminalReporter,
                      run_id: str, exc: BaseException) -> str:
    """Haelt einen Abbruch mitten im Lauf als Befund fest.

    Der Benutzerabbruch ist kein Implementierungsfehler und behaelt seinen
    eigenen Exit-Code; jede andere Ausnahme ist einer.
    """
    if isinstance(exc, KeyboardInterrupt):
        collector.blocker(
            "RUN_ABORTED_BY_USER", Stage.WORKFLOW, run_id,
            "Der Lauf wurde nach einem gebuchten Call abgebrochen.",
            f"'run reconcile --run-id {run_id}' klaert offene Calls; danach "
            "'run execute' wiederholen - bezahlte Units kommen aus dem Cache.",
            exit_code=ExitCode.USER_ABORT)
        reporter.error(Stage.WORKFLOW, run=run_id, code="RUN_ABORTED_BY_USER")
        return _STOP_PROVIDER
    if isinstance(exc, MatcherError):
        collector.extend(exc.findings)
    collector.blocker(
        "RUN_ABORTED_AFTER_CALL", Stage.WORKFLOW, run_id,
        f"Der Lauf brach nach einem gebuchten Call ab: "
        f"{exc.__class__.__name__}: {exc}",
        f"'run reconcile --run-id {run_id}' klaert offene Calls; danach "
        "'run execute' wiederholen - bezahlte Units kommen aus dem Cache.",
        exit_code=ExitCode.INTERNAL_ERROR)
    reporter.error(Stage.WORKFLOW, run=run_id, code="RUN_ABORTED_AFTER_CALL")
    return _STOP_PROVIDER


def _final_state(state: workflow.RunState, unknown: Sequence,
                 stopped: str | None) -> WorkflowState:
    """Endzustand des Laufs; ``outcome_unknown`` schlaegt jedes Mappingurteil."""
    if unknown:
        return WorkflowState.OUTCOME_UNKNOWN
    counts = state.counts()
    if stopped is not None or counts[UnitStatus.FAILED.value]:
        return WorkflowState.FAILED
    if counts[UnitStatus.NOT_DECIDED.value]:
        return WorkflowState.FAILED
    return WorkflowState.AWAITING_MANUAL_REVIEW


@dataclass
class _RunCost:
    """Kostenstand eines laufenden Handlers, auch wenn er mit einer Ausnahme endet."""

    guard: workflow.CostGuard | None = None

    @property
    def spent_usd(self) -> float:
        return self.guard.spent_usd if self.guard is not None else 0.0

    @property
    def reserved_usd(self) -> float:
        return self.guard.reserved_usd if self.guard is not None else 0.0


def handle_run_execute(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str], float]:
    """Fuehrt den geplanten Entwicklungsrun aus (Plan 2.6); kostenpflichtig.

    Der Rahmen haelt allein den Kostenstand fest. Eine Ausnahme im Nachlauf -
    beim Roster, beim Schreiben der Artefakte - darf den Abschlussbericht nicht
    als providerfreie Phase enden lassen, wenn bereits Calls gebucht sind.
    """
    cost = _RunCost()
    try:
        return _execute_run(command, namespace, collector, settings, reporter, cost)
    except (Exception, KeyboardInterrupt) as exc:
        run_id = str(getattr(namespace, "run_id", "") or "-").strip()
        _report_run_abort(collector, reporter, run_id, exc)
        return [
            f"Run:      {run_id}",
            f"Kosten:   {cost.spent_usd:.6f} USD gebucht"
            + (f" | {cost.reserved_usd:.6f} USD reserviert"
               if cost.reserved_usd else ""),
            "Lauf:     abgebrochen; Ergebnisartefakte nicht geschrieben",
        ], [], cost.spent_usd


def _execute_run(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter, cost: _RunCost,
) -> tuple[list[str], list[str], float]:
    """Der Lauf selbst; ``cost`` traegt den Kostenstand nach aussen."""
    if _missing_run_id(command, namespace, collector):
        return [], [], 0.0
    run_id = _run_id(namespace)
    reporter.start(Stage.WORKFLOW, run=run_id, action="execute")
    if not _assert_revision_open(collector, run_id):
        return [f"Run:      {run_id}", "Lauf:     abgelehnt (Revision abgeschlossen)"], [], 0.0

    previous = workflow.load_manifest(run_id)
    if previous is None:
        _run_blocker(
            collector, "RUN_NOT_PLANNED", run_id,
            "Zu dieser Revision existiert kein Runmanifest.",
            f"Zuerst 'run plan --run-id {run_id} --committee ...' ausfuehren; "
            "der Plan friert die Bedingungen ein und nennt die Kosten.")
        return [f"Run:      {run_id}", "Lauf:     nicht geplant"], [], 0.0

    # Ein Call mit offenem Ausgang wird zuerst geklaert. Sonst liefe der Batch
    # erneut, obwohl der erste Versuch bezahlt sein kann - und der
    # Abschlussmarker wuerde die offene Position verschweigen.
    still_open = workflow.open_intents(run_id)
    if still_open:
        collector.blocker(
            "RUN_OUTCOME_UNKNOWN", Stage.COST, f"{len(still_open)} Call(s)",
            "Aus einem frueheren Durchlauf ist der Ausgang einzelner Calls offen.",
            f"Zuerst 'run reconcile --run-id {run_id}' ausfuehren; erst danach "
            "steht fest, was bereits bezahlt wurde.",
            exit_code=ExitCode.OUTCOME_UNKNOWN,
        )
        reporter.error(Stage.COST, run=run_id, open_calls=len(still_open),
                       code="RUN_OUTCOME_UNKNOWN")
        return [f"Run:      {run_id}",
                f"Lauf:     angehalten ({len(still_open)} Call(s) ungeklaert)"], [], 0.0

    prepared = _prepare_run(command, namespace, collector, settings, reporter,
                            run_id, previous)
    if prepared is None:
        return [f"Run:      {run_id}", "Lauf:     blockiert (siehe Befunde)"], [], 0.0
    conflicts = workflow.resume_conflicts(previous, prepared.manifest)
    if conflicts:
        collector.extend([workflow.resume_blocker(run_id, conflicts)])
        reporter.error(Stage.WORKFLOW, run=run_id, code="RUN_MANIFEST_DRIFT")
        return [f"Run:      {run_id}", "Lauf:     abgelehnt (andere Bedingungen)"], [], 0.0

    state = workflow.load_state(run_id)
    keys = _unit_keys(prepared.targets, prepared)
    pending = state.pending(prepared.targets, keys)
    reused = len(prepared.targets) - len(pending)
    reporter.info(Stage.WORKFLOW, run=run_id, pending=len(pending), cached=reused)

    # Das Budget gilt fuer den Lauf, nicht fuer den Durchlauf: was fruehere
    # Durchlaeufe ausgegeben haben, ist bereits gebucht.
    rates = workflow.CostRates.from_settings(prepared.provider_settings)
    prior = workflow.prior_costs(run_id, rates, state)
    guard = workflow.CostGuard(
        budget_usd=prepared.budget_usd, rates=rates,
        enabled=settings.offline.cost_guard,
        spent_usd=prior.spent_usd, reserved_usd=prior.reserved_usd,
        calls=prior.calls, unknown_calls=prior.unknown_calls,
    )
    cost.guard = guard
    if prior.calls:
        reporter.info(Stage.COST, run=run_id, prior_calls=prior.calls,
                      prior_spent_usd=prior.spent_usd)
    if prior.orphaned_audits:
        # Eine bezahlte Antwort ohne Zustandseintrag: der Prozess ist zwischen
        # Auditschreiben und Zustandsschreiben abgebrochen. Der Batch laeuft
        # erneut und kostet erneut - das muss sichtbar sein.
        collector.warning(
            "RUN_AUDIT_WITHOUT_STATE", Stage.WORKFLOW,
            ", ".join(prior.orphaned_audits[:5]),
            f"{len(prior.orphaned_audits)} bezahlte Antwort(en) liegen ohne "
            "Eintrag im Laufzustand vor.",
            "Auditdateien unter calls/ sichten; die betroffenen Units laufen "
            "erneut und werden erneut abgerechnet.")
        reporter.warn(Stage.WORKFLOW, run=run_id,
                      audits_without_state=len(prior.orphaned_audits))
    audited: workflow.AuditedProvider | None = None
    stopped: str | None = None
    batches: tuple[mapping.MappingBatch, ...] = ()

    if pending:
        planned = mapping.plan_batches(
            collector, pending, prepared.snapshot, prepared.mapping_prompt,
            settings.offline, prepared.provider_settings)
        if planned is None:
            return [f"Run:      {run_id}", "Lauf:     blockiert (Kontextbudget)"], [], 0.0
        batches = tuple(planned)
        requests = tuple(
            mapping.build_request(batch, prepared.snapshot, prepared.mapping_prompt,
                                  prepared.provider_settings)
            for batch in batches
        )
        plan_usd = workflow.plan_cost_usd(requests, guard.rates, settings.offline)
        blocker = guard.check(plan_usd, run_id)
        if blocker is not None:
            collector.extend([blocker])
            reporter.error(Stage.COST, run=run_id, code="COST_BUDGET_EXCEEDED",
                           plan_usd=plan_usd, budget_usd=prepared.budget_usd)
            return [
                f"Run:      {run_id}",
                f"Umfang:   {len(pending)} offene Units in {len(batches)} Batches",
                *_cost_lines(budget_usd=prepared.budget_usd, plan_usd=plan_usd,
                             guard_enabled=guard.enabled),
                "Lauf:     abgelehnt (Budget), kein Call gesendet",
            ], [], 0.0

        adapter = provider.OpenRouterProvider.create(collector, prepared.provider_settings)
        if adapter is None:
            return [f"Run:      {run_id}", "Lauf:     blockiert (Providerrand)"], [], 0.0
        audited = workflow.AuditedProvider(
            inner=adapter, run_id=run_id, guard=guard, offline=settings.offline,
            reporter=reporter)
        state.state = WorkflowState.RUNNING
        state.write()

        current: mapping.MappingBatch | None = None
        try:
            for index, batch in enumerate(batches, start=1):
                current = batch
                reporter.start(
                    Stage.MAPPING, run=run_id, batch=batch.batch_id,
                    units=len(batch.units), position=f"{index}/{len(batches)}")
                outcome, stopped = _run_batch(
                    collector, audited, batch, prepared, settings, reporter)
                if outcome is not None:
                    _record_outcome(state, outcome, keys, reporter)
                    reporter.emit(
                        StatusKind.OK if outcome.ok else StatusKind.ERROR,
                        Stage.MAPPING, batch=batch.batch_id,
                        repairs=outcome.repairs, cost_usd=guard.spent_usd)
                else:
                    _report_abort(reporter, batch, guard, stopped)
                current = None
                state.write()
                if stopped is not None:
                    break
        except (Exception, KeyboardInterrupt) as exc:
            # Ab dem ersten gebuchten Call darf keine Ausnahme mehr am
            # Abschlussbericht vorbeilaufen: er wuerde den Lauf sonst als
            # providerfrei ausweisen, obwohl Geld gebunden ist.
            stopped = _report_run_abort(collector, reporter, run_id, exc)
            if current is not None:
                _report_abort(reporter, current, guard, stopped)
            # Der Zustand ist bestenfalls zu retten: war ein Schreibfehler der
            # Ausloeser, scheitert auch dieser Versuch - der Bericht und die
            # Auditspur bleiben davon unberuehrt.
            try:
                state.write()
            except Exception:
                pass

    unknown = tuple(audited.unknown) if audited is not None else ()
    state.state = _final_state(state, unknown, stopped)
    state.write()

    written: list[str] = [paths.relative_to_base(workflow.state_path(run_id))]
    # Der Artefaktsatz schliesst die Revision ab und macht sie unveraenderlich.
    # Ein abgebrochener oder ungeklaerter Lauf bekommt ihn deshalb nicht: sonst
    # koennte derselbe Run seine offenen Units nie zu Ende bringen.
    covered = all(unit.unit_id in state.units for unit in prepared.targets)
    if stopped is None and not unknown and covered:
        rows, occurrences = _roster(collector, prepared.units)
        try:
            result = artifacts.write_run_artifacts(
                # Provenienz stammt aus dem eingefrorenen Manifest des Plans,
                # nicht aus dem neu gebauten: die Artefakte beschreiben die
                # Bedingungen, unter denen die Calls tatsaechlich liefen.
                run_id=run_id, units=prepared.targets, state=state,
                snapshot=prepared.snapshot, manifest=previous, guard=guard,
                roster=rows, roster_occurrences=occurrences, unknown=unknown,
                audited_calls=prior.documented_calls + (
                    len(audited.audited) + len(audited.settled)
                    if audited is not None else 0),
            )
            written.extend(entry.relative for entry in result.files())
            reporter.ok(Stage.ARTIFACTS, run=run_id, files=len(result.files()))
        except MatcherError as exc:
            collector.extend(exc.findings)
            reporter.error(Stage.ARTIFACTS, run=run_id,
                           code=exc.findings[0].code if exc.findings else "ART_WRITE_FAILED")
    else:
        reporter.warn(Stage.ARTIFACTS, run=run_id, skipped=None,
                      reason="Lauf nicht abgeschlossen")
        collector.warning(
            "RUN_ARTIFACTS_DEFERRED", Stage.ARTIFACTS, run_id,
            "Der Lauf ist nicht abgeschlossen; die Ergebnisartefakte entstehen "
            "erst mit dem abschliessenden Durchlauf.",
            f"Offene Punkte klaeren und 'run execute --run-id {run_id}' "
            "wiederholen; bereits bezahlte Units kommen aus dem Cache.")

    if guard.unknown_calls > len(unknown):
        collector.warning(
            "COST_UNKNOWN_REPORTED", Stage.COST,
            f"{guard.unknown_calls - len(unknown)} Call(s)",
            "Einzelne Calls haben eine gueltige Antwort, aber keine Kosten "
            "gemeldet; fuer sie bleibt der Worst Case reserviert.",
            "cost_report.json weist die Reservierung aus; ueber die "
            "Generation-ID laesst sie sich beim Provider aufloesen.")
    if unknown:
        collector.extend([workflow.outcome_unknown_finding(unknown)])
    counts = state.counts()
    if counts[UnitStatus.FAILED.value]:
        collector.blocker(
            "RUN_UNITS_FAILED", Stage.MAPPING, f"{counts[UnitStatus.FAILED.value]} Unit(s)",
            "Einzelne Units haben kein gueltiges Ergebnis erhalten.",
            "unit_coverage.csv nennt Status und Grund je Unit; betroffene Units "
            "gezielt mit --unit-id in einer neuen Revision wiederholen.",
            exit_code=ExitCode.FAILED_MAPPING_UNITS,
        )
    _report_committees(reporter, prepared.targets, state)
    if stopped is not None or state.state is not WorkflowState.AWAITING_MANUAL_REVIEW:
        final_kind = StatusKind.ERROR
    else:
        # Der Lauf endet an einem Human-Gate, nicht mit einem fertigen Ergebnis.
        final_kind = StatusKind.WAIT
    reporter.emit(
        final_kind,
        Stage.WORKFLOW, run=run_id, state=state.state.value,
        mapped=counts[UnitStatus.MAPPED.value],
        no_mapping=counts[UnitStatus.NO_MAPPING.value],
        failed=counts[UnitStatus.FAILED.value],
        cost_usd=guard.spent_usd,
    )
    summary = [
        f"Run:      {run_id}",
        f"Umfang:   {len(prepared.targets)} Units | {reused} aus dem Cache | "
        f"{len(batches)} Batches gesendet",
        "Ergebnis: "
        + " | ".join(f"{status}={counts[status]}" for status in sorted(counts)),
        f"Zustand:  {state.state.value}"
        + (f" (Abbruch: {stopped})" if stopped else ""),
        f"Kosten:   {guard.spent_usd:.6f} USD gebucht"
        + (f" | {guard.reserved_usd:.6f} USD reserviert fuer "
           f"{guard.unknown_calls} ungeklaerte Call(s)" if guard.unknown_calls else "")
        + f" von {prepared.budget_usd:.6f} USD Budget",
    ]
    return summary, written, guard.spent_usd


def _intent_age_s(intent: Mapping[str, object], now: datetime) -> float | None:
    """Alter der Vormerkung in Sekunden; None ohne brauchbaren Zeitstempel."""
    raw = intent.get("created_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        stamp = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds()


def handle_run_reconcile(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Klaert Calls mit offenem Ausgang ueber die Generation-ID (Plan 2.6).

    Der Abgleich ist kostenlos und sendet keinen Modellaufruf. Geklaert wird der
    Kostenstand, nicht der Inhalt: die Antwort ist verloren, die betroffenen
    Units laufen im naechsten ``run execute`` erneut.
    """
    if _missing_run_id(command, namespace, collector):
        return [], []
    run_id = _run_id(namespace)
    reporter.start(Stage.WORKFLOW, run=run_id, action="reconcile")
    provider_settings = settings.require_provider()
    rates = workflow.CostRates.from_settings(provider_settings)

    open_calls = workflow.open_intents(run_id)
    # Ein Call mit gueltiger Antwort, aber ohne gemeldete Kosten haelt ebenfalls
    # eine Reservierung. Sein Ausgang ist bekannt - offen ist nur der Betrag.
    unpriced = workflow.unpriced_audits(run_id)
    pending_calls = open_calls + tuple(
        call_id for call_id in unpriced if call_id not in open_calls)
    if not pending_calls:
        reporter.ok(Stage.WORKFLOW, run=run_id, action="reconcile", open_calls=0)
        return [f"Run:      {run_id}", "Abgleich: kein Call mit offenem Ausgang"], []

    api_key = os.getenv(provider.API_KEY_ENV, "").strip()
    if not api_key:
        collector.blocker(
            "PROV_NO_API_KEY", Stage.PROVIDER, provider.API_KEY_ENV,
            f"Umgebungsvariable {provider.API_KEY_ENV} ist leer oder nicht gesetzt.",
            f"{provider.API_KEY_ENV} in der Datei .env eintragen; der Abgleich "
            "einer Generation gehoert zum Konto und braucht den Key.",
            exit_code=ExitCode.PREFLIGHT_BLOCKED,
        )
        return [f"Run:      {run_id}",
                f"Abgleich: blockiert ({len(pending_calls)} offen)"], []

    settled: list[dict] = []
    unresolved: list[str] = []
    charged_usd = 0.0
    released_usd = 0.0

    now = datetime.now(timezone.utc)
    min_age_s = settings.offline.reconcile_min_age_s

    for call_id in pending_calls:
        # Der bezahlte Call kennt seine Generation aus der Auditdatei, der
        # abgebrochene aus seiner Vormerkung.
        priced = call_id in unpriced and call_id not in open_calls
        intent = (workflow.load_audit(run_id, call_id) if priced
                  else workflow.load_intent(run_id, call_id))
        reserved = (workflow.unknown_reservation(intent, rates) if priced and intent
                    else float((intent or {}).get("reserved_usd")
                               or (intent or {}).get("worst_case_usd") or 0.0))
        generation_id = (intent or {}).get("generation_id")
        if intent is None or not generation_id:
            unresolved.append(call_id)
            reporter.warn(Stage.COST, call=call_id,
                          reason="ohne Generation-ID nicht abgleichbar")
            continue
        try:
            record = provider.fetch_generation(str(generation_id), api_key)
        except provider.CatalogUnavailable as exc:
            unresolved.append(call_id)
            collector.warning(
                "COST_RECONCILE_UNAVAILABLE", Stage.COST, call_id,
                f"Der Abgleich war nicht moeglich: {exc}.",
                "Abgleich spaeter wiederholen; die Reservierung bleibt bis dahin "
                "bestehen.")
            reporter.warn(Stage.COST, call=call_id, reason=str(exc))
            continue

        if not record.found and priced:
            # Die Antwort liegt vor, der Call ist also gelaufen. Ihn als 'nicht
            # abgerechnet' zu werten wuerde eine Reservierung aufloesen, die zu
            # einem tatsaechlich bezahlten Call gehoert.
            unresolved.append(call_id)
            collector.warning(
                "COST_RECONCILE_INCOMPLETE", Stage.COST, call_id,
                "Der Provider kennt die Generation dieses bezahlten Calls nicht.",
                "Abgleich spaeter wiederholen; die Reservierung bleibt bestehen, "
                "denn die Antwort belegt, dass der Call gelaufen ist.")
            reporter.warn(Stage.COST, call=call_id,
                          reason="bezahlter Call ohne bekannte Generation")
            continue
        if not record.found:
            age_s = _intent_age_s(intent, now)
            if age_s is None or age_s < min_age_s:
                unresolved.append(call_id)
                collector.warning(
                    "COST_RECONCILE_TOO_FRESH", Stage.COST, call_id,
                    "Der Provider kennt die Generation nicht; der Versuch ist "
                    "dafuer zu jung oder traegt keinen brauchbaren Zeitstempel.",
                    f"Abgleich fruehestens {min_age_s:.0f} s nach dem Call "
                    "wiederholen; eine frische Generation wird verzoegert "
                    "sichtbar, ein vorschnelles 'nicht abgerechnet' wuerde "
                    "einen zweiten kostenpflichtigen Call erlauben.")
                reporter.warn(Stage.COST, call=call_id,
                              reason="unbekannte Generation zu frueh abgefragt")
                continue
            outcome = {"outcome": "not_charged", "cost_usd": 0.0,
                       "released_usd": reserved, "generation_id": record.generation_id,
                       "intent_age_s": round(age_s, 3)}
        elif record.cost_usd is None:
            unresolved.append(call_id)
            collector.warning(
                "COST_RECONCILE_INCOMPLETE", Stage.COST, call_id,
                "Der Provider kennt die Generation, nennt aber keine Kosten.",
                "Abgleich spaeter wiederholen; ein Betrag wird nicht geschaetzt.")
            reporter.warn(Stage.COST, call=call_id, reason="Provider nennt keine Kosten")
            continue
        else:
            outcome = {"outcome": "charged", "cost_usd": record.cost_usd,
                       "released_usd": max(0.0, reserved - record.cost_usd),
                       "generation_id": record.generation_id,
                       "provider": record.provider, "model": record.model,
                       "finish_reason": record.finish_reason}

        outcome["reserved_usd"] = reserved
        try:
            workflow.write_reconciliation(run_id, call_id, outcome)
        except (MatcherError, OSError) as exc:
            unresolved.append(call_id)
            collector.warning(
                "COST_RECONCILE_NOT_PERSISTED", Stage.COST, call_id,
                f"Die Klaerung konnte nicht festgehalten werden: {exc}.",
                "Schreibrecht unter data/committee_matcher_v2/ pruefen; ohne "
                "Datei gilt der Call weiter als offen.")
            reporter.warn(Stage.COST, call=call_id, reason="Klaerung nicht geschrieben")
            continue

        # Erst die Datei, dann die Buchung: eine nicht festgehaltene Klaerung
        # darf den Kostenstand des naechsten Laufs nicht veraendern.
        settled.append({"call_id": call_id, **outcome})
        released_usd += float(outcome["released_usd"])
        if outcome["outcome"] == "charged":
            charged_usd += float(outcome["cost_usd"])
            reporter.ok(Stage.COST, call=call_id, charged=None,
                        cost_usd=outcome["cost_usd"])
        else:
            reporter.ok(Stage.COST, call=call_id, not_charged=None,
                        released_usd=reserved)

    written = _write_artifact(
        collector, command.name, "reconciliation.json",
        {"run_id": run_id, "settled": settled, "unresolved": unresolved,
         "charged_usd": round(charged_usd, 6),
         "released_usd": round(released_usd, 6)},
        run_id=run_id,
    )
    if unresolved:
        collector.blocker(
            "RUN_OUTCOME_UNKNOWN", Stage.COST, f"{len(unresolved)} Call(s)",
            "Der Ausgang dieser Calls ist weiterhin offen; ihre Reservierung "
            "bleibt gebucht.",
            "Abgleich wiederholen, sobald der Provider antwortet; ohne "
            "Generation-ID nur ueber die Abrechnung des Kontos klaerbar.",
            exit_code=ExitCode.OUTCOME_UNKNOWN,
        )
    reporter.emit(
        StatusKind.ERROR if unresolved else StatusKind.OK,
        Stage.WORKFLOW, run=run_id, action="reconcile",
        settled=len(settled), unresolved=len(unresolved), charged_usd=charged_usd)
    return [
        f"Run:      {run_id}",
        f"Abgleich: {len(settled)} von {len(open_calls)} Call(s) geklaert"
        + (f" | {len(unresolved)} weiterhin offen" if unresolved else ""),
        f"Kosten:   {charged_usd:.6f} USD tatsaechlich abgerechnet | "
        f"{released_usd:.6f} USD Reservierung aufgeloest",
        "Hinweis:  der Inhalt der Antworten ist nicht wiederherstellbar; die "
        "betroffenen Units laufen im naechsten 'run execute' erneut.",
    ], written


def handle_review_export(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Exportiert das Review-Workbook einer abgeschlossenen Revision (Plan 2.8)."""
    if _missing_run_id(command, namespace, collector):
        return [], []
    run_id = _run_id(namespace)
    review_id = str(getattr(namespace, "review_id", None) or f"review-{run_id}").strip()
    reporter.start(Stage.REVIEW, run=run_id, review=review_id)
    # Das Workbook fuehrt die zulaessigen Industries als eigenes Blatt mit; ohne
    # freigegebenen Snapshot gaebe es keine belastbare Auswahlliste.
    snapshot = taxonomy_snapshot.approved_snapshot(collector)
    if collector.has_blockers() or snapshot is None:
        reporter.error(Stage.REVIEW, run=run_id, code="REV_PREFLIGHT")
        return [f"Run:      {run_id}", "Export:   blockiert"], []
    try:
        exported = review_module.export_review(run_id, review_id, snapshot)
    except MatcherError as exc:
        collector.extend(exc.findings)
        reporter.error(Stage.REVIEW, run=run_id, review=review_id,
                       code=exc.findings[0].code if exc.findings else "REV_EXPORT_FAILED")
        return [f"Run:      {run_id}", "Export:   blockiert"], []

    if exported.incomplete_units:
        collector.warning(
            "REV_UNITS_WITHOUT_DECISION", Stage.REVIEW,
            f"{len(exported.incomplete_units)} Unit(s)",
            "Einzelne Units des Laufs tragen kein Urteil und stehen deshalb "
            "nicht im Workbook.",
            "unit_coverage.csv nennt Status und Grund; betroffene Units in "
            "einer neuen Revision wiederholen und danach erneut exportieren.")
        reporter.warn(Stage.REVIEW, review=review_id,
                      without_decision=len(exported.incomplete_units))
    reporter.ok(Stage.REVIEW, review=review_id, rows=exported.rows,
                boundary=exported.boundary_rows)
    return [
        f"Run:      {run_id}",
        f"Review:   {review_id} | {exported.rows} Zeile(n) | "
        f"{exported.boundary_rows} Boundary-Konflikt(e)",
        "Hinweis:  Werte direkt aendern; Zeile loeschen entfernt die Zuordnung, "
        "leere industry_id heisst keine Zuordnung, neue Zeile mit unit_id fuegt "
        "eine hinzu.",
    ], list(exported.files())


def handle_review_import(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Importiert das bearbeitete Workbook einer Reviewrevision (Plan 2.8)."""
    if _missing_run_id(command, namespace, collector):
        return [], []
    run_id = _run_id(namespace)
    review_id = str(getattr(namespace, "review_id", None) or f"review-{run_id}").strip()
    reporter.start(Stage.REVIEW, run=run_id, review=review_id, action="import")

    run_manifest = workflow.load_manifest(run_id)
    if run_manifest is None:
        collector.blocker(
            "REV_MANIFEST_MISSING", Stage.REVIEW, run_id,
            "Zur Revision fehlt das Runmanifest.",
            "Ohne Manifest ist die Bindung des Reviews nicht pruefbar.")
        reporter.error(Stage.REVIEW, run=run_id, code="REV_MANIFEST_MISSING")
        return [f"Run:      {run_id}", "Import:   blockiert"], []

    corpus = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, corpus.documents)
    snapshot = taxonomy_snapshot.approved_snapshot(collector)
    if collector.has_blockers() or snapshot is None:
        reporter.error(Stage.REVIEW, run=run_id, code="REV_PREFLIGHT")
        return [f"Run:      {run_id}", "Import:   blockiert"], []

    given = getattr(namespace, "workbook", None)
    workbook = Path(str(given)).expanduser().resolve() if given else None
    try:
        imported = review_module.import_review(
            review_id, run_id=run_id,
            corpus={unit.unit_id: unit for unit in units},
            snapshot=snapshot, run_manifest=run_manifest,
            input_manifest_hash=corpus.manifest_sha256,
            collector=collector, workbook=workbook)
    except MatcherError as exc:
        collector.extend(exc.findings)
        reporter.error(Stage.REVIEW, run=run_id, review=review_id,
                       code=exc.findings[0].code if exc.findings else "REV_IMPORT_FAILED")
        return [f"Run:      {run_id}", "Import:   blockiert"], []

    if imported is None:
        reporter.error(Stage.REVIEW, review=review_id, rejected=None,
                       findings=len(collector.blockers))
        return [
            f"Run:      {run_id}",
            f"Review:   {review_id}",
            "Import:   abgelehnt; keine Datei geschrieben",
        ], []

    reporter.ok(Stage.REVIEW, review=review_id, units=imported.units,
                assignments=imported.assignments,
                manual=imported.manual_assignments,
                removed=imported.removed, added=imported.added,
                changed=imported.changed)
    return [
        f"Run:      {run_id}",
        f"Review:   {review_id} | {imported.units} Unit(s) | "
        f"{imported.assignments} Zuordnung(en) | {imported.manual_assignments} manuell | "
        f"{imported.no_mapping_units} no_mapping",
        f"Zuordnung: {imported.changed} geaendert | {imported.added} neu | "
        f"{imported.removed} entfernt"
        + (f" | {imported.changed_reasons} Grund geaendert"
           if imported.changed_reasons else ""),
        "Ergebnis: reviewed_assignments.csv und reviewed_unit_coverage.csv "
        "geschrieben",
    ], list(imported.files)


#: Bereits implementierte Aktionen; alles andere meldet die Zielphase.
def _eval_id(collector: ErrorCollector, namespace: argparse.Namespace) -> str | None:
    """Liefert die Evaluationsrevision; sie ist Pflicht und wird nie erraten.

    Eine aus der Uhrzeit erzeugte Kennung waere hier falsch: an der Revision
    haengen Labels, die Bene von Hand vergibt, und die muessen wiederfindbar
    bleiben.
    """
    raw = str(getattr(namespace, "eval_id", "") or "").strip()
    if not raw:
        collector.blocker(
            "CLI_EVAL_ID_REQUIRED", Stage.EVALUATION, "evaluate",
            "Jede Evaluationsaktion ist an genau eine Revision gebunden.",
            "Lauf mit --eval-id <revision> wiederholen.")
        return None
    if not RUN_ID_RE.match(raw):
        collector.blocker(
            "CLI_EVAL_ID_INVALID", Stage.EVALUATION, raw,
            "Evaluations-ID enthaelt unzulaessige Zeichen.",
            "Nur Buchstaben, Ziffern, '-' und '_' verwenden.")
        return None
    return raw


def handle_evaluate_export(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Eroeffnet eine Evaluationsrevision und friert ihre Bedingungen ein (Plan P9).

    Der Vorgang ruft yfinance ab, ruft aber kein Modell auf und kostet nichts.
    Er endet bewusst in ``awaiting_development_goldset``: die Labels entstehen
    von Hand, und erst ``evaluate import`` fuehrt die Revision weiter.
    """
    provider_settings = settings.require_provider()
    budget_usd = settings.require_budget("evaluation")
    eval_id = _eval_id(collector, namespace)
    if eval_id is None:
        return [], []

    reporter.start(Stage.EVALUATION, evaluation=eval_id, action="export")
    try:
        return _export_evaluation(namespace, collector, settings, reporter,
                                  eval_id, provider_settings, budget_usd)
    except MatcherError as exc:
        # Ohne dieses Gegenstueck bliebe der ``START`` dieses Vorgangs offen,
        # sobald ein Fachmodul als Ausnahme abbricht.
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code=exc.findings[0].code if exc.findings else "EVAL_FAILED")
        raise


def _export_evaluation(
    namespace: argparse.Namespace, collector: ErrorCollector,
    settings: MatcherSettings, reporter: TerminalReporter, eval_id: str,
    provider_settings, budget_usd: float,
) -> tuple[list[str], list[str]]:
    """Der Ablauf des Exports; die teure yfinance-Abfrage kommt zuletzt."""
    blocked = [f"Evaluation: {eval_id}", "Export:     blockiert"]
    if not evaluation_module.assert_revision_free(collector, eval_id):
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code="EVAL_REVISION_TAKEN")
        return blocked, []

    sample = evaluation_module.load_sample(collector)
    if sample is None:
        reporter.error(Stage.EVALUATION, evaluation=eval_id, code="EVAL_SAMPLE")
        return blocked, []

    corpus = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, corpus.documents)
    if collector.has_blockers():
        reporter.error(Stage.EVALUATION, evaluation=eval_id, code="EVAL_INPUT")
        return blocked, []
    if not corpus.manifest_sha256:
        collector.blocker(
            "EVAL_INPUT_UNBOUND", Stage.EVALUATION, "corpus_manifest.yaml",
            "Der Inputstand ist nicht hashbar; die Evaluation waere an keinen "
            "Corpus gebunden.",
            "Corpusmanifest wiederherstellen; 'input validate' zeigt den Stand.")
        reporter.error(Stage.EVALUATION, evaluation=eval_id, code="EVAL_INPUT_UNBOUND")
        return blocked, []

    targets = evaluation_module.select_units(collector, units, sample)
    if targets is None:
        reporter.error(Stage.EVALUATION, evaluation=eval_id, code="EVAL_SAMPLE_UNITS")
        return blocked, []
    reporter.ok(Stage.INPUT, evaluation=eval_id, units=len(units),
                targets=len(targets))

    loaded = prompts.load_all(collector)
    if loaded is None:
        reporter.error(Stage.EVALUATION, evaluation=eval_id, code="EVAL_PROMPTS")
        return blocked, []

    # Gemessen wird gegen den freigegebenen Stand, denn genau gegen ihn laeuft
    # auch der Prueflauf. Fehlt die Freigabe, ist das eine Uebergabe an Bene
    # und kein Grund, vorher yfinance abzurufen.
    approved = taxonomy_snapshot.approved_snapshot(collector)
    if approved is None:
        reporter.wait(Stage.EVALUATION, evaluation=eval_id,
                      awaiting_taxonomy_approval=None, reason="no_baseline")
        return blocked, []

    # Der Netzabruf kommt zuletzt: jede billige Pruefung hat vorher entschieden,
    # ob dieser Export ueberhaupt zustande kommen kann.
    client = taxonomy_source.YFinanceClient(settings.offline, reporter=reporter)
    snapshot_result = taxonomy_snapshot.build_snapshot(
        collector, client, run_id=eval_id, reporter=reporter)
    snapshot_lines = _snapshot_summary(snapshot_result)
    if snapshot_result.state is WorkflowState.AWAITING_TAXONOMY_APPROVAL:
        reporter.wait(Stage.EVALUATION, evaluation=eval_id,
                      awaiting_taxonomy_approval=None,
                      reason=snapshot_result.reason or "drift")
        return blocked + snapshot_lines, list(snapshot_result.artifacts)
    if snapshot_result.state is not WorkflowState.COMPLETED:
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code="EVAL_TAXONOMY_NOT_FROZEN",
                       state=snapshot_result.state.value)
        return blocked + snapshot_lines, list(snapshot_result.artifacts)
    if snapshot_result.content_hash != approved.taxonomy_content_hash:
        # Nicht jede Abweichung ist blockierende Drift: eine geaenderte
        # Beschreibung laeuft als Warnung durch. Fuer die Evaluation ist auch
        # sie entscheidend, denn der Beschreibungstext steht im Prompt.
        collector.blocker(
            "EVAL_TAXONOMY_DRIFT", Stage.EVALUATION, eval_id,
            "Der frische Taxonomieabruf weicht vom freigegebenen Stand ab "
            f"(abgerufen {snapshot_result.content_hash[:12]}, freigegeben "
            f"{approved.taxonomy_content_hash[:12]}).",
            f"Abweichung sichten und mit 'snapshot approve --run-id {eval_id}' "
            "freigeben, danach den Export unter einer neuen --eval-id "
            "wiederholen.",
            exit_code=ExitCode.AWAITING_TAXONOMY_APPROVAL)
        reporter.wait(Stage.EVALUATION, evaluation=eval_id,
                      awaiting_taxonomy_approval=None, reason="content_drift")
        return blocked + snapshot_lines, list(snapshot_result.artifacts)

    manifest = evaluation_module.build_export_manifest(
        eval_id=eval_id,
        settings=settings,
        provider_settings=provider_settings,
        prompts_used=(loaded["mapping_system"], loaded["repair_system"]),
        taxonomy_content_hash=approved.taxonomy_content_hash,
        snapshot_id=approved.snapshot_id,
        input_manifest_hash=corpus.manifest_sha256,
        targets=targets,
        sample=sample,
        budget_usd=budget_usd,
    )
    # Zweite Pruefung unmittelbar vor dem Schreiben: zwischen Vorpruefung und
    # hier liegt der Netzabruf, und in dieser Zeit kann die Kennung belegt
    # worden sein.
    if not evaluation_module.assert_revision_free(collector, eval_id):
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code="EVAL_REVISION_TAKEN")
        return blocked + snapshot_lines, list(snapshot_result.artifacts)
    written = evaluation_module.write_export_manifest(eval_id, manifest)
    variants = manifest["variants"]
    reporter.wait(Stage.EVALUATION, evaluation=eval_id,
                  awaiting_development_goldset=None, units=len(targets))
    return (
        [
            f"Evaluation: {eval_id}",
            f"Zustand:    {WorkflowState.AWAITING_DEVELOPMENT_GOLDSET.value}",
            f"Stichprobe: {len(targets)} Unit(s) ({sample.version})",
            f"Taxonomie:  {approved.snapshot_id} | Content-Hash "
            f"{approved.taxonomy_content_hash[:12]} (frisch bestaetigt)",
            f"Varianten:  Batchgroessen {variants['batch_sizes']} x "
            f"{variants['repetitions']} Wiederholung(en) | Budget "
            f"{budget_usd:.2f} USD",
            "Naechster:  Gold-Set labeln, dann "
            f"'evaluate import --eval-id {eval_id}'",
        ] + snapshot_lines,
        [paths.relative_to_base(written)] + list(snapshot_result.artifacts),
    )


def handle_evaluate_import(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Uebernimmt das gelabelte Entwicklungs-Gold-Set in die Revision (Plan P9).

    Abgelehnt heisst: keine Datei geschrieben. Ein halb uebernommenes Gold-Set
    waere schlimmer als keines, denn es saehe vollstaendig aus.
    """
    eval_id = _eval_id(collector, namespace)
    if eval_id is None:
        return [], []
    reporter.start(Stage.EVALUATION, evaluation=eval_id, action="import")
    try:
        return _import_goldset(namespace, collector, settings, reporter, eval_id)
    except MatcherError as exc:
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code=exc.findings[0].code if exc.findings else "EVAL_FAILED")
        raise


def _import_goldset(
    namespace: argparse.Namespace, collector: ErrorCollector,
    settings: MatcherSettings, reporter: TerminalReporter, eval_id: str,
) -> tuple[list[str], list[str]]:
    """Der Ablauf des Labelimports; ohne Netz und ohne Modellaufruf."""
    rejected = [f"Evaluation: {eval_id}", "Gold-Set:   abgelehnt"]
    manifest = evaluation_module.require_export_manifest(eval_id)
    if evaluation_module.load_goldset(eval_id) is not None:
        collector.blocker(
            "EVAL_GOLDSET_PRESENT", Stage.EVALUATION, eval_id,
            "Diese Revision traegt bereits ein Gold-Set.",
            "Neue --eval-id exportieren und gegen sie labeln; ein Gold-Set "
            "wird nie ueberschrieben.",
            exit_code=ExitCode.REVIEW_IMPORT_REJECTED)
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code="EVAL_GOLDSET_PRESENT")
        return rejected, []

    given = str(getattr(namespace, "labels", None) or "").strip()
    source = Path(given) if given else evaluation_module.label_workbook_path()

    snapshot = taxonomy_snapshot.approved_snapshot(collector)
    if snapshot is None:
        reporter.wait(Stage.EVALUATION, evaluation=eval_id,
                      awaiting_taxonomy_approval=None, reason="no_baseline")
        return rejected, []
    corpus = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, corpus.documents)
    if collector.has_blockers():
        reporter.error(Stage.EVALUATION, evaluation=eval_id, code="EVAL_INPUT")
        return rejected, []

    evaluation_module.check_label_binding(
        manifest, snapshot=snapshot, input_manifest_hash=corpus.manifest_sha256,
        units={unit.unit_id: unit for unit in units}, collector=collector)
    rows = evaluation_module.read_labels(source, collector)
    goldset = evaluation_module.build_goldset(
        rows,
        expected_units=list(manifest.get("selection", {}).get("unit_ids") or []),
        industry_ids=snapshot.industry_ids(),
        collector=collector)
    if goldset is None or collector.has_blockers():
        reporter.error(Stage.EVALUATION, evaluation=eval_id,
                       code="EVAL_GOLDSET_REJECTED", rows=len(rows))
        return rejected, []

    payload = evaluation_module.goldset_payload(
        eval_id, manifest, goldset,
        source=source,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        row_count=len(rows))
    written = evaluation_module.write_goldset(eval_id, payload)
    reporter.ok(Stage.EVALUATION, evaluation=eval_id, state=WorkflowState.PENDING.value,
                units=payload["unit_count"], assignments=payload["assignment_count"])
    return (
        [
            f"Evaluation: {eval_id}",
            f"Zustand:    {WorkflowState.PENDING.value}",
            f"Gold-Set:   {payload['unit_count']} Unit(s) | "
            f"{payload['assignment_count']} Zuordnung(en) | "
            f"{payload['no_mapping_units']} ohne Zuordnung",
            f"Labels:     {paths.relative_to_base(source)} ({len(rows)} Zeile(n))",
            "Naechster:  Gate vor dem Prueflauf; danach "
            f"'evaluate run --eval-id {eval_id}'",
        ],
        [paths.relative_to_base(written)],
    )


HANDLERS = {
    "provider check": handle_provider_check,
    "provider smoke": handle_provider_smoke,
    "input validate": handle_input_validate,
    "roster validate": handle_roster_validate,
    "snapshot build": handle_snapshot_build,
    "snapshot resume": handle_snapshot_resume,
    "snapshot approve": handle_snapshot_approve,
    "run plan": handle_run_plan,
    "run execute": handle_run_execute,
    "run reconcile": handle_run_reconcile,
    "review export": handle_review_export,
    "review import": handle_review_import,
    "evaluate export": handle_evaluate_export,
    "evaluate import": handle_evaluate_import,
}


def _report_run_id(command: Command, namespace: argparse.Namespace) -> str | None:
    """Revision eines revisionsgebundenen Kommandos, sofern sie gueltig ist."""
    if command.family == "evaluate":
        raw = str(getattr(namespace, "eval_id", "") or "").strip()
    elif command.requires_run_id:
        raw = str(getattr(namespace, "run_id", "") or "").strip()
    else:
        return None
    return raw if raw and RUN_ID_RE.match(raw) else None


def _abort_report(collector: ErrorCollector, command: Command | None,
                  namespace: argparse.Namespace, headline: str,
                  exit_code: ExitCode | None = None) -> int:
    """Abschlussweg der Top-Level-Faenger.

    Ein Abbruch oder ein interner Fehler schreibt denselben laufgebundenen
    Fehlerbericht wie der Regelweg - sonst erklaert nur der Terminaltext, warum
    ein Lauf mitten im Vorgang endete.
    """
    name = command.name if command is not None else PROGRAM
    run_id = _report_run_id(command, namespace) if command is not None else None
    artifacts, persist_finding = _persist(
        collector, name, run_id, command.family if command is not None else None)
    if persist_finding is not None:
        collector.extend([persist_finding])
    print_report(collector, headline=headline, artifacts=artifacts)
    return int(exit_code if exit_code is not None else collector.exit_code())


def run_command(command: Command, namespace: argparse.Namespace) -> int:
    collector = ErrorCollector()
    # Ein Reporter je Lauf; Fachmodule geben nie selbst aus (Statusplan S2).
    reporter = TerminalReporter()
    settings = _preflight(command, collector)
    handler = HANDLERS.get(command.name)
    stage_summary: list[str] = []
    stage_artifacts: list[str] = []
    stage_cost: float | None = None

    if command.provider and collector.has_blockers():
        collector.blocker(
            "CLI_PROVIDER_GATE_CLOSED", Stage.PROVIDER, command.name,
            "Providerpfad blockiert: die Providersettings sind unvollstaendig "
            "oder ungueltig.",
            "Fehlende COMMITTEE_MATCHER_*-Werte in config/settings.py "
            "ergaenzen; die providerfreien Familien input, roster, snapshot "
            "und review laufen unabhaengig davon.",
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
            # Ein Handler liefert (Zusammenfassung, Artefakte) und optional die
            # tatsaechlich angefallenen Providerkosten. Ohne dritten Wert bleibt
            # der Lauf providerfrei - so bleiben die bestehenden Handler gueltig.
            result = handler(command, namespace, collector, settings, reporter)
            stage_summary, stage_artifacts = result[0], result[1]
            stage_cost = result[2] if len(result) > 2 else None
        except MatcherError as exc:
            collector.extend(exc.findings)

    artifacts, persist_finding = _persist(
        collector, command.name, _report_run_id(command, namespace),
        command.family)
    if persist_finding is not None:
        collector.extend([persist_finding])
    artifacts = stage_artifacts + artifacts

    summary = [
        f"Kommando: {command.name}",
        f"Phase:    {command.phase}",
        f"Settings: offline geprueft"
        + (
            f" | Provider {'bereit' if settings.provider_ready else 'unvollstaendig'}"
            if settings is not None
            else " | Settings ungueltig"
        ),
    ] + stage_summary
    print_report(
        collector,
        headline=f"Committee Matcher V2 - {command.name}",
        summary_lines=summary,
        artifacts=artifacts,
        cost_usd=stage_cost,
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

    command: Command | None = None
    try:
        command = _lookup(namespace)
        return run_command(command, namespace)
    except KeyboardInterrupt:
        collector = ErrorCollector()
        collector.blocker(
            "CLI_ABORTED", Stage.CLI, command.name if command else PROGRAM,
            "Lauf durch Benutzerabbruch beendet.",
            "Lauf erneut starten; bereits geschriebene Artefakte bleiben unveraendert.",
            exit_code=ExitCode.USER_ABORT,
        )
        return _abort_report(
            collector, command, namespace,
            "Committee Matcher V2 - Abbruch", ExitCode.USER_ABORT)
    except MatcherError as exc:
        collector = ErrorCollector()
        collector.extend(exc.findings)
        return _abort_report(
            collector, command, namespace, "Committee Matcher V2 - blockiert")
    except Exception as exc:  # noqa: BLE001 - letzte Sicherung, Exit-Code 10
        collector = ErrorCollector()
        collector.blocker(
            "CLI_INTERNAL_ERROR", Stage.CLI, command.name if command else PROGRAM,
            f"Unerwarteter interner Fehler: {exc.__class__.__name__}: {exc}",
            "Traceback pruefen; dieser Pfad ist immer ein Implementierungsfehler.",
            exit_code=ExitCode.INTERNAL_ERROR,
        )
        return _abort_report(
            collector, command, namespace,
            "Committee Matcher V2 - interner Fehler", ExitCode.INTERNAL_ERROR)
