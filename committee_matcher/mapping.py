"""Mappingkern: Schema, Batching, Context-Preflight, Requestbau, Repair (Plan 2.5, P6).

Dieses Modul fuehrt einen Batch vom Zuschnitt bis zum geprueften Ergebnis. Die
Pruefung der Antwort liegt in ``validation.py``; alles Runweite - Zustand,
Persistenz, Kostenwachen, Cache, Wiederholung nach Providerfehlern - entsteht
in P7. Der Requestbau bleibt bewusst als eigene Funktionsgruppe erkennbar und
ist ohne Provider testbar.

Vier Zusagen des Vertrags werden hier eingeloest:

* **Structured Output** - das JSON-Schema wird aus dem Vertrag 2.5 erzeugt und
  per Golden Test eingefroren. ``industry_id`` bleibt bewusst ein freier String
  statt einer Enum ueber alle Snapshot-IDs: die Enum waere zwar strenger, wuerde
  das Schema aber um mehrere hundert Werte aufblaehen und den Lauf an eine
  Schemafaehigkeit binden, die nicht jeder Endpoint zuverlaessig beherrscht.
  Unbekannte IDs faengt stattdessen die formale Pruefung samt Repair ab.
* **Batching 1/3 mit Split** - die konfigurierte Batchgroesse ist der Wunsch,
  nicht die Zusage. Passt ein Batch nicht in das freigegebene Kontextfenster,
  wird 3->2->1 geteilt; passt eine einzelne Unit nicht, blockiert der Lauf.
* **Nie still kuerzen** - weder Zustaendigkeitstexte noch Taxonomie werden
  beschnitten. Zu grosse Eingaben sind ein Befund, kein Anlass zum Kuerzen.
* **Nie still korrigieren** - eine beanstandete Antwort geht mit ihrer
  Verstossliste zurueck an das Modell, hoechstens
  ``COMMITTEE_MATCHER_MAX_REPAIR_ATTEMPTS`` mal. Bleibt ein Verstoss, scheitert
  der Batch mit benannten Gruenden statt zurechtgestutzt zu werden.

Die Kontextschaetzung arbeitet ohne Tokenizer: das Paket bringt keinen mit, und
ein modellspezifischer Tokenizer waere bei freier Providerwahl ohnehin nur eine
Naeherung. Deshalb wird bewusst mit einem zu kleinen Zeichen-je-Token-Wert
gerechnet (Default 3.0 statt real ~4), sodass die Schaetzung ueber- statt
unterschaetzt. Der Wert ist konfigurierbar.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from .errors import ErrorCollector, Stage
from .models import (
    MAX_CONFIDENCE,
    MAX_UNITS_PER_BATCH,
    MIN_CONFIDENCE,
    InputBasis,
    MappingBatchResult,
    MappingDecision,
    MatcherUnit,
    TaxonomySnapshot,
)
from .prompts import SystemPrompt, untrusted_block
from .provider import StructuredOutputProvider, StructuredRequest, StructuredResponse
from .settings_view import OfflineSettings, ProviderSettings
from .status import ExitCode
from .validation import Violation, format_violations, validate_payload

#: Name des erzwungenen Ausgabeschemas; geht als ``schema_name`` in den Call.
SCHEMA_NAME = "MappingBatchResult"

#: Fester Aufschlag auf jede Schaetzung: Rollenmarkierungen, Schemauebertragung
#: und Formatierungsanteile, die nicht im gemessenen Text stehen.
FIXED_OVERHEAD_TOKENS = 512

#: Zulaessige Batchgroessen nach einem Context-Split (Plan 2.5: 3->2->1).
SPLIT_SEQUENCE: tuple[int, ...] = (3, 2, 1)


# -- Ausgabeschema ---------------------------------------------------------
def _assignment_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "industry_id": {
                "type": "string",
                "description": "Exakte industry_id aus der uebergebenen Taxonomie.",
            },
            "confidence": {
                "type": "integer",
                "enum": list(range(MIN_CONFIDENCE, MAX_CONFIDENCE + 1)),
                "description": "Belegstaerke der Zuordnung nach der Rubrik im Systemprompt.",
            },
            "evidence_excerpt": {
                "type": "string",
                "description": "Woertlicher zusammenhaengender Ausschnitt aus dem "
                               "Zustaendigkeitstext dieser Unit.",
            },
            "rationale_short": {
                "type": "string",
                "description": "Ein bis zwei Saetze, die Evidence und Kerngeschaeft verbinden.",
            },
        },
        "required": ["industry_id", "confidence", "evidence_excerpt", "rationale_short"],
    }


def _unit_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "unit_id": {
                "type": "string",
                "description": "Unveraendert aus der Anfrage uebernehmen.",
            },
            "decision": {
                "type": "string",
                "enum": [decision.value for decision in MappingDecision],
            },
            # Optionale Vertragsfelder werden als nullable notiert statt aus
            # "required" entfernt: strikte Schemamodi verlangen, dass jeder
            # Schluessel gelistet ist, und ein fehlendes Feld waere sonst nicht
            # von einem bewusst leeren zu unterscheiden.
            "no_mapping_reason": {
                "type": ["string", "null"],
                "description": "Nur bei no_mapping; sonst null.",
            },
            "broad_scope_exception": {"type": "boolean"},
            "broad_scope_reason": {
                "type": ["string", "null"],
                "description": "Nur bei mehr als zwei Zuordnungen; sonst null.",
            },
            "assignments": {
                "type": "array",
                "items": _assignment_schema(),
            },
        },
        "required": ["unit_id", "decision", "no_mapping_reason",
                     "broad_scope_exception", "broad_scope_reason", "assignments"],
    }


def batch_schema() -> dict:
    """JSON-Schema des Mappingergebnisses (Plan 2.5).

    Das Schema bildet die Form ab, nicht die Fachregeln: Anzahlgrenzen,
    Broad-Scope-Bedingungen und Snapshotbindung pruefen ``models.py`` und
    ``validation.py``. Ein Schema, das jede Regel erzwingen wollte, waere weder
    ausdrueckbar noch von allen Endpoints unterstuetzt.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "requested_batch_id": {
                "type": "string",
                "description": "Unveraendert aus der Anfrage uebernehmen.",
            },
            "units": {
                "type": "array",
                "items": _unit_schema(),
                "description": "Jede angeforderte Unit genau einmal.",
            },
        },
        "required": ["requested_batch_id", "units"],
    }


