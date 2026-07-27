"""Multi-timeframe matrix: resampling, indicators, and the cold-start contract.

The indicators are ported from System A with their periods and smoothing intact,
because the gate thresholds that consume them were calibrated against those exact
definitions. These tests pin the properties that would silently invalidate those
thresholds if they drifted: Wilder smoothing, session-anchored buckets, and
`None` rather than a neutral default when a timeframe is not warm.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from spy_der.contracts.market import Bar
from spy_der.features.indicators import (
    adx_di,
    bb_compression,
    channel_features,
    cvd_slope,
    ema_slope,
    percentile_rank,
    r_squared,
    range_position,
    realized_vol_rank,
    rsi,
    rv_expansion,
    signed_volume_proxy,
    vwap_roll,
    wilder_rma,
)
from spy_der.features.mtf import (
    DEFAULT_TIMEFRAMES,
    NATIVE_FIELDS,
    compute_mtf,
    mtf_feature_map,
)
from spy_der.features.resample import ET, resample, timeframe_label

OPEN = datetime(2026, 1, 5, 9, 30, tzinfo=ET)


def _bar(offset: int, *, o: float, h: float, low: float, c: float, v: int = 1000) -> Bar:
    return Bar(
        timestamp=OPEN + timedelta(minutes=offset),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=v,
    )


def _series(closes: list[float], *, spread: float = 0.25) -> tuple[Bar, ...]:
    """1-minute bars with a real high/low range around each close."""
    return tuple(
        _bar(i, o=c, h=c + spread, low=c - spread, c=c) for i, c in enumerate(closes)
    )


def _trend(n: int = 300, step: float = 0.05) -> tuple[Bar, ...]:
    return _series([500.0 + i * step for i in range(n)])


def _noise(n: int = 300, seed: int = 11) -> tuple[Bar, ...]:
    rng = np.random.default_rng(seed)
    walk = 500.0 + np.cumsum(rng.normal(0.0, 0.05, n))
    return _series([float(x) for x in walk])


# --------------------------------------------------------------------------- #
# Resampling                                                                  #
# --------------------------------------------------------------------------- #
def test_ohlcv_is_folded_correctly() -> None:
    bars = (
        _bar(0, o=100, h=105, low=99, c=101, v=10),
        _bar(1, o=101, h=103, low=95, c=102, v=20),
        _bar(2, o=102, h=104, low=100, c=103, v=30),
    )
    (folded,) = resample(bars, 5)
    assert folded.open == Decimal("100")
    assert folded.high == Decimal("105")
    assert folded.low == Decimal("95")
    assert folded.close == Decimal("103")
    assert folded.volume == 60


def test_buckets_are_anchored_to_the_session_open() -> None:
    """09:30-10:00, not 09:00-09:30 — the opening range is its own bar."""
    bars = _series([500.0 + i for i in range(60)])  # 09:30 .. 10:29
    folded = resample(bars, 30)
    starts = [b.timestamp.astimezone(ET).strftime("%H:%M") for b in folded]
    assert starts == ["09:30", "10:00"]


def test_premarket_bars_land_in_an_earlier_bucket_not_the_opening_one() -> None:
    """Epoch-anchored bucketing would merge pre-market into the opening bar."""
    pre = Bar(
        timestamp=OPEN - timedelta(minutes=20),
        open=Decimal("499"), high=Decimal("499"),
        low=Decimal("499"), close=Decimal("499"), volume=5,
    )
    folded = resample((pre, *_series([500.0, 500.5])), 30)
    assert len(folded) == 2
    assert folded[0].close == Decimal("499")


def test_daily_bars_group_by_session_date() -> None:
    day_one = _series([500.0 + i for i in range(5)])
    day_two = tuple(
        Bar(
            timestamp=b.timestamp + timedelta(days=1),
            open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
        )
        for b in _series([510.0 + i for i in range(5)])
    )
    folded = resample(day_one + day_two, 1440)
    assert len(folded) == 2
    assert folded[0].close == Decimal("504")
    assert folded[1].close == Decimal("514")


def test_gaps_are_absent_rather_than_forward_filled() -> None:
    """An invented flat bar reads downstream as a real observation of no move."""
    bars = (_bar(0, o=100, h=100, low=100, c=100), _bar(120, o=101, h=101, low=101, c=101))
    folded = resample(bars, 30)
    assert len(folded) == 2  # not 5


def test_resampling_to_one_minute_is_the_identity() -> None:
    bars = _series([500.0, 501.0])
    assert resample(bars, 1) == bars


def test_resampling_an_empty_series_is_empty() -> None:
    assert resample((), 15) == ()


def test_output_is_ordered_ascending() -> None:
    folded = resample(_series([500.0 + i for i in range(120)]), 15)
    stamps = [b.timestamp for b in folded]
    assert stamps == sorted(stamps)


@pytest.mark.parametrize(
    ("minutes", "label"),
    [(1, "1m"), (5, "5m"), (30, "30m"), (60, "1h"), (240, "4h"), (1440, "1d")],
)
def test_timeframe_labels(minutes: int, label: str) -> None:
    assert timeframe_label(minutes) == label


# --------------------------------------------------------------------------- #
# Indicator kernels                                                           #
# --------------------------------------------------------------------------- #
def test_wilder_rma_seeds_with_a_simple_mean_then_smooths() -> None:
    """Wilder's recursion, not an SMA — ADX thresholds depend on the difference."""
    values = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
    out = wilder_rma(values, 3)
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2] == pytest.approx(2.0)  # mean of the first 3
    assert out[3] == pytest.approx((2.0 * 2 + 4.0) / 3)


