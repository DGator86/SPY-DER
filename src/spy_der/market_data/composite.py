"""Composite feed with ordered failover (master spec §13, §63 Phase 2).

Migrated behavior from System A ``composite_feed.CompositeFeed`` (0DTE @
de4a6e7): try each member provider in priority order, first to produce a tick
wins; an optional settlement provider backstops settlement only. The winner and
whether a fallback was used are recorded as ``ProviderSelection`` /
``FeedObservation`` provenance, then assembled into a canonical snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from spy_der.contracts.market import (
    CanonicalMarketSnapshot,
    FeedComponent,
    FeedObservation,
    ProviderSelection,
)
from spy_der.market_data.assembler import CanonicalSnapshotAssembler
from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.freshness import build_observation
from spy_der.market_data.providers.base import MarketDataProvider, RawTick

__all__ = ["CompositeFeed"]

_DEFAULT_FRESHNESS_SECONDS = 60.0
_CHAIN_COMPONENTS = (
    FeedComponent.SPOT,
    FeedComponent.BARS,
    FeedComponent.OPTION_CHAIN,
)


def _has_breadth(tick: RawTick) -> bool:
    return tick.breadth is not None and tick.breadth.is_observed


class CompositeFeed:
    """Ordered failover across providers, producing canonical snapshots."""

    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        settlement_provider: MarketDataProvider | None = None,
        calendar: MarketCalendar | None = None,
        freshness_limit_seconds: float = _DEFAULT_FRESHNESS_SECONDS,
    ) -> None:
        if not providers:
            msg = "CompositeFeed requires at least one provider"
            raise ValueError(msg)
        self._providers = tuple(providers)
        self._settlement_provider = settlement_provider
        self._assembler = CanonicalSnapshotAssembler(calendar)
        self._freshness = freshness_limit_seconds
        self._last_source: str | None = None

    @property
    def last_source(self) -> str | None:
        return self._last_source

    def _select(self, timestamp: datetime) -> tuple[RawTick, int] | None:
        for order, provider in enumerate(self._providers):
            tick = provider.fetch(timestamp)
            if tick is not None:
                return tick, order
        return None

    def snapshot(self, timestamp: datetime) -> CanonicalMarketSnapshot | None:
        selected = self._select(timestamp)
        if selected is None:
            self._last_source = None
            return None
        tick, order = selected
        self._last_source = tick.provider
        fallback_used = order > 0

        observations: list[FeedObservation] = []
        selections: list[ProviderSelection] = []
        for component in _CHAIN_COMPONENTS:
            if not self._component_present(component, tick):
                observations.append(
                    build_observation(
                        component,
                        provider=tick.provider,
                        received_at=timestamp,
                        freshness_limit_seconds=self._freshness,
                        present=False,
                    )
                )
                continue
            observations.append(
                build_observation(
                    component,
                    provider=tick.provider,
                    received_at=timestamp,
                    freshness_limit_seconds=self._freshness,
                    observed_at=tick.observed_at,
                    fallback_used=fallback_used,
                    attempt_order=order,
                )
            )
            selections.append(
                ProviderSelection(
                    component=component,
                    provider=tick.provider,
                    fallback_used=fallback_used,
                    attempt_order=order,
                )
            )

        # Breadth is optional, not required: a snapshot without it is degraded,
        # not unusable, so it is reported rather than folded into `is_live`.
        observations.append(
            build_observation(
                FeedComponent.BREADTH,
                provider=tick.provider,
                received_at=timestamp,
                freshness_limit_seconds=self._freshness,
                observed_at=tick.observed_at if _has_breadth(tick) else None,
                present=_has_breadth(tick),
            )
        )
        settle_tick = self._settlement_tick(timestamp)
        observations.append(
            self._settlement_observation(timestamp, selections, settle_tick)
        )

        # The settlement provider backstops the volatility surface too: Yahoo
        # publishes the real CBOE indices with no entitlement, where a brokerage
        # account often carries only the 30-day VIX. The primary still wins when
        # it has one — this fills a gap rather than overriding a reading.
        term_structure = tick.volatility_term_structure
        if term_structure is None and settle_tick is not None:
            term_structure = settle_tick.volatility_term_structure

        return self._assembler.assemble(
            timestamp=timestamp,
            underlying_symbol=tick.symbol,
            underlying_price=tick.underlying_price,
            underlying_bid=tick.underlying_bid,
            underlying_ask=tick.underlying_ask,
            bars_1m=tick.bars_1m,
            option_chain=tick.option_chain,
            feed_observations=tuple(observations),
            selected_providers=tuple(selections),
            provider_flags=tick.quality_flags,
            volatility_term_structure=term_structure,
            breadth=tick.breadth,
        )

    def _settlement_tick(self, timestamp: datetime) -> RawTick | None:
        """Poll the settlement source once per snapshot, or ``None`` if unset."""
        if self._settlement_provider is None:
            return None
        return self._settlement_provider.fetch(timestamp)

    @staticmethod
    def _component_present(component: FeedComponent, tick: RawTick) -> bool:
        """Whether ``tick`` actually carries data for ``component``.

        Bars are checked against the series rather than assumed: a provider that
        returns no bars must not let the snapshot report ``bars`` as LIVE, or
        ``is_live`` becomes a claim about which adapter ran instead of about what
        data arrived — and every history-dependent feature would then be silently
        absent from a snapshot advertising itself as complete.
        """
        if component is FeedComponent.OPTION_CHAIN:
            return tick.has_chain
        if component is FeedComponent.BARS:
            return bool(tick.bars_1m)
        return True

    def _settlement_observation(
        self,
        timestamp: datetime,
        selections: list[ProviderSelection],
        settle_tick: RawTick | None,
    ) -> FeedObservation:
        component = FeedComponent.SETTLEMENT
        if self._settlement_provider is None:
            return build_observation(
                component,
                provider="none",
                received_at=timestamp,
                freshness_limit_seconds=self._freshness,
                present=False,
            )
        if settle_tick is None:
            return build_observation(
                component,
                provider=self._settlement_provider.name,
                received_at=timestamp,
                freshness_limit_seconds=self._freshness,
                present=False,
            )
        # The settlement provider is settlement's dedicated source, not a
        # failover fallback, so it reports LIVE when fresh.
        selections.append(
            ProviderSelection(component=component, provider=settle_tick.provider)
        )
        return build_observation(
            component,
            provider=settle_tick.provider,
            received_at=timestamp,
            freshness_limit_seconds=self._freshness,
            observed_at=settle_tick.observed_at,
        )