# -- Batches ---------------------------------------------------------------
@dataclass(frozen=True)
class MappingBatch:
    """Ein Mappingcall mit 1 bis 3 Units."""

    batch_id: str
    units: tuple[MatcherUnit, ...]

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self.units)

    def unit(self, unit_id: str) -> MatcherUnit | None:
        for unit in self.units:
            if unit.unit_id == unit_id:
                return unit
        return None


def batch_id_for(unit_ids: "tuple[str, ...] | list[str]") -> str:
    """Deterministische Batch-ID aus den enthaltenen Units.

    Deterministisch, damit derselbe Zuschnitt nach einem Resume dieselbe ID
    traegt und Anfrage und Antwort ueber Laeufe hinweg zusammenfuehrbar
    bleiben. Die Reihenfolge geht bewusst ein: sie ist Teil der Anfrage.
    """
    digest = hashlib.sha256("|".join(unit_ids).encode("utf-8")).hexdigest()
    return f"batch-{digest[:12]}"


def make_batches(units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
                 size: int) -> tuple[MappingBatch, ...]:
    """Teilt Units in Batches der gewuenschten Groesse.

    Der letzte Batch darf kleiner sein; das ist kein Split, sondern der Rest.
    """
    if not 1 <= size <= MAX_UNITS_PER_BATCH:
        raise ValueError(f"Batchgroesse 1..{MAX_UNITS_PER_BATCH} erwartet, {size} erhalten")
    ordered = tuple(units)
    chunks = [ordered[start:start + size] for start in range(0, len(ordered), size)]
    return tuple(MappingBatch(batch_id_for([u.unit_id for u in chunk]), chunk)
                 for chunk in chunks)


