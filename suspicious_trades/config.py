"""Konfigurations-Defaults und validierter Override-Layer.

Die Basis wird spaeter als komplettes Package geklont. Deshalb leitet sich die
eigene Datenablage aus dem Top-Level-Package-Namen ab und alle Projektpfade
bleiben relativ zum Repository-Root. ``effective()`` ist der einzige
Leseeinstieg fuer Pipeline-Code: Es kombiniert die hier definierten Defaults
mit den vom Control-UI geschriebenen Overrides.
"""

from __future__ import annotations

import copy
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
from typing import Any

from dotenv import dotenv_values


_logger = logging.getLogger(__name__)

# Package-Verzeichnis mit eigener Umgebung und eigenen Abhaengigkeiten.
PACKAGE_ROOT = Path(__file__).resolve().parent

# Repository-Root; alle gemeinsamen Projektpfade werden hiervon abgeleitet.
PROJECT_ROOT = PACKAGE_ROOT.parent

def _load_package_env(path: Path) -> tuple[frozenset[str], dict[str, str]]:
    """Load simple dotenv assignments without logging values or parser payloads."""

    preexisting = frozenset(os.environ)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return preexisting, {}

    valid_lines: list[str] = []
    assignment = re.compile(
        r"^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*="
    )
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or assignment.match(line):
            valid_lines.append(line)
            continue
        _logger.warning(
            "Ignoring invalid dotenv statement in %s at line %d.",
            path,
            line_number,
        )

    parsed = dotenv_values(stream=StringIO("\n".join(valid_lines)))
    package_values = {
        str(name): str(value)
        for name, value in parsed.items()
        if value is not None
    }
    for name, value in package_values.items():
        os.environ.setdefault(name, value)
    return preexisting, package_values


# Das Subprojekt verwaltet seine Provider-Keys unabhaengig von der Root-.env.
# Bereits gesetzte Prozessvariablen haben bewusst Vorrang (dotenv override=False).
_env_preexisting, _package_env_values = _load_package_env(PACKAGE_ROOT / ".env")


def credential_value_status(value: str | None) -> str:
    """Classify a credential value without exposing it."""

    if value is None or not value.strip():
        return "missing"
    normalized = value.strip().strip("'\"").lower()
    placeholders = {
        "your_api_key",
        "your-api-key",
        "replace-me",
        "replace_me",
        "changeme",
        "placeholder",
    }
    if normalized in placeholders or (
        normalized.startswith("<") and normalized.endswith(">")
    ):
        return "placeholder"
    return "set"


def credential_status(name: str) -> str:
    """Return missing, placeholder or set for an environment credential."""

    return credential_value_status(os.getenv(name))


def credential_source(name: str) -> str:
    """Return redacted credential provenance for smoke diagnostics."""

    if name in _env_preexisting:
        return "process-env"
    value = os.getenv(name)
    if value is None or not value.strip():
        return "missing"
    if _package_env_values.get(name) == value:
        return "package-env"
    return "runtime-env"

# Eigene Datenablage; beim Package-Klon wechselt der Ordner automatisch mit.
DATA_DIR = PROJECT_ROOT / "data" / __package__.split(".")[0]

# Variantenuebergreifendes Zwischenlager fuer Exporte der Haupt-App.
STAGING_DIR = PROJECT_ROOT / "data" / "agent_staging"

# Vollstaendiger Trades-Master; Suspicious Trades greift ausschliesslich lesend zu.
TRADES_MASTER_PATH = PROJECT_ROOT / "data" / "processed" / "trades_master_complete.csv"

# Gemeinsamer Kurs-Cache der Haupt-Pipeline; ebenfalls nur lesend verwendet.
PRICE_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "price_history.parquet"

# LiteLLM-interne Retries je Call fuer Timeouts, 429 und temporaere 5xx-Fehler.
LLM_NUM_RETRIES = 2

# Harte Request-Obergrenze fuer einen einzelnen LiteLLM-Provideraufruf in Sekunden.
LLM_TIMEOUT_SECONDS = 60.0

# OpenRouter-Notfallpfad; standardmaessig aus, weil Cache-Fidelity verloren geht.
LLM_FALLBACK_ENABLED = False

# Explizite Primaermodell-zu-OpenRouter-Zuordnung fuer den optionalen Fallback.
OPENROUTER_MODEL_MAP = {
    "anthropic/claude-opus-4-8": "openrouter/anthropic/claude-opus-4.8",
    "openai/gpt-5.5": "openrouter/openai/gpt-5.5",
}

