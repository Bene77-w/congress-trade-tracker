"""Ingestion-Schicht: vier Quell-Adapter und die Lauf-Orchestrierung."""

from __future__ import annotations

from .base import (
    EventCandidate,
    IngestionAdapter,
    IngestionResult,
    build_adapters,
    parse_iso_date,
    run_ingestion,
    to_iso_date,
)
from .congress_gov import CongressGovAdapter
from .federal_register import FederalRegisterAdapter
from .sam_gov import SamGovAdapter, is_award_notice
from .war_gov_rss import WarGovRssAdapter

__all__ = [
    "EventCandidate",
    "IngestionAdapter",
    "IngestionResult",
    "build_adapters",
    "run_ingestion",
    "parse_iso_date",
    "to_iso_date",
    "CongressGovAdapter",
    "FederalRegisterAdapter",
    "WarGovRssAdapter",
    "SamGovAdapter",
    "is_award_notice",
]
