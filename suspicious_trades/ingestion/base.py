"""Gemeinsamer Ingestion-Vertrag und Quell-Orchestrierung.

Jeder Quell-Adapter liefert normalisierte ``EventCandidate``-Objekte mit dem
festen Neun-Felder-Vertrag. ``event_id`` ist stets ``"<source>:<source_id>"``;
Re-Ingest desselben ``event_id`` fuehrt in der Registry zu einem Update, nie zu
einem Duplikat. ``event_date`` ist das fachliche Ereignisdatum, ``update_date``
das Aenderungs-/Delta-Datum; beide werden bewusst nie vermischt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)


def to_iso_date(value: Any) -> str | None:
    """Normalisiert diverse Datumsformate auf ``YYYY-MM-DD`` (oder ``None``)."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # ISO-Datum/-Zeitstempel: die ersten zehn Zeichen tragen das Kalenderdatum.
    head = text[:10]
    try:
        return date.fromisoformat(head).isoformat()
    except ValueError:
        return None


def parse_iso_date(value: Any) -> date | None:
    """Parst ein Datum defensiv zu ``date`` oder gibt ``None`` zurueck."""

    iso = to_iso_date(value)
    if iso is None:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


@dataclass(frozen=True)
class EventCandidate:
    """Normalisiertes Roh-Ereignis einer Quelle (fester Neun-Felder-Vertrag)."""

    source: str
    source_id: str
    event_class: str
    title: str
    event_date: str | None
    update_date: str | None
    expected_outcome_date: str | None
    url: str | None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_id"] = self.event_id
        return payload


class IngestionAdapter(ABC):
    """Basis fuer die vier Quell-Adapter.

    ``fetch`` kapselt den quellenspezifischen (ggf. Netzwerk-)Rohabruf,
    ``parse`` die reine, offline testbare Normalisierung. B2-Tests speisen
    eingefrorene Rohantworten direkt in ``parse`` ein.
    """

    #: Quellen-Kennung; zugleich Praefix des ``event_id``.
    SOURCE: str
    #: Ereignisklasse aller Kandidaten dieses Adapters.
    EVENT_CLASS: str
    #: Schluessel in ``INGEST_SOURCES_ENABLED`` fuer den Quellen-Schalter.
    CONFIG_KEY: str

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = dict(settings)

    @abstractmethod
    def fetch(self) -> Any:
        """Liefert die quellenspezifische Rohantwort (Cache-Read oder Netz)."""

    @abstractmethod
    def parse(self, raw: Any, *, today: date) -> list[EventCandidate]:
        """Normalisiert eine Rohantwort zu EventCandidates (netzfrei)."""

    def ingest(self, *, today: date) -> list[EventCandidate]:
        return self.parse(self.fetch(), today=today)


@dataclass
class IngestionResult:
    """Ergebnis eines Ingestion-Laufs ueber alle aktiven Quellen."""

    candidates: list[EventCandidate] = field(default_factory=list)
    per_source: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def build_adapters(settings: dict[str, Any]) -> list[IngestionAdapter]:
    """Instanziiert die vier Basis-Adapter (relative Package-Importe)."""

    from .congress_gov import CongressGovAdapter
    from .federal_register import FederalRegisterAdapter
    from .sam_gov import SamGovAdapter
    from .war_gov_rss import WarGovRssAdapter

    return [
        CongressGovAdapter(settings),
        FederalRegisterAdapter(settings),
        WarGovRssAdapter(settings),
        SamGovAdapter(settings),
    ]


def run_ingestion(
    settings: dict[str, Any],
    *,
    today: date,
    adapters: list[IngestionAdapter] | None = None,
) -> IngestionResult:
    """Fuehrt alle aktivierten Quellen aus; deaktivierte werden protokolliert.

    Eine fehlschlagende Quelle bricht den Gesamtlauf nicht ab: Der Fehler wird
    redigiert protokolliert und die uebrigen Quellen laufen weiter.
    """

    enabled = settings.get("INGEST_SOURCES_ENABLED", {})
    if adapters is None:
        adapters = build_adapters(settings)

    result = IngestionResult()
    for adapter in adapters:
        key = adapter.CONFIG_KEY
        if not enabled.get(key, True):
            logger.info("Ingestion-Quelle %s ist deaktiviert; uebersprungen.", key)
            result.skipped.append(key)
            continue
        try:
            found = adapter.ingest(today=today)
        except Exception as exc:  # pragma: no cover - defensiver Pfad
            logger.warning(
                "Ingestion-Quelle %s fehlgeschlagen: %s", key, type(exc).__name__
            )
            result.errors[key] = f"{type(exc).__name__}: {exc}"
            result.per_source[key] = 0
            continue
        result.candidates.extend(found)
        result.per_source[key] = len(found)
    return result
