"""Federal-Register-Adapter: verarbeitet ausschliesslich RULE/PRORULE.

Der oeffentliche Federal-Register-API-Endpunkt braucht keinen Key. Der Adapter
mappt die API-Typbezeichnungen auf die kanonischen Codes ``RULE``/``PRORULE``
und filtert gegen ``FEDREG_DOC_TYPES`` (Notices bleiben Rauschen).
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

import httpx

from .base import EventCandidate, IngestionAdapter, to_iso_date

logger = logging.getLogger(__name__)

# API-Typbezeichnung -> kanonischer Konfigurations-Code.
_TYPE_MAP = {
    "Rule": "RULE",
    "Proposed Rule": "PRORULE",
    "RULE": "RULE",
    "PRORULE": "PRORULE",
}

# Vom Adapter benoetigte Felder der Dokument-API.
_FIELDS = [
    "document_number",
    "type",
    "title",
    "publication_date",
    "effective_on",
    "comments_close_on",
    "html_url",
    "abstract",
    "agencies",
    "regulation_id_numbers",
    "docket_ids",
]


class FederalRegisterAdapter(IngestionAdapter):
    SOURCE = "federal_register"
    EVENT_CLASS = "regulation"
    CONFIG_KEY = "federal_register"

    def fetch(self) -> dict[str, Any]:
        lookback = int(self.settings.get("INGEST_LOOKBACK_DAYS", 14))
        since = (date.today() - timedelta(days=lookback)).isoformat()
        params: list[tuple[str, str]] = [
            ("per_page", str(int(self.settings.get("FEDERAL_REGISTER_PER_PAGE", 200)))),
            ("order", "newest"),
            ("conditions[publication_date][gte]", since),
        ]
        params.extend(("fields[]", field) for field in _FIELDS)
        # Serverseitiger Typfilter deckt sich mit FEDREG_DOC_TYPES.
        for canonical in self.settings.get("FEDREG_DOC_TYPES", ["RULE", "PRORULE"]):
            params.append(("conditions[type][]", str(canonical).upper()))

        timeout = float(self.settings.get("INGEST_TIMEOUT_SECONDS", 30.0))
        response = httpx.get(
            str(self.settings["FEDERAL_REGISTER_API_URL"]),
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, *, today: date) -> list[EventCandidate]:
        allowed = {str(t).upper() for t in self.settings.get("FEDREG_DOC_TYPES", [])}
        candidates: list[EventCandidate] = []
        for doc in (raw or {}).get("results", []) or []:
            canonical = _TYPE_MAP.get(str(doc.get("type", "")))
            if canonical is None or canonical not in allowed:
                continue
            document_number = doc.get("document_number")
            if not document_number:
                continue

            effective_on = to_iso_date(doc.get("effective_on"))
            comments_close_on = to_iso_date(doc.get("comments_close_on"))
            payload = {
                "type": canonical,
                "abstract": doc.get("abstract", ""),
                "agencies": [
                    a.get("name")
                    for a in doc.get("agencies", []) or []
                    if isinstance(a, dict) and a.get("name")
                ],
                "rin": list(doc.get("regulation_id_numbers", []) or []),
                "docket_ids": list(doc.get("docket_ids", []) or []),
                "effective_on": effective_on,
                "comments_close_on": comments_close_on,
            }
            candidates.append(
                EventCandidate(
                    source=self.SOURCE,
                    source_id=str(document_number),
                    event_class=self.EVENT_CLASS,
                    title=str(doc.get("title") or ""),
                    event_date=to_iso_date(doc.get("publication_date")),
                    update_date=to_iso_date(doc.get("publication_date")),
                    expected_outcome_date=effective_on or comments_close_on,
                    url=doc.get("html_url"),
                    raw_payload=payload,
                )
            )
        return candidates
