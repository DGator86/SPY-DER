"""Multi-timeframe feature matrix (master spec §20).

Full port of System A's ``resample.compute_tf_features`` across the whole
timeframe term structure (DGator86/0DTE @ 2186213). For each timeframe the
1-minute series is resampled (:mod:`spy_der.features.resample`) and the native
indicator set is computed on it (:mod:`spy_der.features.indicators`).

"Native" is the load-bearing word. Every field here is genuinely recomputed from
bars at that resolution, so 1-minute RSI and 4-hour RSI are different
measurements rather than one number copied across columns. Point-in-time state
that has no per-timeframe resolution — net GEX, walls, RND skew — is deliberately
*not* in this matrix; it belongs to the structural state, and broadcasting it
across timeframe columns would dress a single observation up as seven.

Cold start is explicit: a timeframe with too little history reports ``None`` per
field rather than a neutral default (spec §7.5, §20). This is not a degenerate
case but the normal one — at the open, the 4-hour and daily columns are
legitimately unknown, and a gate reading 0 there instead of "unknown" would
trade a fabricated observation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields

import numpy as np

from spy_der.contracts.market import Bar
from spy_der.features.indicators import (
    adx_di,
    bb_compression,
    channel_features,
    cvd_slope,
    ema_slope,
    r_squared,
    range_position,
    realized_vol_rank,
    rsi,
    rv_expansion,
    signed_volume_proxy,
    vwap_roll,
)
from spy_der.features.resample import DAILY_MINUTES, resample, timeframe_label

__all__ = [
    "DEFAULT_TIMEFRAMES",
    "NATIVE_FIELDS",
    "TimeframeFeatures",
    "compute_mtf",
    "mtf_feature_map",
]

#: The full term structure, matching System A's `TIMEFRAMES`.
DEFAULT_TIMEFRAMES: tuple[int, ...] = (1, 5, 15, 30, 60, 240, DAILY_MINUTES)


@dataclass(frozen=True, slots=True)
class TimeframeFeatures:
    """Native indicator state for one timeframe.

    Every indicator field is ``float | None``; ``None`` means "this timeframe
    does not have enough history yet", never "zero".
    """

    timeframe_minutes: int
    n_bars: int

    # -- price geometry
    last_return: float | None = None
    dist_to_vwap: float | None = None
    vwap_slope: float | None = None
    range_position: float | None = None

    # -- trend
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    di_spread: float | None = None
    ema_slope: float | None = None
    rsi: float | None = None
    bb_compression: float | None = None
    trend_cleanliness: float | None = None

    # -- volatility
    realized_vol: float | None = None
    rv_expansion: float | None = None

    # -- order flow
    cvd_persistence: float | None = None

    # -- volatility channels
    bb_width: float | None = None
    bb_position: float | None = None
    bb_squeeze: float | None = None
    bb_expansion: float | None = None
    keltner_width: float | None = None
    keltner_position: float | None = None
    keltner_trend_strength: float | None = None
    donchian_width: float | None = None
    donchian_position: float | None = None
    donchian_breakout_up: float | None = None
    donchian_breakout_down: float | None = None

    @property
    def label(self) -> str:
        return timeframe_label(self.timeframe_minutes)


#: Indicator field names, in declaration order (excludes the two identity fields).
NATIVE_FIELDS: tuple[str, ...] = tuple(
    f.name
    for f in fields(TimeframeFeatures)
    if f.name not in {"timeframe_minutes", "n_bars"}
)


def _features(minutes: int, bars: Sequence[Bar]) -> TimeframeFeatures:
    n = len(bars)
    if n == 0:
        return TimeframeFeatures(timeframe_minutes=minutes, n_bars=0)

    high = np.asarray([float(b.high) for b in bars], dtype=float)
    low = np.asarray([float(b.low) for b in bars], dtype=float)
    close = np.asarray([float(b.close) for b in bars], dtype=float)
    volume = np.asarray([float(b.volume) for b in bars], dtype=float)

    last_return: float | None = None
    if n >= 2 and close[-2] != 0.0:
        last_return = float(close[-1] / close[-2] - 1.0)

    adx, plus_di, minus_di = adx_di(high, low, close)
    distance, slope = vwap_roll(close, volume)
    channels = channel_features(high, low, close)

    return TimeframeFeatures(
        timeframe_minutes=minutes,
        n_bars=n,
        last_return=last_return,
        dist_to_vwap=distance,
        vwap_slope=slope,
        range_position=range_position(high, low, close),
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        di_spread=(plus_di - minus_di) if plus_di is not None and minus_di is not None else None,
        ema_slope=ema_slope(close),
        rsi=rsi(close),
        bb_compression=bb_compression(close),
        trend_cleanliness=r_squared(close),
        realized_vol=realized_vol_rank(close),
        rv_expansion=rv_expansion(close),
        cvd_persistence=cvd_slope(
            signed_volume_proxy(high, low, close, volume), volume
        ),
        **channels,
    )


def compute_mtf(
    bars: Sequence[Bar],
    timeframes: Sequence[int] = DEFAULT_TIMEFRAMES,
) -> tuple[TimeframeFeatures, ...]:
    """Resample ``bars`` to each timeframe and compute its native indicators."""
    return tuple(_features(tf, resample(bars, tf)) for tf in timeframes)


def mtf_feature_map(
    features: Sequence[TimeframeFeatures],
) -> dict[str, float]:
    """Flatten to ``{"<label>.<field>": value}``, dropping unknowns.

    Dropping rather than zero-filling is the same cold-start rule the dataclass
    follows: a consumer sees a missing key and knows the timeframe is not warm,
    where a zero would be indistinguishable from a real reading.
    """
    out: dict[str, float] = {}
    for row in features:
        for name in NATIVE_FIELDS:
            value = getattr(row, name)
            if value is not None:
                out[f"{row.label}.{name}"] = float(value)
    return out
