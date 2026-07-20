"""Deterministic, offline providers (master spec §63 Phase 2, §15).

``StaticProvider`` serves pre-supplied ticks keyed by timestamp; it requires no
network and is the substrate for replay and tests. Live vendor adapters wrap the
same :class:`MarketDataProvider` protocol over their SDKs (deferred: network
I/O).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from spy_der.market_data.providers.base import MarketDataProvider, RawTick

__all__ = ["StaticProvider"]


class StaticProvider(MarketDataProvider):
    """Serve recorded/synthetic ticks by exact timestamp; no network."""

    def __init__(self, name: str, ticks: Iterable[RawTick]) -> None:
        self._name = name
        self._by_ts: dict[datetime, RawTick] = {t.observed_at: t for t in ticks}

    @property
    def name(self) -> str:
        return self._name

    def fetch(self, timestamp: datetime) -> RawTick | None:
        return self._by_ts.get(timestamp)