# Autoritative Kostenquelle in USD je eine Million Tokens fuer das Usage-Log.
PRICING = {
    "deepseek/deepseek-v4-flash": {
        "in": 0.14,
        "out": 0.28,
        "cache_read": 0.0028,
        "cache_write": 0.14,
    },
    "deepseek/deepseek-v4-pro": {
        "in": 0.435,
        "out": 0.87,
        "cache_read": 0.003625,
        "cache_write": 0.435,
    },
    "openai/gpt-5.5": {
        "in": 5.0,
        "out": 30.0,
        "cache_read": 0.5,
        "cache_write": 5.0,
    },
    "gemini/gemini-3.1-pro-preview": {
        "in": 2.0,
        "out": 12.0,
        "cache_read": 0.5,
        "cache_write": 2.0,
    },
    "anthropic/claude-opus-4-8": {
        "in": 5.0,
        "out": 25.0,
        "cache_read": 0.5,
        "cache_write": 6.25,
    },
    "anthropic/claude-sonnet-5": {
        "in": 3.0,
        "out": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75,
    },
    "anthropic/claude-fable-5": {
        "in": 10.0,
        "out": 50.0,
        "cache_read": 1.0,
        "cache_write": 12.5,
    },
    # Verifiziert gegen die offizielle xAI-Preisliste am 2026-07-10.
    "xai/grok-4.5": {
        "in": 2.0,
        "out": 6.0,
        "cache_read": 0.5,
        "cache_write": 2.0,
    },
}

# Zeitlich begrenzte Sonnet-5-Einfuehrungspreise, jeweils inklusive Enddatum.
PRICING_PERIODS = {
    "anthropic/claude-sonnet-5": [
        {
            "through": "2026-08-31",
            "prices": {
                "in": 2.0,
                "out": 10.0,
                "cache_read": 0.2,
                "cache_write": 2.5,
            },
        }
    ]
}

# Ganze-Session-Tarife oberhalb providerspezifischer Input-Kontextschwellen.
PRICING_TOKEN_TIERS = {
    "openai/gpt-5.5": [
        {
            "above_prompt_tokens": 272_000,
            "prices": {
                "in": 10.0,
                "out": 45.0,
                "cache_read": 1.0,
                "cache_write": 10.0,
            },
        }
    ]
}

# Modelle fuer den B1-Live-Smoke; fehlende Provider-Keys fuehren zu SKIP.
SMOKE_MODELS = [
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "openai/gpt-5.5",
    "gemini/gemini-3.1-pro-preview",
    "anthropic/claude-opus-4-8",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-fable-5",
    "xai/grok-4.5",
]

# Kleines Antwortlimit je normalem Modell-Smoke zur Kostenbegrenzung.
SMOKE_MAX_OUTPUT_TOKENS = 16

# Kostenarme Tavily-Testabfrage des B1-Live-Smokes.
SMOKE_SEARCH_QUERY = "United States Congress"

# Maximale Trefferzahl des Tavily-Live-Smokes.
SMOKE_SEARCH_MAX_RESULTS = 1

# Rueckblickfenster der Tavily-Smoke-Suche in Tagen.
SMOKE_SEARCH_LOOKBACK_DAYS = 1

# Anthropic-Modell fuer den verpflichtenden Prompt-Cache-Fidelity-Check.
CACHE_CHECK_MODEL = "anthropic/claude-opus-4-8"

# Antwortlimit je Call des Cache-Fidelity-Checks.
CACHE_CHECK_MAX_OUTPUT_TOKENS = 16

# Antwortlimit fuer vollstaendige Tool-Calls im Cache-Fidelity-Check.
CACHE_CHECK_TOOL_MAX_OUTPUT_TOKENS = 128

# Zielgroesse des stabilen System-Prompts, oberhalb des Anthropic-Minimums.
CACHE_CHECK_SYSTEM_TOKENS = 5000

# Live-kalibriertes Verhaeltnis Anthropic-Usage zu LiteLLM-Lokaltokenzaehler.
CACHE_CHECK_PROVIDER_TO_LOCAL_TOKEN_RATIO = 2.0

# Anzahl zwingend auszufuehrender Tool-Turns vor dem separaten Final-Call.
CACHE_CHECK_REQUIRED_TOOL_TURNS = 2

