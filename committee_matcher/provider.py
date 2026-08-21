"""Providerpfad des Committee Matchers V2 (Plan 2.6, Gate-B-Beschluss).

Dieses Modul haelt bislang nur den **Routing-Preflight**: die Pruefung, ob es
zur konfigurierten Kombination aus Modell, Faehigkeit, Quantisierung und Preis
ueberhaupt einen Endpoint gibt. Sie laeuft gegen den oeffentlichen
OpenRouter-Katalog, braucht keinen Key, kostet nichts und ruft kein Modell auf.

Warum vor dem ersten Call: Eine leere Endpointmenge ist der einzige
Konfigurationsfehler, den ``allow_fallbacks`` nicht abfangen kann - Fallbacks
waehlen innerhalb der gefilterten Menge, nicht daneben. Ohne Preflight
scheitert stattdessen der erste kostenpflichtige Call mit einer generischen
SDK-Meldung, aus der die Ursache nicht hervorgeht.

Der Katalog weist die ZDR-Eigenschaft je Endpoint **nicht** aus. Der Preflight
prueft daher alles ausser ZDR; schlaegt ein realer Call trotz gruenem Preflight
mit einem Routingfehler fehl, ist ZDR die wahrscheinliche Ursache -
``routing_failure_finding`` formuliert genau diesen Befund.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from .errors import ErrorCollector, Finding, MatcherError, Severity, Stage
from .settings_view import ProviderSettings
from .status import ExitCode

#: Oeffentlicher Katalogendpunkt; ohne Key, ohne Kosten, kein Modellaufruf.
CATALOG_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"

#: Kennzeichen der Structured-Output-Faehigkeit im Katalog.
STRUCTURED_OUTPUT_PARAMETER = "structured_outputs"

#: Zeitgrenze des Katalogabrufs in Sekunden.
CATALOG_TIMEOUT_S = 30.0

#: Groessengrenze der Katalogantwort. Die Endpointliste eines Modells liegt bei
#: wenigen zehn Kilobyte; eine groessere Antwort ist ein Fehler und wird nicht
#: erst vollstaendig in den Speicher gelesen.
CATALOG_MAX_BYTES = 4_000_000

#: Handlungshinweis bei leerer Endpointmenge. Beide genannten Stellschrauben
#: aendern die Workflowlogik nicht - Modell und ZDR sind reine Settings.
ADJUSTMENT_HINT = (
    "Modell wechseln (COMMITTEE_MATCHER_MODEL) oder ZDR abschalten "
    "(COMMITTEE_MATCHER_ZDR = False). Beides beruehrt die Workflowlogik nicht. "
    "Quantisierungs- und Preisfilter lassen sich zusaetzlich lockern."
)


@dataclass(frozen=True)
class Endpoint:
    """Ein Endpoint des Modells, reduziert auf die Routingkriterien."""

    tag: str
    quantization: str
    prompt_usd_per_mtok: float
    completion_usd_per_mtok: float
    structured_outputs: bool
    context_tokens: int


@dataclass(frozen=True)
class RoutingReport:
    """Ergebnis des Preflights: was der Katalog kennt und was uebrig bleibt."""

    model: str
    total: int
    eligible: tuple[Endpoint, ...]

    @property
    def cheapest(self) -> Endpoint | None:
        """Guenstigster zulaessiger Endpoint, gemessen am Promptpreis.

        Der Promptanteil dominiert die Kosten des Matchers deutlich (grosser
        Taxonomie- und Unitprefix, kurze JSON-Antwort), deshalb genuegt er als
        Sortierschluessel dieser Anzeige.
        """
        if not self.eligible:
            return None
        return min(self.eligible, key=lambda e: e.prompt_usd_per_mtok)


class CatalogUnavailable(RuntimeError):
    """Der Katalog war nicht erreichbar oder antwortete unlesbar."""


def fetch_endpoints(model: str) -> dict:
    """Holt die Endpointliste eines Modells aus dem oeffentlichen Katalog."""
    url = CATALOG_URL.format(model=model)
    request = urllib.request.Request(
        url, headers={"User-Agent": "congress-tracker/committee-matcher"})
    try:
        with urllib.request.urlopen(request, timeout=CATALOG_TIMEOUT_S) as response:
            payload = response.read(CATALOG_MAX_BYTES + 1)
            if len(payload) > CATALOG_MAX_BYTES:
                raise CatalogUnavailable(
                    f"Antwort ueberschreitet {CATALOG_MAX_BYTES} Bytes")
            return json.loads(payload.decode("utf-8"))
    except CatalogUnavailable:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise CatalogUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:  # Netzfehler, Timeout, unlesbares JSON
        raise CatalogUnavailable(f"{exc.__class__.__name__}: {exc}") from exc


def _price(pricing: dict, key: str) -> float:
    """Katalogpreis (USD pro Token) als USD pro Mtok; fehlend heisst unendlich.

    Fail closed: ein fehlendes, leeres, nicht lesbares oder unsinniges Preisfeld
    darf nicht als niedriger Preis durchgehen, sonst passiert ein Endpoint mit
    unbekanntem Preis jeden Deckel und erschiene sogar als der guenstigste.
    Zulaessig ist nur eine endliche, nichtnegative Zahl; alles andere - auch
    ``-1`` oder ``-inf`` - gilt als unbekannt.
    """
    raw = pricing.get(key)
    if raw is None or raw == "":
        return float("inf")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float("inf")
    if not math.isfinite(value) or value < 0.0:
        return float("inf")
    return value * 1_000_000


def _fmt_price(value: float) -> str:
    return "unbekannt" if value == float("inf") else f"{value:.3f}"


def _endpoints(payload: dict) -> tuple[Endpoint, ...]:
    data = payload.get("data") if isinstance(payload, dict) else None
    raw = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return ()
    result: list[Endpoint] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
        # Nur eine echte Liste belegt die Faehigkeit: ein String wuerde ``in``
        # zur Teilstringsuche machen und Structured Output faelschlich bejahen.
        parameters = entry.get("supported_parameters")
        parameters = parameters if isinstance(parameters, list) else []
        try:
            context_tokens = int(entry.get("context_length") or 0)
        except (TypeError, ValueError):
            context_tokens = 0
        result.append(Endpoint(
            tag=str(entry.get("tag") or entry.get("name") or "?"),
            quantization=str(entry.get("quantization") or "unknown"),
            prompt_usd_per_mtok=_price(pricing, "prompt"),
            completion_usd_per_mtok=_price(pricing, "completion"),
            structured_outputs=STRUCTURED_OUTPUT_PARAMETER in parameters,
            context_tokens=context_tokens,
        ))
    return tuple(result)


def _blocker(collector: ErrorCollector, code: str, obj: str, message: str, hint: str):
    return collector.blocker(
        code, Stage.PROVIDER, obj, message, hint, exit_code=ExitCode.PREFLIGHT_BLOCKED)


def check_routing(
    collector: ErrorCollector,
    settings: ProviderSettings,
    *,
    fetch: Callable[[str], dict] | None = None,
) -> RoutingReport | None:
    """Prueft vor dem ersten Call, ob ein zulaessiger Endpoint existiert.

    Gibt ``None`` zurueck, sobald ein Kriterium die Menge leert; der Befund
    steht dann im Collector und traegt Exit-Code 4. Jede Stufe meldet
    getrennt, damit im Terminal steht, *welches* Kriterium geleert hat.
    """
    fetcher = fetch or fetch_endpoints
    model = settings.model
    try:
        payload = fetcher(model)
    except CatalogUnavailable as exc:
        _blocker(
            collector, "PROV_CATALOG_UNREACHABLE", model,
            f"Endpointkatalog nicht abrufbar ({exc}).",
            "Netzverbindung pruefen; ohne Katalog laesst sich die "
            "Providerverfuegbarkeit nicht vorab klaeren.",
        )
        return None

    endpoints = _endpoints(payload)
    if not endpoints:
        _blocker(
            collector, "PROV_MODEL_UNKNOWN", model,
            "Modell ist im OpenRouter-Katalog nicht vorhanden oder fuehrt "
            "keine Endpoints.",
            f"Modellslug pruefen. {ADJUSTMENT_HINT}",
        )
        return None

    capable = tuple(e for e in endpoints if e.structured_outputs)
    if not capable:
        _blocker(
            collector, "PROV_NO_STRUCTURED_OUTPUT", model,
            f"Keiner der {len(endpoints)} Endpoints unterstuetzt Structured "
            "Output; der Matcher verlangt striktes JSON-Schema.",
            "Modell gegen eines mit Structured-Output-Unterstuetzung "
            "tauschen (COMMITTEE_MATCHER_MODEL). Die Workflowlogik bleibt "
            "unveraendert.",
        )
        return None

    eligible = capable
    if settings.quantizations is not None:
        allowed = set(settings.quantizations)
        after = tuple(e for e in eligible if e.quantization in allowed)
        if not after:
            _blocker(
                collector, "PROV_NO_ENDPOINT", model,
                f"Kein Endpoint erfuellt den Quantisierungsfilter "
                f"{sorted(allowed)}; vorhanden sind "
                f"{sorted({e.quantization for e in eligible})}.",
                f"COMMITTEE_MATCHER_QUANTIZATIONS lockern oder auf None "
                f"setzen. {ADJUSTMENT_HINT}",
            )
            return None
        eligible = after

    limits = settings.max_price_usd_per_mtok
    after = tuple(
        e for e in eligible
        if e.prompt_usd_per_mtok <= limits["prompt"]
        and e.completion_usd_per_mtok <= limits["completion"]
    )
    if not after:
        cheapest = min(eligible, key=lambda e: e.prompt_usd_per_mtok)
        _blocker(
            collector, "PROV_NO_ENDPOINT", model,
            f"Kein Endpoint bleibt unter dem Preisdeckel "
            f"{limits['prompt']}/{limits['completion']} USD pro Mtok; der "
            f"guenstigste kostet "
            f"{_fmt_price(cheapest.prompt_usd_per_mtok)}/"
            f"{_fmt_price(cheapest.completion_usd_per_mtok)} "
            f"('unbekannt' heisst: der Katalog nennt keinen lesbaren Preis, "
            f"der Endpoint faellt damit bewusst durch den Deckel).",
            f"COMMITTEE_MATCHER_MAX_PRICE_USD_PER_MTOK anheben. "
            f"{ADJUSTMENT_HINT}",
        )
        return None

    return RoutingReport(model=model, total=len(endpoints), eligible=after)


def routing_failure_finding(
    collector: ErrorCollector,
    settings: ProviderSettings,
    detail: str,
) -> None:
    """Uebersetzt einen Routingfehler des Calls in einen lesbaren Befund.

    Gedacht fuer den Fall, den der Preflight nicht sehen kann: Der Katalog
    weist ZDR nicht aus, also bleibt eine unter ``zdr=True`` leere Menge bis
    zum echten Call unsichtbar.
    """
    _blocker(
        collector, "PROV_NO_ENDPOINT", settings.model,
        f"OpenRouter findet unter den gesetzten Providerbedingungen keinen "
        f"Endpoint ({detail}). Wirksam sind: require_parameters="
        f"{settings.require_parameters}, zdr={settings.zdr}, "
        f"data_collection={settings.data_collection!r}, "
        f"quantizations={list(settings.quantizations or ()) or 'keine'}, "
        f"max_price={settings.max_price_usd_per_mtok}.",
        f"Da der Preflight ZDR nicht pruefen kann, ist zdr=True die "
        f"wahrscheinlichste Ursache. {ADJUSTMENT_HINT}",
    )


# ===========================================================================
# Providerneutrales Structured-Output-Interface (Plan 2.6, P5)
# ===========================================================================
#
# Der Fachcode kennt nur die Datenklassen dieses Abschnitts. Kein SDK-Typ
# verlaesst den Adapter: ``mapping_engine`` und ``workflow`` sollen austauschbar
# gegen den Fake laufen, und ein SDK-Wechsel darf kein Fachmodul beruehren.


@dataclass(frozen=True)
class StructuredRequest:
    """Ein Call: Systemprompt, Nutzdaten und das erzwungene Ausgabeschema."""

    call_id: str
    system_prompt: str
    user_content: str
    schema_name: str
    json_schema: dict
    max_output_tokens: int


@dataclass(frozen=True)
class StructuredResponse:
    """Antwort eines Providers, bereits als JSON-Objekt geparst.

    ``cost_usd`` ist bewusst optional: meldet der Provider keine Kosten, ist
    das der Zustand ``cost_unknown``, den die Kostenwache in P7 behandelt -
    der Adapter erfindet keine Zahl.
    """

    call_id: str
    payload: dict
    model: str
    provider: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    finish_reason: str | None
    generation_id: str | None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def manifest_entry(self) -> dict[str, object]:
        """Provenienz eines Calls fuer Auditartefakte (nie den API-Key)."""
        return {
            "call_id": self.call_id,
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "finish_reason": self.finish_reason,
            "generation_id": self.generation_id,
        }


class ProviderCallError(MatcherError):
    """Fehlgeschlagener Providercall samt Befund und Retryeignung.

    ``retryable`` entscheidet, ob der Workflow denselben Call wiederholen darf.
    Wiederholbar sind nur Zustaende, die ohne Aenderung an Anfrage oder
    Konfiguration vergehen koennen (Drosselung, Ueberlast, Zeitueberschreitung).
    """

    def __init__(self, findings, message=None, *, retryable: bool = False):
        super().__init__(findings, message)
        self.retryable = retryable


def _call_failure(code: str, obj: str, message: str, hint: str, *,
                  retryable: bool) -> ProviderCallError:
    finding = Finding(
        severity=Severity.BLOCKER,
        code=code,
        stage=Stage.PROVIDER,
        obj=obj,
        message=message,
        hint=hint,
        exit_code=ExitCode.PROVIDER_ERROR,
    )
    return ProviderCallError([finding], f"{code}: {obj}", retryable=retryable)


class StructuredOutputProvider(Protocol):
    """Was der Fachcode von einem Provider verlangt - mehr nicht."""

    def complete(self, request: StructuredRequest) -> StructuredResponse:
        ...


class FakeProvider:
    """Skriptbarer Provider fuer netzwerkfreie Tests von P6 und P7.

    Jeder Eintrag in ``script`` ist entweder ein Antwort-Payload (dict) oder
    eine Exception, die beim entsprechenden Call geworfen wird. So laesst sich
    ein Repair-Durchlauf oder ein Retry deterministisch nachstellen.
    """

    def __init__(self, script, *, model: str = "fake/model",
                 provider_name: str = "fake", cost_usd: float | None = 0.0):
        self._script = list(script)
        self._model = model
        self._provider = provider_name
        self._cost = cost_usd
        self.calls: list[StructuredRequest] = []

    @property
    def remaining(self) -> int:
        return len(self._script)

    def complete(self, request: StructuredRequest) -> StructuredResponse:
        self.calls.append(request)
        if not self._script:
            raise AssertionError(
                f"FakeProvider hat kein Skript mehr fuer Call {request.call_id!r}")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return StructuredResponse(
            call_id=request.call_id,
            payload=item,
            model=self._model,
            provider=self._provider,
            prompt_tokens=len(request.system_prompt) + len(request.user_content),
            completion_tokens=len(json.dumps(item)),
            cost_usd=self._cost,
            finish_reason="stop",
            generation_id=f"fake-{request.call_id}",
        )


#: Umgebungsvariable des API-Keys. Der Key steht ausschliesslich in ``.env``
#: und erscheint in keinem Artefakt, Manifest oder Terminalbericht.
API_KEY_ENV = "OPENROUTER_API_KEY"

#: SDK-Fehlerklassen, die ohne Aenderung an Anfrage oder Konfiguration vergehen
#: koennen. Alles andere gilt als nicht wiederholbar (Exit 9).
RETRYABLE_ERRORS: tuple[str, ...] = (
    "TooManyRequestsResponseError",
    "ProviderOverloadedResponseError",
    "ServiceUnavailableResponseError",
    "InternalServerResponseError",
    "BadGatewayResponseError",
    "RequestTimeoutResponseError",
    "EdgeNetworkTimeoutResponseError",
    "NoResponseError",
)


class OpenRouterProvider:
    """Einziger Ort, an dem das OpenRouter-SDK vorkommt.

    Der Konstruktor stellt nur die Verbindung her; die Vorbedingungen
    (API-Key, zulaessiger Endpoint) prueft ``create`` und blockiert sonst mit
    Exit 4, bevor ein kostenpflichtiger Call abgesetzt wird.
    """

    def __init__(self, settings: ProviderSettings, api_key: str, *, client=None):
        self._settings = settings
        self._client = client if client is not None else self._build_client(api_key)

    @staticmethod
    def _build_client(api_key: str):
        from openrouter import OpenRouter  # lazy: haelt den Kern importfrei

        return OpenRouter(api_key=api_key)

    @classmethod
    def create(cls, collector: ErrorCollector, settings: ProviderSettings,
               *, client=None) -> "OpenRouterProvider | None":
        """Prueft Key und Routing und gibt sonst ``None`` samt Befund zurueck."""
        api_key = os.getenv(API_KEY_ENV, "").strip()
        if not api_key:
            _blocker(
                collector, "PROV_NO_API_KEY", API_KEY_ENV,
                f"Umgebungsvariable {API_KEY_ENV} ist leer oder nicht gesetzt.",
                f"{API_KEY_ENV} in der Datei .env eintragen; sie ist "
                "gitignored und gehoert nicht in config/settings.py.",
            )
            return None

        # Verbindliche Vorschaltung: eine leere Endpointmenge muss auffallen,
        # bevor der erste Call Kosten erzeugt.
        if check_routing(collector, settings) is None:
            return None

        return cls(settings, api_key, client=client)

    def _preferences(self):
        from openrouter.components.providerpreferences import (
            MaxPrice, ProviderPreferences)

        limits = self._settings.max_price_usd_per_mtok
        return ProviderPreferences(
            allow_fallbacks=self._settings.allow_fallbacks,
            require_parameters=self._settings.require_parameters,
            data_collection=self._settings.data_collection,
            zdr=self._settings.zdr,
            quantizations=list(self._settings.quantizations)
            if self._settings.quantizations else None,
            # Das SDK erwartet Preise als Strings in USD pro Million Token.
            max_price=MaxPrice(prompt=str(limits["prompt"]),
                               completion=str(limits["completion"])),
        )

    def _response_format(self, request: StructuredRequest):
        from openrouter.components.chatformatjsonschemaconfig import (
            ChatFormatJSONSchemaConfig)
        from openrouter.components.chatjsonschemaconfig import ChatJSONSchemaConfig

        return ChatFormatJSONSchemaConfig(
            type="json_schema",
            json_schema=ChatJSONSchemaConfig(
                name=request.schema_name,
                strict=True,
                schema_=request.json_schema,
            ),
        )

    @staticmethod
    def _no_sdk_retries():
        """Genau ein Versuch je Call: SDK-Retries koennen doppelt abrechnen."""
        from openrouter import RetryConfig
        from openrouter.utils.retries import BackoffStrategy

        return RetryConfig("none", BackoffStrategy(1, 1, 1.0, 1), False)

    def complete(self, request: StructuredRequest) -> StructuredResponse:
        """Setzt genau einen Call ab und uebersetzt das Ergebnis fachlich."""
        try:
            result = self._client.chat.send(
                model=self._settings.model,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_content},
                ],
                provider=self._preferences(),
                response_format=self._response_format(request),
                max_tokens=request.max_output_tokens,
                temperature=0.0,
                stream=False,
                timeout_ms=int(self._settings.request_timeout_s * 1000),
                retries=self._no_sdk_retries(),
            )
        except Exception as exc:  # SDK-Fehler bleiben im Adapter
            raise self._translate(request, exc) from exc

        return self._to_response(request, result)

    def _translate(self, request: StructuredRequest, exc: Exception) -> ProviderCallError:
        name = exc.__class__.__name__
        retryable = name in RETRYABLE_ERRORS
        if name in ("UnauthorizedResponseError", "ForbiddenResponseError"):
            return _call_failure(
                "PROV_AUTH_FAILED", request.call_id,
                f"OpenRouter lehnt die Authentifizierung ab ({name}).",
                f"{API_KEY_ENV} in .env pruefen; Guthaben und Berechtigung des "
                "Schluessels ebenfalls.",
                retryable=False,
            )
        if name == "PaymentRequiredResponseError":
            return _call_failure(
                "PROV_NO_CREDIT", request.call_id,
                "OpenRouter meldet fehlendes Guthaben.",
                "Guthaben aufladen; der Lauf laesst sich danach fortsetzen.",
                retryable=False,
            )
        if name in ("BadRequestResponseError", "UnprocessableEntityResponseError"):
            return _call_failure(
                "PROV_REQUEST_REJECTED", request.call_id,
                f"OpenRouter weist die Anfrage zurueck ({name}: {exc}).",
                "Schema, Modell und Providerfilter pruefen; ein erneuter "
                "Versuch mit identischer Anfrage scheitert genauso. "
                f"{ADJUSTMENT_HINT}",
                retryable=False,
            )
        if name == "PayloadTooLargeResponseError":
            return _call_failure(
                "PROV_PAYLOAD_TOO_LARGE", request.call_id,
                "Die Anfrage ist fuer den Endpoint zu gross.",
                "Batchgroesse senken (COMMITTEE_MATCHER_BATCH_SIZE) oder den "
                "Context-Split nutzen; Texte werden nie still gekuerzt.",
                retryable=False,
            )
        return _call_failure(
            "PROV_CALL_FAILED", request.call_id,
            f"Providercall fehlgeschlagen ({name}: {exc}).",
            "Voruebergehende Stoerung; der Workflow darf den Call wiederholen."
            if retryable else
            "Nicht wiederholbarer Providerfehler; Ursache oben pruefen.",
            retryable=retryable,
        )

    def _to_response(self, request: StructuredRequest, result) -> StructuredResponse:
        choices = getattr(result, "choices", None) or []
        if not choices:
            raise _call_failure(
                "PROV_EMPTY_RESPONSE", request.call_id,
                "Antwort enthaelt keine Auswahl (choices leer).",
                "Wiederholbar; bleibt der Zustand, Modell oder Endpoint pruefen.",
                retryable=True,
            )
        choice = choices[0]
        message = getattr(choice, "message", None)
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise _call_failure(
                "PROV_REFUSED", request.call_id,
                f"Das Modell verweigert die Antwort: {refusal}.",
                "Prompt und Eingabetext pruefen; eine Wiederholung liefert "
                "dasselbe Ergebnis.",
                retryable=False,
            )

        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise _call_failure(
                "PROV_TRUNCATED", request.call_id,
                "Antwort wurde am Ausgabelimit abgeschnitten "
                f"({request.max_output_tokens} Tokens).",
                "COMMITTEE_MATCHER_MAX_OUTPUT_TOKENS anheben oder die "
                "Batchgroesse senken; eine abgeschnittene Antwort wird nie "
                "still repariert.",
                retryable=False,
            )

        content = getattr(message, "content", None)
        text = content if isinstance(content, str) else self._join_content(content)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise _call_failure(
                "PROV_INVALID_JSON", request.call_id,
                f"Antwort ist trotz erzwungenem Schema kein gueltiges JSON ({exc}).",
                "Wiederholbar; haeuft sich der Fall, unterstuetzt der geroutete "
                "Endpoint Structured Output nur nominell - 'provider check' "
                "zeigt die zulaessigen Endpoints.",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise _call_failure(
                "PROV_INVALID_JSON", request.call_id,
                f"Antwort ist ein {type(payload).__name__}, erwartet wird ein "
                "JSON-Objekt.",
                "Wiederholbar; Schema und Prompt pruefen, falls es bleibt.",
                retryable=True,
            )

        usage = getattr(result, "usage", None)
        cost = getattr(usage, "cost", None)
        return StructuredResponse(
            call_id=request.call_id,
            payload=payload,
            model=str(getattr(result, "model", None) or self._settings.model),
            provider=self._routed_provider(result),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            finish_reason=finish_reason,
            generation_id=getattr(result, "id", None),
        )

    @staticmethod
    def _join_content(content) -> str:
        """Setzt eine als Teilstuecke gelieferte Antwort zusammen."""
        if not isinstance(content, (list, tuple)):
            return ""
        parts = [getattr(part, "text", None) or (
            part.get("text") if isinstance(part, dict) else None)
            for part in content]
        return "".join(part for part in parts if isinstance(part, str))

    @staticmethod
    def _routed_provider(result) -> str | None:
        """Liest den tatsaechlich gerouteten Provider - nur zum Protokoll."""
        metadata = getattr(result, "openrouter_metadata", None)
        attempts = getattr(metadata, "attempts", None) or []
        for attempt in reversed(attempts):
            provider_name = getattr(attempt, "provider", None)
            if provider_name:
                return str(provider_name)
        return None
