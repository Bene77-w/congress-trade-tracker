"""Formale Pruefung eines Mappingergebnisses (Plan 2.5, P6).

Hier wird ausschliesslich geprueft, was aus Vertrag, Unit und Snapshot heraus
entscheidbar ist: Form, Anzahlgrenzen, Snapshotbindung, Evidenzherkunft und
Batchvollstaendigkeit. Ueber die fachliche Direktheit einer Zuordnung urteilt
dieses Modul nicht - dafuer stehen Prompt, Gold-Set, Holdout und der manuelle
Review.

Zwei Grundsaetze:

* **Kein Silent-Fix.** Ein Verstoss fuehrt nie zu einer stillen Kappung,
  Loeschung oder Confidence-Aenderung. Er wird benannt und geht als
  Reparaturauftrag zurueck an das Modell; bleibt er bestehen, scheitert der
  Batch.
* **Alles oder nichts je Batch.** Ein Ergebnis mit Verstoessen wird nicht
  teilweise uebernommen. Die Zuordnung einer Unit haengt an derselben Antwort
  wie die der anderen, und ein halb uebernommener Batch waere im Audit nicht
  mehr rekonstruierbar.

Die Verstoesse sind maschinenlesbar (Code, Unit, Meldung) und werden fuer den
Reparaturcall als nummerierte Liste gerendert.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from .models import (
    MAX_ASSIGNMENTS,
    MAX_ASSIGNMENTS_REGULAR,
    RESEARCH_MAX_CONFIDENCE,
    InputBasis,
    MappingBatchResult,
    MappingDecision,
    MatcherUnit,
    TaxonomySnapshot,
    UnitKind,
    comparable_text,
    evidence_word_count,
    is_research_evidence,
)

#: Unittypen, die nie mehr als ``MAX_ASSIGNMENTS_REGULAR`` Zuordnungen tragen
#: duerfen: der uebergeordnete Zustaendigkeitskern wird nicht aus den Kindern
#: aggregiert, und eine Unit ohne Zustaendigkeitstext kann eine breite
#: Ausnahme nicht belegen (Plan 2.2, 2.5).
NARROW_KINDS: frozenset[UnitKind] = frozenset({UnitKind.COMMITTEE_PARENT_CORE})


#: Feldnamen des Ausgabevertrags. Nur sie duerfen in einem gemeldeten Feldpfad
#: erscheinen; alles Uebrige koennte ein vom Modell erfundener Schluessel sein.
KNOWN_FIELDS: frozenset[str] = frozenset({
    "requested_batch_id", "units", "unit_id", "decision", "no_mapping_reason",
    "broad_scope_exception", "broad_scope_reason", "assignments",
    "industry_id", "confidence", "evidence_excerpt", "rationale_short",
})


def field_path(location: "tuple | list") -> str:
    """Feldpfad eines Schemafehlers, ausschliesslich aus eigenem Vokabular.

    Die Verstossliste steht im Reparaturprompt **ausserhalb** der Datenbloecke:
    sie ist der verbindliche Auftrag. Deshalb darf kein Zeichen aus der Antwort
    des Modells in sie gelangen - auch kein einzeilig zitiertes. Genannt werden
    nur Vertragsfeldnamen und Positionen; die Stelle selbst findet die Reparatur
    ueber diese Position in ihrer eigenen, im Datenblock beigelegten Antwort.
    """
    parts: list[str] = []
    for element in location:
        if isinstance(element, int) and not isinstance(element, bool):
            parts.append(f"[{element + 1}]")
            continue
        name = str(element) if element in KNOWN_FIELDS else "<unbekanntes Feld>"
        parts.append(name if not parts else f".{name}")
    return "".join(parts) or "Antwort"


@dataclass(frozen=True)
class Violation:
    """Ein formaler Verstoss gegen den Mappingvertrag."""

    code: str
    message: str
    unit_id: str | None = None

    def rendered(self) -> str:
        scope = self.unit_id or "Batch"
        return f"{scope} [{self.code}]: {self.message}"


@dataclass(frozen=True)
class ValidationOutcome:
    """Ergebnis der formalen Pruefung.

    ``result`` ist nur gesetzt, wenn kein einziger Verstoss vorliegt - ein
    fachlich halb gueltiges Ergebnis wird nie als Ergebnis ausgegeben.
    ``parsed`` haelt daneben den strukturell gelesenen Stand, auch wenn
    Fachverstoesse vorliegen: der gezielte Repair braucht ihn, um die
    unbeanstandeten Units unveraendert zu uebernehmen.
    """

    result: MappingBatchResult | None
    violations: tuple[Violation, ...]
    parsed: MappingBatchResult | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.violations

    def units_with_violations(self) -> tuple[str, ...]:
        seen = [v.unit_id for v in self.violations if v.unit_id]
        return tuple(dict.fromkeys(seen))

    def batch_wide_violations(self) -> tuple[Violation, ...]:
        """Verstoesse ohne Unitbezug - sie treffen den ganzen Batch."""
        return tuple(v for v in self.violations if v.unit_id is None)


def format_violations(violations: "tuple[Violation, ...] | list[Violation]") -> str:
    """Verstossliste fuer den Reparaturprompt."""
    return "\n".join(f"{index}. {violation.rendered()}"
                     for index, violation in enumerate(violations, start=1))


# -- Evidenz ---------------------------------------------------------------
def evidence_contexts(unit: MatcherUnit) -> tuple[tuple[str, str], ...]:
    """Erlaubte Belegquellen einer Unit als ``(Bezeichnung, Text)``.

    ``name_only`` darf ausschliesslich den offiziellen Unitnamen verwenden;
    jede andere Unit zusaetzlich ihren eigenen Zustaendigkeitstext. Der
    Parent-Text fehlt bewusst: er ist Kontext, nie Beleg (Plan 2.2).
    """
    contexts: list[tuple[str, str]] = [("Name der Einheit", unit.unit_name)]
    if unit.kind is not UnitKind.SUBCOMMITTEE and unit.committee_name != unit.unit_name:
        contexts.append(("Name des Committees", unit.committee_name))
    if unit.input_basis is not InputBasis.NAME_ONLY and unit.evidence_text:
        contexts.append(("Zustaendigkeitstext", unit.evidence_text))
    return tuple(contexts)


def evidence_supported(unit: MatcherUnit, excerpt: str) -> bool:
    """Kommt der Ausschnitt zusammenhaengend in einer erlaubten Quelle vor?"""
    needle = comparable_text(excerpt)
    if not needle:
        return False
    if is_research_evidence(excerpt):
        return True
    return any(needle in comparable_text(text) for _, text in evidence_contexts(unit))


def _evidence_violation(unit: MatcherUnit, position: int, excerpt: str) -> Violation:
    """Benennt, warum eine Evidence nicht traegt - inklusive Parent-Fall.

    Der Parent-Fall bekommt eine eigene Meldung, weil er der haeufigste ist und
    das Modell sonst dieselbe unzulaessige Stelle erneut vorschlagen wuerde.
    """
    if unit.parent_context_text and comparable_text(excerpt) in comparable_text(
            unit.parent_context_text):
        return Violation(
            "MAP_EVIDENCE_FROM_PARENT",
            f"Die Evidence der {position}. Zuordnung stammt aus dem Text des "
            "uebergeordneten Committees. Der ist Kontext, kein Beleg: belege "
            "mit dem eigenen Namen oder dem eigenen Zustaendigkeitstext dieser "
            "Einheit oder entferne die Zuordnung.",
            unit.unit_id,
        )
    quellen = ", ".join(label for label, _ in evidence_contexts(unit))
    return Violation(
        "MAP_EVIDENCE_UNSUPPORTED",
        f"Die Evidence der {position}. Zuordnung kommt nicht woertlich und "
        f"zusammenhaengend in den erlaubten Quellen vor ({quellen}). Zitiere "
        "eine belegbare Stelle oder entferne die Zuordnung.",
        unit.unit_id,
    )


# -- Pruefung --------------------------------------------------------------
def _unit_of(location: "tuple | list", payload: object, batch) -> str | None:
    """Ordnet einen Schemafehler seiner Unit zu, wenn das eindeutig geht.

    Der Index in ``units[n]`` bezieht sich auf die Antwort, nicht auf den Batch.
    Die dort genannte ``unit_id`` ist Fremdtext und wird deshalb nicht
    uebernommen, sondern nur gegen die angefragten IDs abgeglichen: passt sie,
    ist der Bezug sicher, sonst bleibt der Fehler batchweit. Ohne diesen Bezug
    wuerde ein Tippfehler in einer einzelnen Unit den ganzen Batch wiederholen.
    """
    if len(location) < 2 or location[0] != "units":
        return None
    index = location[1]
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, list) or not 0 <= index < len(units):
        return None
    entry = units[index]
    claimed = entry.get("unit_id") if isinstance(entry, dict) else None
    return claimed if claimed in batch.unit_ids else None


def _schema_violations(exc: ValidationError, payload: object,
                       batch) -> tuple[Violation, ...]:
    """Uebersetzt Pydantic-Fehler in Verstoesse.

    Uebernommen werden nur Feldpfad, Positionen und der Pydantic-Fehlertyp -
    alles eigenes Vokabular, nichts aus dem Antworttext.
    """
    violations = []
    for error in exc.errors():
        location = error.get("loc", ())
        # ``type`` ist ein fester Pydantic-Code (``missing``,
        # ``extra_forbidden``, ...). Die ausformulierte ``msg`` bleibt draussen:
        # sie kann Werte aus der Antwort enthalten.
        violations.append(Violation(
            "MAP_SCHEMA_INVALID",
            f"{field_path(location)}: {error.get('type', 'ungueltig')}",
            unit_id=_unit_of(location, payload, batch),
        ))
    return tuple(violations)


def _unit_limit(unit: MatcherUnit) -> int:
    """Obergrenze der Zuordnungen dieser Unit."""
    if unit.kind in NARROW_KINDS or unit.input_basis is InputBasis.NAME_ONLY:
        return MAX_ASSIGNMENTS_REGULAR
    return MAX_ASSIGNMENTS


def validate_payload(payload: object, batch, snapshot: TaxonomySnapshot
                     ) -> ValidationOutcome:
    """Prueft eine Modellantwort gegen Vertrag, Batch und Snapshot.

    ``batch`` ist ein ``mapping.MappingBatch``; die Annotation bleibt offen,
    damit ``mapping`` und ``validation`` nicht zirkulaer importieren.
    """
    try:
        result = MappingBatchResult.model_validate(payload)
    except ValidationError as exc:
        return ValidationOutcome(None, _schema_violations(exc, payload, batch))

    violations: list[Violation] = []

    if result.requested_batch_id != batch.batch_id:
        violations.append(Violation(
            "MAP_BATCH_ID_MISMATCH",
            f"requested_batch_id weicht von der Anfrage ab; angefragt war "
            f"{batch.batch_id}. Uebernimm die Batch-ID zeichengenau.",
        ))

    requested = set(batch.unit_ids)
    answered = set(result.unit_ids())
    missing = sorted(requested - answered)
    if missing:
        violations.append(Violation(
            "MAP_UNITS_MISSING",
            f"Nicht beantwortet: {', '.join(missing)}. Jede angefragte Unit "
            "erscheint genau einmal.",
        ))
    extra = sorted(answered - requested)
    if extra:
        violations.append(Violation(
            "MAP_UNITS_UNEXPECTED",
            f"{len(extra)} nicht angefragte Unit(s) in der Antwort. Antworte "
            f"ausschliesslich auf: {', '.join(sorted(requested))}.",
        ))

    known_ids = snapshot.industry_ids()
    for unit_result in result.units:
        unit = batch.unit(unit_result.unit_id)
        if unit is None:
            continue  # bereits als MAP_UNITS_UNEXPECTED gemeldet

        limit = _unit_limit(unit)
        count = len(unit_result.assignments)
        if count > limit:
            violations.append(Violation(
                "MAP_TOO_MANY_ASSIGNMENTS",
                f"{count} Zuordnungen, zulaessig sind hoechstens {limit} "
                f"(Unittyp {unit.kind.value}, Basis {unit.input_basis.value}). "
                "Behalte die tragfaehigsten und entferne die uebrigen.",
                unit.unit_id,
            ))
        if unit_result.broad_scope_exception and limit == MAX_ASSIGNMENTS_REGULAR:
            violations.append(Violation(
                "MAP_BROAD_SCOPE_NOT_ALLOWED",
                f"Die Broad-Scope-Ausnahme ist fuer Unittyp {unit.kind.value} "
                f"mit Basis {unit.input_basis.value} nicht zulaessig; hier "
                f"gelten hoechstens {MAX_ASSIGNMENTS_REGULAR} Zuordnungen.",
                unit.unit_id,
            ))

        if unit_result.decision is MappingDecision.NO_MAPPING:
            continue

        for position, assignment in enumerate(unit_result.assignments, start=1):
            if assignment.industry_id not in known_ids:
                violations.append(Violation(
                    "MAP_UNKNOWN_INDUSTRY",
                    f"Die industry_id der {position}. Zuordnung kommt in der "
                    "uebergebenen Taxonomie nicht vor. Verwende eine ID aus der "
                    "Liste, zeichengenau, oder entferne die Zuordnung.",
                    unit.unit_id,
                ))
            if not evidence_supported(unit, assignment.evidence_excerpt):
                violations.append(
                    _evidence_violation(unit, position, assignment.evidence_excerpt))
            elif is_research_evidence(assignment.evidence_excerpt):
                if assignment.confidence > RESEARCH_MAX_CONFIDENCE:
                    violations.append(Violation(
                        "MAP_RESEARCH_CONFIDENCE_EXCEEDED",
                        f"Die {position}. Zuordnung verwendet Recherche als "
                        f"Evidence und darf hoechstens Confidence "
                        f"{RESEARCH_MAX_CONFIDENCE} tragen.",
                        unit.unit_id,
                    ))
            elif evidence_word_count(assignment.evidence_excerpt) < 2:
                violations.append(Violation(
                    "MAP_EVIDENCE_TOO_SHORT",
                    f"Die Evidence der {position}. Zuordnung besteht nur aus "
                    "einem Wort. Zitiere mindestens den tragenden Begriff mit "
                    "seinem zusammenhaengenden Kontext.",
                    unit.unit_id,
                ))

    if violations:
        return ValidationOutcome(None, tuple(violations), parsed=result)
    return ValidationOutcome(result, (), parsed=result)


# -- Boundary --------------------------------------------------------------
@dataclass(frozen=True)
class BoundaryConflict:
    """Dieselbe Industry in mehreren Units derselben Committee-Familie."""

    industry_id: str
    family_id: str
    unit_ids: tuple[str, ...]


def family_id(unit: MatcherUnit) -> str:
    """Familienschluessel einer Unit.

    Subcommittees teilen ihn mit ihrem Parent-Core, weil beide dieselbe
    Committee-Zustaendigkeit aufteilen; jede andere Unit bildet ihre eigene
    Familie.
    """
    return unit.parent_unit_id or unit.unit_id


def find_boundary_conflicts(
        units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
        results: "tuple | list") -> tuple[BoundaryConflict, ...]:
    """Findet gleiche Industries in Geschwister-Units (Plan 2.8).

    Ein Konflikt ist kein Fehler, sondern der Regelfall: getrennte
    Zustaendigkeiten beruehren dieselbe Industry haeufig zu Recht. Er wird
    deshalb nur erkannt, im Review als Information ausgewiesen und nie
    entfernt; wer eine der Zuordnungen nicht will, loescht sie im Blatt
    ``assignments``.
    """
    by_id = {unit.unit_id: unit for unit in units}
    seen: dict[tuple[str, str], list[str]] = {}
    for unit_result in results:
        unit = by_id.get(unit_result.unit_id)
        if unit is None:
            continue
        for assignment in unit_result.assignments:
            key = (family_id(unit), assignment.industry_id)
            bucket = seen.setdefault(key, [])
            if unit.unit_id not in bucket:
                bucket.append(unit.unit_id)

    # Sortiert, weil die Antwortreihenfolge des Providers sonst bestimmen
    # wuerde, welche Unit in Folgeartefakten als "a" und welche als "b" gilt.
    conflicts = [
        BoundaryConflict(industry_id=industry, family_id=family,
                         unit_ids=tuple(sorted(members)))
        for (family, industry), members in seen.items()
        if len(members) > 1
    ]
    conflicts.sort(key=lambda item: (item.family_id, item.industry_id))
    return tuple(conflicts)


__all__ = [
    "NARROW_KINDS", "KNOWN_FIELDS", "Violation", "ValidationOutcome",
    "BoundaryConflict", "format_violations", "field_path", "evidence_contexts", "evidence_supported",
    "validate_payload", "family_id", "find_boundary_conflicts",
]
