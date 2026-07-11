"""Congress.gov-Adapter: liest ausschliesslich den bestehenden Cache.

Der Adapter fasst die vom Haupt-Scraper (``scrapers/fetch_congress_data.py``)
befuellten Cache-Dateien zu je einem EventCandidate pro Bill zusammen. Es
findet KEIN eigener Download statt; der Cache-Refresh bleibt Sache der
Hauptpipeline. Gefiltert wird ueber ``updateDate`` gegen
``INGEST_LOOKBACK_DAYS``.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import logging
from pathlib import Path
from typing import Any

from .base import EventCandidate, IngestionAdapter, parse_iso_date, to_iso_date

logger = logging.getLogger(__name__)

# congress.gov-Web-Pfadsegment je Bill-Typ fuer die Link-Konstruktion.
_WEB_TYPE = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
    "hres": "house-resolution",
    "sres": "senate-resolution",
}

_CACHE_FILES = {
    "bills_list": "bills_list.json",
    "bill_details": "bill_details.json",
    "bill_actions": "bill_actions.json",
    "bill_committees": "bill_committees.json",
    "bill_subjects": "bill_subjects.json",
}


def _read_json(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Congress-Cache %s unlesbar: %s", path, exc)
        return []
    return payload if isinstance(payload, list) else []


def _bill_type(raw: dict[str, Any]) -> str:
    value = raw.get("bill_type") or raw.get("type") or ""
    return str(value).lower()


def _bill_id(raw: dict[str, Any]) -> str | None:
    """Rekonstruiert den Cache-Schluessel ``<typ>-<congress>-<nummer>``."""

    explicit = raw.get("bill_id")
    if explicit:
        return str(explicit)
    bill_type = _bill_type(raw)
    congress = raw.get("congress")
    number = raw.get("number")
    if not bill_type or congress in (None, "") or number in (None, ""):
        return None
    return f"{bill_type}-{congress}-{number}"


def _bill_url(raw: dict[str, Any]) -> str | None:
    bill_type = _bill_type(raw)
    congress = raw.get("congress")
    number = raw.get("number")
    web_type = _WEB_TYPE.get(bill_type)
    if not web_type or congress in (None, "") or number in (None, ""):
        return raw.get("url")
    return (
        f"https://www.congress.gov/bill/{congress}th-congress/"
        f"{web_type}/{number}"
    )


class CongressGovAdapter(IngestionAdapter):
    SOURCE = "congress_gov"
    EVENT_CLASS = "legislation"
    CONFIG_KEY = "congress_gov"

    def fetch(self) -> dict[str, list[dict[str, Any]]]:
        cache_dir = Path(self.settings["CONGRESS_CACHE_DIR"])
        return {
            name: _read_json(cache_dir / filename)
            for name, filename in _CACHE_FILES.items()
        }

    def parse(self, raw: Any, *, today: date) -> list[EventCandidate]:
        lookback = int(self.settings.get("INGEST_LOOKBACK_DAYS", 14))
        cutoff = today - timedelta(days=lookback)

        details = _index(raw.get("bill_details"))
        actions = _index(raw.get("bill_actions"))
        committees = _index(raw.get("bill_committees"))
        subjects = _index(raw.get("bill_subjects"))

        candidates: list[EventCandidate] = []
        for bill in raw.get("bills_list", []) or []:
            bill_id = _bill_id(bill)
            if not bill_id:
                continue
            update_date = to_iso_date(bill.get("updateDate") or bill.get("update_date"))
            update_dt = parse_iso_date(update_date)
            if update_dt is None or update_dt < cutoff:
                continue

            detail = details.get(bill_id, {})
            action_entry = actions.get(bill_id, {})
            latest_action = detail.get("latest_action") or bill.get("latestAction") or {}

            payload = {
                "bill_id": bill_id,
                "bill_type": _bill_type(bill),
                "policy_area": detail.get("policy_area", ""),
                "short_title": detail.get("short_title", ""),
                "sponsors": detail.get("sponsors", []),
                "committees": committees.get(bill_id, {}).get("committees", []),
                "legislative_subjects": subjects.get(bill_id, {}).get(
                    "legislative_subjects", []
                ),
                "latest_action": latest_action,
                "actions": action_entry.get("actions", []),
            }

            candidates.append(
                EventCandidate(
                    source=self.SOURCE,
                    source_id=bill_id,
                    event_class=self.EVENT_CLASS,
                    title=str(detail.get("title") or bill.get("title") or ""),
                    event_date=to_iso_date(
                        detail.get("introduced_date") or bill.get("introducedDate")
                    ),
                    update_date=update_date,
                    expected_outcome_date=None,
                    url=_bill_url(bill),
                    raw_payload=payload,
                )
            )
        return candidates


def _index(records: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        return {}
    return {
        str(rec.get("bill_id")): rec
        for rec in records
        if isinstance(rec, dict) and rec.get("bill_id")
    }
