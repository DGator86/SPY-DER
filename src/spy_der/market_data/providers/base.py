"""Provider protocol and raw tick contract (master spec §13.1, §63 Phase 2).

A provider owns authentication, provider calls, response parsing, source
timestamps, and provider errors — nothing downstream (spec §13.1). It emits a
:class:`RawTick`; the composite feed and assembler turn ticks into canonical
snapshots. Live vendor adapters (Tradier/Tastytrade/Massive/Yahoo) implement
:class:`MarketDataProvider` over the network; the deterministic providers used
in tests and replay implement the same protocol offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from spy_der.contracts.common import require_tz_aware
from spy_der.contracts.market import Bar, OptionQuote

__all__ = ["MarketDataProvider", "RawTick"]


@dataclass(frozen=True, slots=True)
class RawTick:
    """A single provider observation, before canonicalization."""

    provider: str
    symbol: str
    observed_at: datetime
    underlying_price: Decimal
    underlying_bid: Decimal | None = None
    underlying_ask: Decimal | None = None
    bars_1m: tuple[Bar, ...] = ()
    option_chain: tuple[OptionQuote, ...] = ()
    has_chain: bool = True

    def __post_init__(self) -> None:
        require_tz_aware(self.observed_at, "RawTick.observed_at")


@runtime_checkable
class MarketDataProvider(Protocol):
    """Ordered failover member. ``fetch`` returns ``None`` when unavailable."""

    @property
    def name(self) -> str: ...

    def fetch(self, timestamp: datetime) -> RawTick | None: ...
