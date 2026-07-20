"""Feed freshness and provenance (master spec §13.2).

Turns raw provider timing into a typed ``FeedStatus`` and a ``FeedObservation``.
Missing or unusable data is reported explicitly; it never becomes a silent
neutral default (spec §7.5).
"""

from __future__ import annotations

import datetime as dt

from spy_der.contracts.common import require_tz_aware
from spy_der.contracts.market import (
    FeedComponent,
    FeedObservation,
    FeedStatus,
)

__all__ = ["age_seconds", "build_observation", "classify_status"]

# A feed observed within its freshness limit is LIVE; up to this multiple of the
# limit it is DELAYED; beyond it, STALE.
_DELAYED_LIMIT_MULTIPLE = 3.0


def age_seconds(observed_at: dt.datetime | None, received_at: dt.datetime) -> float | None:
    """Seconds between when data was observed and received, or ``None``."""
    require_tz_aware(received_at, "received_at")
    if observed_at is None:
        return None
    require_tz_aware(observed_at, "observed_at")
    return (received_at - observed_at).total_seconds()


def classify_status(
    age: float | None,
    freshness_limit_seconds: float,
    *,
    present: bool = True,
    valid: bool = True,
    fallback_used: bool = False,
) -> FeedStatus:
    """Classify a feed component from its age and validity flags.

    Precedence is fail-closed: missing and invalid win over freshness so a
    degraded feed can never masquerade as LIVE.
    """
    if not present:
        return FeedStatus.MISSING
    if not valid:
        return FeedStatus.INVALID
    if age is None:
        # Present and valid but no observation timestamp to age against.
        return FeedStatus.FALLBACK if fallback_used else FeedStatus.DELAYED
    if age < 0:
        return FeedStatus.INVALID
    if age > freshness_limit_seconds * _DELAYED_LIMIT_MULTIPLE:
        return FeedStatus.STALE
    if age > freshness_limit_seconds:
        return FeedStatus.DELAYED
    return FeedStatus.FALLBACK if fallback_used else FeedStatus.LIVE


def build_observation(
    component: FeedComponent,
    provider: str,
    received_at: dt.datetime,
    freshness_limit_seconds: float,
    *,
    observed_at: dt.datetime | None = None,
    present: bool = True,
    valid: bool = True,
    fallback_used: bool = False,
    attempt_order: int = 0,
    error_code: str | None = None,
    error_message_hash: str | None = None,
) -> FeedObservation:
    """Build a fully-classified :class:`FeedObservation`."""
    age = age_seconds(observed_at, received_at) if present else None
    status = classify_status(
        age,
        freshness_limit_seconds,
        present=present,
        valid=valid,
        fallback_used=fallback_used,
    )
    return FeedObservation(
        component=component,
        provider=provider,
        received_at=received_at,
        status=status,
        freshness_limit_seconds=freshness_limit_seconds,
        observed_at=observed_at,
        age_seconds=age,
        attempt_order=attempt_order,
        fallback_used=fallback_used,
        error_code=error_code,
        error_message_hash=error_message_hash,
    )
