"""Canonical market-data ingestion (master spec sections 13 to 15)."""

from __future__ import annotations

from spy_der.market_data.assembler import CanonicalSnapshotAssembler
from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.freshness import (
    age_seconds,
    build_observation,
    classify_status,
)
from spy_der.market_data.legacy_adapter import SystemASnapshotAdapter

__all__ = [
    "CanonicalSnapshotAssembler",
    "MarketCalendar",
    "SystemASnapshotAdapter",
    "age_seconds",
    "build_observation",
    "classify_status",
]
