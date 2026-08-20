"""Unitbildung, Kanonisierung und Sollmengenpruefung (Plan 2.2).

Zwei versionierte Kanonisierungen werden hier festgeschrieben und per Golden
Test eingefroren:

``unit-id-v1``
    Deterministische Unicode-, Whitespace- und Slug-Kanonisierung des
    Unitnamens. Schritte in genau dieser Reihenfolge:

    1. NFKC-Normalisierung und Vereinheitlichung typografischer Zeichen
       (Gedankenstriche, Anfuehrungszeichen, geschuetzte Leerzeichen);
    2. ``&`` wird zu ``and``;
    3. wiederholtes Entfernen fuehrender Amtsbezeichnungen
       (``Permanent Select Committee on``, ``Select Committee on``,
       ``Joint Committee of Congress on``, ``Joint Committee on``,
       ``Permanent Subcommittee on``, ``Subcommittee on``, ``Committee on``)
       sowie eines fuehrenden ``the``;
    4. NFKD-Zerlegung und Entfernen aller kombinierenden Zeichen (Akzente);
    5. ``casefold``;
    6. jede Nicht-``[a-z0-9]``-Folge wird zu ``-``, Laeufe kollabieren, Raender
       werden getrimmt.

    Die ID lautet ``{chamber}--{committee}`` fuer Committee-Units und
    ``{chamber}--{committee}--{subcommittee}`` fuer Subcommittees. Der doppelte
    Bindestrich trennt Ebenen und kann in einem Slug nicht auftreten. Die ID
    traegt bewusst nicht den Unittyp: bekommt ein Committee spaeter ein
    Subcommittee, wechselt sein Typ von ``committee_direct`` zu
    ``committee_parent_core``, die Identitaet bleibt stabil.

``mapping-input-v1``
    SHA-256 ueber die kanonische JSON-Serialisierung genau der fachlichen
    Daten, die eine Unit in den Mappingcall traegt: Identitaet, Unittyp, Basis
    und normalisierter Evidenztext. Volatile Provenienz (Run-, Snapshot- und
    Call-IDs) geht nicht ein. Die exakten Quelldateibytes erhalten mit
    ``source_bytes_sha256`` einen getrennten Hash.

Kollidierende IDs blockieren; es wird nichts durch Nummerierung entschaerft.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import yaml

from . import paths
from .errors import ErrorCollector, Stage
from .input_corpus import CommitteeDocument, normalize_text
from .models import MatcherUnit
from .status import InputBasis, UnitKind

#: Versionen der beiden Kanonisierungen; jede Aenderung braucht eine neue
#: Version und einen neuen Golden Test.
UNIT_ID_VERSION = "unit-id-v1"
MAPPING_INPUT_VERSION = "mapping-input-v1"

#: Trennzeichen zwischen Kammer, Committee und Subcommittee.
ID_SEPARATOR = "--"

#: Eingefrorene Sollmenge der Unit-IDs (Kontrollinput, read-only gelesen).
UNIT_ID_BASELINE_PATH = paths.CONFIG_ROOT / "committee_matcher_unit_ids_v1.yaml"

#: Fuehrende Amtsbezeichnungen, die der Slug nicht traegt.
OFFICE_PREFIXES = (
    "permanent select committee on ",
    "select committee on ",
    "joint committee of congress on ",
    "joint committee on ",
    "permanent subcommittee on ",
    "subcommittee on ",
    "committee on ",
)

#: Typografische Zeichen, die vor dem Slug vereinheitlicht werden.
_TYPOGRAPHY = {
    "–": "-", "—": "-", "‑": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", " ": " ",
}

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def strip_office_prefix(name: str) -> str:
    """Entfernt fuehrende Amtsbezeichnungen und ein fuehrendes ``the``."""
    result = name.strip()
    changed = True
    while changed:
        changed = False
        lowered = result.casefold()
        for prefix in OFFICE_PREFIXES:
            if lowered.startswith(prefix):
                result = result[len(prefix) :].strip()
                changed = True
                break
    if result.casefold().startswith("the "):
        result = result[4:].strip()
    return result


def slugify(name: str) -> str:
    """Kanonischer Slug gemaess ``unit-id-v1``."""
    text = unicodedata.normalize("NFKC", name)
    for source, target in _TYPOGRAPHY.items():
        text = text.replace(source, target)
    text = text.replace("&", " and ")
    text = strip_office_prefix(text)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_SLUG_RE.sub("-", stripped.casefold()).strip("-")


def match_key(name: str) -> str:
    """Vergleichsschluessel fuer Roster- und Evidenzabgleiche.

    Gleiche Normalisierung wie der Slug, aber mit Leerzeichen statt
    Bindestrichen - damit bleiben Wortgrenzen erkennbar.
    """
    return slugify(name).replace("-", " ")


def committee_unit_id(chamber: str, committee: str) -> str:
    return f"{chamber}{ID_SEPARATOR}{slugify(committee)}"


def subcommittee_unit_id(chamber: str, committee: str, subcommittee: str) -> str:
    return (
        f"{chamber}{ID_SEPARATOR}{slugify(committee)}"
        f"{ID_SEPARATOR}{slugify(subcommittee)}"
    )


def mapping_input_hash(
    *,
    unit_id: str,
    kind: UnitKind,
    chamber: str,
    committee_name: str,
    unit_name: str,
    input_basis: InputBasis,
    evidence_text: str,
    parent_committee_name: str | None,
) -> str:
    """SHA-256 des fachlichen Mappinginputs gemaess ``mapping-input-v1``."""
    payload = {
        "version": MAPPING_INPUT_VERSION,
        "unit_id": unit_id,
        "kind": kind.value,
        "chamber": chamber,
        "committee_name": normalize_text(committee_name),
        "unit_name": normalize_text(unit_name),
        "input_basis": input_basis.value,
        "evidence_text": normalize_text(evidence_text),
        "parent_committee_name": (
            normalize_text(parent_committee_name) if parent_committee_name else None
        ),
    }
    serialized = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _unit(
    document: CommitteeDocument,
    *,
    kind: UnitKind,
    unit_id: str,
    unit_name: str,
    input_basis: InputBasis,
    evidence_text: str,
    parent_unit_id: str | None,
) -> MatcherUnit:
    return MatcherUnit(
        unit_id=unit_id,
        kind=kind,
        chamber=document.chamber,
        committee_name=document.committee,
        unit_name=unit_name,
        input_basis=input_basis,
        source_path=document.rel_path,
        evidence_text=normalize_text(evidence_text),
        parent_unit_id=parent_unit_id,
        mapping_input_hash=mapping_input_hash(
            unit_id=unit_id,
            kind=kind,
            chamber=document.chamber,
            committee_name=document.committee,
            unit_name=unit_name,
            input_basis=input_basis,
            evidence_text=evidence_text,
            parent_committee_name=document.committee if parent_unit_id else None,
        ),
        source_bytes_sha256=document.source_bytes_sha256 or None,
    )


def build_units(
    collector: ErrorCollector, documents: "tuple[CommitteeDocument, ...] | list[CommitteeDocument]"
) -> tuple[MatcherUnit, ...]:
    """Bildet die Units eines geladenen Corpus.

    Ohne Subcommittees entsteht ``committee_direct``; mit Subcommittees je H2
    eine ``subcommittee``-Unit und zusaetzlich ``committee_parent_core``.
    Parent-Core wird nie aus Child-Mappings aggregiert - es traegt den eigenen
    Committee-Text als Evidenz.
    """
    units: list[MatcherUnit] = []
    origin: dict[str, str] = {}

    def register(unit: MatcherUnit, where: str) -> None:
        previous = origin.get(unit.unit_id)
        if previous is not None:
            collector.blocker(
                "UNIT_ID_COLLISION", Stage.INPUT, unit.unit_id,
                f"Unit-ID entsteht doppelt: {previous} und {where}.",
                "Namen im Corpus eindeutig machen; der Matcher nummeriert nicht durch.",
            )
            return
        origin[unit.unit_id] = where
        units.append(unit)

    for document in documents:
        committee_id = committee_unit_id(document.chamber, document.committee)
        if not slugify(document.committee):
            collector.blocker(
                "UNIT_SLUG_EMPTY", Stage.INPUT, document.rel_path,
                f"Committeename {document.committee!r} ergibt keinen Slug.",
                "Committeename im Frontmatter korrigieren.",
            )
            continue

        if document.has_subcommittees:
            register(
                _unit(
                    document,
                    kind=UnitKind.COMMITTEE_PARENT_CORE,
                    unit_id=committee_id,
                    unit_name=document.committee,
                    input_basis=document.committee_basis,
                    evidence_text=document.preamble,
                    parent_unit_id=None,
                ),
                f"{document.rel_path} (parent core)",
            )
            for section in document.sections:
                if not slugify(section.name):
                    collector.blocker(
                        "UNIT_SLUG_EMPTY", Stage.INPUT, document.rel_path,
                        f"Abschnittsname {section.name!r} ergibt keinen Slug.",
                        "H2-Ueberschrift korrigieren.", line=section.line,
                    )
                    continue
                basis = document.subcommittee_basis.get(
                    section.name, InputBasis.JURISDICTION_TEXT
                )
                register(
                    _unit(
                        document,
                        kind=UnitKind.SUBCOMMITTEE,
                        unit_id=subcommittee_unit_id(
                            document.chamber, document.committee, section.name
                        ),
                        unit_name=section.name,
                        input_basis=basis,
                        evidence_text=(
                            "" if basis is InputBasis.NAME_ONLY else section.text
                        ),
                        parent_unit_id=committee_id,
                    ),
                    f"{document.rel_path}:{section.line}",
                )
        else:
            register(
                _unit(
                    document,
                    kind=UnitKind.COMMITTEE_DIRECT,
                    unit_id=committee_id,
                    unit_name=document.committee,
                    input_basis=document.committee_basis,
                    evidence_text=document.preamble,
                    parent_unit_id=None,
                ),
                f"{document.rel_path} (direct)",
            )

    return tuple(units)


def build_baseline_payload(units: "tuple[MatcherUnit, ...] | list[MatcherUnit]") -> dict:
    """Serialisierbare Sollmenge der Unit-IDs (Kontrollinput ohne Zeitstempel)."""
    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.kind.value] = counts.get(unit.kind.value, 0) + 1
    return {
        "canonicalization_version": UNIT_ID_VERSION,
        "mapping_input_version": MAPPING_INPUT_VERSION,
        "unit_count": len(units),
        "unit_counts": dict(sorted(counts.items())),
        "units": [
            {
                "unit_id": unit.unit_id,
                "kind": unit.kind.value,
                "chamber": unit.chamber,
                "source": unit.source_path,
            }
            for unit in sorted(units, key=lambda u: u.unit_id)
        ],
    }


def load_baseline_ids(
    collector: ErrorCollector, path: Path = UNIT_ID_BASELINE_PATH
) -> frozenset[str] | None:
    """Liest die eingefrorene Unit-ID-Sollmenge."""
    if not path.exists():
        collector.blocker(
            "UNIT_BASELINE_MISSING", Stage.INPUT, paths.relative_to_base(path),
            "Eingefrorene Unit-ID-Sollmenge fehlt.",
            "scripts/committee_matcher_baseline.py units --write ausfuehren.",
        )
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        collector.blocker(
            "UNIT_BASELINE_MALFORMED", Stage.INPUT, paths.relative_to_base(path),
            f"Sollmenge ist kein gueltiges YAML ({exc.__class__.__name__}).",
            "Datei neu erzeugen.",
        )
        return None
    if not isinstance(data, dict) or not isinstance(data.get("units"), list):
        collector.blocker(
            "UNIT_BASELINE_MALFORMED", Stage.INPUT, paths.relative_to_base(path),
            "Sollmenge hat kein 'units'-Feld.",
            "Datei neu erzeugen.",
        )
        return None
    if data.get("canonicalization_version") != UNIT_ID_VERSION:
        collector.blocker(
            "UNIT_BASELINE_VERSION", Stage.INPUT, paths.relative_to_base(path),
            f"Sollmenge traegt {data.get('canonicalization_version')!r} statt "
            f"{UNIT_ID_VERSION!r}.",
            "Kanonisierung und Sollmenge gemeinsam versionieren.",
        )
        return None
    ids: list[str] = []
    for entry in data["units"]:
        if not isinstance(entry, dict) or not entry.get("unit_id"):
            collector.blocker(
                "UNIT_BASELINE_MALFORMED", Stage.INPUT, paths.relative_to_base(path),
                "Eintrag ohne 'unit_id'.",
                "Datei neu erzeugen.",
            )
            return None
        ids.append(str(entry["unit_id"]))
    if len(ids) != len(set(ids)):
        collector.blocker(
            "UNIT_BASELINE_DUPLICATE", Stage.INPUT, paths.relative_to_base(path),
            "Sollmenge enthaelt doppelte Unit-IDs.",
            "Datei neu erzeugen.",
        )
        return None
    return frozenset(ids)


def check_against_baseline(
    collector: ErrorCollector,
    units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
    path: Path = UNIT_ID_BASELINE_PATH,
) -> bool:
    """Prueft exakte Setgleichheit gegen die eingefrorene Sollmenge."""
    expected = load_baseline_ids(collector, path)
    if expected is None:
        return False
    actual = frozenset(unit.unit_id for unit in units)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    for unit_id in missing:
        collector.blocker(
            "UNIT_MISSING", Stage.INPUT, unit_id,
            "Unit der eingefrorenen Sollmenge fehlt im Corpus.",
            "Corpus wiederherstellen oder Sollmenge bewusst neu einfrieren.",
        )
    for unit_id in unexpected:
        collector.blocker(
            "UNIT_UNEXPECTED", Stage.INPUT, unit_id,
            "Unit ist nicht Teil der eingefrorenen Sollmenge.",
            "Corpusaenderung pruefen; Sollmenge nur bewusst neu einfrieren.",
        )
    return not missing and not unexpected