# -- Rendering -------------------------------------------------------------
def render_taxonomy(snapshot: TaxonomySnapshot) -> str:
    """Die vollstaendige Taxonomie als Auswahlliste - nie gekuerzt."""
    lines = [
        f"- {industry.industry_id} | {industry.industry_name} "
        f"| Sektor: {industry.sector_name} | {industry.description}"
        for industry in snapshot.industries
    ]
    return "\n".join(lines)


def render_unit(unit: MatcherUnit) -> str:
    """Eine Unit als Datenabschnitt.

    Der Parent-Text erscheint ausdruecklich als Kontext und nie als Beleg
    (Plan 2.2); ``name_only`` bekommt gar keinen Zustaendigkeitstext, weil dort
    ausschliesslich der Unitname belegen darf.
    """
    lines = [
        f"## unit_id: {unit.unit_id}",
        f"Typ: {unit.kind.value}",
        f"Kammer: {unit.chamber}",
        f"Committee: {unit.committee_name}",
        f"Name der Einheit: {unit.unit_name}",
    ]
    if unit.parent_committee_name:
        lines.append(f"Uebergeordnetes Committee: {unit.parent_committee_name}")
    if unit.input_basis is InputBasis.NAME_ONLY:
        lines.append(
            "Zustaendigkeitstext: nicht vorhanden. Belege ausschliesslich mit "
            "dem Namen der Einheit.")
    else:
        lines.append(f"Zustaendigkeitstext: {unit.evidence_text}")
    if unit.parent_context_text:
        lines.append(
            "Kontext des uebergeordneten Committees (nur zum Verstaendnis, "
            f"nicht als Beleg zulaessig): {unit.parent_context_text}")
    return "\n".join(lines)


def render_units(units: "tuple[MatcherUnit, ...] | list[MatcherUnit]") -> str:
    return "\n\n".join(render_unit(unit) for unit in units)


def build_user_content(batch: MappingBatch, snapshot: TaxonomySnapshot) -> str:
    """Nutzerteil eines Mappingcalls.

    Die Auftragszeilen stehen ausserhalb der Datenbloecke: was das Modell tun
    soll, darf nicht aus einer fremden Datei stammen. Die Unit-IDs sind Slugs
    ohne Whitespace und deshalb auch ausserhalb des Blocks unbedenklich.
    """
    header = [
        f"Batch: {batch.batch_id}",
        "Angeforderte Units (jede genau einmal beantworten, unit_id "
        f"zeichengenau uebernehmen): {', '.join(batch.unit_ids)}",
        f"Taxonomie: {len(snapshot.industries)} Industries, Snapshot "
        f"{snapshot.snapshot_id}.",
    ]
    return "\n".join([
        "\n".join(header),
        "",
        untrusted_block("TAXONOMIE", render_taxonomy(snapshot)),
        "",
        untrusted_block("UNITS", render_units(batch.units)),
    ])


def build_repair_content(batch: MappingBatch, snapshot: TaxonomySnapshot,
                         previous_payload: str, violations_text: str) -> str:
    """Nutzerteil eines Reparaturcalls.

    Die vorherige Antwort ist Modelloutput und damit ebenfalls kein
    vertrauenswuerdiger Text; sie wird deshalb wie Fremddaten gerahmt.
    """
    return "\n".join([
        build_user_content(batch, snapshot),
        "",
        untrusted_block("VORHERIGE_ANTWORT", previous_payload),
        "",
        "Festgestellte Verstoesse (verbindlich, von der formalen Pruefung "
        "erzeugt):",
        violations_text,
    ])


# -- Kontext ---------------------------------------------------------------
@dataclass(frozen=True)
class ContextBudget:
    """Aufteilung des freigegebenen Kontextfensters."""

    context_tokens: int
    max_output_tokens: int
    reasoning_reserve_tokens: int

    @property
    def input_tokens(self) -> int:
        """Was nach Ausgabe- und Reasoningreserve fuer die Eingabe bleibt."""
        return self.context_tokens - self.max_output_tokens - self.reasoning_reserve_tokens


