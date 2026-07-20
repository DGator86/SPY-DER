"""System A snapshot adapter (master spec §4.1, §13).

Bridges a System A (``DGator86/0DTE`` @ de4a6e7) ``prediction.canonical_snapshot``
``CanonicalSnapshot`` — as produced by its ``to_dict()`` — into System B's
:class:`CanonicalMarketSnapshot`. This is the adapter path required before any
parity comparison (spec §4.1): System B never imports System A code, it consumes
System A's own serialized output.

Missing required inputs fail closed with :class:`MissingInputError`; they are
never mapped to a silent neutral default (spec §5, §7.5). Naive timestamps are
rejected (spec §11).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from spy_der.contracts.common import MissingInputError, require_tz_aware
from spy_der.contracts.market import (
    CanonicalMarketSnapshot,
    FeedComponent,
    FeedObservation,
    FeedStatus,
)
from spy_der.market_data.assembler import CanonicalSnapshotAssembler
from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.freshness import build_observation

__all__ = ["SystemASnapshotAdapter"]

# System A records feed status per *source* keyed by component-like names in
# ``feed_statuses``; recognized keys map onto System B feed components.
_COMPONENT_ALIASES: dict[str, FeedComponent] = {
    "spot": FeedComponent.SPOT,
    "price": FeedComponent.SPOT,
    "bars": FeedComponent.BARS,
    "chain": FeedComponent.OPTION_CHAIN,
    "option_chain": FeedComponent.OPTION_CHAIN,
    "settlement": FeedComponent.SETTLEMENT,
}

_DEFAULT_FRESHNESS_SECONDS = 60.0


def _map_status(raw: Any) -> FeedStatus:
    """Map a System A feed status string onto System B's enum; unknown → INVALID."""
    if raw is None:
        return FeedStatus.MISSING
    name = str(raw).strip().upper()
    try:
        return FeedStatus(name)
    except ValueError:
        return FeedStatus.INVALID


def _require(source: Mapping[str, Any], key: str) -> Any:
    if key not in source or source[key] is None:
        raise MissingInputError(key)
    return source[key]


def _parse_ts(raw: Any) -> dt.datetime:
    if isinstance(raw, dt.datetime):
        return require_tz_aware(raw, "ts")
    parsed = dt.datetime.fromisoformat(str(raw))
    return require_tz_aware(parsed, "ts")


class SystemASnapshotAdapter:
    """Adapt System A ``CanonicalSnapshot.to_dict()`` output to System B."""

    def __init__(self, calendar: MarketCalendar | None = None) -> None:
        self.calendar = calendar or MarketCalendar()
        self.assembler = CanonicalSnapshotAssembler(self.calendar)

    def adapt(self, legacy: Mapping[str, Any]) -> CanonicalMarketSnapshot:
        symbol = str(_require(legacy, "symbol"))
        timestamp = _parse_ts(_require(legacy, "ts"))

        market = legacy.get("market") or {}
        spot_raw = market.get("spot") if isinstance(market, Mapping) else None
        if spot_raw is None:
            spot_raw = legacy.get("spot")
        if spot_raw is None:
            raise MissingInputError("spot")
        underlying_price = Decimal(str(spot_raw))

        underlying_bid = _opt_decimal(market, "bid")
        underlying_ask = _opt_decimal(market, "ask")

        observations = self._observations(timestamp, legacy)

        return self.assembler.assemble(
            timestamp=timestamp,
            underlying_symbol=symbol,
            underlying_price=underlying_price,
            underlying_bid=underlying_bid,
            underlying_ask=underlying_ask,
            feed_observations=observations,
        )

    def _observations(
        self,
        received_at: dt.datetime,
        legacy: Mapping[str, Any],
    ) -> tuple[FeedObservation, ...]:
        raw_statuses = legacy.get("feed_statuses") or {}
        ages = legacy.get("source_ages_seconds") or {}
        statuses: dict[FeedComponent, FeedStatus] = {}
        component_ages: dict[FeedComponent, float] = {}
        if isinstance(raw_statuses, Mapping):
            for key, value in raw_statuses.items():
                component = _COMPONENT_ALIASES.get(str(key).lower())
                if component is not None:
                    statuses[component] = _map_status(value)
        if isinstance(ages, Mapping):
            for key, value in ages.items():
                component = _COMPONENT_ALIASES.get(str(key).lower())
                if component is not None and value is not None:
                    component_ages[component] = float(value)

        observations: list[FeedObservation] = []
        for component in FeedComponent:
            if component not in statuses and component not in component_ages:
                continue
            observed_at = None
            age = component_ages.get(component)
            if age is not None:
                observed_at = received_at - dt.timedelta(seconds=age)
            status = statuses.get(component)
            if status is not None:
                # System A already classified this feed; honor its status
                # verbatim rather than re-deriving it (System A is the baseline).
                observations.append(
                    FeedObservation(
                        component=component,
                        provider="system_a",
                        received_at=received_at,
                        status=status,
                        freshness_limit_seconds=_DEFAULT_FRESHNESS_SECONDS,
                        observed_at=observed_at,
                        age_seconds=age,
                    )
                )
                continue
            observations.append(
                build_observation(
                    component,
                    provider="system_a",
                    received_at=received_at,
                    freshness_limit_seconds=_DEFAULT_FRESHNESS_SECONDS,
                    observed_at=observed_at,
                    present=True,
                    valid=True,
                )
            )
        return tuple(observations)


def _opt_decimal(source: Any, key: str) -> Decimal | None:
    if not isinstance(source, Mapping):
        return None
    value = source.get(key)
    return None if value is None else Decimal(str(value))
