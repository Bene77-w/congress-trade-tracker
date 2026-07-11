"""SAM.gov-Adapter: Get-Opportunities-API (v2).

Solicitation/Presolicitation-Notices sind offene Beschaffungs-Ereignisse.
Award-Notices werden ueber ``solicitationNumber`` dem offenen Ereignis
zugeordnet (Outcome) und in der Registry NICHT als eigenes Ereignis abgelegt.
Der Adapter normalisiert lediglich; die Zuordnung leistet die Registry.
"""

from __future__ import annotations

from datetime import date
import logging
import os
from typing import Any

import httpx

from .base import EventCandidate, IngestionAdapter, to_iso_date

logger = logging.getLogger(__name__)


def _award(raw_award: Any) -> dict[str, Any] | None:
    if not isinstance(raw_award, dict) or not raw_award:
        return None
    awardee = raw_award.get("awardee")
    awardee_name = (
        awardee.get("name") if isinstance(awardee, dict) else awardee
    )
    return {
        "date": to_iso_date(raw_award.get("date")),
        "amount": raw_award.get("amount"),
        "awardee": awardee_name,
    }


def is_award_notice(payload: dict[str, Any]) -> bool:
    """True, wenn ein Kandidat eine Award-Notice ist (Outcome, kein Ereignis)."""

    if payload.get("award"):
        return True
    return "award" in str(payload.get("type", "")).lower()


class SamGovAdapter(IngestionAdapter):
    SOURCE = "sam_gov"
    EVENT_CLASS = "procurement"
    CONFIG_KEY = "sam_gov"

    def fetch(self) -> dict[str, Any]:
        api_key = os.getenv("SAM_GOV_API_KEY")
        if not api_key:
            raise RuntimeError("SAM_GOV_API_KEY ist nicht gesetzt.")
        timeout = float(self.settings.get("INGEST_TIMEOUT_SECONDS", 30.0))
        params = {
            "api_key": api_key,
            "limit": int(self.settings.get("SAM_GOV_LIMIT", 100)),
        }
        response = httpx.get(
            str(self.settings["SAM_GOV_API_URL"]),
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, *, today: date) -> list[EventCandidate]:
        records = (raw or {}).get("opportunitiesData", []) or []
        candidates: list[EventCandidate] = []
        for op in records:
            notice_id = op.get("noticeId")
            if not notice_id:
                continue
            payload = {
                "type": op.get("type", ""),
                "solicitationNumber": op.get("solicitationNumber"),
                "naicsCode": op.get("naicsCode"),
                "classificationCode": op.get("classificationCode"),
                "agency": _agency_path(op),
                "award": _award(op.get("award")),
                "responseDeadLine": to_iso_date(op.get("responseDeadLine")),
            }
            candidates.append(
                EventCandidate(
                    source=self.SOURCE,
                    source_id=str(notice_id),
                    event_class=self.EVENT_CLASS,
                    title=str(op.get("title") or ""),
                    event_date=to_iso_date(op.get("postedDate")),
                    update_date=to_iso_date(
                        op.get("modifiedDate") or op.get("postedDate")
                    ),
                    expected_outcome_date=to_iso_date(op.get("responseDeadLine")),
                    url=op.get("uiLink"),
                    raw_payload=payload,
                )
            )
        return candidates


def _agency_path(op: dict[str, Any]) -> str:
    hierarchy = op.get("organizationHierarchy")
    if isinstance(hierarchy, list):
        names = [h.get("name") for h in hierarchy if isinstance(h, dict) and h.get("name")]
        if names:
            return " / ".join(names)
    return str(op.get("fullParentPathName") or op.get("department") or "")