def context_budget(settings: ProviderSettings) -> ContextBudget:
    return ContextBudget(
        context_tokens=settings.endpoint_context_tokens,
        max_output_tokens=settings.max_output_tokens,
        reasoning_reserve_tokens=settings.reasoning_reserve_tokens,
    )


def estimate_tokens(text: str, chars_per_token: float) -> int:
    """Konservative Tokenschaetzung eines Textes.

    Aufgerundet und mit einem bewusst zu kleinen Zeichen-je-Token-Wert, damit
    die Schaetzung ueber- statt unterschaetzt: eine zu niedrige Schaetzung
    fuehrt zu einer abgeschnittenen Antwort, eine zu hohe nur zu einem
    kleineren Batch.
    """
    if chars_per_token <= 0:
        raise ValueError("chars_per_token muss groesser als 0 sein")
    return math.ceil(len(text) / chars_per_token)


def estimate_request_tokens(system_prompt: str, user_content: str,
                            chars_per_token: float) -> int:
    """Geschaetzte Eingabetokens eines Calls einschliesslich Fixaufschlag."""
    return (estimate_tokens(system_prompt, chars_per_token)
            + estimate_tokens(user_content, chars_per_token)
            + FIXED_OVERHEAD_TOKENS)


def _blocker(collector: ErrorCollector, code: str, obj: str, message: str,
             hint: str) -> None:
    collector.blocker(code, Stage.MAPPING, obj, message, hint,
                      exit_code=ExitCode.PREFLIGHT_BLOCKED)


def plan_batches(collector: ErrorCollector,
                 units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
                 snapshot: TaxonomySnapshot,
                 prompt: SystemPrompt,
                 offline: OfflineSettings,
                 provider_settings: ProviderSettings) -> tuple[MappingBatch, ...] | None:
    """Bildet Batches und teilt sie, bis jeder in das Kontextfenster passt.

    Gibt ``None`` zurueck, sobald ein Batch auch als Einzelunit nicht passt
    oder das Budget selbst unbrauchbar ist; der Befund steht dann im Collector
    und traegt Exit-Code 4. Es wird nie gekuerzt (Plan 2.5).
    """
    budget = context_budget(provider_settings)
    if budget.input_tokens <= 0:
        _blocker(
            collector, "MAP_CONTEXT_BUDGET_INVALID", "context_budget",
            f"Nach Ausgabereserve ({budget.max_output_tokens}) und "
            f"Reasoningreserve ({budget.reasoning_reserve_tokens}) bleiben von "
            f"{budget.context_tokens} Kontexttokens keine fuer die Eingabe.",
            "COMMITTEE_MATCHER_ENDPOINT_CONTEXT_TOKENS anheben oder die "
            "Reserven senken; ohne Eingabebudget ist kein Call moeglich.",
        )
        return None

    ordered = tuple(units)
    if not ordered:
        return ()

    start_size = min(offline.batch_size, MAX_UNITS_PER_BATCH)
    planned: list[MappingBatch] = []
    for batch in make_batches(ordered, start_size):
        split = _split_to_fit(collector, batch, snapshot, prompt, offline, budget)
        if split is None:
            return None
        planned.extend(split)
    return tuple(planned)


def _split_to_fit(collector: ErrorCollector, batch: MappingBatch,
                  snapshot: TaxonomySnapshot, prompt: SystemPrompt,
                  offline: OfflineSettings,
                  budget: ContextBudget) -> tuple[MappingBatch, ...] | None:
    """Teilt einen zu grossen Batch entlang 3->2->1."""
    estimated = estimate_request_tokens(
        prompt.text, build_user_content(batch, snapshot), offline.chars_per_token)
    if estimated <= budget.input_tokens:
        return (batch,)

    if len(batch.units) == 1:
        unit = batch.units[0]
        _blocker(
            collector, "MAP_UNIT_TOO_LARGE", unit.unit_id,
            f"Die Unit passt allein nicht in das Eingabebudget: geschaetzt "
            f"{estimated} Tokens gegen {budget.input_tokens} verfuegbare "
            f"(Fenster {budget.context_tokens} minus Ausgabe "
            f"{budget.max_output_tokens} und Reasoning "
            f"{budget.reasoning_reserve_tokens}).",
            "Kontextfenster eines groesseren Endpoints freigeben oder die "
            "Reserven senken. Zustaendigkeitstext und Taxonomie werden nie "
            "gekuerzt.",
        )
        return None

    smaller = next(size for size in SPLIT_SEQUENCE if size < len(batch.units))
    result: list[MappingBatch] = []
    for piece in make_batches(batch.units, smaller):
        split = _split_to_fit(collector, piece, snapshot, prompt, offline, budget)
        if split is None:
            return None
        result.extend(split)
    return tuple(result)


