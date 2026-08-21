"""Typisierte Sicht auf die ``COMMITTEE_MATCHER_*``-Settings.

Plan 2.6: alle veraenderbaren Laufparameter liegen in ``config/settings.py``.
Dieses Modul liest sie genau einmal, prueft Typ und Wertebereich und stellt
dem Fachcode unveraenderliche Objekte bereit. Damit gibt es im Paket keinen
``getattr(settings, ...)``-Streuzugriff und keine stillen Defaults.

Zwei Gruppen mit unterschiedlicher Strenge:

- **offline**: muss immer gueltig sein; ein Fehler blockiert jeden Lauf
  (``SettingsBlocked``, Exit-Code 4).
- **provider**: seit dem Gate-B-Beschluss vollstaendig gesetzt, technisch
  aber weiterhin einzeln abschaltbar (``None``). Der providerfreie Kern laeuft
  auch ohne diese Werte vollstaendig; jeder Providerpfad blockiert dann ueber
  ``require_provider`` bzw. ``require_budget`` mit einem Befund je fehlendem
  Setting.

Die Sicherheitsinvarianten des Providervertrags sind Konstanten und keine
Settings: sie duerfen nicht per Konfiguration aufgeweicht werden.
"""
from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable

from .errors import ErrorCollector, Finding, MatcherError, Severity, Stage
from .status import ExitCode

#: Feste Providerinvarianten (Plan 2.6) - bewusst nicht konfigurierbar.
PROVIDER_REQUIRE_PARAMETERS = True
#: Gate-B-Beschluss: OpenRouter waehlt den Endpoint, gefiltert wird nur
#: ueber Faehigkeit, Quantisierung, Datenschutz und Preis.
PROVIDER_ALLOW_FALLBACKS = True
PROVIDER_SDK_RETRIES = 0
PROVIDER_MAX_CONCURRENT_CALLS = 1

#: Zulaessige Batchgroessen (ein Context-Split darf zur Laufzeit 3->2->1 machen).
ALLOWED_BATCH_SIZES = (1, 3)
#: Zulaessige Datenschutzmodi des Providerroutings.
ALLOWED_DATA_COLLECTION = ("deny", "allow")
#: Quantisierungsliterale des gepinnten OpenRouter-SDK.
ALLOWED_QUANTIZATIONS = (
    "int4", "int8", "fp4", "mxfp4", "nvfp4", "fp6", "fp8", "mxfp8",
    "fp16", "bf16", "fp32", "unknown",
)

_PREFIX = "COMMITTEE_MATCHER_"


class SettingsBlocked(MatcherError):
    """Ungueltige oder fuer den Pfad fehlende Settings."""


def _finding(code: str, name: str, message: str, hint: str) -> Finding:
    return Finding(
        severity=Severity.BLOCKER,
        code=code,
        stage=Stage.CONFIG,
        obj=name,
        message=message,
        hint=hint,
        exit_code=ExitCode.PREFLIGHT_BLOCKED,
    )


class _Reader:
    """Liest und prueft Settings, sammelt dabei alle Befunde."""

    def __init__(self, module: ModuleType):
        self.module = module
        self.collector = ErrorCollector()

    def raw(self, name: str) -> Any:
        return getattr(self.module, name, _MISSING)

    def _fail(self, code: str, name: str, message: str, hint: str) -> None:
        self.collector.extend([_finding(code, name, message, hint)])

    def required(
        self,
        name: str,
        caster: Callable[[Any], Any],
        expected: str,
        default: Any,
    ) -> Any:
        value = self.raw(name)
        if value is _MISSING:
            self._fail(
                "CFG_MISSING", name,
                f"Pflichtsetting {name} fehlt in config/settings.py.",
                f"{name} = {default!r} ergaenzen ({expected}).",
            )
            return default
        try:
            return caster(value)
        except (TypeError, ValueError) as exc:
            self._fail(
                "CFG_INVALID", name,
                f"{name}={value!r} ist unzulaessig: {exc}.",
                f"Erwartet: {expected}.",
            )
            return default

    def optional(
        self,
        name: str,
        caster: Callable[[Any], Any],
        expected: str,
    ) -> Any:
        """Providerabhaengiges Setting: ``None``/fehlend blockiert nur Providerpfade."""
        value = self.raw(name)
        if value is _MISSING or value is None:
            return None
        try:
            return caster(value)
        except (TypeError, ValueError) as exc:
            self._fail(
                "CFG_INVALID", name,
                f"{name}={value!r} ist unzulaessig: {exc}.",
                f"Erwartet: {expected} oder None (sperrt alle Providerpfade).",
            )
            return None


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - nur Diagnose
        return "<nicht gesetzt>"