def test_wilder_rma_is_all_nan_below_the_period() -> None:
    assert np.isnan(wilder_rma(np.array([1.0, 2.0]), 5)).all()


def test_rsi_is_100_on_a_monotonic_advance() -> None:
    closes = np.array([500.0 + i for i in range(40)], dtype=float)
    assert rsi(closes) == pytest.approx(100.0)


def test_rsi_is_0_on_a_monotonic_decline() -> None:
    closes = np.array([500.0 - i for i in range(40)], dtype=float)
    assert rsi(closes) == pytest.approx(0.0)


def test_rsi_of_a_flat_series_is_neutral_not_unknown() -> None:
    """Zero gain and zero loss is a real, readable state: no momentum."""
    assert rsi(np.full(40, 500.0)) == pytest.approx(50.0)


def test_rsi_needs_history() -> None:
    assert rsi(np.array([500.0, 501.0])) is None


def test_adx_rises_on_a_clean_trend_and_di_favours_the_direction() -> None:
    bars = _trend(120)
    high = np.array([float(b.high) for b in bars])
    low = np.array([float(b.low) for b in bars])
    close = np.array([float(b.close) for b in bars])
    adx, plus_di, minus_di = adx_di(high, low, close)
    assert adx is not None and adx > 25.0
    assert plus_di is not None and minus_di is not None
    assert plus_di > minus_di


def test_adx_needs_two_smoothing_periods() -> None:
    n = 20  # < 2 * 14
    flat = np.full(n, 500.0)
    assert adx_di(flat, flat, flat) == (None, None, None)


def test_ema_slope_is_signed_and_scaled_per_bar() -> None:
    up = np.array([500.0 + i * 0.5 for i in range(60)], dtype=float)
    down = up[::-1].copy()
    assert (ema_slope(up) or 0) > 0
    assert (ema_slope(down) or 0) < 0


def test_ema_slope_needs_period_plus_lookback() -> None:
    assert ema_slope(np.array([500.0] * 20)) is None


def test_trend_cleanliness_separates_a_line_from_a_walk() -> None:
    line = np.array([500.0 + i for i in range(40)], dtype=float)
    assert r_squared(line) == pytest.approx(1.0)
    walk = np.array([float(x) for x in np.random.default_rng(3).normal(500, 1, 40)])
    assert (r_squared(walk) or 1.0) < 0.9


def test_range_position_is_0_at_the_low_and_1_at_the_high() -> None:
    high = np.array([102.0, 103.0, 104.0])
    low = np.array([98.0, 99.0, 100.0])
    assert range_position(high, low, np.array([100.0, 100.0, 104.0])) == pytest.approx(1.0)
    assert range_position(high, low, np.array([100.0, 100.0, 98.0])) == pytest.approx(0.0)