# -- Requests --------------------------------------------------------------
def build_request(batch: MappingBatch, snapshot: TaxonomySnapshot,
                  prompt: SystemPrompt,
                  provider_settings: ProviderSettings) -> StructuredRequest:
    """Mappingcall eines Batches."""
    return StructuredRequest(
        call_id=batch.batch_id,
        system_prompt=prompt.text,
        user_content=build_user_content(batch, snapshot),
        schema_name=SCHEMA_NAME,
        json_schema=batch_schema(),
        max_output_tokens=provider_settings.max_output_tokens,
    )


def build_repair_request(batch: MappingBatch, snapshot: TaxonomySnapshot,
                         prompt: SystemPrompt, provider_settings: ProviderSettings,
                         *, attempt: int, previous_payload: str,
                         violations_text: str) -> StructuredRequest:
    """Reparaturcall eines Batches.

    Die ``call_id`` traegt den Versuch, damit Auditartefakte Erstantwort und
    Reparatur nicht vermischen.
    """
    return StructuredRequest(
        call_id=f"{batch.batch_id}-repair-{attempt}",
        system_prompt=prompt.text,
        user_content=build_repair_content(
            batch, snapshot, previous_payload, violations_text),
        schema_name=SCHEMA_NAME,
        json_schema=batch_schema(),
        max_output_tokens=provider_settings.max_output_tokens,
    )


def fits_context(request: StructuredRequest, offline: OfflineSettings,
                 provider_settings: ProviderSettings) -> tuple[bool, int, int]:
    """Prueft einen fertigen Request gegen das Eingabebudget.

    Gibt ``(passt, geschaetzt, verfuegbar)`` zurueck. Gebraucht wird das vor
    allem fuer Reparaturcalls: sie tragen zusaetzlich die vorherige Antwort und
    die Verstossliste und koennen deshalb ueber das Budget wachsen, obwohl der
    Erstcall passte.
    """
    budget = context_budget(provider_settings)
    estimated = estimate_request_tokens(
        request.system_prompt, request.user_content, offline.chars_per_token)
    return estimated <= budget.input_tokens, estimated, budget.input_tokens


# -- Ausfuehrung eines Batches ---------------------------------------------
@dataclass(frozen=True)
class CallRecord:
    """Ein einzelner Providercall dieses Batches, fuer das Auditartefakt."""

    #: ``mapping`` fuer den Erstcall, ``repair`` fuer jede Reparatur.
    kind: str
    request: StructuredRequest
    response: StructuredResponse
    violations: tuple[Violation, ...]


@dataclass(frozen=True)
class BatchOutcome:
    """Ergebnis eines Batches einschliesslich aller Versuche.

    ``result`` ist gesetzt, sobald eine Antwort die formale Pruefung
    vollstaendig bestanden hat - egal ob im Erstcall oder nach einer Reparatur.
    ``blocked`` markiert Abbrueche, die kein zweiter Call heilen kann (etwa ein
    Batch, der nicht in das Kontextfenster passt).
    """

    batch: MappingBatch
    result: MappingBatchResult | None
    violations: tuple[Violation, ...]
    calls: tuple[CallRecord, ...] = ()
    blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.result is not None

    @property
    def repairs(self) -> int:
        return sum(1 for call in self.calls if call.kind == "repair")

    @property
    def cost_usd(self) -> float | None:
        """Summe der gemeldeten Kosten; ``None``, sobald ein Call sie verschweigt.

        Teilsummen waeren gefaehrlicher als kein Wert: die Kostenwache in P7
        muss einen unvollstaendigen Betrag als unbekannt behandeln koennen.
        """
        total = 0.0
        for call in self.calls:
            if call.response.cost_usd is None:
                return None
            total += call.response.cost_usd
        return total

    @property
    def total_tokens(self) -> int:
        return sum(call.response.prompt_tokens + call.response.completion_tokens
                   for call in self.calls)


