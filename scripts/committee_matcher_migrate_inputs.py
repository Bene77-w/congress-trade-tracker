#!/usr/bin/env python3
"""
Committee Matcher V2 - P0-Inputmigration (Version input-basis-v1)

Migriert die 46 kanonischen Mapping-Markdowns unter
``data/committee_matcher_inputs/md/`` auf den Inputvertrag aus
``organisation/committee_jurisdiction_input_template_codex.md``:

- ergaenzt ``unit_input_basis`` im Frontmatter
  (``committee: jurisdiction_text``; je Subcommittee ``jurisdiction_text``
  oder ``name_only``),
- entfernt die zwoelf alten ``input_basis:``-Frontmatterfelder
  (Legacywerte ``committee_direct`` / ``summarized_mandate``),
- entfernt die acht HTML-Ersatzmarker ``<!-- input_basis: name_only -->``.

Jurisdiktionstexte, Identitaeten und alle uebrigen Frontmatterzeilen bleiben
byteweise unveraendert. Das Skript ist idempotent: nach ``--apply`` meldet
``--check`` keine ausstehenden Aenderungen mehr, weil ein bereits vorhandenes
``unit_input_basis`` die massgebliche Quelle der Basiswerte ist und die
HTML-Marker nur beim Erstlauf ausgewertet werden.

Modi
    --check     Sollzustand berechnen, nichts schreiben, Abweichungen melden
    --apply     Sollzustand atomar schreiben, danach vollstaendig validieren
    --report    Klassifikation je Datei ausgeben (Units, Basiswerte, Delta)

Exit-Codes (Teilmenge der verbindlichen Plan-Matrix)
    0   erfolgreich; bei --check ohne ausstehende Aenderungen
    4   Input-Preflight blockiert: Strukturfehler oder ausstehende Migration
    10  unerwarteter interner Fehler

Beispiele
    python scripts/committee_matcher_migrate_inputs.py --check
    python scripts/committee_matcher_migrate_inputs.py --apply
    python scripts/committee_matcher_migrate_inputs.py --report
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent

MIGRATION_VERSION = "input-basis-v1"

DEFAULT_INPUT_ROOT = BASE_DIR / "data" / "committee_matcher_inputs"

# Die beiden Sammel-MDs sind Quelltexte (Rolle source_only) und erzeugen keine
# Mapping-Units; sie werden nicht migriert.
SOURCE_ONLY_MD = frozenset(
    {
        "house-rule-x-committee-jurisdictions.md",
        "senate-rule-xxv-committee-jurisdictions.md",
    }
)

# Reservierter H2-Abschnitt; nie Mapping-Unit und nie Promptkontext.
COMMENT_HEADING = "Zusätzliche Kommentare"
JURISDICTION_HEADING = "Committee jurisdiction"

BASIS_JURISDICTION_TEXT = "jurisdiction_text"
BASIS_NAME_ONLY = "name_only"
CANONICAL_BASIS_VALUES = (BASIS_JURISDICTION_TEXT, BASIS_NAME_ONLY)

LEGACY_FIELD_RE = re.compile(r"^input_basis:\s*(\S+)\s*$")
LEGACY_MARKER_RE = re.compile(r"^\s*<!--\s*input_basis:\s*name_only\s*-->\s*$")
H2_RE = re.compile(r"^##\s+(.*?)\s*$", re.MULTILINE)
H1_RE = re.compile(r"^#\s+(.*?)\s*$", re.MULTILINE)

CANONICAL_FRONTMATTER_KEYS = (
    "schema_version",
    "congress",
    "chamber",
    "committee",
    "committee_type",
    "source_url",
    "retrieved_at",
    "source_metadata_status",
    "source_files",
)
VALID_CHAMBERS = ("house", "senate", "joint")
VALID_METADATA_STATUS = ("complete", "pending")

# Verifizierte Sollwerte des freigegebenen Corpus (Plan Abschnitt 2.1/2.2).
EXPECTED_MAPPING_FILES = 46
EXPECTED_LEGACY_FIELDS = 12
EXPECTED_LEGACY_MARKERS = 8
EXPECTED_NAME_ONLY_UNITS = 8
EXPECTED_UNITS_SUBCOMMITTEE = 179
EXPECTED_UNITS_PARENT_CORE = 31
EXPECTED_UNITS_DIRECT = 15
EXPECTED_UNITS_TOTAL = 225

SEVERITY_ORDER = ("BLOCKER", "WARNUNG", "INFO")

# Objektliste je Fehlercode im Terminalbericht begrenzen; der Rest wird gezaehlt.
MAX_REPORTED_OBJECTS = 5


@dataclass
class Finding:
    """Einheitlicher Befund fuer den Terminalbericht."""

    severity: str
    code: str
    obj: str
    message: str
    hint: str
    line: int | None = None

    @property
    def location(self) -> str:
        return f"{self.obj}:{self.line}" if self.line else self.obj


@dataclass
class FileReport:
    """Migrationsergebnis einer einzelnen Mapping-Markdown."""

    path: Path
    chamber: str = ""
    committee: str = ""
    unit_basis: "OrderedDict[str, str]" = field(default_factory=OrderedDict)
    legacy_field: str | None = None
    marker_count: int = 0
    had_basis: bool = False
    changed: bool = False
    target_text: str = ""
    findings: List[Finding] = field(default_factory=list)

    @property
    def subcommittee_count(self) -> int:
        return len(self.unit_basis)

    @property
    def name_only_count(self) -> int:
        return sum(1 for v in self.unit_basis.values() if v == BASIS_NAME_ONLY)

    @property
    def unit_count(self) -> int:
        # Ohne Subcommittees: genau eine committee_direct-Unit.
        # Mit Subcommittees: je Subcommittee eine Unit plus committee_parent_core.
        return self.subcommittee_count + 1


class MigrationBlocked(Exception):
    """Strukturfehler, der eine Datei unmigrierbar macht."""

    def __init__(self, finding: Finding) -> None:
        super().__init__(finding.message)
        self.finding = finding


def split_frontmatter(text: str, obj: str) -> Tuple[str, str, int]:
    """Zerlegt eine Markdown in (Frontmattertext, Body, Body-Startzeile)."""
    if not text.startswith("---\n"):
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_NO_FRONTMATTER",
                obj,
                "Datei beginnt nicht mit einem YAML-Frontmatterblock",
                "Frontmatter mit '---' in Zeile 1 ergaenzen",
                line=1,
            )
        )
    end = text.find("\n---\n", 3)
    if end == -1:
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_UNCLOSED_FRONTMATTER",
                obj,
                "Frontmatterblock ist nicht mit '---' geschlossen",
                "Abschlusszeile '---' nach dem Frontmatter ergaenzen",
                line=1,
            )
        )
    frontmatter = text[4 : end + 1]
    body = text[end + 5 :]
    body_start_line = frontmatter.count("\n") + 3
    return frontmatter, body, body_start_line


def parse_frontmatter(frontmatter: str, obj: str) -> dict:
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:  # pragma: no cover - defensiv
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_FRONTMATTER_YAML",
                obj,
                f"Frontmatter ist kein gueltiges YAML: {exc}",
                "YAML-Syntax im Frontmatter korrigieren",
                line=2,
            )
        ) from exc
    if not isinstance(data, dict):
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_FRONTMATTER_TYPE",
                obj,
                "Frontmatter ist kein Mapping",
                "Frontmatter als 'key: value'-Mapping schreiben",
                line=2,
            )
        )
    return data


def heading_sections(body: str, obj: str) -> "OrderedDict[str, Tuple[str, int]]":
    """Liefert alle H2-Abschnitte ausser dem Kommentarabschnitt.

    Rueckgabe: Name -> (Abschnittstext ohne Ueberschrift, Zeilennummer im Body).
    """
    h1 = H1_RE.findall(body)
    if h1 != [JURISDICTION_HEADING]:
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_H1_CONTRACT",
                obj,
                f"Erwartet genau eine H1 '# {JURISDICTION_HEADING}', gefunden {h1}",
                "H1-Ueberschrift auf den Template-Wortlaut bringen",
            )
        )

    matches = list(H2_RE.finditer(body))
    sections: "OrderedDict[str, Tuple[str, int]]" = OrderedDict()
    comment_seen = False
    for idx, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        line = body.count("\n", 0, match.start()) + 1
        if name == COMMENT_HEADING:
            comment_seen = True
            continue
        if comment_seen:
            raise MigrationBlocked(
                Finding(
                    "BLOCKER",
                    "MIG_SECTION_AFTER_COMMENTS",
                    obj,
                    f"Subcommittee-Abschnitt '{name}' steht hinter '{COMMENT_HEADING}'",
                    f"'## {COMMENT_HEADING}' als letzten Abschnitt fuehren",
                    line=line,
                )
            )
        if name in sections:
            raise MigrationBlocked(
                Finding(
                    "BLOCKER",
                    "MIG_DUPLICATE_SUBCOMMITTEE",
                    obj,
                    f"Subcommittee-Abschnitt '{name}' kommt mehrfach vor",
                    "Doppelte H2-Ueberschrift entfernen oder eindeutig benennen",
                    line=line,
                )
            )
        sections[name] = (body[start:end], line)
    return sections


def derive_unit_basis(
    existing: object,
    sections: "OrderedDict[str, Tuple[str, int]]",
    obj: str,
) -> "OrderedDict[str, str]":
    """Bestimmt die Sollwerte von ``unit_input_basis.subcommittees``.

    Vorhandene Werte sind massgeblich (Idempotenz). Fehlen sie, entscheiden die
    HTML-Ersatzmarker; alle uebrigen Abschnitte sind ``jurisdiction_text``.
    Ein Widerspruch zwischen bestehendem Wert und Marker blockiert.
    """
    marker_based: "OrderedDict[str, str]" = OrderedDict()
    for name, (text, _line) in sections.items():
        has_marker = any(LEGACY_MARKER_RE.match(l) for l in text.splitlines())
        marker_based[name] = BASIS_NAME_ONLY if has_marker else BASIS_JURISDICTION_TEXT

    if existing is None:
        return marker_based

    if not isinstance(existing, dict):
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_BASIS_TYPE",
                obj,
                "'unit_input_basis' ist kein Mapping",
                "Frontmatterblock 'unit_input_basis' auf Template-Form bringen",
            )
        )

    committee_value = existing.get("committee")
    if committee_value not in (None, BASIS_JURISDICTION_TEXT):
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_BASIS_COMMITTEE",
                obj,
                f"'unit_input_basis.committee' ist '{committee_value}', erwartet "
                f"'{BASIS_JURISDICTION_TEXT}'",
                "Wert auf 'jurisdiction_text' setzen",
            )
        )

    subs = existing.get("subcommittees") or {}
    if not isinstance(subs, dict):
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_BASIS_SUBS_TYPE",
                obj,
                "'unit_input_basis.subcommittees' ist kein Mapping",
                "Subcommittee-Basiswerte als Mapping 'Name: wert' fuehren",
            )
        )

    missing = [n for n in sections if n not in subs]
    extra = [n for n in subs if n not in sections]
    if missing or extra:
        raise MigrationBlocked(
            Finding(
                "BLOCKER",
                "MIG_BASIS_KEY_MISMATCH",
                obj,
                "unit_input_basis.subcommittees stimmt nicht mit der H2-Menge ueberein; "
                f"fehlend={missing}; zusaetzlich={extra}",
                "Keys exakt an die H2-Ueberschriften angleichen",
            )
        )

    resolved: "OrderedDict[str, str]" = OrderedDict()
    for name in sections:
        value = subs[name]
        if value not in CANONICAL_BASIS_VALUES:
            raise MigrationBlocked(
                Finding(
                    "BLOCKER",
                    "MIG_BASIS_VALUE",
                    obj,
                    f"Basiswert '{value}' fuer '{name}' ist nicht kanonisch",
                    f"Nur {' oder '.join(CANONICAL_BASIS_VALUES)} verwenden",
                    line=sections[name][1],
                )
            )
        if value != marker_based[name] and marker_based[name] == BASIS_NAME_ONLY:
            raise MigrationBlocked(
                Finding(
                    "BLOCKER",
                    "MIG_BASIS_MARKER_CONFLICT",
                    obj,
                    f"'{name}' traegt den name_only-Marker, ist im Frontmatter aber "
                    f"'{value}'",
                    "Widerspruch manuell aufloesen; das Skript raet nicht",
                    line=sections[name][1],
                )
            )
        resolved[name] = value
    return resolved


def render_unit_basis_block(unit_basis: "OrderedDict[str, str]") -> List[str]:
    """Rendert den ``unit_input_basis``-Block in stabiler Blockform.

    Subcommittee-Keys werden ausnahmslos doppelt gequotet. Das haelt die Form
    unabhaengig von Sonderzeichen und Namenslaenge deterministisch; PyYAML
    wuerde Namen ueber 128 Zeichen sonst in die Explicit-Key-Form ``? name``
    ausweichen lassen, die sich von Hand kaum korrekt pflegen laesst.
    """
    lines = [
        "unit_input_basis:",
        f"  committee: {BASIS_JURISDICTION_TEXT}",
    ]
    if not unit_basis:
        lines.append("  subcommittees: {}")
        return lines
    lines.append("  subcommittees:")
    for name, value in unit_basis.items():
        # json.dumps liefert einen gueltigen YAML-Double-Quoted-Scalar
        # einschliesslich korrektem Escaping von " und \\.
        lines.append(f"    {json.dumps(name, ensure_ascii=False)}: {value}")
    return lines


def strip_existing_basis_block(lines: Sequence[str]) -> List[str]:
    """Entfernt einen vorhandenen ``unit_input_basis``-Block samt Kindzeilen."""
    out: List[str] = []
    skipping = False
    for line in lines:
        if skipping:
            if line.startswith((" ", "\t")) or not line.strip():
                continue
            skipping = False
        if line.startswith("unit_input_basis:"):
            skipping = True
            continue
        out.append(line)
    return out


def build_target(path: Path, text: str, input_root: Path) -> FileReport:
    try:
        obj = str(path.relative_to(BASE_DIR))
    except ValueError:
        obj = str(path)
    report = FileReport(path=path)

    frontmatter, body, _body_start = split_frontmatter(text, obj)
    fm_data = parse_frontmatter(frontmatter, obj)

    for key in CANONICAL_FRONTMATTER_KEYS:
        if key not in fm_data:
            raise MigrationBlocked(
                Finding(
                    "BLOCKER",
                    "MIG_FRONTMATTER_KEY_MISSING",
                    obj,
                    f"Pflichtfeld '{key}' fehlt im Frontmatter",
                    "Feld gemaess Inputtemplate ergaenzen",
                    line=2,
                )
            )

    report.chamber = str(fm_data.get("chamber", ""))
    report.committee = str(fm_data.get("committee", ""))
    report.had_basis = "unit_input_basis" in fm_data
    sections = heading_sections(body, obj)
    report.unit_basis = derive_unit_basis(fm_data.get("unit_input_basis"), sections, obj)

    fm_lines = frontmatter.rstrip("\n").split("\n")
    kept: List[str] = []
    for line in fm_lines:
        legacy = LEGACY_FIELD_RE.match(line)
        if legacy:
            report.legacy_field = legacy.group(1)
            continue
        kept.append(line)
    kept = strip_existing_basis_block(kept)
    kept.extend(render_unit_basis_block(report.unit_basis))

    body_lines = body.split("\n")
    new_body_lines = []
    for line in body_lines:
        if LEGACY_MARKER_RE.match(line):
            report.marker_count += 1
            continue
        new_body_lines.append(line)

    report.target_text = "---\n" + "\n".join(kept) + "\n---\n" + "\n".join(new_body_lines)
    report.changed = report.target_text != text
    return report


def validate_migrated(report: FileReport, text: str, obj: str) -> List[Finding]:
    """Prueft den migrierten Sollstand einer Datei fachlich und strukturell."""
    findings: List[Finding] = []
    frontmatter, body, _ = split_frontmatter(text, obj)
    fm_data = parse_frontmatter(frontmatter, obj)

    if "input_basis" in fm_data:
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_LEGACY_FIELD",
                obj,
                "Legacyfeld 'input_basis' ist weiterhin vorhanden",
                "Migration mit --apply erneut ausfuehren",
            )
        )
    for idx, line in enumerate(body.split("\n"), start=1):
        if LEGACY_MARKER_RE.match(line):
            findings.append(
                Finding(
                    "BLOCKER",
                    "VAL_LEGACY_MARKER",
                    obj,
                    "HTML-Ersatzmarker 'input_basis: name_only' ist weiterhin vorhanden",
                    "Migration mit --apply erneut ausfuehren",
                    line=idx,
                )
            )

    basis = fm_data.get("unit_input_basis")
    if not isinstance(basis, dict):
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_BASIS_MISSING",
                obj,
                "'unit_input_basis' fehlt oder ist kein Mapping",
                "Migration mit --apply ausfuehren",
            )
        )
        return findings

    if basis.get("committee") != BASIS_JURISDICTION_TEXT:
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_BASIS_COMMITTEE",
                obj,
                f"'unit_input_basis.committee' ist '{basis.get('committee')}'",
                "Wert auf 'jurisdiction_text' setzen",
            )
        )

    subs = basis.get("subcommittees")
    if not isinstance(subs, dict):
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_BASIS_SUBS",
                obj,
                "'unit_input_basis.subcommittees' fehlt oder ist kein Mapping",
                "Block gemaess Inputtemplate schreiben",
            )
        )
        return findings

    sections = heading_sections(body, obj)
    if list(subs.keys()) != list(sections.keys()):
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_BASIS_KEYS",
                obj,
                "Subcommittee-Keys weichen von der H2-Menge ab: "
                f"frontmatter={list(subs.keys())}; h2={list(sections.keys())}",
                "Keys exakt an die H2-Ueberschriften angleichen",
            )
        )
    for name, value in subs.items():
        if value not in CANONICAL_BASIS_VALUES:
            findings.append(
                Finding(
                    "BLOCKER",
                    "VAL_BASIS_VALUE",
                    obj,
                    f"Basiswert '{value}' fuer '{name}' ist nicht kanonisch",
                    f"Nur {' oder '.join(CANONICAL_BASIS_VALUES)} verwenden",
                )
            )

    chamber = fm_data.get("chamber")
    if chamber not in VALID_CHAMBERS:
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_CHAMBER",
                obj,
                f"chamber '{chamber}' ist unzulaessig",
                f"Einen Wert aus {VALID_CHAMBERS} setzen",
            )
        )
    elif not report.path.name.startswith(f"{chamber}_"):
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_CHAMBER_FILENAME",
                obj,
                f"Dateiname passt nicht zu chamber '{chamber}'",
                "Dateiname oder chamber angleichen; das Skript raet nicht",
            )
        )

    status = fm_data.get("source_metadata_status")
    if status not in VALID_METADATA_STATUS:
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_METADATA_STATUS",
                obj,
                f"source_metadata_status '{status}' ist unzulaessig",
                f"Einen Wert aus {VALID_METADATA_STATUS} setzen",
            )
        )
    elif status == "complete" and not (fm_data.get("source_url") and fm_data.get("retrieved_at")):
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_METADATA_INCOMPLETE",
                obj,
                "source_metadata_status ist 'complete', aber source_url/retrieved_at fehlen",
                "Status auf 'pending' setzen oder Metadaten ergaenzen; nie erfinden",
            )
        )
    elif status == "pending":
        findings.append(
            Finding(
                "INFO",
                "VAL_METADATA_PENDING",
                obj,
                "Quellmetadaten unvollstaendig (source_metadata_status=pending)",
                "Sichtbar belassen; blockiert die inhaltlich vollstaendige Datei nicht",
            )
        )

    source_files = fm_data.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        findings.append(
            Finding(
                "BLOCKER",
                "VAL_SOURCE_FILES",
                obj,
                "source_files fehlt oder ist leer",
                "Mindestens eine Rohquelle referenzieren",
            )
        )
    return findings


def discover_mapping_files(input_root: Path) -> List[Path]:
    md_dir = input_root / "md"
    return sorted(p for p in md_dir.glob("*.md") if p.name not in SOURCE_ONLY_MD)


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def state_coherence_findings(
    reports: Sequence[FileReport], strict_legacy: bool
) -> List[Finding]:
    """Blockiert jeden Mischzustand aus migrierten und unmigrierten Dateien.

    Ohne diese Pruefung wuerde eine Datei, deren ``name_only``-Marker bereits
    von Hand entfernt wurde, die aber noch kein ``unit_input_basis`` traegt,
    still als ``jurisdiction_text`` klassifiziert - also geraten. Ein solcher
    Teilstand ist immer ein Mischzustand und wird deshalb vor jedem Schreiben
    abgewiesen.

    Restgrenze: Ein Corpus, dem VOR der Erstmigration saemtliche Marker von Hand
    entfernt wurden, ist von einem Corpus ohne ``name_only``-Faelle nicht
    unterscheidbar. Fuer den kanonischen Inputroot faengt das die eingefrorene
    8er-Sollmenge ab, die dank des Preflights vor dem Schreiben greift.
    """
    findings: List[Finding] = []
    if not reports:
        return findings

    migrated = [r for r in reports if r.had_basis]
    unmigrated = [r for r in reports if not r.had_basis]
    if migrated and unmigrated:
        findings.append(
            Finding(
                "BLOCKER",
                "COR_STATE_MIXED",
                "corpus",
                f"Mischzustand: {len(migrated)} Dateien mit unit_input_basis, "
                f"{len(unmigrated)} ohne",
                "Teilstand manuell aufloesen; das Skript raet keine Basiswerte",
            )
        )
        return findings

    legacy_fields = sum(1 for r in reports if r.legacy_field)
    markers = sum(r.marker_count for r in reports)
    if migrated and (legacy_fields or markers):
        findings.append(
            Finding(
                "BLOCKER",
                "COR_STATE_LEGACY_RESIDUE",
                "corpus",
                f"Corpus traegt unit_input_basis, aber noch {legacy_fields} Legacyfelder "
                f"und {markers} HTML-Marker",
                "Reste manuell pruefen; ein Teilstand wird nicht ueberschrieben",
            )
        )
    if strict_legacy and unmigrated and bool(legacy_fields) != bool(markers):
        findings.append(
            Finding(
                "BLOCKER",
                "COR_STATE_LEGACY_INCOHERENT",
                "corpus",
                f"Unmigrierter Corpus ist in sich widerspruechlich: {legacy_fields} "
                f"Legacyfelder, {markers} HTML-Marker",
                "Vormigrationsstand wiederherstellen; name_only darf nicht geraten werden",
            )
        )
    return findings


def corpus_findings(reports: Sequence[FileReport]) -> List[Finding]:
    """Prueft die verbindlichen Corpus-Sollwerte aus Plan 2.1/2.2.

    Nur fuer den kanonischen Inputroot sinnvoll; Aufrufer mit abweichendem Root
    (Tests) ueberspringen diese Pruefung.
    """
    findings: List[Finding] = []
    obj = "corpus"

    def check(actual: int, expected: int, code: str, label: str, hint: str) -> None:
        if actual != expected:
            findings.append(
                Finding("BLOCKER", code, obj, f"{label}: {actual}, erwartet {expected}", hint)
            )

    check(
        len(reports),
        EXPECTED_MAPPING_FILES,
        "COR_FILE_COUNT",
        "Mapping-Markdowns",
        "Corpusbestand pruefen; Sollmenge ist im Plan eingefroren",
    )
    # Legacybestand ist nur beim Erstlauf vorhanden; nach der Migration ist 0
    # der korrekte Wert. Jeder Zwischenwert deutet auf einen Teilstand hin.
    def check_legacy(actual: int, expected: int, code: str, label: str) -> None:
        if actual == expected:
            return
        if actual == 0:
            findings.append(
                Finding(
                    "INFO",
                    code,
                    obj,
                    f"{label}: 0 - Corpus ist bereits migriert",
                    "Kein Handlungsbedarf",
                )
            )
            return
        findings.append(
            Finding(
                "BLOCKER",
                code,
                obj,
                f"{label}: {actual}, erwartet {expected} (Erstlauf) oder 0 (migriert)",
                "Teilmigrierten Corpus pruefen; das Skript raet nicht",
            )
        )

    check_legacy(
        sum(1 for r in reports if r.legacy_field),
        EXPECTED_LEGACY_FIELDS,
        "COR_LEGACY_FIELDS",
        "entfernte Legacyfelder",
    )
    check_legacy(
        sum(r.marker_count for r in reports),
        EXPECTED_LEGACY_MARKERS,
        "COR_LEGACY_MARKERS",
        "entfernte HTML-Marker",
    )
    findings.extend(state_coherence_findings(reports, strict_legacy=True))
    check(
        sum(r.name_only_count for r in reports),
        EXPECTED_NAME_ONLY_UNITS,
        "COR_NAME_ONLY",
        "name_only-Units",
        "Genau die acht markierten Faelle sind name_only",
    )

    subs = sum(r.subcommittee_count for r in reports)
    parents = sum(1 for r in reports if r.subcommittee_count)
    direct = sum(1 for r in reports if not r.subcommittee_count)
    check(subs, EXPECTED_UNITS_SUBCOMMITTEE, "COR_UNITS_SUB", "subcommittee-Units", "Corpus pruefen")
    check(
        parents,
        EXPECTED_UNITS_PARENT_CORE,
        "COR_UNITS_PARENT",
        "committee_parent_core-Units",
        "Corpus pruefen",
    )
    check(
        direct,
        EXPECTED_UNITS_DIRECT,
        "COR_UNITS_DIRECT",
        "committee_direct-Units",
        "Corpus pruefen",
    )
    check(
        subs + parents + direct,
        EXPECTED_UNITS_TOTAL,
        "COR_UNITS_TOTAL",
        "Units gesamt",
        "Corpus pruefen; die 225er-Sollmenge ist verbindlich",
    )
    return findings


def print_terminal_report(
    mode: str,
    reports: Sequence[FileReport],
    findings: Sequence[Finding],
    applied: bool,
) -> None:
    """Immer sichtbarer Abschlussbericht, nach Schweregrad und Stage gruppiert."""
    print("=" * 78)
    print(f"Committee Matcher V2 - Inputmigration ({MIGRATION_VERSION}) - Modus {mode}")
    print("=" * 78)

    changed = [r for r in reports if r.changed]
    subs = sum(r.subcommittee_count for r in reports)
    parents = sum(1 for r in reports if r.subcommittee_count)
    direct = sum(1 for r in reports if not r.subcommittee_count)
    print(
        f"Dateien: {len(reports)} geprueft | "
        f"{len(changed)} {'geschrieben' if applied else 'mit ausstehender Aenderung'}"
    )
    print(
        f"Units:   {subs} subcommittee + {parents} committee_parent_core + "
        f"{direct} committee_direct = {subs + parents + direct}"
    )
    print(
        f"Basis:   {sum(r.name_only_count for r in reports)} name_only | "
        f"{subs - sum(r.name_only_count for r in reports)} jurisdiction_text (Subcommittees)"
    )
    print(
        f"Legacy:  {sum(1 for r in reports if r.legacy_field)} input_basis-Felder | "
        f"{sum(r.marker_count for r in reports)} HTML-Marker entfernt"
    )

    by_severity: Dict[str, List[Finding]] = {s: [] for s in SEVERITY_ORDER}
    for finding in findings:
        by_severity.setdefault(finding.severity, []).append(finding)

    print("-" * 78)
    if not findings:
        print("Befunde: keine")
    for severity in SEVERITY_ORDER:
        bucket = by_severity.get(severity) or []
        if not bucket:
            continue
        print(f"[{severity}] {len(bucket)}")
        by_code: Dict[str, List[Finding]] = {}
        for finding in bucket:
            by_code.setdefault(finding.code, []).append(finding)
        for code, group in by_code.items():
            head = group[0]
            print(f"  {code:<28} {len(group)}x")
            print(f"    {head.message}")
            shown = [f.location for f in group[:MAX_REPORTED_OBJECTS]]
            rest = len(group) - len(shown)
            suffix = f" (+{rest} weitere)" if rest else ""
            print(f"    Objekte: {', '.join(shown)}{suffix}")
            print(f"    -> {head.hint}")
    print("-" * 78)
    print(
        f"Artefakte: {DEFAULT_INPUT_ROOT.relative_to(BASE_DIR)}/md/*.md "
        f"({EXPECTED_MAPPING_FILES} Mapping-Inputs)"
    )
    print("Kosten: 0.00 USD (providerfreie Phase, keine Modellaufrufe)")


def print_file_report(reports: Sequence[FileReport]) -> None:
    print("-" * 78)
    print(f"{'Datei':<52}{'Kam':<6}{'Subs':>5}{'n_only':>7}{'Delta':>7}")
    print("-" * 78)
    for report in reports:
        print(
            f"{report.path.name:<52}{report.chamber:<6}{report.subcommittee_count:>5}"
            f"{report.name_only_count:>7}{('ja' if report.changed else 'nein'):>7}"
        )
    chambers = Counter(r.chamber for r in reports)
    print("-" * 78)
    print("Kammern: " + ", ".join(f"{k}={v}" for k, v in sorted(chambers.items())))


def object_name(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def run(mode: str, input_root: Path) -> int:
    """Fuehrt Migration bzw. Pruefung corpusweit all-or-nothing aus.

    Phase 1 berechnet fuer jede Datei den Sollzustand und sammelt alle
    Struktur- und Corpusbefunde. Erst wenn dieser Preflight ohne Blocker
    durchlaeuft, schreibt Phase 2. Damit hinterlaesst ein Fehler in einer
    spaeten Datei keinen halbmigrierten Corpus.
    """
    paths = discover_mapping_files(input_root)
    reports: List[FileReport] = []
    findings: List[Finding] = []
    canonical = input_root == DEFAULT_INPUT_ROOT

    # --- Phase 1: Sollzustand berechnen, nichts schreiben ---
    for path in paths:
        obj = object_name(path)
        text = path.read_text(encoding="utf-8")
        try:
            report = build_target(path, text, input_root)
        except MigrationBlocked as blocked:
            findings.append(blocked.finding)
            continue
        reports.append(report)
        findings.extend(validate_migrated(report, report.target_text, obj))
        if mode == "check" and report.changed:
            findings.append(
                Finding(
                    "BLOCKER",
                    "MIG_PENDING",
                    obj,
                    "Datei entspricht noch nicht dem Inputvertrag",
                    "Migration mit --apply ausfuehren",
                )
            )

    # Die eingefrorenen Sollwerte (46 Dateien, 225 Units, 8 name_only) gelten
    # ausschliesslich fuer den kanonischen Inputroot; die Kohaerenz des
    # Vormigrationszustands gilt fuer jeden Root.
    if canonical:
        findings.extend(corpus_findings(reports))
    else:
        findings.extend(state_coherence_findings(reports, strict_legacy=False))
    if len(reports) != len(paths):
        findings.append(
            Finding(
                "BLOCKER",
                "MIG_INCOMPLETE_PREFLIGHT",
                "corpus",
                f"{len(paths) - len(reports)} Dateien konnten nicht geprueft werden",
                "Strukturfehler oben beheben; es wird nichts geschrieben",
            )
        )

    blocked_preflight = [f for f in findings if f.severity == "BLOCKER"]

    # --- Phase 2: schreiben, nur bei sauberem Preflight ---
    applied = False
    if mode == "apply" and not blocked_preflight:
        findings.extend(write_all(reports))
        applied = True
        blocked_preflight = [f for f in findings if f.severity == "BLOCKER"]
    elif mode == "apply":
        findings.append(
            Finding(
                "BLOCKER",
                "MIG_WRITE_SKIPPED",
                "corpus",
                "Preflight blockiert - es wurde keine Datei geschrieben",
                "Blocker oben beheben und --apply wiederholen",
            )
        )

    print_terminal_report(mode, reports, findings, applied=applied)
    if mode == "report":
        print_file_report(reports)

    return 4 if any(f.severity == "BLOCKER" for f in findings) else 0


def write_all(reports: Sequence[FileReport]) -> List[Finding]:
    """Schreibt alle geaenderten Dateien; bei Fehler Rollback des Laufs."""
    written: List[Tuple[Path, str]] = []
    for report in reports:
        if not report.changed:
            continue
        original = report.path.read_text(encoding="utf-8")
        try:
            atomic_write(report.path, report.target_text)
        except OSError as exc:
            for path, previous in reversed(written):
                try:
                    atomic_write(path, previous)
                except OSError:  # pragma: no cover - defensiv
                    pass
            return [
                Finding(
                    "BLOCKER",
                    "MIG_WRITE_FAILED",
                    object_name(report.path),
                    f"Schreiben fehlgeschlagen ({exc}); {len(written)} Dateien zurueckgerollt",
                    "Schreibrechte/Speicherplatz pruefen und --apply wiederholen",
                )
            ]
        written.append((report.path, original))
    return []


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Migriert die kanonischen Committee-Markdowns auf unit_input_basis "
            f"({MIGRATION_VERSION}); idempotent."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="nur pruefen, nichts schreiben")
    group.add_argument("--apply", action="store_true", help="Sollzustand atomar schreiben")
    group.add_argument("--report", action="store_true", help="Klassifikation je Datei ausgeben")
    parser.add_argument(
        "--input-root",
        type=Path,
        default=DEFAULT_INPUT_ROOT,
        help="Abweichender Inputroot (nur fuer Tests)",
    )
    args = parser.parse_args(argv)

    mode = "check" if args.check else "apply" if args.apply else "report"
    try:
        return run(mode, args.input_root.resolve())
    except MigrationBlocked as blocked:  # pragma: no cover - defensiv
        print(f"[BLOCKER] {blocked.finding.code} {blocked.finding.location}: {blocked.finding.message}")
        return 4
    except Exception as exc:  # pragma: no cover - defensiv
        print(f"[BLOCKER] MIG_INTERNAL: unerwarteter interner Fehler: {exc}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