# Aktiviert die laufweite Kosten-Notbremse der LLM-Schicht.
COST_CAP_ENABLED = True

# Maximale autoritativ berechnete LLM-Kosten in USD je Lauf.
COST_CAP_USD_PER_RUN = 20.0

# Rueckblick beim Erstlauf oder nach Ingestion-Luecken in Tagen.
INGEST_LOOKBACK_DAYS = 14

# Einzeln schaltbare Gov-Quellen; deaktivierte Quellen behalten Registry-Daten.
INGEST_SOURCES_ENABLED = {
    "congress_gov": True,
    "federal_register": True,
    "war_gov_rss": True,
    "sam_gov": True,
}

# Aktiver Search-Provider; ein Austausch erfolgt ueber einen neuen Adapter.
SEARCH_PROVIDER = "tavily"

# Provider-neutrale Search-, Extract-, Map- und Crawl-Defaults.
SEARCH_API = {
    "max_results": 15,
    "depth": "basic",
    "extract_score_threshold": 0.5,
    "extract_chunks_per_source": 3,
    "map": {
        "max_depth": 2,
        "max_breadth": 50,
        "limit": 100,
    },
    "crawl": {
        "max_depth": 2,
        "max_breadth": 20,
        "limit": 30,
        "extract_depth": "basic",
    },
}

# Tavily-spezifische Features, Budgetgrenzen und Klon-Attribution.
TAVILY = {
    "use_research_endpoint": False,
    "research_model": "mini",
    "monthly_credit_budget": 4000,
    "credit_reserve": 200,
    "project_ids": {
        "suspicious_trades": "st-base",
        "suspicious_trades_a": "st-a",
        "suspicious_trades_b": "st-b",
        "suspicious_trades_c": "st-c",
    },
}

# Maximale Retries nur fuer Tavily-429; Budgetfehler 432/433 werden nie wiederholt.
SEARCH_MAX_RETRIES = 2

# Empfehlungsbewertung: bis zum Outcome oder ueber einen festen Horizont.
EVAL_MODE = "outcome"

# Zusaetzliche Bewertungstage nach Eintritt des Ereignis-Outcomes.
EVAL_OUTCOME_BUFFER_DAYS = 5

# Harte Bewertungsobergrenze, falls kein Outcome eintritt.
EVAL_MAX_DAYS = 180

# Fester Bewertungszeitraum fuer den EVAL_MODE ``horizon``.
EVAL_HORIZON_DAYS = 90

# Primaerer Trade-Eingang aus manueller Staging-Auswahl oder Zeitfenster.
TRADE_SOURCE = "staging"

# Datumsfeld fuer die mechanische Fensterauswahl.
TRADE_DATE_FIELD = "disclosure_date"

# Fenstergroesse des optionalen ``window``-Modus in Tagen.
TRADE_WINDOW_DAYS = 14

# Aktiviert die Injektion offener User-Hinweise in vorgesehene Agent-Prompts.
USER_HINTS_ENABLED = True

# Maximales Tokenbudget des kompakten Empfehlungsdokuments.
SUMMARY_MAX_TOKENS = 1500

# Package-relativer Ordner mit den versionierten System-Prompt-Templates.
PROMPT_DIR = "agents/prompts"

# Tage vom bekannten Outcome bis zum Schliessen eines Registry-Events.
EVENT_CLOSE_AFTER_DAYS = 120

# SAM.gov-Timeout nach responseDeadLine ohne passende Award-Notice.
PROCUREMENT_AWARD_TIMEOUT_DAYS = 180

# Mindestvolumen eines war.gov-Vertragsereignisses in USD.
WAR_GOV_MIN_CONTRACT_USD = 50_000_000

# Erlaubte Federal-Register-Dokumenttypen; Notices bleiben ausgeschlossen.
FEDREG_DOC_TYPES = ["RULE", "PRORULE"]

# Poll-Intervall der Control-UI fuer den Live-Run-Status in Sekunden.
STATUS_POLL_SECONDS = 2

# Wartezeit nach kooperativem Stop vor angebotenem SIGTERM-Fallback.
STOP_GRACE_SECONDS = 120

# Kollisionsfreie Taipy-Ports je Package-Klon.
UI_PORTS = {
    "suspicious_trades": 5010,
    "suspicious_trades_a": 5011,
    "suspicious_trades_b": 5012,
    "suspicious_trades_c": 5013,
}

