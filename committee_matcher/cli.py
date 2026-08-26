"""CLI-Skelett des Committee Matchers V2.

Plan 2.7 legt die CLI-Familien und die Exit-Codes fest. Dieses Modul haelt
beides zusammen: es kennt alle Familien bereits vollstaendig, fuehrt den
Offline-Preflight der Settings aus, blockiert jeden Providerpfad mit
unvollstaendigen Providersettings und gibt in jedem Fall den Terminalbericht
aus.

Stages, die erst in spaeteren Phasen entstehen, melden ``CLI_STAGE_NOT_IMPLEMENTED``
und beenden mit Exit-Code 4 (Preflight blockiert). Das ist bewusst kein neuer
Exit-Code: die Codeliste ist Vertrag und wird nicht erweitert.

Es gibt weiterhin kein ``promote``, ``finalize``, ``research`` oder LLM-``review``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Mapping, Sequence

from . import SCAFFOLD_VERSION
from . import (
    artifacts,
    input_corpus,
    mapping,
    paths,
    prompts,
    provider,
    roster_coverage,
    taxonomy_snapshot,
    taxonomy_source,
    units as unit_module,
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
    reporter: TerminalReporter,
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
    reporter: TerminalReporter,
) -> tuple[list[str], list[str]]:
    """Roster-Coverage gegen die Unit-Sollmenge pruefen (Plan 2.3)."""
    result = input_corpus.load_documents(collector, settings.offline)
    units = unit_module.build_units(collector, result.documents)
    rows = roster_coverage.run_coverage(collector, units)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.coverage_status.value] = counts.get(row.coverage_status.value, 0) + 1
    without_roster = roster_coverage.report_gaps(collector, units, rows)
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
    client = taxonomy_source.YFinanceClient(settings.offline)
    result = taxonomy_snapshot.build_snapshot(
        collector, client, run_id=run_id, resume=resume
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
    digest = taxonomy_snapshot.approve_snapshot(collector, run_id)
    if digest is None:
        return [f"Run:      {run_id}", "Freigabe: abgelehnt"], []
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


def _selection(namespace: argparse.Namespace) -> dict[str, object]:
    """Die Zielangabe des Laufs in der Form, in der sie im Manifest steht."""
    return {
        "chamber": getattr(namespace, "chamber", None) or None,
        "committee": getattr(namespace, "committee", None) or None,
        "unit_ids": sorted(set(getattr(namespace, "unit_id", []) or [])),
    }


def _targets(collector: ErrorCollector, units: Sequence[MatcherUnit],
             selection: Mapping[str, object]) -> tuple[MatcherUnit, ...] | None:
    """Waehlt die Units des Entwicklungsruns aus.

    Ohne Zielangabe laeuft nichts: ein filterloser Lauf ueber die Sollmenge ist
    der Full-Run und braucht die Qualifikation aus P10. Ein versehentlich
    filterloser ``run execute`` waere der teuerste Bedienfehler des Werkzeugs.
    """
    chamber = selection.get("chamber")
    committee = selection.get("committee")
    unit_ids = tuple(selection.get("unit_ids") or ())
    if not (chamber or committee or unit_ids):
        _run_blocker(
            collector, "CLI_RUN_TARGET_REQUIRED", "run",
            "Ein Entwicklungsrun braucht eine Zielangabe.",
            "--chamber, --committee oder --unit-id setzen; der filterlose Lauf "
            "ueber die gesamte Sollmenge ist 'full-run' und ab P11 verfuegbar.",
        )
        return None

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
                 ) -> RunPreparation | None:
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

    selection = _selection(namespace)
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
    attempts = 1 + max(0, settings.offline.workflow_retries)
    for attempt in range(attempts):
        prefix = "" if attempt == 0 else f"retry{attempt}-"
        try:
            return mapping.map_batch(
                audited, batch, prepared.snapshot,
                mapping_prompt=prepared.mapping_prompt,
                repair_prompt=prepared.repair_prompt,
                offline=settings.offline,
                provider_settings=prepared.provider_settings,
                call_prefix=prefix,
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
            retryable = exc.retryable and not exc.outcome_unknown
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
            reporter.error(Stage.MAPPING, unit=unit.unit_id, failed=None,
                           reasons=len(reasons))
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


def handle_run_execute(
    command: Command, namespace: argparse.Namespace,
    collector: ErrorCollector, settings: MatcherSettings,
    reporter: TerminalReporter,
) -> tuple[list[str], list[str], float]:
    """Fuehrt den geplanten Entwicklungsrun aus (Plan 2.6); kostenpflichtig."""
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

    prepared = _prepare_run(command, namespace, collector, settings, reporter, run_id)
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

    guard = workflow.CostGuard(
        budget_usd=prepared.budget_usd,
        rates=workflow.CostRates.from_settings(prepared.provider_settings),
        enabled=settings.offline.cost_guard,
    )
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
            inner=adapter, run_id=run_id, guard=guard, offline=settings.offline)
        state.state = WorkflowState.RUNNING
        state.write()

        for index, batch in enumerate(batches, start=1):
            reporter.start(Stage.MAPPING, run=run_id, batch=batch.batch_id,
                           units=len(batch.units), position=f"{index}/{len(batches)}")
            outcome, stopped = _run_batch(
                collector, audited, batch, prepared, settings, reporter)
            if outcome is not None:
                _record_outcome(state, outcome, keys, reporter)
                reporter.ok(Stage.MAPPING, batch=batch.batch_id,
                            repairs=outcome.repairs,
                            cost_usd=guard.spent_usd)
            state.write()
            if stopped is not None:
                break

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
                run_id=run_id, units=prepared.targets, state=state,
                snapshot=prepared.snapshot, manifest=prepared.manifest, guard=guard,
                roster=rows, roster_occurrences=occurrences, unknown=unknown,
                audited_calls=len(audited.audited) if audited is not None else 0,
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
    reporter.emit(
        StatusKind.OK if not stopped else StatusKind.ERROR,
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


#: Bereits implementierte Aktionen; alles andere meldet die Zielphase.
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
}


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
            "ergaenzen; die providerfreien Familien input, roster, snapshot, "
            "review und report laufen unabhaengig davon.",
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

    artifacts, persist_finding = _persist(collector, command.name)
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
