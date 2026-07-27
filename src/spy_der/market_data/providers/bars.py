"""Shared 1-minute bar normalization for live providers (spec §13.1, §20).

Tradier and Massive both return 1-minute OHLCV, in different shapes and from
different endpoints, but the *normalization* they need afterwards is identical
and easy to get subtly wrong. It lives here so there is one implementation:

* **Sorted ascending by timestamp.** Every indicator in :mod:`spy_der.features`
  treats ``bars[-1]`` as "now" and diffs adjacent closes. A vendor page returned
  out of order silently inverts returns rather than failing.
* **Deduplicated by timestamp.** Paginated endpoints repeat boundary bars, and a
  duplicated minute double-counts in volume, VWAP and CVD.
* **Bounded.** ``bars_1m`` is part of the canonical snapshot's identity and is
  written to the recording on *every* tick, so an unbounded lookback multiplies
  recording size by the lookback. See :data:`DEFAULT_LOOKBACK_MINUTES`.
* **Finite and non-negative.** A ``null``/``NaN`` OHLC field or a negative volume
  is dropped, because :class:`~spy_der.contracts.market.Bar` is a value type that
  the rest of the system trusts.

The window is expressed in minutes of wall clock, not bars: the vendor decides
whether a quiet minute produces a bar at all, and neither adapter should be
filling gaps.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from spy_der.contracts.market import Bar

__all__ = [
    "DEFAULT_LOOKBACK_MINUTES",
    "MAX_BARS",
    "bar_from_ohlcv",
    "lookback_window",
    "normalize_bars",
]

#: One regular-hours session (09:30-16:00 ET) plus a small buffer.
#:
#: Enough history for the 1m/5m/15m/30m/1h timeframes to be fully populated and
#: for 4h to be partially populated. Longer timeframes (4h, 1d) report ``None``
#: until a deployment configures a larger lookback — the documented cold-start
#: behavior (spec §20), not a fabricated value. The default is deliberately one
#: session because these bars are re-recorded on every tick: at ~390 bars the
#: recording costs roughly 15 MB/session, where the 20-day lookback System A
#: used in memory would cost roughly 300 MB/session on disk.
DEFAULT_LOOKBACK_MINUTES = 420

#: Hard ceiling regardless of the configured lookback, so a vendor that ignores
#: the date range cannot blow up snapshot size or hash cost.
MAX_BARS = 8_000


def lookback_window(
    timestamp: datetime, lookback_minutes: int
) -> tuple[datetime, datetime]:
    """``(start, end)`` in UTC for a bar request ending at ``timestamp``.

    The start is padded by an extra hour: vendors align windows to their own bar
    boundaries, and a window that starts exactly on the first bar of interest
    routinely comes back one bar short.
    """
    end = timestamp.astimezone(UTC)
    start = end - timedelta(minutes=max(lookback_minutes, 1) + 60)
    return start, end


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    # NaN and +/-inf survive Decimal(str(...)) and would poison every downstream
    # indicator; Decimal comparisons on NaN are false, so test explicitly.
    if not parsed.is_finite():
        return None
    return parsed


def bar_from_ohlcv(
    timestamp: datetime,
    open_: object,
    high: object,
    low: object,
    close: object,
    volume: object,
) -> Bar | None:
    """Build a :class:`Bar`, or ``None`` when any field is unusable."""
    open_d = _decimal(open_)
    high_d = _decimal(high)
    low_d = _decimal(low)
    close_d = _decimal(close)
    if open_d is None or high_d is None or low_d is None or close_d is None:
        return None
    if open_d <= 0 or high_d <= 0 or low_d <= 0 or close_d <= 0:
        return None

    volume_decimal = Decimal(0) if volume is None else _decimal(volume)
    if volume_decimal is None or volume_decimal < 0:
        return None
    # Vendors report volume as a float; truncating matches "shares traded".
    volume_int = int(volume_decimal)

    return Bar(
        timestamp=timestamp,
        open=open_d,
        high=high_d,
        low=low_d,
        close=close_d,
        volume=volume_int,
    )


def normalize_bars(bars: Iterable[Bar], *, max_bars: int = MAX_BARS) -> tuple[Bar, ...]:
    """Sort ascending, drop duplicate timestamps, and keep the newest window.

    When a timestamp repeats, the later occurrence wins: paginated feeds emit the
    boundary bar again on the next page, and the second copy is the settled one.
    """
    by_timestamp: dict[datetime, Bar] = {}
    for bar in bars:
        by_timestamp[bar.timestamp] = bar
    ordered = [by_timestamp[key] for key in sorted(by_timestamp)]
    if max_bars > 0 and len(ordered) > max_bars:
        ordered = ordered[-max_bars:]
    return tuple(ordered)
