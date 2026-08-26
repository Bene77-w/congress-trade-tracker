"""Befunde, Fehlersammlung und Terminalbericht des Committee Matchers V2.

Plan 2.7 verlangt: jeder CLI-Lauf persistiert Fehler strukturiert und zeigt am
Ende immer einen nach Schweregrad und Stage gruppierten Terminalbericht mit
Fehlercode, betroffenem Objekt, Handlungshinweis, Kosten und Artefaktpfaden -
auch bei Abbruch oder null Fehlern.

Dieses Modul ist die kanonische Implementierung fuer den Paketcode. Die beiden
eigenstaendigen P0-Skripte behalten ihren eigenen Renderer, bis ihre Corpus-
logik in P3 nach ``input_corpus`` wandert; danach gibt es genau einen Renderer.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence, TextIO

from .status import ExitCode, worst_exit_code


class Severity(str, Enum):
    """Schweregrad eines Befunds; die Reihenfolge steuert den Bericht."""

    BLOCKER = "BLOCKER"
    WARNUNG = "WARNUNG"
    INFO = "INFO"


#: Ausgabereihenfolge im Terminalbericht.
SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.BLOCKER,
    Severity.WARNUNG,
    Severity.INFO,
)

#: Objektliste je Fehlercode im Terminalbericht begrenzen; der Rest wird
#: gezaehlt, damit ein Massenfehler den Bericht nicht unlesbar macht.
MAX_REPORTED_OBJECTS = 5

#: Berichtsbreite; identisch zu den P0-Werkzeugen.
REPORT_WIDTH = 78


class Stage(str, Enum):
    """Stages, denen Befunde zugeordnet werden.

    Die Stageliste folgt den CLI-Familien aus Plan 2.7 und waechst mit den
    Phasen; ``config`` und ``cli`` sind phasenuebergreifend.
    """

    CONFIG = "config"
    CLI = "cli"
    INPUT = "input"
    ROSTER = "roster"
    TAXONOMY = "taxonomy"
    PROVIDER = "provider"
    MAPPING = "mapping"
    WORKFLOW = "workflow"
    COST = "cost"
    ARTIFACTS = "artifacts"
    REVIEW = "review"
    EVALUATION = "evaluation"


#: Ausgabereihenfolge der Stages innerhalb eines Schweregrads.
STAGE_ORDER: tuple[Stage, ...] = tuple(Stage)


@dataclass(frozen=True)
class Finding:
    """Ein einzelner Befund.

    ``obj`` benennt das betroffene Objekt so, wie Bene es wiederfindet:
    projektrelativer Pfad, Unit-ID, Settingname oder Industry-ID.
    ``exit_code`` ueberschreibt den aus dem Schweregrad abgeleiteten Code, wenn
    der Fachvertrag einen bestimmten Code verlangt (etwa 6 bei Kostenstop).
    """

    severity: Severity
    code: str
    stage: Stage
    obj: str
    message: str
    hint: str
    line: int | None = None
    exit_code: ExitCode | None = None

    @property
    def location(self) -> str:
        return f"{self.obj}:{self.line}" if self.line else self.obj

    def as_record(self) -> dict[str, object]:
        """Strukturierte Form fuer die Persistenz im Fehlerbericht."""
        return {
            "severity": self.severity.value,
            "code": self.code,
            "stage": self.stage.value,
            "object": self.obj,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
            "exit_code": int(self.exit_code) if self.exit_code is not None else None,
        }


class MatcherError(Exception):
    """Basisfehler mit Befunden; wird von der CLI in einen Bericht uebersetzt."""

    def __init__(self, findings: Sequence[Finding], message: str | None = None):
        self.findings: tuple[Finding, ...] = tuple(findings)
        text = message or "; ".join(f"{f.code} ({f.obj})" for f in self.findings)
        super().__init__(text or self.__class__.__name__)

    @property
    def exit_code(self) -> ExitCode:
        collector = ErrorCollector()
        collector.extend(self.findings)
        return collector.exit_code()


@dataclass
class ErrorCollector:
    """Sammelt Befunde eines Laufs und leitet Exit-Code und Bericht ab."""

    findings: list[Finding] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        code: str,
        stage: Stage,
        obj: str,
        message: str,
        hint: str,
        line: int | None = None,
        exit_code: ExitCode | None = None,
    ) -> Finding:
        finding = Finding(
            severity=severity,
            code=code,
            stage=stage,
            obj=obj,
            message=message,
            hint=hint,
            line=line,
            exit_code=exit_code,
        )
        self.findings.append(finding)
        return finding

    def blocker(self, code: str, stage: Stage, obj: str, message: str, hint: str,
                line: int | None = None, exit_code: ExitCode | None = None) -> Finding:
        return self.add(Severity.BLOCKER, code, stage, obj, message, hint, line, exit_code)

    def warning(self, code: str, stage: Stage, obj: str, message: str, hint: str,
                line: int | None = None) -> Finding:
        return self.add(Severity.WARNUNG, code, stage, obj, message, hint, line)

    def info(self, code: str, stage: Stage, obj: str, message: str, hint: str,
             line: int | None = None) -> Finding:
        return self.add(Severity.INFO, code, stage, obj, message, hint, line)

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]

    @property
    def blockers(self) -> list[Finding]:
        return self.by_severity(Severity.BLOCKER)

    @property
    def warnings(self) -> list[Finding]:
        return self.by_severity(Severity.WARNUNG)

    def has_blockers(self) -> bool:
        return bool(self.blockers)

    def exit_code(self, default: ExitCode = ExitCode.SUCCESS) -> ExitCode:
        """Leitet den Exit-Code ab.

        Blocker gewinnen immer; ohne expliziten Code gilt ``4`` (Preflight
        blockiert). Ohne Blocker ergeben Warnungen ``2``, sonst ``default``.
        """
        blockers = self.blockers
        if blockers:
            codes = [f.exit_code or ExitCode.PREFLIGHT_BLOCKED for f in blockers]
            return worst_exit_code(codes)
        if self.warnings:
            return worst_exit_code([default, ExitCode.SUCCESS_WITH_WARNINGS])
        return default

    def as_records(self) -> list[dict[str, object]]:
        return [f.as_record() for f in self.findings]

    def write_json(self, path: Path) -> Path:
        """Persistiert die Befunde geprueft und atomar.

        Der Schreibweg laeuft immer ueber die Pfadwache; ein Aufrufer kann die
        Grenze damit nicht versehentlich umgehen. Der Import erfolgt lokal,
        weil ``paths`` seinerseits ``errors`` verwendet.
        """
        from .paths import write_text_atomic

        payload = {"findings": self.as_records()}
        return write_text_atomic(
            path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    def raise_if_blocked(self, message: str | None = None) -> None:
        if self.has_blockers():
            raise MatcherError(self.blockers, message)


class StatusKind(str, Enum):
    """Statusarten des Live-Reporters (Terminalstatusplan S1)."""

    START = "START"
    OK = "OK"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    WAIT = "WAIT"


#: Diese Statusarten gehen auf stderr, alle uebrigen auf stdout.
STDERR_KINDS: frozenset[StatusKind] = frozenset({StatusKind.WARN, StatusKind.ERROR})

#: Laengengrenze je Feldwert; verhindert, dass Prompt- oder Responsetexte
#: versehentlich vollstaendig im Terminal landen. Kuerzung bleibt sichtbar.
MAX_FIELD_CHARS = 60

#: Feldnamen, die auf ein Geheimnis hindeuten; ihr Gebrauch ist ein
#: Programmierfehler und scheitert sofort, statt still zu maskieren.
SECRET_FIELD_PARTS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "apikey",
)

#: Feldnamensegmente, die fuer sich genommen ein Geheimnis benennen.
SECRET_FIELD_SEGMENTS: frozenset[str] = frozenset({"key", "keys", "auth"})


def _reject_secret_field(name: str) -> None:
    flat = name.replace("_", "").replace("-", "").lower()
    segments = set(name.lower().replace("-", "_").split("_"))
    if segments & SECRET_FIELD_SEGMENTS or any(part in flat for part in SECRET_FIELD_PARTS):
        raise ValueError(f"Statusfeld {name!r} darf kein Geheimnis tragen")


def _field_value(value: object) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".") or "0"
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > MAX_FIELD_CHARS:
        text = text[: MAX_FIELD_CHARS - 3] + "..."
    return text.replace("|", "/")


def status_line(kind: StatusKind, stage: Stage, **fields: object) -> str:
    """Baut eine Statuszeile ``[KIND] stage | feld=wert | marker``.

    Ein Feldwert ``None`` erzeugt einen Marker ohne Wert (``no_mapping``);
    die Reihenfolge der Schluesselwortargumente bleibt erhalten.
    """
    parts = [f"[{kind.value}] {stage.value}"]
    for name, value in fields.items():
        _reject_secret_field(name)
        parts.append(name if value is None else f"{name}={_field_value(value)}")
    return " | ".join(parts)


@dataclass
class TerminalReporter:
    """Einziger Live-Renderer; Fachmodule geben nie selbst mit ``print`` aus.

    ``enabled=False`` unterdrueckt die Ausgabe, baut die Zeile aber weiter und
    liefert sie zurueck, damit Aufrufer und Tests denselben Pfad nehmen.
    """

    enabled: bool = True
    stdout: TextIO | None = None
    stderr: TextIO | None = None

    def emit(self, kind: StatusKind, stage: Stage, **fields: object) -> str:
        line = status_line(kind, stage, **fields)
        if not self.enabled:
            return line
        target = self.stderr if kind in STDERR_KINDS else self.stdout
        if target is None:
            target = sys.stderr if kind in STDERR_KINDS else sys.stdout
        print(line, file=target, flush=True)
        return line

    def start(self, stage: Stage, **fields: object) -> str:
        return self.emit(StatusKind.START, stage, **fields)

    def ok(self, stage: Stage, **fields: object) -> str:
        return self.emit(StatusKind.OK, stage, **fields)

    def info(self, stage: Stage, **fields: object) -> str:
        return self.emit(StatusKind.INFO, stage, **fields)

    def warn(self, stage: Stage, **fields: object) -> str:
        return self.emit(StatusKind.WARN, stage, **fields)

    def error(self, stage: Stage, **fields: object) -> str:
        return self.emit(StatusKind.ERROR, stage, **fields)

    def wait(self, stage: Stage, **fields: object) -> str:
        return self.emit(StatusKind.WAIT, stage, **fields)


#: Stiller Reporter fuer Aufrufer ohne Terminal (Tests, Bibliotheksnutzung).
SILENT_REPORTER = TerminalReporter(enabled=False)


def _grouped(findings: Sequence[Finding], severity: Severity) -> dict[Stage, dict[str, list[Finding]]]:
    grouped: dict[Stage, dict[str, list[Finding]]] = {}
    for finding in findings:
        if finding.severity is not severity:
            continue
        grouped.setdefault(finding.stage, {}).setdefault(finding.code, []).append(finding)
    return grouped


def render_report(
    collector: ErrorCollector,
    *,
    headline: str,
    summary_lines: Sequence[str] = (),
    artifacts: Sequence[str] = (),
    cost_usd: float | None = None,
    exit_code: ExitCode | None = None,
) -> str:
    """Baut den Terminalbericht als Text.

    Der Bericht erscheint immer, auch ohne Befunde und bei Abbruch.
    ``cost_usd=None`` markiert eine providerfreie Phase.
    """
    rule = "-" * REPORT_WIDTH
    lines: list[str] = [rule, headline, rule]
    lines.extend(summary_lines)
    if summary_lines:
        lines.append(rule)

    if not collector.findings:
        lines.append("Befunde: keine")
    for severity in SEVERITY_ORDER:
        grouped = _grouped(collector.findings, severity)
        if not grouped:
            continue
        total = sum(len(g) for stage in grouped.values() for g in stage.values())
        lines.append(f"[{severity.value}] {total}")
        for stage in STAGE_ORDER:
            by_code = grouped.get(stage)
            if not by_code:
                continue
            lines.append(f"  Stage {stage.value}")
            for code, group in by_code.items():
                head = group[0]
                lines.append(f"    {code:<30} {len(group)}x")
                lines.append(f"      {head.message}")
                shown = [f.location for f in group[:MAX_REPORTED_OBJECTS]]
                rest = len(group) - len(shown)
                suffix = f" (+{rest} weitere)" if rest else ""
                lines.append(f"      Objekte: {', '.join(shown)}{suffix}")
                lines.append(f"      -> {head.hint}")

    lines.append(rule)
    if artifacts:
        lines.append("Artefakte:")
        lines.extend(f"  {item}" for item in artifacts)
    else:
        lines.append("Artefakte: keine")
    if cost_usd is None:
        lines.append("Kosten: 0.00 USD (providerfreie Phase, keine Modellaufrufe)")
    else:
        lines.append(f"Kosten: {cost_usd:.6f} USD")
    resolved = collector.exit_code() if exit_code is None else exit_code
    lines.append(f"Exit-Code: {int(resolved)} ({resolved.name.lower()})")
    return "\n".join(lines)


def print_report(collector: ErrorCollector, **kwargs: object) -> str:
    """Gibt den Bericht aus und liefert ihn fuer Tests zurueck."""
    text = render_report(collector, **kwargs)  # type: ignore[arg-type]
    print(text)
    return text
