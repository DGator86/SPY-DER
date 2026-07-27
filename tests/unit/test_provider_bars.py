"""Shared 1-minute bar normalization.

These are the invariants every indicator in `spy_der.features` assumes without
re-checking: ascending order, no duplicate minutes, bounded length, and only
finite positive prices. A vendor breaking any of them produces wrong numbers
rather than an error, which is why they are asserted here rather than trusted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spy_der.contracts.market import Bar
from spy_der.market_data.providers.bars import (
    DEFAULT_LOOKBACK_MINUTES,
    bar_from_ohlcv,
    lookback_window,
    normalize_bars,
)

TS = datetime(2026, 7, 24, 17, 30, tzinfo=UTC)


def _bar(minute_offset: int, *, close: str = "600") -> Bar:
    return Bar(
        timestamp=TS + timedelta(minutes=minute_offset),
        open=Decimal("600"),
        high=Decimal("601"),
        low=Decimal("599"),
        close=Decimal(close),
        volume=100,
    )


# ------------------------------------------------------------------ window ----
def test_window_ends_at_the_requested_timestamp() -> None:
    start, end = lookback_window(TS, 60)
    assert end == TS
    assert start < end


def test_window_is_padded_so_the_first_bar_of_interest_is_not_clipped() -> None:
    start, _ = lookback_window(TS, 60)
    assert (TS - start) == timedelta(minutes=120)


def test_window_is_utc_regardless_of_the_input_zone() -> None:
    from zoneinfo import ZoneInfo

    start, end = lookback_window(TS.astimezone(ZoneInfo("America/New_York")), 30)
    assert start.utcoffset() == timedelta(0)
    assert end.utcoffset() == timedelta(0)


def test_a_nonpositive_lookback_still_produces_a_usable_window() -> None:
    start, end = lookback_window(TS, 0)
    assert start < end


# -------------------------------------------------------------- bar mapping ----
def test_a_complete_row_maps_to_a_bar() -> None:
    bar = bar_from_ohlcv(TS, 1.0, 2.0, 0.5, 1.5, 300)
    assert bar is not None
    assert bar.close == Decimal("1.5")
    assert bar.volume == 300
    assert isinstance(bar.close, Decimal)


def test_absent_volume_is_zero_not_a_rejection() -> None:
    """A quiet minute is real data; a missing price is not."""
    bar = bar_from_ohlcv(TS, 1.0, 2.0, 0.5, 1.5, None)
    assert bar is not None
    assert bar.volume == 0


def test_fractional_volume_is_truncated() -> None:
    bar = bar_from_ohlcv(TS, 1.0, 2.0, 0.5, 1.5, 300.7)
    assert bar is not None
    assert bar.volume == 300


@pytest.mark.parametrize(
    "row",
    [
        (None, 2.0, 0.5, 1.5, 10),
        (1.0, None, 0.5, 1.5, 10),
        (1.0, 2.0, 0.5, None, 10),
        (0, 2.0, 0.5, 1.5, 10),
        (1.0, 2.0, 0.5, -1.5, 10),
        (1.0, 2.0, 0.5, "not-a-price", 10),
        (float("nan"), 2.0, 0.5, 1.5, 10),
        (1.0, float("inf"), 0.5, 1.5, 10),
        (1.0, 2.0, 0.5, 1.5, -10),
    ],
    ids=[
        "no_open",
        "no_high",
        "no_close",
        "zero_open",
        "negative_close",
        "unparseable",
        "nan",
        "inf",
        "negative_volume",
    ],
)
def test_an_unusable_row_is_dropped_not_defaulted(row: tuple[object, ...]) -> None:
    assert bar_from_ohlcv(TS, *row) is None


# --------------------------------------------------------------- normalize ----
def test_bars_are_sorted_ascending() -> None:
    normalized = normalize_bars([_bar(2), _bar(0), _bar(1)])
    expected = [_bar(0).timestamp, _bar(1).timestamp, _bar(2).timestamp]
    assert [b.timestamp for b in normalized] == expected


def test_duplicate_minutes_collapse_to_the_later_copy() -> None:
    """Paginated feeds repeat the boundary bar; the second copy is the settled one."""
    normalized = normalize_bars([_bar(0, close="600"), _bar(0, close="601")])
    assert len(normalized) == 1
    assert normalized[0].close == Decimal("601")


def test_the_newest_window_is_kept_when_capped() -> None:
    normalized = normalize_bars([_bar(i) for i in range(10)], max_bars=3)
    assert len(normalized) == 3
    assert normalized[-1].timestamp == _bar(9).timestamp


def test_an_empty_series_normalizes_to_empty() -> None:
    assert normalize_bars([]) == ()


def test_the_default_lookback_covers_a_full_session() -> None:
    """Enough 1-minute history for the intraday timeframes to populate."""
    assert DEFAULT_LOOKBACK_MINUTES >= 390