def test_range_position_is_unknown_in_a_zero_range() -> None:
    flat = np.full(5, 100.0)
    assert range_position(flat, flat, flat) is None


def test_vwap_distance_is_positive_above_the_average() -> None:
    close = np.array([100.0] * 19 + [110.0], dtype=float)
    volume = np.full(20, 1000.0)
    distance, _ = vwap_roll(close, volume)
    assert distance is not None and distance > 0


def test_vwap_is_unknown_without_volume() -> None:
    assert vwap_roll(np.array([100.0, 101.0]), np.zeros(2)) == (None, None)


def test_realized_vol_rank_is_a_bounded_percentile() -> None:
    steps = np.random.default_rng(5).normal(0, 0.1, 200)
    rank = realized_vol_rank(np.asarray(500 + np.cumsum(steps), dtype=float))
    assert rank is not None and 0.0 <= rank <= 1.0


def test_rv_expansion_is_positive_when_volatility_picks_up() -> None:
    rng = np.random.default_rng(7)
    calm = 500 + np.cumsum(rng.normal(0, 0.01, 60))
    burst = calm[-1] + np.cumsum(rng.normal(0, 0.50, 10))
    series = np.concatenate([calm, burst])
    assert (rv_expansion(series) or -1.0) > 0


def test_percentile_rank_counts_the_window_including_the_current_value() -> None:
    """3 of the 4 samples sit below the last one, so a new high ranks 0.75.

    The current value is part of its own comparison window (System A's
    convention), which caps the rank below 1.0 and keeps it continuous as the
    window fills — worth pinning, since a 1.0-topping variant would move every
    downstream threshold calibrated against these ranks.
    """
    assert percentile_rank(np.array([1.0, 2.0, 3.0, 10.0])) == pytest.approx(0.75)


def test_percentile_rank_ignores_nan() -> None:
    """Warm-up NaNs must not count as samples below the current value."""
    assert percentile_rank(np.array([1.0, np.nan, 2.0, 3.0])) == pytest.approx(2 / 3)


def test_signed_volume_proxy_signs_by_close_location() -> None:
    high = np.array([101.0, 101.0, 101.0])
    low = np.array([99.0, 99.0, 99.0])
    close = np.array([101.0, 99.0, 100.0])  # top, bottom, middle
    volume = np.array([100.0, 100.0, 100.0])
    signed = signed_volume_proxy(high, low, close, volume)
    assert signed[0] == pytest.approx(100.0)
    assert signed[1] == pytest.approx(-100.0)
    assert signed[2] == pytest.approx(0.0)


def test_signed_volume_proxy_treats_a_zero_range_bar_as_neutral() -> None:
    flat = np.full(3, 100.0)
    assert signed_volume_proxy(flat, flat, flat, np.full(3, 50.0)).tolist() == [0.0, 0.0, 0.0]


def test_cvd_slope_is_positive_under_sustained_buying() -> None:
    volume = np.full(20, 100.0)
    buying = np.full(20, 80.0)
    assert (cvd_slope(buying, volume) or 0) > 0
    assert (cvd_slope(-buying, volume) or 0) < 0


def test_bb_compression_is_below_one_when_the_range_tightens() -> None:
    rng = np.random.default_rng(13)
    wide = 500 + np.cumsum(rng.normal(0, 0.5, 60))
    tight = wide[-1] + np.cumsum(rng.normal(0, 0.01, 40))
    assert (bb_compression(np.concatenate([wide, tight])) or 2.0) < 1.0


# --------------------------------------------------------------------------- #
# Volatility channels                                                         #
# --------------------------------------------------------------------------- #
def test_channel_features_always_return_every_key() -> None:
    from spy_der.features.indicators import CHANNEL_KEYS

    short = np.array([100.0, 101.0])
    out = channel_features(short, short, short)
    assert set(out) == set(CHANNEL_KEYS)
    assert all(v is None for v in out.values())  # honest cold start


