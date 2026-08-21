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
    InputBasis,
    MappingBatchResult,
    MappingDecision,
    MatcherUnit,
    TaxonomySnapshot,
    UnitKind,
    comparable_text,
)

#: Unittypen, die nie mehr als ``MAX_ASSIGNMENTS_REGULAR`` Zuordnungen tragen
#: duerfen: der uebergeordnete Zustaendigkeitskern wird nicht aus den Kindern
#: aggregiert, und eine Unit ohne Zustaendigkeitstext kann eine breite
#: Ausnahme nicht belegen (Plan 2.2, 2.5).
NARROW_KINDS: frozenset[UnitKind] = frozenset({UnitKind.COMMITTEE_PARENT_CORE})


#: Laengengrenze eines zitierten Fremdwertes in einer Verstossmeldung.
MAX_QUOTED_CHARS = 80


def quote_untrusted(value: object) -> str:
    """Zitiert einen Wert aus der Modellantwort gefahrlos.

    Die Verstossliste steht im Reparaturprompt **ausserhalb** der Datenbloecke:
    sie ist der verbindliche Auftrag. Ein Wert aus der vorherigen Antwort - eine
    erfundene ``industry_id``, eine fremde ``unit_id`` - darf dort keine eigenen
    Zeilen oder Blockrahmen erzeugen und damit selbst wie eine Anweisung wirken.
    Deshalb wird er auf eine Zeile gebracht, gekuerzt und in Anfuehrungszeichen
    gesetzt; genannt werden muss er, damit die Reparatur die Stelle findet.
    """
    text = " ".join(str(value).split())
    if len(text) > MAX_QUOTED_CHARS:
        text = text[:MAX_QUOTED_CHARS] + "..."
    return f'"{text}"'


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
    return any(needle in comparable_text(text) for _, text in evidence_contexts(unit))


def _evidence_violation(unit: MatcherUnit, industry_id: str,
                        excerpt: str) -> Violation:
    """Benennt, warum eine Evidence nicht traegt - inklusive Parent-Fall.

    Der Parent-Fall bekommt eine eigene Meldung, weil er der haeufigste ist und
    das Modell sonst dieselbe unzulaessige Stelle erneut vorschlagen wuerde.
    """
    if unit.parent_context_text and comparable_text(excerpt) in comparable_text(
            unit.parent_context_text):
        return Violation(
            "MAP_EVIDENCE_FROM_PARENT",
            f"Die Evidence zu {quote_untrusted(industry_id)} stammt aus dem Text des "
            "uebergeordneten Committees. Der ist Kontext, kein Beleg: belege "
            "mit dem eigenen Namen oder dem eigenen Zustaendigkeitstext dieser "
            "Einheit oder entferne die Zuordnung.",
            unit.unit_id,
        )
    quellen = ", ".join(label for label, _ in evidence_contexts(unit))
    return Violation(
        "MAP_EVIDENCE_UNSUPPORTED",
        f"Die Evidence zu {quote_untrusted(industry_id)} kommt nicht woertlich und "
        f"zusammenhaengend in den erlaubten Quellen vor ({quellen}). Zitiere "
        "eine belegbare Stelle oder entferne die Zuordnung.",
        unit.unit_id,
    )


# -- Pruefung --------------------------------------------------------------
def _schema_violations(exc: ValidationError) -> tuple[Violation, ...]:
    """Uebersetzt Pydantic-Fehler in Verstoesse.

    Uebernommen werden nur Feldpfad und die von ``models.py`` selbst
    formulierte Meldung - beides stammt aus dem eigenen Vertrag, nicht aus dem
    Antworttext des Modells.
    """
    violations = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        violations.append(Violation(
            "MAP_SCHEMA_INVALID",
            f"{quote_untrusted(location) if location else 'Antwort'}: "
            f"{quote_untrusted(error.get('msg', 'ungueltig'))}",
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
        return ValidationOutcome(None, _schema_violations(exc))

    violations: list[Violation] = []

    if result.requested_batch_id != batch.batch_id:
        violations.append(Violation(
            "MAP_BATCH_ID_MISMATCH",
            f"requested_batch_id ist {quote_untrusted(result.requested_batch_id)}, "
            f"angefragt war {batch.batch_id}. Uebernimm die Batch-ID zeichengenau.",
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
            f"Nicht angefragt: {', '.join(quote_untrusted(u) for u in extra)}. "
            "Antworte ausschliesslich auf die angefragten Units.",
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

        for assignment in unit_result.assignments:
            if assignment.industry_id not in known_ids:
                violations.append(Violation(
                    "MAP_UNKNOWN_INDUSTRY",
                    f"industry_id {quote_untrusted(assignment.industry_id)} kommt "
                    "in der uebergebenen Taxonomie nicht vor. Verwende eine ID "
                    "aus der Liste, zeichengenau, oder entferne die Zuordnung.",
                    unit.unit_id,
                ))
            if not evidence_supported(unit, assignment.evidence_excerpt):
                violations.append(
                    _evidence_violation(unit, assignment.industry_id,
                                        assignment.evidence_excerpt))

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

    Ein Konflikt ist kein Fehler: getrennte Zustaendigkeiten koennen dieselbe
    Industry legitim beruehren. Er ist ein Reviewposten, der spaeter mit
    ``keep_a``, ``keep_b``, ``remove_both`` oder ``legitimate_overlap``
    entschieden wird - deshalb wird hier nur erkannt und nichts entfernt.
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
    "NARROW_KINDS", "MAX_QUOTED_CHARS", "Violation", "ValidationOutcome",
    "BoundaryConflict", "format_violations", "quote_untrusted", "evidence_contexts", "evidence_supported",
    "validate_payload", "family_id", "find_boundary_conflicts",
]
