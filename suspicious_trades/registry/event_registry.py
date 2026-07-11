"""Ereignis-Registry: events.jsonl, Lebenszyklus und Outcome-Detektor.

Die Registry fuehrt EventCandidates in ``events.jsonl`` zusammen (Re-Ingest =
Update, kein Duplikat) und verwaltet den Lebenszyklus
``open -> outcome_known -> closed`` sowie den Sonderzustand ``expired``.

Die vier bindenden Outcome-Regeln (§5f des Basisplans):

* ``legislation``   -> Floor-Action mit Roll-Call ODER "Became Public Law".
* ``regulation``    -> RULE: ``effective_on`` erreicht; PRORULE: spaeteres RULE
  mit gleichem RIN/Docket ingested.
* ``defense_contract`` -> sofort bei Ingest (Announcement = Outcome).
* ``procurement``   -> Award-Notice gleicher ``solicitationNumber`` ODER
  ``responseDeadLine`` + ``PROCUREMENT_AWARD_TIMEOUT_DAYS`` ueberschritten
  (dann ``expired``).

Abschluss aller Klassen: ``outcome_known -> closed``, sobald
``actual_outcome_date`` aelter als ``EVENT_CLOSE_AFTER_DAYS`` ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from ..ingestion.base import EventCandidate, parse_iso_date, to_iso_date
from ..ingestion.sam_gov import is_award_notice

logger = logging.getLogger(__name__)

STATUS_OPEN = "open"
STATUS_OUTCOME_KNOWN = "outcome_known"
STATUS_CLOSED = "closed"
STATUS_EXPIRED = "expired"


@dataclass
class RegistryStats:
    """Zaehl-Kennzahlen eines ``registry-update`` (Schema §5e)."""

    new_events: int = 0
    updated: int = 0
    outcomes_detected: int = 0
    closed: int = 0
    expired: int = 0


class EventRegistry:
    """Persistente Ereignisliste mit Lebenszyklus-Logik."""

    def __init__(self, path: Path, settings: dict[str, Any]) -> None:
        self.path = Path(path)
        self.settings = dict(settings)

    # -- Persistenz -----------------------------------------------------
    def load(self) -> dict[str, dict[str, Any]]:
        events: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return events
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Ungueltige events.jsonl-Zeile ignoriert: %s", exc)
                continue
            event_id = record.get("event_id")
            if event_id:
                events[str(event_id)] = record
        return events

    def save(self, events: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for record in events.values():
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    # -- Lauf ------------------------------------------------------------
    def update(
        self, candidates: Iterable[EventCandidate], *, today: date
    ) -> RegistryStats:
        events = self.load()
        stats = RegistryStats()

        awards: list[EventCandidate] = []
        for candidate in candidates:
            if candidate.event_class == "procurement" and is_award_notice(
                candidate.raw_payload
            ):
                awards.append(candidate)
                continue
            self._upsert(events, candidate, today=today, stats=stats)

        self._apply_awards(events, awards, today=today, stats=stats)
        self._detect_outcomes(events, today=today, stats=stats)
        self._close_and_expire(events, today=today, stats=stats)

        self.save(events)
        return stats

    # -- interne Schritte ------------------------------------------------
    def _upsert(
        self,
        events: dict[str, dict[str, Any]],
        candidate: EventCandidate,
        *,
        today: date,
        stats: RegistryStats,
    ) -> None:
        now = today.isoformat()
        existing = events.get(candidate.event_id)
        if existing is None:
            record = candidate.to_dict()
            record.update(
                status=STATUS_OPEN,
                actual_outcome_date=None,
                first_seen=now,
                last_updated=now,
            )
            events[candidate.event_id] = record
            stats.new_events += 1
            return

        existing["title"] = candidate.title
        existing["update_date"] = candidate.update_date
        existing["expected_outcome_date"] = candidate.expected_outcome_date
        if candidate.url:
            existing["url"] = candidate.url
        merged = dict(existing.get("raw_payload") or {})
        merged.update(candidate.raw_payload or {})
        existing["raw_payload"] = merged
        existing["last_updated"] = now
        stats.updated += 1

    def _apply_awards(
        self,
        events: dict[str, dict[str, Any]],
        awards: list[EventCandidate],
        *,
        today: date,
        stats: RegistryStats,
    ) -> None:
        for award in awards:
            solicitation = (award.raw_payload or {}).get("solicitationNumber")
            target = _find_open_solicitation(events, solicitation)
            if target is None:
                logger.warning(
                    "SAM-Award ohne offenes Solicitation-Event (solicitationNumber=%s).",
                    solicitation,
                )
                continue
            award_info = (award.raw_payload or {}).get("award") or {}
            outcome_date = to_iso_date(award_info.get("date")) or award.event_date
            target["status"] = STATUS_OUTCOME_KNOWN
            target["actual_outcome_date"] = outcome_date
            target.setdefault("raw_payload", {})["award"] = award_info
            target["last_updated"] = today.isoformat()
            stats.outcomes_detected += 1

    def _detect_outcomes(
        self,
        events: dict[str, dict[str, Any]],
        *,
        today: date,
        stats: RegistryStats,
    ) -> None:
        for event in events.values():
            if event.get("status") != STATUS_OPEN:
                continue
            event_class = event.get("event_class")
            outcome_date: str | None = None
            if event_class == "defense_contract":
                outcome_date = event.get("event_date")
            elif event_class == "legislation":
                outcome_date = _legislation_outcome(event)
            elif event_class == "regulation":
                outcome_date = _regulation_outcome(event, events, today=today)
            # procurement: Outcome via Award (_apply_awards) oder Timeout (expire).

            if outcome_date:
                event["status"] = STATUS_OUTCOME_KNOWN
                event["actual_outcome_date"] = to_iso_date(outcome_date)
                event["last_updated"] = today.isoformat()
                stats.outcomes_detected += 1

    def _close_and_expire(
        self,
        events: dict[str, dict[str, Any]],
        *,
        today: date,
        stats: RegistryStats,
    ) -> None:
        close_days = int(self.settings.get("EVENT_CLOSE_AFTER_DAYS", 120))
        timeout_days = int(self.settings.get("PROCUREMENT_AWARD_TIMEOUT_DAYS", 180))
        for event in events.values():
            status = event.get("status")
            if status == STATUS_OUTCOME_KNOWN:
                outcome = parse_iso_date(event.get("actual_outcome_date"))
                if outcome is not None and (today - outcome).days > close_days:
                    event["status"] = STATUS_CLOSED
                    event["last_updated"] = today.isoformat()
                    stats.closed += 1
            elif status == STATUS_OPEN and event.get("event_class") == "procurement":
                deadline = parse_iso_date(
                    (event.get("raw_payload") or {}).get("responseDeadLine")
                    or event.get("expected_outcome_date")
                )
                if deadline is not None and (today - deadline).days > timeout_days:
                    event["status"] = STATUS_EXPIRED
                    event["last_updated"] = today.isoformat()
                    stats.expired += 1


def _find_open_solicitation(
    events: dict[str, dict[str, Any]], solicitation: Any
) -> dict[str, Any] | None:
    if not solicitation:
        return None
    for event in events.values():
        if event.get("event_class") != "procurement":
            continue
        if event.get("status") != STATUS_OPEN:
            continue
        if (event.get("raw_payload") or {}).get("solicitationNumber") == solicitation:
            return event
    return None


def _legislation_outcome(event: dict[str, Any]) -> str | None:
    payload = event.get("raw_payload") or {}
    for action in payload.get("actions", []) or []:
        if not isinstance(action, dict):
            continue
        if action.get("type") == "Floor" and action.get("roll_call"):
            return action.get("date")
    latest = payload.get("latest_action") or {}
    text = str(latest.get("text") or "")
    if "became public law" in text.lower():
        return latest.get("actionDate") or latest.get("date")
    return None


def _regulation_outcome(
    event: dict[str, Any],
    events: dict[str, dict[str, Any]],
    *,
    today: date,
) -> str | None:
    payload = event.get("raw_payload") or {}
    reg_type = payload.get("type")
    if reg_type == "RULE":
        effective = parse_iso_date(payload.get("effective_on"))
        if effective is not None and effective <= today:
            return payload.get("effective_on")
        return None
    if reg_type == "PRORULE":
        rins = set(payload.get("rin") or [])
        dockets = set(payload.get("docket_ids") or [])
        for other in events.values():
            if other is event or other.get("event_class") != "regulation":
                continue
            other_payload = other.get("raw_payload") or {}
            if other_payload.get("type") != "RULE":
                continue
            if (rins & set(other_payload.get("rin") or [])) or (
                dockets & set(other_payload.get("docket_ids") or [])
            ):
                return other.get("event_date")
    return None
