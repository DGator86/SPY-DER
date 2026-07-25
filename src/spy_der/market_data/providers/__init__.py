"""Market-data providers (master spec §13.1, §63 Phase 2)."""

from __future__ import annotations

from spy_der.market_data.providers._http import HttpConfig, ProviderHttpError
from spy_der.market_data.providers.base import MarketDataProvider, RawTick
from spy_der.market_data.providers.factory import (
    AVAILABLE_PROVIDERS,
    PENDING_PROVIDERS,
    ProviderChain,
    UnknownProviderError,
    build_provider_chain,
)
from spy_der.market_data.providers.massive import MassiveProvider
from spy_der.market_data.providers.spot import ChainQuoteView, estimate_spot_from_chain
from spy_der.market_data.providers.static import StaticProvider

__all__ = [
    "AVAILABLE_PROVIDERS",
    "PENDING_PROVIDERS",
    "ChainQuoteView",
    "HttpConfig",
    "MarketDataProvider",
    "MassiveProvider",
    "ProviderChain",
    "ProviderHttpError",
    "RawTick",
    "StaticProvider",
    "UnknownProviderError",
    "build_provider_chain",
    "estimate_spot_from_chain",
]
