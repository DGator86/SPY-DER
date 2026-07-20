"""Market-data providers (master spec §13.1, §63 Phase 2)."""

from __future__ import annotations

from spy_der.market_data.providers.base import MarketDataProvider, RawTick
from spy_der.market_data.providers.static import StaticProvider

__all__ = ["MarketDataProvider", "RawTick", "StaticProvider"]