# In der neutralen Basis gibt es noch keine Agent-Rollen; Varianten fuellen sie.
# Konvention je Rolle: model (voller LiteLLM-String), effort, max_turns, cache,
# search/extract/crawl, max_output_tokens und prompt_template. Ein crawl-Block
# schaltet spaeter ausschliesslich fuer diese Rolle web_map/web_crawl frei.
AGENT_SPECS: dict[str, dict[str, Any]] = {}


_effective_cache: dict[str, Any] | None = None


def _defaults() -> dict[str, Any]:
    """Liefert alle UPPERCASE-Modulkonstanten als unabhaengige Defaults."""

    return {
        key: copy.deepcopy(value)
        for key, value in globals().items()
        if key.isupper()
    }


def _warn_ignored(path: tuple[str, ...], reason: str) -> None:
    """Protokolliert einen ignorierten Override mit vollstaendigem Schluesselpfad."""

    _logger.warning("Ignoring config override %s: %s", ".".join(path), reason)


def _types_compatible(default: Any, override: Any) -> bool:
    """Prueft JSON-Override-Typen ohne bool als int zu missverstehen."""

    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(override, bool)
    if isinstance(default, int):
        return isinstance(override, int) and not isinstance(override, bool)
    if isinstance(default, float):
        return isinstance(override, (int, float)) and not isinstance(override, bool)
    return type(default) is type(override)


def _merge_value(default: Any, override: Any, path: tuple[str, ...]) -> Any:
    """Merged einen validierten Wert rekursiv; Listen werden voll ersetzt."""

    if isinstance(default, dict):
        if not isinstance(override, dict):
            _warn_ignored(path, "type conflict (expected dict)")
            return copy.deepcopy(default)

        merged = copy.deepcopy(default)
        for key, value in override.items():
            child_path = (*path, str(key))
            if key not in default:
                if path == ("AGENT_SPECS",):
                    _warn_ignored(child_path, "unknown AGENT_SPECS role")
                else:
                    _warn_ignored(child_path, "unknown key")
                continue
            merged[key] = _merge_value(default[key], value, child_path)
        return merged

    if isinstance(default, list):
        if not isinstance(override, list):
            _warn_ignored(path, "type conflict (expected list)")
            return copy.deepcopy(default)
        return copy.deepcopy(override)

    if not _types_compatible(default, override):
        _warn_ignored(
            path,
            f"type conflict (expected {type(default).__name__}, "
            f"got {type(override).__name__})",
        )
        return copy.deepcopy(default)

    return copy.deepcopy(override)


def _load_overrides(path: Path) -> dict[str, Any]:
    """Liest eine Override-Datei fehlertolerant; ungueltige Dateien sind leer."""

    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Ignoring config override file %s: %s", path, exc)
        return {}

    if not isinstance(payload, dict):
        _logger.warning(
            "Ignoring config override file %s: top level must be an object",
            path,
        )
        return {}
    return payload


def _warn_unpriced_models(config: dict[str, Any]) -> None:
    """Warnt fuer erlaubte Freitext-Modelle ohne autoritativen Preiseintrag."""

    pricing = config.get("PRICING", {})
    for role, spec in config.get("AGENT_SPECS", {}).items():
        model = spec.get("model") if isinstance(spec, dict) else None
        if isinstance(model, str) and model not in pricing:
            _logger.warning(
                "AGENT_SPECS.%s.model '%s' has no PRICING entry; "
                "the model remains enabled",
                role,
                model,
            )


def effective(*, reload: bool = False) -> dict[str, Any]:
    """Gibt Defaults mit validierten UI-Overrides zurueck.

    Das Ergebnis wird pro Prozess memoisiert. ``reload=True`` verwirft das Memo
    explizit, insbesondere fuer Tests und den spaeteren UI-Preview-Pfad.
    Rueckgaben sind tiefe Kopien, damit Aufrufer weder Defaults noch Memo
    versehentlich veraendern koennen.
    """

    global _effective_cache

    if _effective_cache is not None and not reload:
        return copy.deepcopy(_effective_cache)

    defaults = _defaults()
    overrides = _load_overrides(DATA_DIR / "config_overrides.json")
    merged = copy.deepcopy(defaults)

    for key, value in overrides.items():
        if key not in defaults:
            _warn_ignored((str(key),), "unknown top-level key")
            continue
        merged[key] = _merge_value(defaults[key], value, (key,))

    _warn_unpriced_models(merged)
    _effective_cache = merged
    return copy.deepcopy(merged)
