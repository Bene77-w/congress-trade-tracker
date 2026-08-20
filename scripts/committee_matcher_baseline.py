#!/usr/bin/env python3
"""
Committee Matcher V2 - P0-Baselinewerkzeug

Erzeugt und prueft die beiden unveraenderlichen Kontrollinputs der
Matching-Phase sowie die abgeleitete Unit-Vorschau:

``data/committee_matcher_inputs/corpus_manifest.yaml``
    Klassifiziert und hasht jede regulaere Datei unter dem Inputroot genau
    einmal mit ihrer Rolle (``mapping_input``, ``source_only``, ``raw_source``,
    ``administrative_excluded``). Nur ``mapping_input`` erzeugt Units. Das
    Manifest ist Kontrollmetadata und damit selbst kein Corpusmitglied.

``data/config/committee_matcher_protected_baseline.yaml``
    Haelt die SHA-256-Werte der matcherbezogenen Produktionspfade fest, die V2
    ausschliesslich liest. Die Liste ist matcherbezogen und wird nicht aus der
    globalen ``reset_pipeline.py``-Schutzliste abgeleitet.

Beide Artefakte enthalten bewusst keine Zeitstempel, damit ``--write``
reproduzierbar bleibt und Provenienz ueber Git laeuft.

Unterbefehle
    manifest --write | --verify     Corpusmanifest erzeugen beziehungsweise pruefen
    protect  --write | --verify     Schutzbaseline erzeugen beziehungsweise pruefen
    units    [--list]               Strukturelle Unit-Sollmenge ableiten

Exit-Codes (Teilmenge der verbindlichen Plan-Matrix)
    0   erfolgreich
    4   Input-/Config-Preflight blockiert
    10  unerwarteter interner Fehler

Beispiele
    python scripts/committee_matcher_baseline.py manifest --write
    python scripts/committee_matcher_baseline.py manifest --verify
    python scripts/committee_matcher_baseline.py protect --verify
    python scripts/committee_matcher_baseline.py units --list
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from committee_matcher_migrate_inputs import (  # noqa: E402
    BASE_DIR,
    BASIS_NAME_ONLY,
    DEFAULT_INPUT_ROOT,
    EXPECTED_MAPPING_FILES,
    EXPECTED_NAME_ONLY_UNITS,
    EXPECTED_UNITS_DIRECT,
    EXPECTED_UNITS_PARENT_CORE,
    EXPECTED_UNITS_SUBCOMMITTEE,
    EXPECTED_UNITS_TOTAL,
    MAX_REPORTED_OBJECTS,
    MIGRATION_VERSION,
    SEVERITY_ORDER,
    SOURCE_ONLY_MD,
    Finding,
    MigrationBlocked,
    heading_sections,
    parse_frontmatter,
    split_frontmatter,
)

MANIFEST_VERSION = "corpus-manifest-v1"
PROTECTED_BASELINE_VERSION = "protected-paths-v1"

MANIFEST_PATH = DEFAULT_INPUT_ROOT / "corpus_manifest.yaml"
PROTECTED_BASELINE_PATH = BASE_DIR / "data" / "config" / "committee_matcher_protected_baseline.yaml"

ROLE_MAPPING_INPUT = "mapping_input"
ROLE_SOURCE_ONLY = "source_only"
ROLE_RAW_SOURCE = "raw_source"
ROLE_ADMINISTRATIVE = "administrative_excluded"

# Rollen, die in `source_files` referenziert werden duerfen.
REFERENCEABLE_ROLES = (ROLE_RAW_SOURCE, ROLE_SOURCE_ONLY)

EXPECTED_ROLE_COUNTS = {
    ROLE_MAPPING_INPUT: 46,
    ROLE_SOURCE_ONLY: 2,
    ROLE_RAW_SOURCE: 46,
    ROLE_ADMINISTRATIVE: 1,
}

ADMINISTRATIVE_FILES = frozenset({"Extrahierungsanleitung.csv"})

# Matcherbezogene Produktionspfade: V2 liest sie, veraendert sie nie.
PROTECTED_PRODUCTION_PATHS = (
    "data/mappings/subcommittee_sector_industry_mapping.csv",
    "data/mappings/subcommittee_sector_industry_mapping.xlsx",
    "data/mappings/house_member&(sub)committees.csv",
    "data/mappings/senate_member&(sub)committees.csv",
    "data/mappings/yf_sectors_with_industries_enriched.json",
    "data/mappings/yf_sectors_with_industries.json",
    "data/mappings/yf_sectors_with_industries.xlsx",
    "data/mappings/us_house_all_committees.csv",
    "data/mappings/us_senate_all_committees.csv",
)

UNIT_TYPE_SUBCOMMITTEE = "subcommittee"
UNIT_TYPE_PARENT_CORE = "committee_parent_core"
UNIT_TYPE_DIRECT = "committee_direct"


def relative_to_base(path: Path) -> str:
    """Projektrelativer Pfad; ausserhalb des Projekts der absolute Pfad."""
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(rel_path: str) -> str | None:
    """Ordnet einen Inputroot-relativen Pfad seiner Corpusrolle zu."""
    parts = rel_path.split("/")
    if len(parts) == 1:
        return ROLE_ADMINISTRATIVE if parts[0] in ADMINISTRATIVE_FILES else None
    if len(parts) == 2 and parts[0] == "md" and parts[1].endswith(".md"):
        return ROLE_SOURCE_ONLY if parts[1] in SOURCE_ONLY_MD else ROLE_MAPPING_INPUT
    if len(parts) == 2 and parts[0] == "txt" and parts[1].endswith(".txt"):
        return ROLE_RAW_SOURCE
    return None


def iter_corpus_files(input_root: Path) -> List[Path]:
    """Alle regulaeren Dateien unter dem Inputroot ausser dem Manifest selbst."""
    return sorted(
        p
        for p in input_root.rglob("*")
        if p.is_file() and not p.is_symlink() and p != MANIFEST_PATH
    )


def build_manifest(input_root: Path) -> Tuple[dict, List[Finding]]:
    findings: List[Finding] = []
    entries: List[dict] = []
    counts: Counter = Counter()

    for path in iter_corpus_files(input_root):
        rel = path.relative_to(input_root).as_posix()
        role = classify(rel)
        if role is None:
            findings.append(
                Finding(
                    "BLOCKER",
                    "MAN_UNKNOWN_FILE",
                    rel,
                    "Datei unter dem Inputroot hat keine bekannte Corpusrolle",
                    "Datei entfernen oder Rollenregel im Baselinewerkzeug ergaenzen",
                )
            )
            continue
        counts[role] += 1
        entries.append(
            {
                "path": rel,
                "role": role,
                "bytes": path.stat().st_size,
                "sha256": sha256_of(path),
            }
        )

    for role, expected in (
        EXPECTED_ROLE_COUNTS.items() if input_root == DEFAULT_INPUT_ROOT else ()
    ):
        if counts[role] != expected:
            findings.append(
                Finding(
                    "BLOCKER",
                    "MAN_ROLE_COUNT",
                    "corpus",
                    f"Rolle '{role}': {counts[role]} Dateien, erwartet {expected}",
                    "Corpusbestand gegen den eingefrorenen Plan pruefen",
                )
            )

    findings.extend(check_source_references(input_root, entries))

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "migration_version": MIGRATION_VERSION,
        "input_root": relative_to_base(input_root),
        "role_counts": {role: counts[role] for role in EXPECTED_ROLE_COUNTS},
        "unit_counts": {
            UNIT_TYPE_SUBCOMMITTEE: EXPECTED_UNITS_SUBCOMMITTEE,
            UNIT_TYPE_PARENT_CORE: EXPECTED_UNITS_PARENT_CORE,
            UNIT_TYPE_DIRECT: EXPECTED_UNITS_DIRECT,
            "total": EXPECTED_UNITS_TOTAL,
        },
        "files": entries,
    }
    return manifest, findings


def check_source_references(input_root: Path, entries: Sequence[dict]) -> List[Finding]:
    """Prueft `source_files` aller Mapping-MDs gegen die manifestierten Rollen."""
    findings: List[Finding] = []
    role_by_rel = {e["path"]: e["role"] for e in entries}
    referenced: set[str] = set()
    root_prefix = DEFAULT_INPUT_ROOT.relative_to(BASE_DIR).as_posix() + "/"

    for entry in entries:
        if entry["role"] != ROLE_MAPPING_INPUT:
            continue
        path = input_root / entry["path"]
        text = path.read_text(encoding="utf-8")
        frontmatter, _body, _ = split_frontmatter(text, entry["path"])
        data = parse_frontmatter(frontmatter, entry["path"])
        for raw in data.get("source_files") or []:
            ref = str(raw)
            if ".." in ref.split("/") or ref.startswith("/"):
                findings.append(
                    Finding(
                        "BLOCKER",
                        "MAN_PATH_TRAVERSAL",
                        entry["path"],
                        f"source_files-Eintrag '{ref}' verlaesst den Inputroot",
                        "Nur Pfade unterhalb des Inputroots referenzieren",
                    )
                )
                continue
            if not ref.startswith(root_prefix):
                findings.append(
                    Finding(
                        "BLOCKER",
                        "MAN_REF_OUTSIDE_ROOT",
                        entry["path"],
                        f"source_files-Eintrag '{ref}' liegt nicht unter '{root_prefix}'",
                        "Referenz auf eine manifestierte Corpusdatei umstellen",
                    )
                )
                continue
            rel = ref[len(root_prefix) :]
            role = role_by_rel.get(rel)
            if role is None:
                findings.append(
                    Finding(
                        "BLOCKER",
                        "MAN_REF_UNKNOWN",
                        entry["path"],
                        f"source_files-Eintrag '{ref}' ist nicht manifestiert",
                        "Nur vorhandene Corpusdateien referenzieren",
                    )
                )
                continue
            if role not in REFERENCEABLE_ROLES:
                findings.append(
                    Finding(
                        "BLOCKER",
                        "MAN_REF_ROLE",
                        entry["path"],
                        f"source_files-Eintrag '{ref}' hat Rolle '{role}'",
                        f"Nur Rollen {REFERENCEABLE_ROLES} sind referenzierbar",
                    )
                )
                continue
            referenced.add(rel)

    unreferenced = sorted(
        rel
        for rel, role in role_by_rel.items()
        if role in REFERENCEABLE_ROLES and rel not in referenced
    )
    if unreferenced:
        findings.append(
            Finding(
                "BLOCKER",
                "MAN_REF_MISSING",
                "corpus",
                f"{len(unreferenced)} referenzierbare Quelldateien werden von keiner "
                f"Mapping-MD referenziert: {unreferenced[:MAX_REPORTED_OBJECTS]}",
                "source_files der zugehoerigen Mapping-MD ergaenzen",
            )
        )
    return findings


def derive_units(input_root: Path) -> Tuple[List[dict], List[Finding]]:
    """Leitet die strukturelle Unit-Sollmenge ab.

    Bewusst ohne kanonische Unit-IDs: die Kanonisierung ``unit-id-v1`` wird
    erst in P3 festgeschrieben und per Golden Test eingefroren.
    """
    findings: List[Finding] = []
    units: List[dict] = []
    md_dir = input_root / "md"
    for path in sorted(md_dir.glob("*.md")):
        if path.name in SOURCE_ONLY_MD:
            continue
        rel = path.relative_to(input_root).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            frontmatter, body, _ = split_frontmatter(text, rel)
            data = parse_frontmatter(frontmatter, rel)
            sections = heading_sections(body, rel)
        except MigrationBlocked as blocked:
            findings.append(blocked.finding)
            continue
        basis = (data.get("unit_input_basis") or {}).get("subcommittees") or {}
        committee = str(data.get("committee", ""))
        chamber = str(data.get("chamber", ""))
        if sections:
            units.append(
                {
                    "source": rel,
                    "chamber": chamber,
                    "unit_type": UNIT_TYPE_PARENT_CORE,
                    "name": committee,
                    "input_basis": "jurisdiction_text",
                }
            )
            for name in sections:
                units.append(
                    {
                        "source": rel,
                        "chamber": chamber,
                        "unit_type": UNIT_TYPE_SUBCOMMITTEE,
                        "name": name,
                        "input_basis": basis.get(name, "jurisdiction_text"),
                    }
                )
        else:
            units.append(
                {
                    "source": rel,
                    "chamber": chamber,
                    "unit_type": UNIT_TYPE_DIRECT,
                    "name": committee,
                    "input_basis": "jurisdiction_text",
                }
            )

    counts = Counter(u["unit_type"] for u in units)
    # Eingefrorene Sollwerte gelten nur fuer den kanonischen Inputroot.
    expected = (
        {
            UNIT_TYPE_SUBCOMMITTEE: EXPECTED_UNITS_SUBCOMMITTEE,
            UNIT_TYPE_PARENT_CORE: EXPECTED_UNITS_PARENT_CORE,
            UNIT_TYPE_DIRECT: EXPECTED_UNITS_DIRECT,
        }
        if input_root == DEFAULT_INPUT_ROOT
        else {}
    )
    for unit_type, want in expected.items():
        if counts[unit_type] != want:
            findings.append(
                Finding(
                    "BLOCKER",
                    "UNIT_COUNT",
                    "corpus",
                    f"Unittyp '{unit_type}': {counts[unit_type]}, erwartet {want}",
                    "Corpus gegen die eingefrorene 225er-Sollmenge pruefen",
                )
            )
    if expected and len(units) != EXPECTED_UNITS_TOTAL:
        findings.append(
            Finding(
                "BLOCKER",
                "UNIT_TOTAL",
                "corpus",
                f"Units gesamt: {len(units)}, erwartet {EXPECTED_UNITS_TOTAL}",
                "Corpus gegen die eingefrorene 225er-Sollmenge pruefen",
            )
        )
    name_only = sum(1 for u in units if u["input_basis"] == BASIS_NAME_ONLY)
    if expected and name_only != EXPECTED_NAME_ONLY_UNITS:
        findings.append(
            Finding(
                "BLOCKER",
                "UNIT_NAME_ONLY",
                "corpus",
                f"name_only-Units: {name_only}, erwartet {EXPECTED_NAME_ONLY_UNITS}",
                "Basiswerte gegen den Inputvertrag pruefen",
            )
        )
    return units, findings


def build_protected_baseline() -> Tuple[dict, List[Finding]]:
    findings: List[Finding] = []
    entries: List[dict] = []
    for rel in PROTECTED_PRODUCTION_PATHS:
        path = BASE_DIR / rel
        if not path.is_file():
            findings.append(
                Finding(
                    "BLOCKER",
                    "PRT_MISSING",
                    rel,
                    "Geschuetzter Produktionspfad fehlt",
                    "Pfad wiederherstellen oder Schutzliste bewusst anpassen",
                )
            )
            continue
        entries.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_of(path)})
    baseline = {
        "baseline_version": PROTECTED_BASELINE_VERSION,
        "scope": "committee_matcher_v2",
        "note": (
            "Matcherbezogene Produktionspfade; V2 liest sie ausschliesslich und "
            "schreibt nie hinein. Nicht aus der globalen reset_pipeline.py-Liste "
            "abgeleitet."
        ),
        "paths": entries,
    }
    return baseline, findings


def verify_entries(
    entries: Iterable[dict], root: Path, code_prefix: str
) -> List[Finding]:
    findings: List[Finding] = []
    for entry in entries:
        path = root / entry["path"]
        if not path.is_file():
            findings.append(
                Finding(
                    "BLOCKER",
                    f"{code_prefix}_MISSING",
                    entry["path"],
                    "Datei aus der Baseline fehlt auf der Platte",
                    "Datei wiederherstellen oder Baseline bewusst neu schreiben",
                )
            )
            continue
        actual = sha256_of(path)
        if actual != entry["sha256"]:
            findings.append(
                Finding(
                    "BLOCKER",
                    f"{code_prefix}_HASH",
                    entry["path"],
                    f"SHA-256 weicht ab: baseline={entry['sha256'][:12]}, "
                    f"platte={actual[:12]}",
                    "Aenderung pruefen; die Baseline gilt als unveraenderlich",
                )
            )
    return findings


def dump_yaml(path: Path, payload: dict, header: str) -> None:
    body = yaml.safe_dump(
        payload, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10**9
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {header}\n{body}", encoding="utf-8")


def load_yaml(path: Path, obj: str) -> Tuple[dict | None, List[Finding]]:
    if not path.is_file():
        return None, [
            Finding(
                "BLOCKER",
                "BASE_MISSING",
                obj,
                "Baselinedatei fehlt",
                "Mit '--write' erzeugen",
            )
        ]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None, [
            Finding(
                "BLOCKER",
                "BASE_MALFORMED",
                obj,
                "Baselinedatei ist kein YAML-Mapping",
                "Datei mit '--write' neu erzeugen",
            )
        ]
    return data, []


def print_report(title: str, findings: Sequence[Finding], summary: Sequence[str]) -> None:
    print("=" * 78)
    print(f"Committee Matcher V2 - {title}")
    print("=" * 78)
    for line in summary:
        print(line)
    print("-" * 78)
    if not findings:
        print("Befunde: keine")
    grouped: Dict[str, List[Finding]] = {s: [] for s in SEVERITY_ORDER}
    for finding in findings:
        grouped.setdefault(finding.severity, []).append(finding)
    for severity in SEVERITY_ORDER:
        bucket = grouped.get(severity) or []
        if not bucket:
            continue
        print(f"[{severity}] {len(bucket)}")
        by_code: Dict[str, List[Finding]] = {}
        for finding in bucket:
            by_code.setdefault(finding.code, []).append(finding)
        for code, group in by_code.items():
            head = group[0]
            print(f"  {code:<24} {len(group)}x")
            print(f"    {head.message}")
            shown = [f.location for f in group[:MAX_REPORTED_OBJECTS]]
            rest = len(group) - len(shown)
            print(f"    Objekte: {', '.join(shown)}{f' (+{rest} weitere)' if rest else ''}")
            print(f"    -> {head.hint}")
    print("-" * 78)
    print("Kosten: 0.00 USD (providerfreie Phase, keine Modellaufrufe)")


def cmd_manifest(args: argparse.Namespace) -> int:
    input_root = args.input_root.resolve()
    manifest, findings = build_manifest(input_root)
    rel_manifest = MANIFEST_PATH.relative_to(BASE_DIR).as_posix()

    if args.write:
        if findings:
            print_report(
                "Corpusmanifest (write abgebrochen)",
                findings,
                [f"Ziel: {rel_manifest}", "Es wurde nichts geschrieben."],
            )
            return 4
        dump_yaml(
            MANIFEST_PATH,
            manifest,
            "Committee Matcher V2 - Corpusmanifest; erzeugt von "
            "scripts/committee_matcher_baseline.py (nicht von Hand pflegen).",
        )
        summary = [f"Geschrieben: {rel_manifest}", f"Dateien: {len(manifest['files'])}"]
    else:
        stored, load_findings = load_yaml(MANIFEST_PATH, rel_manifest)
        findings = list(findings) + load_findings
        if stored is not None:
            findings.extend(verify_entries(stored.get("files") or [], input_root, "MAN"))
            stored_paths = {e["path"] for e in stored.get("files") or []}
            actual_paths = {e["path"] for e in manifest["files"]}
            for extra in sorted(actual_paths - stored_paths):
                findings.append(
                    Finding(
                        "BLOCKER",
                        "MAN_UNMANIFESTED",
                        extra,
                        "Datei liegt im Inputroot, fehlt aber im Manifest",
                        "Manifest mit '--write' neu erzeugen oder Datei entfernen",
                    )
                )
            if stored.get("manifest_version") != MANIFEST_VERSION:
                findings.append(
                    Finding(
                        "BLOCKER",
                        "MAN_VERSION",
                        rel_manifest,
                        f"manifest_version '{stored.get('manifest_version')}' erwartet "
                        f"'{MANIFEST_VERSION}'",
                        "Manifest mit '--write' neu erzeugen",
                    )
                )
        summary = [f"Geprueft: {rel_manifest}", f"Dateien: {len(manifest['files'])}"]

    counts = manifest["role_counts"]
    summary.append("Rollen:   " + " | ".join(f"{k}={v}" for k, v in counts.items()))
    print_report("Corpusmanifest", findings, summary)
    return 4 if any(f.severity == "BLOCKER" for f in findings) else 0


def cmd_protect(args: argparse.Namespace) -> int:
    baseline, findings = build_protected_baseline()
    rel = PROTECTED_BASELINE_PATH.relative_to(BASE_DIR).as_posix()

    if args.write:
        if findings:
            print_report(
                "Schutzbaseline (write abgebrochen)",
                findings,
                [f"Ziel: {rel}", "Es wurde nichts geschrieben."],
            )
            return 4
        dump_yaml(
            PROTECTED_BASELINE_PATH,
            baseline,
            "Committee Matcher V2 - Schutzbaseline matcherbezogener "
            "Produktionspfade; erzeugt von scripts/committee_matcher_baseline.py.",
        )
        summary = [f"Geschrieben: {rel}", f"Pfade: {len(baseline['paths'])}"]
    else:
        stored, load_findings = load_yaml(PROTECTED_BASELINE_PATH, rel)
        findings = list(findings) + load_findings
        if stored is not None:
            findings.extend(verify_entries(stored.get("paths") or [], BASE_DIR, "PRT"))
            stored_paths = {e["path"] for e in stored.get("paths") or []}
            for missing in sorted(set(PROTECTED_PRODUCTION_PATHS) - stored_paths):
                findings.append(
                    Finding(
                        "BLOCKER",
                        "PRT_UNTRACKED",
                        missing,
                        "Geschuetzter Pfad fehlt in der Baselinedatei",
                        "Baseline mit '--write' neu erzeugen",
                    )
                )
        summary = [f"Geprueft: {rel}", f"Pfade: {len(PROTECTED_PRODUCTION_PATHS)}"]
    print_report("Schutzbaseline", findings, summary)
    return 4 if any(f.severity == "BLOCKER" for f in findings) else 0


def cmd_units(args: argparse.Namespace) -> int:
    units, findings = derive_units(args.input_root.resolve())
    counts = Counter(u["unit_type"] for u in units)
    chambers = Counter(u["chamber"] for u in units)
    summary = [
        f"Units:    {counts[UNIT_TYPE_SUBCOMMITTEE]} {UNIT_TYPE_SUBCOMMITTEE} + "
        f"{counts[UNIT_TYPE_PARENT_CORE]} {UNIT_TYPE_PARENT_CORE} + "
        f"{counts[UNIT_TYPE_DIRECT]} {UNIT_TYPE_DIRECT} = {len(units)}",
        "Kammern:  " + " | ".join(f"{k}={v}" for k, v in sorted(chambers.items())),
        f"Basis:    {sum(1 for u in units if u['input_basis'] == BASIS_NAME_ONLY)} name_only",
        "Hinweis:  ohne kanonische Unit-IDs; unit-id-v1 wird erst in P3 eingefroren.",
    ]
    if args.list:
        print("-" * 78)
        for unit in units:
            print(f"{unit['unit_type']:<22}{unit['chamber']:<7}{unit['name']}")
    print_report("Unit-Vorschau", findings, summary)
    return 4 if any(f.severity == "BLOCKER" for f in findings) else 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest", help="Corpusmanifest erzeugen oder pruefen")
    manifest_mode = manifest.add_mutually_exclusive_group(required=True)
    manifest_mode.add_argument("--write", action="store_true")
    manifest_mode.add_argument("--verify", action="store_true")
    manifest.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    manifest.set_defaults(func=cmd_manifest)

    protect = sub.add_parser("protect", help="Schutzbaseline erzeugen oder pruefen")
    protect_mode = protect.add_mutually_exclusive_group(required=True)
    protect_mode.add_argument("--write", action="store_true")
    protect_mode.add_argument("--verify", action="store_true")
    protect.set_defaults(func=cmd_protect)

    units = sub.add_parser("units", help="Strukturelle Unit-Sollmenge ableiten")
    units.add_argument("--list", action="store_true", help="alle Units einzeln ausgeben")
    units.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    units.set_defaults(func=cmd_units)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MigrationBlocked as blocked:  # pragma: no cover - defensiv
        print(f"[BLOCKER] {blocked.finding.code} {blocked.finding.location}: {blocked.finding.message}")
        return 4
    except Exception as exc:  # pragma: no cover - defensiv
        print(f"[BLOCKER] BASE_INTERNAL: unerwarteter interner Fehler: {exc}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
