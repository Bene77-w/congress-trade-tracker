"""Runzustand, Auditspur, Kostenkontrolle und Cache/Resume (Plan 2.6).

Dieses Modul haelt den Lauf zusammen, ohne selbst zu mappen. Es beantwortet
vier Fragen:

- Was ist fuer diesen Lauf eingefroren?  ``RunManifest``
- Was ist bisher passiert?               ``RunState`` (atomar persistiert)
- Darf der naechste Call Geld kosten?    ``CostGuard``
- Gibt es das Ergebnis schon?            ``cache_key`` + ``RunState.cached``

Kostenwache und Auditspur sitzen bewusst am Providerrand (``AuditedProvider``)
statt im Mappingkern: so wird jeder Call erfasst - auch ein Reparaturcall -,
die Response liegt vor der Fachvalidierung auf der Platte, und ``mapping.py``
bleibt frei von Runzustand.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import mapping, paths, provider as provider_module
from .errors import Finding, MatcherError, Severity, Stage
from .models import MappingUnitResult, MatcherUnit
from .prompts import SystemPrompt
from .settings_view import MatcherSettings, OfflineSettings, ProviderSettings
from .status import ExitCode, MappingDecision, UnitStatus, WorkflowState

#: Verzeichnis der Runrevisionen; identisch mit dem der Taxonomiephase, damit
#: Snapshot, Calls und Ergebnisse eines Laufs beieinander liegen.
RUNS_DIR = "runs"
MAPPING_DIRNAME = "mapping"
CALLS_DIRNAME = "calls"
MANIFEST_FILENAME = "run_manifest.json"
STATE_FILENAME = "run_state.json"

#: Vertragsversionen der persistierten Formen. Ein fremder Wert fuehrt nie zu
#: einer Teilinterpretation, sondern zum Neuanfang beziehungsweise Blocker.
MANIFEST_VERSION = "run-manifest-v1"
STATE_VERSION = "run-state-v1"
CALL_AUDIT_VERSION = "call-audit-v1"
CALL_INTENT_VERSION = "call-intent-v1"
CACHE_KEY_VERSION = "cache-key-v1"

#: Inferenzparameter, die in den Cache-Key eingehen. Bewusst eine feste Liste:
#: ein spaeter hinzugefuegter Parameter darf nicht stillschweigend aus dem
#: Schluessel fallen.
CACHE_INFERENCE_FIELDS = ("max_output_tokens", "reasoning_reserve_tokens")

#: Welcher Unitstatus zu welchem Modellurteil gehoert. Andere Kombinationen
#: gibt es nicht; ``not_decided`` und ``failed`` tragen nie ein Ergebnis.
STATUS_FOR_DECISION: dict[MappingDecision, UnitStatus] = {
    MappingDecision.MAPPED: UnitStatus.MAPPED,
    MappingDecision.NO_MAPPING: UnitStatus.NO_MAPPING,
}


# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

def mapping_path(run_id: str, *parts: str, create_parents: bool = False) -> Path:
    return paths.artifact_path(RUNS_DIR, run_id, MAPPING_DIRNAME, *parts,
                               create_parents=create_parents)


def manifest_path(run_id: str, *, create_parents: bool = False) -> Path:
    return mapping_path(run_id, MANIFEST_FILENAME, create_parents=create_parents)


def state_path(run_id: str, *, create_parents: bool = False) -> Path:
    return mapping_path(run_id, STATE_FILENAME, create_parents=create_parents)


def call_path(run_id: str, call_id: str, *, create_parents: bool = False) -> Path:
    """Auditdatei eines Calls.

    ``call_id`` geht durch die Pfadwache und darf keinen Pfadanteil enthalten;
    er stammt aus dem Mappingkern, nicht aus einer Providerantwort.
    """
    return mapping_path(run_id, CALLS_DIRNAME, f"{call_id}.json",
                        create_parents=create_parents)


def intent_path(run_id: str, call_id: str, *, create_parents: bool = False) -> Path:
    """Vormerkung eines Calls, der gleich gesendet wird.

    Sie entsteht **vor** dem Netzaufruf und verschwindet erst, wenn die
    Auditdatei steht oder feststeht, dass der Provider die Anfrage abgewiesen
    hat. Eine liegengebliebene Vormerkung ist damit die einzige Spur, die einen
    bezahlten Call nach einem Prozessabsturz noch belegt.
    """
    return mapping_path(run_id, CALLS_DIRNAME, f"{call_id}.intent.json",
                        create_parents=create_parents)


def open_intents(run_id: str) -> tuple[str, ...]:
    """Call-IDs, deren Vormerkung ohne Auditdatei liegengeblieben ist.

    Jede davon ist ein Call mit offenem Ausgang: gesendet, moeglicherweise
    bezahlt, ohne verwertbare Antwort auf der Platte.
    """
    folder = mapping_path(run_id, CALLS_DIRNAME)
    if not folder.is_dir():
        return ()
    open_calls = []
    for entry in sorted(folder.glob("*.intent.json")):
        call_id = entry.name[: -len(".intent.json")]
        if not call_path(run_id, call_id).exists():
            open_calls.append(call_id)
    return tuple(open_calls)


def _write_json(target: Path, payload: object) -> Path:
    return paths.write_text_atomic(
        target, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Cache-Key
# ---------------------------------------------------------------------------

def schema_hash(json_schema: Mapping) -> str:
    """Hash des erzwungenen Ausgabeschemas; sortiert, damit er stabil bleibt."""
    text = json.dumps(json_schema, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inference_fingerprint(provider_settings: ProviderSettings) -> dict[str, object]:
    """Die Inferenzparameter, die den fachlichen Schluessel mitbestimmen."""
    return {name: getattr(provider_settings, name) for name in CACHE_INFERENCE_FIELDS}


def cache_key(*, mapping_input_hash: str, taxonomy_content_hash: str,
              prompt: SystemPrompt, schema_name: str, schema_sha256: str,
              model: str, inference: Mapping[str, object]) -> str:
    """Fachlicher Schluessel eines Unitergebnisses.

    Enthalten ist ausschliesslich, was das Ergebnis inhaltlich bestimmt:
    Unitinput, Taxonomiestand, Prompt, Schema, Modell und Inferenzparameter.
    Volatile Provenienz - Run-, Snapshot-, Call-, Evaluations- und
    Wiederholungs-IDs - bleibt bewusst draussen, sonst waere jeder Lauf ein
    Cache-Miss.

    Auch der geroutete Endpoint bleibt draussen. Plan 2.6 nennt ihn noch als
    Schluesselbestandteil; das stammt aus der Zeit vor dem Gate-B-Beschluss
    vom 2026-08-21, der die Providerwahl ausdruecklich freigegeben hat. Bei
    freier Wahl ist der Endpoint des naechsten Calls im Voraus unbekannt - ein
    Schluessel mit Endpoint waere nie wieder treffbar und der Cache damit tot.
    Der Endpoint bleibt Auditprovenienz und wird je Call protokolliert.
    """
    payload = {
        "version": CACHE_KEY_VERSION,
        "mapping_input_hash": mapping_input_hash,
        "taxonomy_content_hash": taxonomy_content_hash,
        "prompt_id": prompt.prompt_id,
        "prompt_sha256": prompt.sha256,
        "schema_name": schema_name,
        "schema_sha256": schema_sha256,
        "model": model,
        "inference": dict(sorted(inference.items())),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unit_cache_key(unit: MatcherUnit, *, taxonomy_content_hash: str,
                   prompt: SystemPrompt, model: str,
                   inference: Mapping[str, object],
                   json_schema: Mapping | None = None) -> str:
    """Cache-Key einer Unit; ohne fachlichen Inputhash nicht bildbar.

    ``inference`` muss genau ``CACHE_INFERENCE_FIELDS`` enthalten. Ein
    unvollstaendiges Mapping wuerde den Schluessel gegenueber einem
    ergebnisrelevanten Parameter blind machen - ein Fehler des Aufrufers, kein
    Laufzustand, deshalb ``ValueError``.
    """
    if not unit.mapping_input_hash:
        raise ValueError(f"Unit {unit.unit_id} hat keinen mapping_input_hash")
    if set(inference) != set(CACHE_INFERENCE_FIELDS):
        raise ValueError(
            f"inference muss genau {set(CACHE_INFERENCE_FIELDS)} enthalten, "
            f"erhalten: {sorted(inference)}")
    schema = json_schema if json_schema is not None else mapping.batch_schema()
    return cache_key(
        mapping_input_hash=unit.mapping_input_hash,
        taxonomy_content_hash=taxonomy_content_hash,
        prompt=prompt,
        schema_name=mapping.SCHEMA_NAME,
        schema_sha256=schema_hash(schema),
        model=model,
        inference=inference,
    )


# ---------------------------------------------------------------------------
# Kosten
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostRates:
    """Preisdeckel des Laufs in USD je Million Token.

    Gerechnet wird mit dem Deckel, nicht mit einem erwarteten Preis: er ist die
    einzige Zahl, die vor dem Call feststeht, und die Routingbedingungen lassen
    keinen teureren Endpoint zu.
    """

    prompt: float
    completion: float

    @classmethod
    def from_settings(cls, provider_settings: ProviderSettings) -> "CostRates":
        prices = provider_settings.max_price_usd_per_mtok
        return cls(prompt=float(prices["prompt"]), completion=float(prices["completion"]))

    def worst_case_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.prompt + completion_tokens * self.completion) / 1_000_000


def request_worst_case_usd(request: provider_module.StructuredRequest,
                           rates: CostRates, offline: OfflineSettings) -> float:
    """Obergrenze der Kosten eines Calls.

    Eingabe wird geschaetzt (dieselbe Schaetzung wie im Context-Gate,
    absichtlich zu hoch), Ausgabe mit ``max_output_tokens`` angesetzt - mehr
    kann der Endpoint nicht liefern.
    """
    prompt_tokens = mapping.estimate_request_tokens(
        request.system_prompt, request.user_content, offline.chars_per_token,
        json_schema=request.json_schema)
    return rates.worst_case_usd(prompt_tokens, request.max_output_tokens)


@dataclass
class CostGuard:
    """Budgetwache eines Laufs.

    Gefuehrt werden zwei Betraege: tatsaechlich gemeldete Kosten und
    Reservierungen fuer Calls, deren Kosten unbekannt blieben. Beide zaehlen
    gegen das Budget - ein Call ohne Kostenmeldung ist nicht kostenlos, nur
    ungeklaert.

    ``enabled=False`` schaltet allein das Blockieren ab, nicht die Buchfuehrung:
    der Kostenbericht bleibt vollstaendig.
    """

    budget_usd: float
    rates: CostRates
    enabled: bool = True
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    unknown_calls: int = 0
    calls: int = 0

    @property
    def committed_usd(self) -> float:
        return self.spent_usd + self.reserved_usd

    @property
    def remaining_usd(self) -> float:
        return self.budget_usd - self.committed_usd

    def _blocker(self, obj: str, amount: float) -> Finding:
        return Finding(
            severity=Severity.BLOCKER,
            code="COST_BUDGET_EXCEEDED",
            stage=Stage.COST,
            obj=obj,
            message=(f"Worst-Case-Kosten {amount:.6f} USD passen nicht in den "
                     f"Rest von {self.remaining_usd:.6f} USD "
                     f"(Budget {self.budget_usd:.6f} USD, gebucht "
                     f"{self.committed_usd:.6f} USD)."),
            hint=("Budget in config/settings.py erhoehen oder den Laufumfang "
                  "verkleinern; ungeklaerte Calls zuerst abgleichen."),
            exit_code=ExitCode.COST_BLOCKED,
        )

    def check(self, amount_usd: float, obj: str) -> Finding | None:
        """Prueft, ob ``amount_usd`` noch in das Budget passt.

        Gibt den Blocker zurueck, statt ihn zu werfen: der Aufrufer entscheidet,
        ob der Lauf endet oder der Befund nur gesammelt wird.
        """
        if not self.enabled:
            return None
        if amount_usd <= self.remaining_usd:
            return None
        return self._blocker(obj, amount_usd)

    def settle(self, worst_case_usd: float,
               response: provider_module.StructuredResponse | None = None,
               *, cost_usd: float | None = None) -> None:
        """Verbucht einen ausgefuehrten Call.

        Die Kosten stammen aus der Antwort oder - wenn ein Call zwar scheiterte,
        die Antwort aber eine Kostenmeldung trug - aus ``cost_usd``. Fehlt beides,
        wird der Worst Case reserviert. Reservierungen loest erst die Reconciliation
        ueber die Generation-ID auf, nie eine Schaetzung.
        """
        self.calls += 1
        reported = cost_usd if cost_usd is not None else getattr(response, "cost_usd", None)
        if reported is not None:
            self.spent_usd += float(reported)
            return
        self.reserved_usd += worst_case_usd
        self.unknown_calls += 1

    def report(self) -> dict[str, object]:
        """Kostenstand fuer Runmanifest und Terminalbericht."""
        return {
            "budget_usd": self.budget_usd,
            "spent_usd": round(self.spent_usd, 6),
            "reserved_usd": round(self.reserved_usd, 6),
            "committed_usd": round(self.committed_usd, 6),
            "remaining_usd": round(self.remaining_usd, 6),
            "calls": self.calls,
            "unknown_calls": self.unknown_calls,
            "guard_enabled": self.enabled,
        }


def plan_cost_usd(requests: Sequence[provider_module.StructuredRequest],
                  rates: CostRates, offline: OfflineSettings) -> float:
    """Worst-Case-Kosten aller geplanten Erstcalls samt erlaubter Reparaturen.

    Ein Batch darf ``max_repair_attempts`` mal nachgebessert werden; die
    Vorabpruefung rechnet mit dem teuersten Fall, damit ein Lauf nicht auf
    halber Strecke am Budget scheitert.
    """
    factor = 1 + max(0, offline.max_repair_attempts)
    return sum(request_worst_case_usd(request, rates, offline)
               for request in requests) * factor


# ---------------------------------------------------------------------------
# Auditspur am Providerrand
# ---------------------------------------------------------------------------

def audit_record(request: provider_module.StructuredRequest,
                 response: provider_module.StructuredResponse) -> dict[str, object]:
    """Bereinigte Auditform eines Calls.

    Enthalten sind Provenienz und die rohe Antwortnutzlast - nicht enthalten
    sind Systemprompt, Nutzdaten und jede Form von Zugangsdaten. Der Prompt
    liegt bereits versioniert als Kontrollinput vor; ihn je Call zu kopieren
    wuerde den Auditordner vervielfachen, ohne etwas nachweisbar zu machen.
    """
    entry = response.manifest_entry()
    entry.update({
        "version": CALL_AUDIT_VERSION,
        "schema_name": request.schema_name,
        "max_output_tokens": request.max_output_tokens,
        "payload": response.payload,
    })
    return entry


@dataclass
class AuditedProvider:
    """Providerdekorator: persistiert jede Antwort und fuehrt die Kostenwache.

    Die Reihenfolge ist Vertrag: pruefen, senden, persistieren, verbuchen,
    zurueckgeben. Die Antwort liegt damit auf der Platte, bevor irgendeine
    Fachpruefung sie sehen kann; ein Absturz waehrend der Validierung verliert
    keinen bezahlten Call.

    Ein fehlgeschlagener Call wird ebenfalls verbucht: ob der Provider ihn
    abgerechnet hat, ist von hier aus nicht feststellbar, also gilt der Worst
    Case als reserviert.
    """

    inner: provider_module.StructuredOutputProvider
    run_id: str
    guard: CostGuard
    offline: OfflineSettings
    #: Befunde der Kostenwache; der Lauf wertet sie nach dem Batch aus.
    blockers: list[Finding] = field(default_factory=list)
    audited: list[str] = field(default_factory=list)
    #: Calls, deren Ausgang offen blieb. Sie halten ihre Reservierung, bis der
    #: Abgleich ueber die Generation-ID sie aufloest.
    unknown: list["UnknownCall"] = field(default_factory=list)

    def complete(self, request: provider_module.StructuredRequest
                 ) -> provider_module.StructuredResponse:
        worst_case = request_worst_case_usd(request, self.guard.rates, self.offline)
        blocker = self.guard.check(worst_case, request.call_id)
        if blocker is not None:
            self.blockers.append(blocker)
            raise CostBlocked([blocker], "Kostenbudget erschoepft")
        self._reserve(request, worst_case)
        try:
            response = self.inner.complete(request)
        except provider_module.ProviderCallError as exc:
            raise self._failed(request, worst_case, exc) from None
        # Gebucht wird, sobald die Antwort vorliegt - vor der Persistenz. Sonst
        # bliebe ein bezahlter Call bei einem Schreibfehler ungebucht, und das
        # Budget haette Luft, die es nicht mehr hat.
        self.guard.settle(worst_case, response)
        self._persist(request, response, worst_case)
        return response

    def _reserve(self, request, worst_case: float) -> None:
        """Legt die Vormerkung an, bevor die Anfrage das Haus verlaesst."""
        _write_json(intent_path(self.run_id, request.call_id, create_parents=True), {
            "version": CALL_INTENT_VERSION,
            "run_id": self.run_id,
            "call_id": request.call_id,
            "schema_name": request.schema_name,
            "worst_case_usd": round(worst_case, 6),
        })

    def _release(self, call_id: str) -> None:
        """Loest die Vormerkung auf; nur nach belegter Nichtausfuehrung."""
        target = intent_path(self.run_id, call_id)
        if target.exists():
            target.unlink()

    def _mark_open(self, call_id: str, *, reason: str, reserved_usd: float,
                   generation_id: str | None = None,
                   cost_usd: float | None = None) -> None:
        """Schreibt fest, was ueber einen Call ohne Auditdatei bekannt ist.

        Die Vormerkung bleibt liegen und nimmt auf, was die Antwort noch
        hergab. Ohne sie waere ein bezahlter Call nach einem Prozessabbruch
        weder rekonstruierbar noch vom Wiederaufsetzen als bereits gesendet
        erkennbar - und wuerde ein zweites Mal bezahlt.
        """
        entry = UnknownCall(call_id=call_id, generation_id=generation_id,
                            reserved_usd=float(reserved_usd), reason=reason)
        self.unknown.append(entry)
        target = intent_path(self.run_id, call_id, create_parents=True)
        payload = dict(entry.as_payload())
        payload.update({
            "version": CALL_INTENT_VERSION,
            "run_id": self.run_id,
            "cost_usd": cost_usd,
        })
        try:
            _write_json(target, payload)
        except Exception:
            # Die Vormerkung aus ``_reserve`` liegt bereits und benennt den
            # Call; mehr ist an dieser Stelle nicht zu retten.
            pass

    def _failed(self, request, worst_case: float,
                exc: provider_module.ProviderCallError
                ) -> provider_module.ProviderCallError:
        """Verbucht einen gescheiterten Call und entscheidet ueber die Wiederholung.

        Drei Faelle: meldet die Antwort ihre Kosten, wird gebucht und der Ausgang
        gilt als geklaert. Steht fest, dass der Provider die Anfrage abgewiesen
        hat, kostet sie nichts und darf wiederholt werden. Bleibt der Ausgang
        offen, gilt der Worst Case als reserviert, der Call wird als ungeklaert
        vorgemerkt und nie automatisch wiederholt (Plan 2.6).
        """
        reason = exc.findings[0].code if exc.findings else "PROV_CALL_FAILED"
        if exc.cost_usd is not None:
            # Die Antwort kam an und trug ihre Kosten; unbrauchbar war nur ihr
            # Inhalt. Der Ausgang ist bekannt, die Spur fehlt trotzdem.
            self.guard.settle(worst_case, cost_usd=exc.cost_usd)
            self._mark_open(request.call_id, reason=reason,
                            reserved_usd=exc.cost_usd,
                            generation_id=exc.generation_id, cost_usd=exc.cost_usd)
            return exc
        if not exc.outcome_unknown:
            self._release(request.call_id)
            return exc
        self.guard.settle(worst_case, None)
        self._mark_open(request.call_id, reason=reason, reserved_usd=worst_case,
                        generation_id=exc.generation_id)
        exc.retryable = False
        return exc

    def _persist(self, request, response, worst_case: float = 0.0) -> None:
        target = call_path(self.run_id, request.call_id, create_parents=True)
        try:
            _write_json(target, audit_record(request, response))
        except Exception:
            # Jede Ausnahme nach bezahlter Antwort hat dieselbe Folge, nicht nur
            # ein OSError: die Pfadwache, ein Serialisierungsfehler oder ein
            # voller Datentraeger lassen denselben unbelegten Call zurueck.
            self._mark_open(request.call_id, reason="AUDIT_WRITE_FAILED",
                            reserved_usd=(response.cost_usd if response.cost_usd
                                          is not None else worst_case),
                            generation_id=response.generation_id,
                            cost_usd=response.cost_usd)
            raise
        self._release(request.call_id)
        self.audited.append(request.call_id)


class CostBlocked(MatcherError):
    """Der naechste Call wuerde das Budget sprengen; er wird nicht gesendet."""


# ---------------------------------------------------------------------------
# Runzustand
# ---------------------------------------------------------------------------

@dataclass
class UnitRecord:
    """Stand einer Unit im Lauf.

    ``result`` traegt das vollstaendig gepruefte Modellergebnis; nur damit ist
    eine Unit fuer Cache und Resume brauchbar. ``violations`` haelt die Gruende
    eines Fehlschlags fest, damit ein Resume nicht blind wiederholt.
    """

    unit_id: str
    status: UnitStatus
    cache_key: str
    result: MappingUnitResult | None = None
    violations: tuple[str, ...] = ()
    #: Herkunft des Ergebnisses. Sie gehoert zum Urteil, nicht zum Lauf: erst
    #: sie erlaubt es, eine Zeile der Long-CSV auf den Call zurueckzufuehren,
    #: der sie erzeugt hat - auch wenn der Cache sie Runden spaeter wieder
    #: hervorholt.
    call_id: str | None = None
    provider: str | None = None
    generation_id: str | None = None

    @property
    def reusable(self) -> bool:
        """Nur ein entschiedenes, geprueftes Ergebnis darf wiederverwendet werden.

        Status und Modellurteil muessen dasselbe sagen. Ein Eintrag mit Status
        ``mapped`` und Entscheidung ``no_mapping`` ist in sich widerspruechlich
        und wird nie uebernommen, egal wie er entstanden ist.
        """
        if self.result is None or self.result.unit_id != self.unit_id:
            return False
        expected = STATUS_FOR_DECISION.get(self.result.decision)
        return expected is not None and expected is self.status

    def as_payload(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "status": self.status.value,
            "cache_key": self.cache_key,
            "result": self.result.model_dump(mode="json") if self.result else None,
            "violations": list(self.violations),
            "call_id": self.call_id,
            "provider": self.provider,
            "generation_id": self.generation_id,
        }

    @classmethod
    def from_payload(cls, data: Mapping) -> "UnitRecord | None":
        """Liest einen Eintrag; alles Unstimmige gilt als nicht vorhanden."""
        try:
            unit_id = data["unit_id"]
            status = UnitStatus(data["status"])
            key = data["cache_key"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(unit_id, str) or not isinstance(key, str):
            return None
        raw = data.get("result")
        result: MappingUnitResult | None = None
        if raw is not None:
            try:
                result = MappingUnitResult.model_validate(raw)
            except Exception:
                return None
            if result.unit_id != unit_id:
                return None
        violations = data.get("violations") or []
        if not isinstance(violations, list) or any(
                not isinstance(item, str) for item in violations):
            return None
        provenance = {}
        for field_name in ("call_id", "provider", "generation_id"):
            value = data.get(field_name)
            if value is not None and not isinstance(value, str):
                return None
            provenance[field_name] = value
        return cls(unit_id=unit_id, status=status, cache_key=key,
                   result=result, violations=tuple(violations), **provenance)


@dataclass
class RunState:
    """Persistierter Laufzustand: Workflowstatus plus Stand je Unit.

    Workflowstatus und Unitstatus bleiben getrennt (Plan 2.6). Ein Lauf in
    ``outcome_unknown`` faellt nie in ein Mappingurteil zurueck; die Units
    behalten ihren eigenen, unabhaengigen Stand.
    """

    run_id: str
    state: WorkflowState = WorkflowState.PENDING
    units: dict[str, UnitRecord] = field(default_factory=dict)

    def record(self, entry: UnitRecord) -> None:
        self.units[entry.unit_id] = entry

    def cached(self, unit_id: str, key: str) -> UnitRecord | None:
        """Wiederverwendbarer Stand einer Unit unter genau diesem Schluessel."""
        entry = self.units.get(unit_id)
        if entry is None or entry.cache_key != key or not entry.reusable:
            return None
        return entry

    def pending(self, units: Iterable[MatcherUnit],
                keys: Mapping[str, str]) -> tuple[MatcherUnit, ...]:
        """Die Units, die noch laufen muessen.

        Fehlt ein Schluessel, laeuft die Unit - eine Unit ohne bildbaren
        Schluessel gilt nie als erledigt.
        """
        return tuple(unit for unit in units
                     if self.cached(unit.unit_id, keys.get(unit.unit_id, "")) is None)

    def counts(self) -> dict[str, int]:
        result = {status.value: 0 for status in UnitStatus}
        for entry in self.units.values():
            result[entry.status.value] += 1
        return result

    def as_payload(self) -> dict[str, object]:
        return {
            "version": STATE_VERSION,
            "run_id": self.run_id,
            "workflow_state": self.state.value,
            "units": [self.units[unit_id].as_payload()
                      for unit_id in sorted(self.units)],
        }

    def write(self) -> Path:
        return _write_json(state_path(self.run_id, create_parents=True),
                           self.as_payload())


class RunStateUnusable(MatcherError):
    """Der vorhandene Laufzustand ist nicht verwendbar; der Lauf haelt an."""


def _state_blocker(target: Path, detail: str) -> Finding:
    return Finding(
        severity=Severity.BLOCKER,
        code="RUN_STATE_MALFORMED",
        stage=Stage.WORKFLOW,
        obj=paths.relative_to_base(target),
        message=f"Laufzustand ist nicht verwendbar: {detail}",
        hint=("Nicht ueberschreiben: die Auditdateien unter calls/ zeigen, welche "
              "Calls bereits bezahlt wurden. Ein Neustart unter derselben Run-ID "
              "sendet diese Calls erneut und rechnet sie ein zweites Mal ab. "
              "Regelweg ist eine neue --run-id; den Zustand zu entfernen ist eine "
              "Entscheidung von Bene nach Sichtung der Auditdateien."),
        exit_code=ExitCode.PREFLIGHT_BLOCKED,
    )


def load_state(run_id: str) -> RunState:
    """Laedt den Laufzustand; ein vorhandener, unbrauchbarer Zustand blockiert.

    Fehlt die Datei, beginnt der Lauf frisch - dann ist auch nichts bezahlt
    worden. Ist sie vorhanden, aber unlesbar oder vertragswidrig, wird der Lauf
    angehalten statt neu begonnen: die Datei belegt, dass bereits gelaufen ist,
    und ein stiller Neustart wuerde bezahlte Calls ein zweites Mal senden. Ein
    teilweises Uebernehmen kommt ebenso wenig in Frage - dann wuerden einzelne
    Units aus einer kaputten Datei geglaubt.
    """
    target = state_path(run_id)
    if not target.exists():
        return RunState(run_id=run_id)

    def stop(detail: str) -> "RunState":
        raise RunStateUnusable([_state_blocker(target, detail)],
                               f"Laufzustand unbrauchbar: {detail}")

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        stop("kein gueltiges JSON.")
    if not isinstance(data, dict):
        stop("kein JSON-Objekt.")
    if data.get("version") != STATE_VERSION:
        stop(f"Version {data.get('version')!r} statt {STATE_VERSION!r}.")
    if data.get("run_id") != run_id:
        stop(f"gehoert zu Run {data.get('run_id')!r}.")
    try:
        state = WorkflowState(data.get("workflow_state"))
    except ValueError:
        stop(f"unbekannter Workflowstatus {data.get('workflow_state')!r}.")
    entries = data.get("units")
    if not isinstance(entries, list):
        stop("Unitliste fehlt oder ist keine Liste.")
    records: dict[str, UnitRecord] = {}
    for item in entries:
        if not isinstance(item, dict):
            stop("Uniteintrag ist kein JSON-Objekt.")
        entry = UnitRecord.from_payload(item)
        if entry is None:
            stop("Uniteintrag verletzt den Zustandsvertrag.")
        if entry.unit_id in records:
            # Ein Duplikat wuerde beim Einlesen still den frueheren Eintrag
            # ueberschreiben - also genau die teilweise Interpretation, die
            # ausgeschlossen sein soll.
            stop(f"Unit {entry.unit_id} kommt mehrfach vor.")
        records[entry.unit_id] = entry
    return RunState(run_id=run_id, state=state, units=records)


# ---------------------------------------------------------------------------
# Runmanifest
# ---------------------------------------------------------------------------

def build_manifest(*, run_id: str, settings: MatcherSettings,
                   provider_settings: ProviderSettings,
                   prompts_used: Sequence[SystemPrompt],
                   taxonomy_content_hash: str, snapshot_id: str,
                   input_manifest_hash: str, unit_count: int,
                   budget_usd: float, json_schema: Mapping | None = None,
                   ) -> dict[str, object]:
    """Friert die Laufparameter ein (Plan 2.6).

    Kein Zugangsdatum und kein Prompttext: das Manifest wird versioniert
    abgelegt und ist damit so oeffentlich wie das Repository. Der Prompt ist
    ueber ``prompt_sha256`` eindeutig belegt.
    """
    schema = json_schema if json_schema is not None else mapping.batch_schema()
    return {
        "version": MANIFEST_VERSION,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "input_manifest_hash": input_manifest_hash,
        "taxonomy_content_hash": taxonomy_content_hash,
        "unit_count": unit_count,
        "model": provider_settings.model,
        "routing": {
            "require_parameters": provider_settings.require_parameters,
            "allow_fallbacks": provider_settings.allow_fallbacks,
            "quantizations": list(provider_settings.quantizations or []),
            "data_collection": provider_settings.data_collection,
            "zdr": provider_settings.zdr,
            "max_price_usd_per_mtok": dict(provider_settings.max_price_usd_per_mtok),
            "sdk_retries": provider_settings.sdk_retries,
            "max_concurrent_calls": provider_settings.max_concurrent_calls,
        },
        "context": {
            "endpoint_context_tokens": provider_settings.endpoint_context_tokens,
            "max_output_tokens": provider_settings.max_output_tokens,
            "reasoning_reserve_tokens": provider_settings.reasoning_reserve_tokens,
            "request_timeout_s": provider_settings.request_timeout_s,
        },
        "batching": {
            "batch_size": settings.offline.batch_size,
            "max_repair_attempts": settings.offline.max_repair_attempts,
            "chars_per_token": settings.offline.chars_per_token,
            "workflow_retries": settings.offline.workflow_retries,
        },
        "prompts": [prompt.manifest_entry() for prompt in prompts_used],
        "schema": {
            "schema_name": mapping.SCHEMA_NAME,
            "schema_sha256": schema_hash(schema),
        },
        "cache_key_version": CACHE_KEY_VERSION,
        "budget_usd": budget_usd,
        "cost_guard": settings.offline.cost_guard,
    }


def write_manifest(run_id: str, manifest: Mapping) -> Path:
    return _write_json(manifest_path(run_id, create_parents=True), dict(manifest))


def load_manifest(run_id: str) -> dict | None:
    """Liest ein vorhandenes Manifest; fehlt oder faellt es aus dem Vertrag, None."""
    target = manifest_path(run_id)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        return None
    return data


#: Manifestfelder, deren Aenderung einen Resume verbietet: sie bestimmen das
#: fachliche Ergebnis. Volatile Provenienz steht bewusst nicht darin.
RESUME_BOUND_FIELDS = ("taxonomy_content_hash", "input_manifest_hash", "model",
                       "prompts", "schema", "batching", "context",
                       "cache_key_version")


def resume_conflicts(previous: Mapping, current: Mapping) -> tuple[str, ...]:
    """Felder, in denen sich ein fortgesetzter Lauf vom urspruenglichen unterscheidet.

    Nur diese Felder entscheiden ueber die Wiederverwendbarkeit. Ein anderer
    Zeitpunkt, ein anderes Budget oder ein anderer Endpoint sind kein Grund,
    bezahlte Ergebnisse wegzuwerfen.
    """
    return tuple(name for name in RESUME_BOUND_FIELDS
                 if previous.get(name) != current.get(name))


def resume_blocker(run_id: str, conflicts: Sequence[str]) -> Finding:
    return Finding(
        severity=Severity.BLOCKER,
        code="RUN_MANIFEST_DRIFT",
        stage=Stage.WORKFLOW,
        obj=run_id,
        message=("Der Lauf wurde unter anderen Bedingungen begonnen; abweichend: "
                 + ", ".join(conflicts) + "."),
        hint=("Neuen Run starten statt fortzusetzen; Ergebnisse aus "
              "unterschiedlichen Bedingungen werden nicht gemischt."),
        exit_code=ExitCode.PREFLIGHT_BLOCKED,
    )


# ---------------------------------------------------------------------------
# Ungeklaerte Calls und Reconciliation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnknownCall:
    """Ein Call, dessen Ausgang offen blieb.

    Nur ``generation_id`` erlaubt den Abgleich beim Provider. Fehlt sie, bleibt
    der Call dauerhaft ungeklaert - erneutes Senden ist eine Entscheidung von
    Bene, keine automatische Wiederholung (Plan 2.6).
    """

    call_id: str
    generation_id: str | None
    reserved_usd: float
    reason: str

    @property
    def reconcilable(self) -> bool:
        return bool(self.generation_id)

    def as_payload(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "generation_id": self.generation_id,
            "reserved_usd": round(self.reserved_usd, 6),
            "reason": self.reason,
            "reconcilable": self.reconcilable,
        }


def outcome_unknown_finding(unknown: Sequence[UnknownCall]) -> Finding:
    """Meldet ungeklaerte Calls als eigenen Endzustand, nie als Mappingurteil."""
    blind = [call.call_id for call in unknown if not call.reconcilable]
    hint = ("Kosten ueber die Generation-ID abgleichen; erneutes Senden nur nach "
            "ausdruecklicher Freigabe, es kann doppelt abgerechnet werden.")
    if blind:
        hint = (f"{len(blind)} Call(s) ohne Generation-ID sind nicht abgleichbar. "
                + hint)
    return Finding(
        severity=Severity.BLOCKER,
        code="RUN_OUTCOME_UNKNOWN",
        stage=Stage.COST,
        obj=f"{len(unknown)} Call(s)",
        message=("Der Ausgang einzelner Calls ist offen; die reservierten Kosten "
                 "bleiben gebucht."),
        hint=hint,
        exit_code=ExitCode.OUTCOME_UNKNOWN,
    )
