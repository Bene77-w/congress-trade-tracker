"""Ereignis-Registry mit Lebenszyklus und Outcome-Detektor."""

from __future__ import annotations

from .event_registry import (
    EventRegistry,
    RegistryStats,
    STATUS_CLOSED,
    STATUS_EXPIRED,
    STATUS_OPEN,
    STATUS_OUTCOME_KNOWN,
)

__all__ = [
    "EventRegistry",
    "RegistryStats",
    "STATUS_OPEN",
    "STATUS_OUTCOME_KNOWN",
    "STATUS_CLOSED",
    "STATUS_EXPIRED",
]
