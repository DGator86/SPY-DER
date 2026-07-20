"""Canonical market-data ingestion (master spec sections 13 to 15)."""

from __future__ import annotations

from spy_der.market_data.assembler import CanonicalSnapshotAssembler
from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.composite import CompositeFeed
from spy_der.market_data.freshness import (
    age_seconds,
    build_observation,
    classify_status,
)
from spy_der.market_data.legacy_adapter import SystemASnapshotAdapter
from spy_der.market_data.providers import MarketDataProvider, RawTick, StaticProvider
from spy_der.market_data.recording import SnapshotRecorder, build_record
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed

__all__ = [
    "CanonicalSnapshotAssembler",
    "CompositeFeed",
    "CorruptRecordingError",
    "MarketCalendar",
    "MarketDataProvider",
    "RawTick",
    "ReplayFeed",
    "SnapshotRecorder",
    "StaticProvider",
    "SystemASnapshotAdapter",
    "age_seconds",
    "build_observation",
    "build_record",
    "classify_status",
]