def test_channel_positions_are_bounded_in_normal_conditions() -> None:
    bars = _noise(200)
    high = np.array([float(b.high) for b in bars])
    low = np.array([float(b.low) for b in bars])
    close = np.array([float(b.close) for b in bars])
    out = channel_features(high, low, close)
    for key in ("bb_position", "keltner_position", "donchian_position"):
        assert out[key] is not None
        assert -0.5 <= (out[key] or 0.0) <= 1.5


def test_squeeze_is_graded_between_zero_and_one() -> None:
    bars = _noise(200)
    out = channel_features(
        np.array([float(b.high) for b in bars]),
        np.array([float(b.low) for b in bars]),
        np.array([float(b.close) for b in bars]),
    )
    assert out["bb_squeeze"] is not None
    assert 0.0 <= (out["bb_squeeze"] or 0.0) <= 1.0


def test_a_breakout_registers_against_the_prior_channel() -> None:
    """Measuring against the current channel would absorb the new extreme."""
    closes = [500.0] * 40 + [520.0]
    bars = _series(closes)
    out = channel_features(
        np.array([float(b.high) for b in bars]),
        np.array([float(b.low) for b in bars]),
        np.array([float(b.close) for b in bars]),
    )
    assert (out["donchian_breakout_up"] or 0.0) > 0
    assert out["donchian_breakout_down"] == pytest.approx(0.0)


def test_no_breakout_reads_zero_not_negative() -> None:
    bars = _noise(120)
    out = channel_features(
        np.array([float(b.high) for b in bars]),
        np.array([float(b.low) for b in bars]),
        np.array([float(b.close) for b in bars]),
    )
    assert (out["donchian_breakout_up"] or 0.0) >= 0.0
    assert (out["donchian_breakout_down"] or 0.0) >= 0.0


# --------------------------------------------------------------------------- #
# The matrix                                                                  #
# --------------------------------------------------------------------------- #
def test_the_full_term_structure_is_computed() -> None:
    rows = compute_mtf(_trend(600))
    assert tuple(r.timeframe_minutes for r in rows) == DEFAULT_TIMEFRAMES
    assert [r.label for r in rows] == ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def test_indicators_are_native_per_timeframe_not_broadcast() -> None:
    """The point of the matrix: each column is its own measurement."""
    rows = {r.label: r for r in compute_mtf(_noise(600))}
    slopes = [rows[tf].ema_slope for tf in ("1m", "5m", "15m") if rows[tf].ema_slope]
    assert len(set(slopes)) == len(slopes) > 1


def test_a_warm_timeframe_populates_the_whole_native_set() -> None:
    one_minute = compute_mtf(_noise(600), timeframes=(1,))[0]
    missing = [f for f in NATIVE_FIELDS if getattr(one_minute, f) is None]
    assert missing == []


def test_a_cold_timeframe_reports_none_rather_than_zero() -> None:
    """At the open the daily column is unknown; a 0 would be a fabricated read."""
    rows = {r.label: r for r in compute_mtf(_trend(30))}
    daily = rows["1d"]
    assert daily.n_bars == 1
    assert daily.rsi is None
    assert daily.adx is None
    assert daily.bb_width is None


def test_an_empty_series_yields_empty_rows_not_an_error() -> None:
    rows = compute_mtf(())
    assert all(r.n_bars == 0 for r in rows)
    assert all(getattr(r, f) is None for r in rows for f in NATIVE_FIELDS)


def test_di_spread_is_the_signed_difference() -> None:
    row = compute_mtf(_trend(300), timeframes=(1,))[0]
    assert row.plus_di is not None and row.minus_di is not None
    assert row.di_spread == pytest.approx(row.plus_di - row.minus_di)


def test_feature_map_is_prefixed_by_timeframe_and_drops_unknowns() -> None:
    flat = mtf_feature_map(compute_mtf(_trend(600)))
    assert "1m.rsi" in flat
    assert "4h.rsi" in flat or "4h.last_return" in flat
    assert all(isinstance(v, float) for v in flat.values())
    assert not any(math.isnan(v) for v in flat.values())


def test_feature_map_omits_cold_timeframes_instead_of_zero_filling() -> None:
    flat = mtf_feature_map(compute_mtf(_trend(30)))
    assert "1d.rsi" not in flat
    assert "1m.last_return" in flat
