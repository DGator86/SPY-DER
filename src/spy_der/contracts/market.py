"""Canonical market-data contracts (master spec §13).

Immutable, typed, timezone-aware, deterministically serializable. Provider SDK
objects stop before these types (spec §11). Bad data is *flagged*, never
silently repaired (spec §13.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from spy_der.contracts.common import (
    SCHEMA_VERSION,
    require_finite,
    require_tz_aware,
)

__all__ = [
    "REQUIRED_FEED_COMPONENTS",
    "Bar",
    "CanonicalMarketSnapshot",
    "CatalystState",
    "ChainCoverage",
    "DataQuality",
    "FeedComponent",
    "FeedObservation",
    "FeedStatus",
    "OptionContract",
    "OptionQuote",
    "OptionType",
    "ProviderSelection",
    "SessionStatus",
]


class FeedComponent(StrEnum):
    SPOT = "spot"
    BARS = "bars"
    OPTION_CHAIN = "option_chain"
    SETTLEMENT = "settlement"
    BREADTH = "breadth"
    FLOW = "flow"
    MARKET_INTERNALS = "market_internals"
    CATALYST = "catalyst"


REQUIRED_FEED_COMPONENTS: tuple[FeedComponent, ...] = (
    FeedComponent.SPOT,
    FeedComponent.BARS,
    FeedComponent.OPTION_CHAIN,
    FeedComponent.SETTLEMENT,
)


class FeedStatus(StrEnum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    FALLBACK = "FALLBACK"


class SessionStatus(StrEnum):
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HOLIDAY = "HOLIDAY"


class OptionType(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        require_tz_aware(self.timestamp, "Bar.timestamp")


@dataclass(frozen=True, slots=True)
class OptionContract:
    contract_id: str
    underlying_symbol: str
    expiration: date
    option_type: OptionType
    strike: Decimal
    multiplier: int = 100
    settlement_style: str = "PM"


@dataclass(frozen=True, slots=True)
class OptionQuote:
    contract: OptionContract
    received_at: datetime
    source: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    mark: Decimal | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    observed_at: datetime | None = None
    age_seconds: float | None = None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_tz_aware(self.received_at, "OptionQuote.received_at")
        if self.observed_at is not None:
            require_tz_aware(self.observed_at, "OptionQuote.observed_at")
        for name in ("implied_volatility", "delta", "gamma", "theta", "vega"):
            value = getattr(self, name)
            if value is not None:
                require_finite(value, f"OptionQuote.{name}")


@dataclass(frozen=True, slots=True)
class FeedObservation:
    component: FeedComponent
    provider: str
    received_at: datetime
    status: FeedStatus
    freshness_limit_seconds: float
    observed_at: datetime | None = None
    age_seconds: float | None = None
    attempt_order: int = 0
    fallback_used: bool = False
    error_code: str | None = None
    error_message_hash: str | None = None

    def __post_init__(self) -> None:
        require_tz_aware(self.received_at, "FeedObservation.received_at")
        if self.observed_at is not None:
            require_tz_aware(self.observed_at, "FeedObservation.observed_at")


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    component: FeedComponent
    provider: str
    fallback_used: bool = False
    attempt_order: int = 0


@dataclass(frozen=True, slots=True)
class ChainCoverage:
    contracts_total: int = 0
    strikes_total: int = 0
    min_strike: Decimal | None = None
    max_strike: Decimal | None = None
    has_calls: bool = False
    has_puts: bool = False


@dataclass(frozen=True, slots=True)
class CatalystState:
    lockout_active: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DataQuality:
    is_healthy: bool = True
    penalty: float = 0.0
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_finite(self.penalty, "DataQuality.penalty")


@dataclass(frozen=True, slots=True)
class CanonicalMarketSnapshot:
    """The canonical market snapshot (spec §13.5).

    ``snapshot_id`` and ``content_hash`` are assigned by the assembler; identity
    includes the schema and normalization versions.
    """

    snapshot_id: str
    content_hash: str
    timestamp: datetime
    session_date: date
    underlying_symbol: str
    underlying_price: Decimal
    session_status: SessionStatus
    schema_version: str = SCHEMA_VERSION
    normalization_version: str = "1.0.0"
    underlying_bid: Decimal | None = None
    underlying_ask: Decimal | None = None
    minutes_from_open: int | None = None
    minutes_to_close: int | None = None
    bars_1m: tuple[Bar, ...] = ()
    option_chain: tuple[OptionQuote, ...] = ()
    feed_observations: tuple[FeedObservation, ...] = ()
    selected_providers: tuple[ProviderSelection, ...] = ()
    chain_coverage: ChainCoverage = field(default_factory=ChainCoverage)
    catalyst_state: CatalystState = field(default_factory=CatalystState)
    data_quality: DataQuality = field(default_factory=DataQuality)
    missing_components: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_tz_aware(self.timestamp, "CanonicalMarketSnapshot.timestamp")

    @property
    def is_live(self) -> bool:
        """Live only if every required component reports LIVE (spec §13.2)."""
        by_component = {obs.component: obs.status for obs in self.feed_observations}
        return all(
            by_component.get(component) == FeedStatus.LIVE
            for component in REQUIRED_FEED_COMPONENTS
        )