_MISSING = _Missing()


def _int_in(minimum: int, maximum: int) -> Callable[[Any], int]:
    def caster(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("kein int")
        if not minimum <= value <= maximum:
            raise ValueError(f"ausserhalb {minimum}..{maximum}")
        return value

    return caster


def _float_in(minimum: float, maximum: float) -> Callable[[Any], float]:
    def caster(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("keine Zahl")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("NaN und Unendlich sind unzulaessig")
        if not minimum <= number <= maximum:
            raise ValueError(f"ausserhalb {minimum}..{maximum}")
        return number

    return caster


def _choice(options: tuple[Any, ...]) -> Callable[[Any], Any]:
    """Wertliste; ``True``/``False`` gelten nicht als 1/0."""

    def caster(value: Any) -> Any:
        if isinstance(value, bool):
            raise TypeError("bool ist kein zulaessiger Wert")
        if not any(value == option and type(value) is type(option) for option in options):
            raise ValueError(f"nicht in {options}")
        return value

    return caster


def _non_empty_str(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError("kein nicht-leerer String")
    return value.strip()


#: Preisschluessel des Providervertrags; genau diese und keine weiteren.
PRICE_KEYS = ("prompt", "completion")


def _price_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise TypeError("kein nicht-leeres Dict")
    if set(value) != set(PRICE_KEYS):
        raise ValueError(f"Schluesselmenge muss exakt {set(PRICE_KEYS)} sein")
    result: dict[str, float] = {}
    for key, price in value.items():
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise TypeError(f"Preis fuer '{key}' ist keine Zahl")
        number = float(price)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"Preis fuer '{key}' muss endlich und > 0 sein")
        result[key] = number
    return result


def _strict_bool(value: Any) -> bool:
    """Nimmt ausschliesslich echte Booleans; 0/1 waere zu leicht verwechselbar."""
    if not isinstance(value, bool):
        raise TypeError("Wert ist kein bool")
    return value


def _quantizations(value: Any) -> tuple[str, ...]:
    """Prueft die Quantisierungs-Allowlist; leere Menge ist kein Filter."""
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise TypeError("Wert ist keine Liste von Quantisierungen")
    if not value:
        raise ValueError("leere Liste; fuer 'kein Filter' None setzen")
    unknown = [q for q in value if q not in ALLOWED_QUANTIZATIONS]
    if unknown:
        raise ValueError(f"unbekannte Quantisierung(en): {unknown}")
    return tuple(value)


@dataclass(frozen=True)
class OfflineSettings:
    """Immer gueltige Laufparameter des providerfreien Kerns."""

    max_file_bytes: int
    max_frontmatter_bytes: int
    min_evidence_chars: int
    yfinance_request_pause_s: float
    yfinance_max_retries: int
    yfinance_checkpoint_every: int
    batch_size: int
    workflow_retries: int
    max_repair_attempts: int
    chars_per_token: float
    #: Kostenwache; per CLI-Flag je Lauf abschaltbar.
    cost_guard: bool


@dataclass(frozen=True)
class ProviderSettings:
    """Vollstaendig gesetzte Providerparameter (Gate-B-Beschluss)."""

    model: str
    data_collection: str
    #: True verlangt Zero Data Retention beim Routing.
    zdr: bool
    max_price_usd_per_mtok: dict[str, float]
    endpoint_context_tokens: int
    max_output_tokens: int
    reasoning_reserve_tokens: int
    request_timeout_s: float
    #: Zulaessige Rechenpraezisionen; None laesst jede Quantisierung zu.
    quantizations: tuple[str, ...] | None = None

    #: Invarianten, die im Runmanifest miteingefroren werden.
    require_parameters: bool = PROVIDER_REQUIRE_PARAMETERS
    allow_fallbacks: bool = PROVIDER_ALLOW_FALLBACKS
    sdk_retries: int = PROVIDER_SDK_RETRIES
    max_concurrent_calls: int = PROVIDER_MAX_CONCURRENT_CALLS


#: Budgetarten und ihr Setting.
BUDGET_SETTINGS = {
    "run": f"{_PREFIX}MAX_RUN_COST_USD",
    "evaluation": f"{_PREFIX}MAX_EVALUATION_COST_USD",
    "qualification": f"{_PREFIX}MAX_QUALIFICATION_COST_USD",
}


@dataclass(frozen=True)
class MatcherSettings:
    """Gesamtsicht: offline immer gueltig, Provider nur bei vollstaendigen Werten."""

    offline: OfflineSettings
    provider: ProviderSettings | None
    budgets: dict[str, float | None]
    provider_findings: tuple[Finding, ...]

    @property
    def provider_ready(self) -> bool:
        return self.provider is not None

    def require_provider(self) -> ProviderSettings:
        """Gibt die Providersettings frei oder blockiert den Providerpfad."""
        if self.provider is None:
            raise SettingsBlocked(
                self.provider_findings or (
                    _finding(
                        "CFG_PROVIDER_UNSET",
                        f"{_PREFIX}*",
                        "Providersettings sind nicht gesetzt.",
                        "Modell, Endpoint, Kontext und Preise in "
                        "config/settings.py eintragen.",
                    ),
                ),
                "Providerpfad blockiert: Settings unvollstaendig",
            )
        return self.provider

    def require_budget(self, kind: str) -> float:
        """Gibt das Budget einer Laufart frei oder blockiert mit Exit-Code 6."""
        if kind not in BUDGET_SETTINGS:
            raise KeyError(f"unbekannte Budgetart: {kind}")
        value = self.budgets.get(kind)
        if value is None:
            name = BUDGET_SETTINGS[kind]
            finding = Finding(
                severity=Severity.BLOCKER,
                code="CFG_BUDGET_UNSET",
                stage=Stage.COST,
                obj=name,
                message=f"Kostenbudget {name} ist nicht gesetzt.",
                hint="Budget in config/settings.py setzen; ohne Budget laeuft kein Call.",
                exit_code=ExitCode.COST_BLOCKED,
            )
            raise SettingsBlocked([finding], f"Budget {kind} fehlt")
        return value


def load_settings(module: ModuleType | None = None) -> MatcherSettings:
    """Liest und validiert die Settings.

    Fehlerhafte Offlinesettings blockieren sofort; fehlende Providersettings
    werden nur gesammelt, damit der providerfreie Kern laufen kann.
    """
    settings_module = module or importlib.import_module("config.settings")
    reader = _Reader(settings_module)

    offline = OfflineSettings(
        max_file_bytes=reader.required(
            f"{_PREFIX}MAX_FILE_BYTES", _int_in(1_000, 10_000_000),
            "int 1000..10000000", 512_000),
        max_frontmatter_bytes=reader.required(
            f"{_PREFIX}MAX_FRONTMATTER_BYTES", _int_in(200, 1_000_000),
            "int 200..1000000", 32_000),
        min_evidence_chars=reader.required(
            f"{_PREFIX}MIN_EVIDENCE_CHARS", _int_in(0, 5_000),
            "int 0..5000", 60),
        yfinance_request_pause_s=reader.required(
            f"{_PREFIX}YFINANCE_REQUEST_PAUSE_S", _float_in(0.0, 60.0),
            "float 0..60", 1.5),
        yfinance_max_retries=reader.required(
            f"{_PREFIX}YFINANCE_MAX_RETRIES", _int_in(0, 10),
            "int 0..10", 3),
        yfinance_checkpoint_every=reader.required(
            f"{_PREFIX}YFINANCE_CHECKPOINT_EVERY", _int_in(1, 500),
            "int 1..500", 10),
        batch_size=reader.required(
            f"{_PREFIX}BATCH_SIZE", _choice(ALLOWED_BATCH_SIZES),
            "1 oder 3", 3),
        workflow_retries=reader.required(
            f"{_PREFIX}WORKFLOW_RETRIES", _int_in(0, 5),
            "int 0..5", 2),
        max_repair_attempts=reader.required(
            f"{_PREFIX}MAX_REPAIR_ATTEMPTS", _int_in(0, 3),
            "int 0..3", 1),
        chars_per_token=reader.required(
            f"{_PREFIX}CHARS_PER_TOKEN", _float_in(1.0, 8.0),
            "float 1..8", 3.0),
        cost_guard=reader.required(
            f"{_PREFIX}COST_GUARD", _strict_bool, "bool", True),
    )
    if reader.collector.has_blockers():
        raise SettingsBlocked(reader.collector.blockers, "Offlinesettings ungueltig")

    provider_reader = _Reader(settings_module)
    values = {
        "model": provider_reader.optional(
            f"{_PREFIX}MODEL", _non_empty_str, "Modellslug"),
        "data_collection": provider_reader.optional(
            f"{_PREFIX}DATA_COLLECTION", _choice(ALLOWED_DATA_COLLECTION),
            "'deny' oder 'allow'"),
        "zdr": provider_reader.optional(
            f"{_PREFIX}ZDR", _strict_bool, "bool"),
        "max_price_usd_per_mtok": provider_reader.optional(
            f"{_PREFIX}MAX_PRICE_USD_PER_MTOK", _price_map,
            "Dict mit 'prompt' und 'completion' > 0"),
        "endpoint_context_tokens": provider_reader.optional(
            f"{_PREFIX}ENDPOINT_CONTEXT_TOKENS", _int_in(1_000, 10_000_000),
            "int 1000..10000000"),
        "max_output_tokens": provider_reader.optional(
            f"{_PREFIX}MAX_OUTPUT_TOKENS", _int_in(256, 1_000_000),
            "int 256..1000000"),
        "reasoning_reserve_tokens": provider_reader.optional(
            f"{_PREFIX}REASONING_RESERVE_TOKENS", _int_in(0, 1_000_000),
            "int 0..1000000"),
        "request_timeout_s": provider_reader.optional(
            f"{_PREFIX}REQUEST_TIMEOUT_S", _float_in(1.0, 3_600.0),
            "float 1..3600"),
    }

    quantizations = provider_reader.optional(
        f"{_PREFIX}QUANTIZATIONS", _quantizations,
        f"Liste aus {ALLOWED_QUANTIZATIONS}")

    budgets: dict[str, float | None] = {}
    for kind, name in BUDGET_SETTINGS.items():
        budgets[kind] = provider_reader.optional(
            name, _float_in(0.000_001, 10_000.0), "float > 0")

    missing = [
        f"{_PREFIX}{key.upper()}" for key, value in values.items() if value is None
    ]
    for name in missing:
        provider_reader.collector.extend([
            _finding(
                "CFG_PROVIDER_UNSET", name,
                f"{name} ist nicht gesetzt.",
                "Wert in config/settings.py eintragen; ohne ihn bleibt jeder "
                "Providerpfad blockiert.",
            )
        ])

    provider: ProviderSettings | None = None
    if not missing and not provider_reader.collector.has_blockers():
        provider = ProviderSettings(
            quantizations=quantizations, **values)  # type: ignore[arg-type]

    context_ok_findings: list[Finding] = list(provider_reader.collector.blockers)
    if provider is not None:
        reserve = provider.max_output_tokens + provider.reasoning_reserve_tokens
        if reserve >= provider.endpoint_context_tokens:
            context_ok_findings.append(
                _finding(
                    "CFG_CONTEXT_RESERVE",
                    f"{_PREFIX}ENDPOINT_CONTEXT_TOKENS",
                    "Output- und Reasoningreserve fuellen das Kontextfenster "
                    "vollstaendig; fuer Prompt und Taxonomie bleibt nichts.",
                    "Kontextfenster erhoehen oder Reserven senken.",
                )
            )
            provider = None

    return MatcherSettings(
        offline=offline,
        provider=provider,
        budgets=budgets,
        provider_findings=tuple(context_ok_findings),
    )
