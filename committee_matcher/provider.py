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
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .errors import ErrorCollector, Stage
from .settings_view import ProviderSettings
from .status import ExitCode

#: Oeffentlicher Katalogendpunkt; ohne Key, ohne Kosten, kein Modellaufruf.
CATALOG_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"

#: Kennzeichen der Structured-Output-Faehigkeit im Katalog.
STRUCTURED_OUTPUT_PARAMETER = "structured_outputs"

#: Zeitgrenze des Katalogabrufs in Sekunden.
CATALOG_TIMEOUT_S = 30.0

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
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise CatalogUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:  # Netzfehler, Timeout, unlesbares JSON
        raise CatalogUnavailable(f"{exc.__class__.__name__}: {exc}") from exc


def _price(pricing: dict, key: str) -> float:
    try:
        return float(pricing.get(key) or 0.0) * 1_000_000
    except (TypeError, ValueError):
        return 0.0


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
        parameters = entry.get("supported_parameters") or []
        result.append(Endpoint(
            tag=str(entry.get("tag") or entry.get("name") or "?"),
            quantization=str(entry.get("quantization") or "unknown"),
            prompt_usd_per_mtok=_price(pricing, "prompt"),
            completion_usd_per_mtok=_price(pricing, "completion"),
            structured_outputs=STRUCTURED_OUTPUT_PARAMETER in parameters,
            context_tokens=int(entry.get("context_length") or 0),
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
            f"guenstigste kostet {cheapest.prompt_usd_per_mtok:.3f}/"
            f"{cheapest.completion_usd_per_mtok:.3f}.",
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
