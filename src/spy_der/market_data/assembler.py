"""Canonical snapshot assembler (master spec §13.5, §15).

Combines normalized provider outputs into an immutable
:class:`CanonicalMarketSnapshot` with a deterministic, content-addressed
``snapshot_id`` and ``content_hash``. Identity depends only on the snapshot
content plus schema and normalization versions (spec §13.5) — never on wall
clock, host, or insertion order — so recording and replay reproduce the same
identity (spec §15).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from spy_der.contracts.common import (
    SCHEMA_VERSION,
    content_hash,
    deterministic_id,
    require_tz_aware,
)
from spy_der.contracts.market import (
    REQUIRED_FEED_COMPONENTS,
    Bar,
    CanonicalMarketSnapshot,
    CatalystState,
    ChainCoverage,
    DataQuality,
    FeedObservation,
    FeedStatus,
    OptionQuote,
    OptionType,
    ProviderSelection,
)
from spy_der.market_data.calendar import MarketCalendar

__all__ = ["CanonicalSnapshotAssembler"]

_NORMALIZATION_VERSION = "1.0.0"


def _chain_coverage(chain: tuple[OptionQuote, ...]) -> ChainCoverage:
    if not chain:
        return ChainCoverage()
    strikes = {q.contract.strike for q in chain}
    return ChainCoverage(
        contracts_total=len(chain),
        strikes_total=len(strikes),
        min_strike=min(strikes),
        max_strike=max(strikes),
        has_calls=any(q.contract.option_type is OptionType.CALL for q in chain),
        has_puts=any(q.contract.option_type is OptionType.PUT for q in chain),
    )


def _missing_components(observations: tuple[FeedObservation, ...]) -> tuple[str, ...]:
    seen = {
        obs.component: obs.status
        for obs in observations
    }
    missing: list[str] = []
    for component in REQUIRED_FEED_COMPONENTS:
        status = seen.get(component)
        if status is None or status in (FeedStatus.MISSING, FeedStatus.INVALID):
            missing.append(component.value)
    return tuple(missing)


def _data_quality(
    missing: tuple[str, ...],
    observations: tuple[FeedObservation, ...],
) -> DataQuality:
    flags: list[str] = [f"missing:{name}" for name in missing]
    degraded = [
        f"{obs.component.value}:{obs.status.value}"
        for obs in observations
        if obs.status in (FeedStatus.STALE, FeedStatus.DELAYED)
    ]
    flags.extend(degraded)
    penalty = min(1.0, 0.5 * len(missing) + 0.1 * len(degraded))
    return DataQuality(is_healthy=not missing, penalty=penalty, flags=tuple(flags))


class CanonicalSnapshotAssembler:
    """Assemble deterministic canonical snapshots."""

    def __init__(self, calendar: MarketCalendar | None = None) -> None:
        self.calendar = calendar or MarketCalendar()

    def assemble(
        self,
        *,
        timestamp: dt.datetime,
        underlying_symbol: str,
        underlying_price: Decimal,
        bars_1m: tuple[Bar, ...] = (),
        option_chain: tuple[OptionQuote, ...] = (),
        feed_observations: tuple[FeedObservation, ...] = (),
        selected_providers: tuple[ProviderSelection, ...] = (),
        underlying_bid: Decimal | None = None,
        underlying_ask: Decimal | None = None,
        catalyst_state: CatalystState | None = None,
    ) -> CanonicalMarketSnapshot:
        require_tz_aware(timestamp, "timestamp")
        session_date = self.calendar.session_date(timestamp)
        session_status = self.calendar.session_status(timestamp)
        minutes_from_open = self.calendar.minutes_from_open(timestamp)
        minutes_to_close = self.calendar.minutes_to_close(timestamp)
        coverage = _chain_coverage(option_chain)
        missing = _missing_components(feed_observations)
        quality = _data_quality(missing, feed_observations)
        catalyst = catalyst_state or CatalystState()

        # Identity payload: everything that defines the snapshot except the
        # assigned id/hash. Deterministic across processes and hosts.
        identity = {
            "schema_version": SCHEMA_VERSION,
            "normalization_version": _NORMALIZATION_VERSION,
            "timestamp": timestamp,
            "session_date": session_date,
            "session_status": session_status,
            "underlying_symbol": underlying_symbol,
            "underlying_price": underlying_price,
            "underlying_bid": underlying_bid,
            "underlying_ask": underlying_ask,
            "minutes_from_open": minutes_from_open,
            "minutes_to_close": minutes_to_close,
            "bars_1m": bars_1m,
            "option_chain": option_chain,
            "feed_observations": feed_observations,
            "selected_providers": selected_providers,
            "chain_coverage": coverage,
            "catalyst_state": catalyst,
            "data_quality": quality,
            "missing_components": missing,
        }
        digest = content_hash(identity)
        snapshot_id = deterministic_id("snap", digest)

        return CanonicalMarketSnapshot(
            snapshot_id=snapshot_id,
            content_hash=digest,
            timestamp=timestamp,
            session_date=session_date,
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            session_status=session_status,
            normalization_version=_NORMALIZATION_VERSION,
            underlying_bid=underlying_bid,
            underlying_ask=underlying_ask,
            minutes_from_open=minutes_from_open,
            minutes_to_close=minutes_to_close,
            bars_1m=bars_1m,
            option_chain=option_chain,
            feed_observations=feed_observations,
            selected_providers=selected_providers,
            chain_coverage=coverage,
            catalyst_state=catalyst,
            data_quality=quality,
            missing_components=missing,
        )