def _payload_text(response: StructuredResponse) -> str:
    """Die vorherige Antwort so, wie sie dem Modell erneut vorgelegt wird."""
    return json.dumps(response.payload, ensure_ascii=False, indent=2, sort_keys=True)


def _context_violation(request: StructuredRequest, estimated: int,
                       available: int, *, repair: bool) -> Violation:
    code = "MAP_REPAIR_CONTEXT_EXCEEDED" if repair else "MAP_CONTEXT_EXCEEDED"
    what = ("Der Reparaturcall traegt zusaetzlich die vorherige Antwort und die "
            "Verstossliste und") if repair else "Der Call"
    return Violation(
        code,
        f"{what} passt mit geschaetzten {estimated} Tokens nicht in die "
        f"{available} verfuegbaren Eingabetokens. Batchgroesse senken, "
        "Reserven anpassen oder ein groesseres Kontextfenster freigeben; "
        "gekuerzt wird nicht.",
    )


def map_batch(provider: StructuredOutputProvider,
              batch: MappingBatch,
              snapshot: TaxonomySnapshot,
              *,
              mapping_prompt: SystemPrompt,
              repair_prompt: SystemPrompt,
              offline: OfflineSettings,
              provider_settings: ProviderSettings) -> BatchOutcome:
    """Fuehrt einen Batch aus und repariert ihn bei formalen Verstoessen.

    Wirft ``ProviderCallError`` weiter; ueber Wiederholung entscheidet der
    Workflow.
    """
    request = build_request(batch, snapshot, mapping_prompt, provider_settings)
    fits, estimated, available = fits_context(request, offline, provider_settings)
    if not fits:
        return BatchOutcome(
            batch=batch, result=None, blocked=True,
            violations=(_context_violation(request, estimated, available, repair=False),))

    response = provider.complete(request)
    outcome = validate_payload(response.payload, batch, snapshot)
    calls = [CallRecord("mapping", request, response, outcome.violations)]
    if outcome.ok:
        return BatchOutcome(batch, outcome.result, (), tuple(calls))

    for attempt in range(1, offline.max_repair_attempts + 1):
        repair_request = build_repair_request(
            batch, snapshot, repair_prompt, provider_settings,
            attempt=attempt,
            previous_payload=_payload_text(response),
            violations_text=format_violations(outcome.violations),
        )
        fits, estimated, available = fits_context(
            repair_request, offline, provider_settings)
        if not fits:
            return BatchOutcome(
                batch=batch, result=None, calls=tuple(calls), blocked=True,
                violations=outcome.violations + (
                    _context_violation(repair_request, estimated, available, repair=True),))

        response = provider.complete(repair_request)
        outcome = validate_payload(response.payload, batch, snapshot)
        calls.append(CallRecord("repair", repair_request, response, outcome.violations))
        if outcome.ok:
            return BatchOutcome(batch, outcome.result, (), tuple(calls))

    return BatchOutcome(batch, None, outcome.violations, tuple(calls))


__all__ = [
    "SCHEMA_NAME", "FIXED_OVERHEAD_TOKENS", "SPLIT_SEQUENCE",
    "MappingBatch", "ContextBudget",
    "batch_schema", "batch_id_for", "make_batches",
    "render_taxonomy", "render_unit", "render_units",
    "build_user_content", "build_repair_content",
    "context_budget", "estimate_tokens", "estimate_request_tokens",
    "plan_batches", "build_request", "build_repair_request", "fits_context",
    "CallRecord", "BatchOutcome", "map_batch",
]
