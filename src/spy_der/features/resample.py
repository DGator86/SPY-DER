"""Deterministic timeframe resampling (master spec §20).

Turns the canonical 1-minute series into higher timeframes. System A used
``pandas.DataFrame.resample``; this is a direct implementation instead, for one
reason that matters to SPY-DER specifically: snapshot identity is content-
addressed and replay must reproduce it exactly, so bucket boundaries cannot
depend on a library default that has changed across pandas releases.

**Bucketing is anchored to the ET session open, not to midnight UTC.** A 30-
minute bucket therefore runs 09:30-10:00, and an hourly one 09:30-10:30, which
is what an intraday trader means by "the 30-minute bar". Anchoring to the epoch
instead — the naive choice — puts the first session bucket half inside the
overnight session, so the opening range lands in the same bar as the pre-market.
Daily bars group by ET *session date*, which is the only grouping that survives
daylight-saving transitions intact.

Aggregation is the standard OHLCV fold: first open, max high, min low, last
close, summed volume. Buckets with no bars are absent rather than
forward-filled — a gap in the vendor's data is not a flat minute, and inventing
one would show up downstream as a real observation of zero volatility.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from spy_der.contracts.market import Bar

__all__ = [
    "DAILY_MINUTES",
    "ET",
    "SESSION_OPEN_MINUTE",
    "TIMEFRAME_LABELS",
    "resample",
    "timeframe_label",
]

ET = ZoneInfo("America/New_York")

#: Minutes past ET midnight at which the regular session opens (09:30).
SESSION_OPEN_MINUTE = 9 * 60 + 30

#: The sentinel timeframe meaning "one session", handled by date rather than by
#: minute arithmetic so DST transitions cannot split or merge a trading day.
DAILY_MINUTES = 1440

#: Human labels, used as feature-name prefixes and in the dashboard.
TIMEFRAME_LABELS: dict[int, str] = {
    1: "1m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
    240: "4h",
    DAILY_MINUTES: "1d",
}


def timeframe_label(minutes: int) -> str:
    """Label for ``minutes``, falling back to a generated one."""
    if minutes in TIMEFRAME_LABELS:
        return TIMEFRAME_LABELS[minutes]
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _bucket_key(timestamp: datetime, minutes: int) -> tuple[object, ...]:
    """The bucket ``timestamp`` belongs to, as a sortable key."""
    local = timestamp.astimezone(ET)
    if minutes >= DAILY_MINUTES:
        return (local.date(),)
    # Offset from the session open so buckets align to 09:30 rather than to
    # midnight; the floor divide handles pre-market (negative offset) correctly
    # because Python floors toward negative infinity.
    minute_of_day = local.hour * 60 + local.minute
    index = (minute_of_day - SESSION_OPEN_MINUTE) // minutes
    return (local.date(), index)


def _bucket_start(first: datetime, minutes: int) -> datetime:
    """Representative timestamp for a bucket containing ``first``.

    The bucket's own start, so a resampled series is ordered identically whether
    or not the first minute of a bucket happened to be missing.
    """
    local = first.astimezone(ET)
    if minutes >= DAILY_MINUTES:
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    minute_of_day = local.hour * 60 + local.minute
    index = (minute_of_day - SESSION_OPEN_MINUTE) // minutes
    start_minute = SESSION_OPEN_MINUTE + index * minutes
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=start_minute)


def resample(bars: Sequence[Bar], minutes: int) -> tuple[Bar, ...]:
    """Aggregate ``bars`` into ``minutes``-wide buckets, ascending.

    ``bars`` is assumed sorted and deduplicated — the provider layer guarantees
    both (:mod:`spy_der.market_data.providers.bars`). Passing 1 returns the
    input unchanged rather than doing pointless work.
    """
    if minutes <= 1:
        return tuple(bars)
    if not bars:
        return ()

    grouped: dict[tuple[object, ...], list[Bar]] = {}
    for bar in bars:
        grouped.setdefault(_bucket_key(bar.timestamp, minutes), []).append(bar)

    out = [
        Bar(
            timestamp=_bucket_start(window[0].timestamp, minutes),
            open=window[0].open,
            high=max(b.high for b in window),
            low=min(b.low for b in window),
            close=window[-1].close,
            volume=sum(b.volume for b in window),
        )
        for window in grouped.values()
    ]
    # Insertion order already follows the input, but sorting makes the result
    # independent of that assumption rather than quietly dependent on it.
    out.sort(key=lambda b: b.timestamp)
    return tuple(out)
