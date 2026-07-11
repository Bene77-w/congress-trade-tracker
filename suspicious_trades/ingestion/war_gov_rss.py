"""war.gov/defense.gov-Contracts-RSS-Adapter.

Jeder Tagesartikel des Contracts-Feeds gliedert sich in Teilstreitkraft-Header
und darunter je einen Absatz pro Vertrag. Der Adapter erzeugt EINEN
EventCandidate je Contract-Absatz oberhalb ``WAR_GOV_MIN_CONTRACT_USD``. Die
Veroeffentlichung IST bereits das oeffentliche Outcome; die Registry setzt
``defense_contract``-Ereignisse deshalb sofort auf ``outcome_known``.
"""

from __future__ import annotations

from datetime import date
from email.utils import parsedate_to_datetime
import html
import logging
import re
from typing import Any
import xml.etree.ElementTree as ET

import httpx

from .base import EventCandidate, IngestionAdapter, to_iso_date

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_PARA_SPLIT_RE = re.compile(r"</p>|<br\s*/?>", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(billion|million|thousand)?", re.IGNORECASE
)
_MULTIPLIER = {
    "": 1.0,
    "thousand": 1_000.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
}


def _strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _is_branch_header(text: str) -> bool:
    """Teilstreitkraft-Header: kurz, gross geschrieben, ohne Betrag/Ziffern."""

    if not text or "$" in text or any(ch.isdigit() for ch in text):
        return False
    if len(text) > 60:
        return False
    return any(ch.isalpha() for ch in text) and text == text.upper()


def _parse_amount(text: str) -> float | None:
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").lower()
    return number * _MULTIPLIER[unit]


def _contractor(text: str) -> str:
    """Grobe Contractor-Extraktion: Name bis zum ersten Komma."""

    return text.split(",", 1)[0].strip()


def _iter_contracts(description_html: str) -> list[tuple[str, str]]:
    """Liefert ``(branch, absatz_text)`` je erkanntem Vertragsabsatz."""

    contracts: list[tuple[str, str]] = []
    current_branch = ""
    for fragment in _PARA_SPLIT_RE.split(description_html):
        text = _strip_tags(fragment)
        if not text:
            continue
        if _is_branch_header(text):
            current_branch = text
            continue
        if "$" in text and _parse_amount(text) is not None:
            contracts.append((current_branch, text))
    return contracts


def _rss_items(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for item in root.iter("item"):
        entry: dict[str, str] = {}
        for child in item:
            tag = child.tag.split("}")[-1]
            entry[tag] = (child.text or "").strip()
        items.append(entry)
    return items


class WarGovRssAdapter(IngestionAdapter):
    SOURCE = "war_gov_rss"
    EVENT_CLASS = "defense_contract"
    CONFIG_KEY = "war_gov_rss"

    def fetch(self) -> str:
        timeout = float(self.settings.get("INGEST_TIMEOUT_SECONDS", 30.0))
        response = httpx.get(str(self.settings["WAR_GOV_RSS_URL"]), timeout=timeout)
        response.raise_for_status()
        return response.text

    def parse(self, raw: Any, *, today: date) -> list[EventCandidate]:
        threshold = float(self.settings.get("WAR_GOV_MIN_CONTRACT_USD", 0))
        candidates: list[EventCandidate] = []
        for item in _rss_items(str(raw)):
            guid = item.get("guid") or item.get("link") or ""
            link = item.get("link") or ""
            pub_iso = _rfc822_to_iso(item.get("pubDate"))
            description = item.get("description") or item.get("encoded") or ""

            # Laufende Contract-Nr. ueber ALLE Absaetze -> ID stabil auch bei
            # geaenderter Volumen-Schwelle.
            for number, (branch, para) in enumerate(_iter_contracts(description), start=1):
                amount = _parse_amount(para)
                if amount is None or amount < threshold:
                    continue
                contractor = _contractor(para)
                title = " — ".join(part for part in (contractor, branch) if part)
                candidates.append(
                    EventCandidate(
                        source=self.SOURCE,
                        source_id=f"{guid}#{number}",
                        event_class=self.EVENT_CLASS,
                        title=title,
                        event_date=pub_iso,
                        update_date=pub_iso,
                        expected_outcome_date=None,
                        url=link,
                        raw_payload={
                            "branch": branch,
                            "contractor": contractor,
                            "amount_usd": amount,
                            "description": para,
                            "pub_date": pub_iso,
                        },
                    )
                )
        return candidates


def _rfc822_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return to_iso_date(value)
