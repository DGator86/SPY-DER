"""Technical-indicator kernels (master spec §20).

Ported from System A ``resample.py`` (DGator86/0DTE @ 2186213). The definitions
and periods are preserved deliberately — these are the inputs the gate scorer,
regime classifier and forecast stack were calibrated against, so a "cleaner"
RSI or a different ATR smoothing would silently invalidate every threshold that
came with them.

Three conventions run through the whole module:

* **Wilder smoothing, not simple averages.** ADX, DI, RSI and ATR all use
  Wilder's RMA. A simple moving average produces plausible-looking values that
  sit systematically differently against a 25/50 ADX threshold.
* **Too little history returns ``None``, never a default.** A zero ADX and an
  unknown ADX mean opposite things to a trend gate (spec §7.5, §20 cold start).
* **Last value only.** Callers need the state now, and returning whole series
  would force every caller to re-derive the alignment rules that differ per
  indicator (ATR/ADX are aligned to bars 1..n-1; the rest to 0..n-1).

The one input System A had that a canonical :class:`~spy_der.contracts.market.Bar`
does not is signed volume. It never came from a vendor there either — both
adapters fell back to the close-location proxy, which is what
:func:`signed_volume_proxy` implements, so nothing is lost in the port.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "ADX_PERIOD",
    "ATR_PERIOD",
    "BB_PERIOD",
    "CHANNEL_KEYS",
    "EMA_PERIOD",
    "RSI_PERIOD",
    "RV_WINDOW",
    "SLOPE_LOOKBACK",
    "adx_di",
    "atr_series",
    "bb_compression",
    "channel_features",
    "cvd_slope",
    "ema_series",
    "ema_slope",
    "percentile_rank",
    "r_squared",
    "range_position",
    "realized_vol_rank",
    "rsi",
    "rv_expansion",
    "signed_volume_proxy",
    "vwap_roll",
    "wilder_rma",
]

Floats = NDArray[np.float64]

# -- periods, in bars of the timeframe being measured (System A constants) ----
ADX_PERIOD = 14
RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_PERIOD = 20
BB_PERIOD = 20
SLOPE_LOOKBACK = 5
RV_WINDOW = 20
R2_WINDOW = 20
VWAP_WINDOW = 20
RV_RANK_WINDOW = 100

# -- volatility-channel parameters -------------------------------------------
BB_STDDEV = 2.0
KELTNER_PERIOD = 20
KELTNER_MULTIPLIER = 1.5
KELTNER_TREND_LOOKBACK = 10
DONCHIAN_PERIOD = 20
BB_EXPANSION_LOOKBACK = 5
CHANNEL_RANK_WINDOW = RV_RANK_WINDOW

#: TTM squeeze grading on ``bollinger_width / keltner_width``: no squeeze at or
#: above SQUEEZE_ON, full squeeze at or below SQUEEZE_FULL, linear between.
SQUEEZE_ON = 1.0
SQUEEZE_FULL = 0.6

#: Every key :func:`channel_features` returns, present even when ``None``.
CHANNEL_KEYS: tuple[str, ...] = (
    "bb_width",
    "bb_position",
    "bb_squeeze",
    "bb_expansion",
    "keltner_width",
    "keltner_position",
    "keltner_trend_strength",
    "donchian_width",
    "donchian_position",
    "donchian_breakout_up",
    "donchian_breakout_down",
)


def _finite(value: float) -> float | None:
    """``None`` unless ``value`` is a real number — NaN never leaves this module."""
    return float(value) if np.isfinite(value) else None


def _rolling(values: Floats, window: int) -> Floats:
    """Sliding windows of ``values``; shape ``(n - window + 1, window)``."""
    return np.lib.stride_tricks.sliding_window_view(values, window)


def _rolling_mean(values: Floats, window: int) -> Floats:
    """Trailing mean, NaN-padded to the input length (pandas ``rolling`` shape)."""
    out = np.full(len(values), np.nan)
    if len(values) >= window:
        out[window - 1 :] = _rolling(values, window).mean(axis=1)
    return out


def _rolling_std(values: Floats, window: int) -> Floats:
    """Trailing population standard deviation (``ddof=0``), NaN-padded."""
    out = np.full(len(values), np.nan)
    if len(values) >= window:
        out[window - 1 :] = _rolling(values, window).std(axis=1, ddof=0)
    return out


# --------------------------------------------------------------------------- #
# Smoothing primitives                                                        #
# --------------------------------------------------------------------------- #
def wilder_rma(values: Floats, period: int) -> Floats:
    """Wilder's running moving average, NaN until ``period`` samples exist."""
    out = np.full(len(values), np.nan)
    if len(values) < period or period < 1:
        return out
    out[period - 1] = float(np.mean(values[:period]))
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def ema_series(values: Floats, period: int) -> Floats:
    """Exponential moving average seeded at the first sample."""
    k = 2.0 / (period + 1.0)
    out = np.empty(len(values), dtype=float)
    if not len(values):
        return out
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1.0 - k)
    return out


def _true_range(high: Floats, low: Floats, close: Floats) -> Floats:
    """True range, aligned to bars ``1..n-1`` (it needs a previous close)."""
    ranges: Floats = np.maximum.reduce(
        [
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ]
    )
    return ranges


def atr_series(
    high: Floats, low: Floats, close: Floats, period: int = ATR_PERIOD
) -> Floats | None:
    """Wilder ATR aligned to bars ``1..n-1``; ``None`` when history is short."""
    if len(close) < period + 1:
        return None
    return wilder_rma(_true_range(high, low, close), period)


# --------------------------------------------------------------------------- #
# Trend                                                                       #
# --------------------------------------------------------------------------- #
def adx_di(
    high: Floats, low: Floats, close: Floats, period: int = ADX_PERIOD
) -> tuple[float | None, float | None, float | None]:
    """``(adx, plus_di, minus_di)``; all ``None`` when history is short.

    ADX needs roughly two smoothing periods to mean anything — one to build DI,
    another to smooth DX into ADX — hence the ``2 * period`` floor.
    """
    if len(close) < 2 * period:
        return None, None, None
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    atr = wilder_rma(_true_range(high, low, close), period)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100.0 * wilder_rma(plus_dm, period) / atr
        minus_di = 100.0 * wilder_rma(minus_dm, period) / atr
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = wilder_rma(np.nan_to_num(dx), period)
    return _finite(adx[-1]), _finite(plus_di[-1]), _finite(minus_di[-1])


def rsi(close: Floats, period: int = RSI_PERIOD) -> float | None:
    """Wilder RSI, 0..100; ``None`` when history is short."""
    if len(close) < period + 1:
        return None
    delta = np.diff(close)
    gain = wilder_rma(np.where(delta > 0, delta, 0.0), period)
    loss = wilder_rma(np.where(delta < 0, -delta, 0.0), period)
    if not np.isfinite(gain[-1]) or not np.isfinite(loss[-1]):
        return None
    if loss[-1] <= 0:
        # No down moves in the window: RSI is 100 by definition, and dividing
        # by zero to discover that would produce a NaN the caller reads as
        # "unknown" rather than "maximally overbought".
        return 100.0 if gain[-1] > 0 else 50.0
    rs = gain[-1] / loss[-1]
    return _finite(100.0 - 100.0 / (1.0 + rs))


def ema_slope(
    close: Floats, period: int = EMA_PERIOD, lookback: int = SLOPE_LOOKBACK
) -> float | None:
    """EMA slope in percent per bar over ``lookback`` bars."""
    if len(close) < period + lookback:
        return None
    ema = ema_series(close, period)
    base = ema[-1 - lookback]
    if base == 0:
        return None
    return _finite((ema[-1] - base) / base * 100.0 / lookback)


def bb_compression(close: Floats, period: int = BB_PERIOD) -> float | None:
    """Current Bollinger width over its own trailing median (< 1 = compressed)."""
    if len(close) < period * 2:
        return None
    mid = _rolling_mean(close, period)
    sd = _rolling_std(close, period)
    with np.errstate(invalid="ignore", divide="ignore"):
        width = (2.0 * BB_STDDEV * sd) / mid
    valid = width[np.isfinite(width)]
    if len(valid) < 2:
        return None
    base = float(np.median(valid[-period:] if len(valid) >= period else valid))
    if base <= 0:
        return None
    return _finite(valid[-1] / base)


def r_squared(close: Floats, window: int = R2_WINDOW) -> float | None:
    """Trend cleanliness: R^2 of close against time over ``window`` bars."""
    if len(close) < window:
        return None
    y = close[-window:]
    x = np.arange(window, dtype=float)
    x_mean, y_mean = float(x.mean()), float(y.mean())
    sxx = float(np.sum((x - x_mean) ** 2))
    syy = float(np.sum((y - y_mean) ** 2))
    if sxx <= 0 or syy <= 0:
        return None
    sxy = float(np.sum((x - x_mean) * (y - y_mean)))
    return _finite(sxy**2 / (sxx * syy))


# --------------------------------------------------------------------------- #
# Volatility                                                                  #
# --------------------------------------------------------------------------- #
def percentile_rank(
    values: Floats, rank_window: int = RV_RANK_WINDOW
) -> float | None:
    """Share of the trailing window below the last value (0..1)."""
    valid = values[np.isfinite(values)]
    if len(valid) < 2:
        return None
    recent = valid[-rank_window:]
    return float(np.mean(recent < valid[-1]))


def realized_vol_rank(
    close: Floats, window: int = RV_WINDOW, rank_window: int = RV_RANK_WINDOW
) -> float | None:
    """Percentile rank (0..1) of current realized vol against its own history.

    A rank rather than a level: absolute volatility is not comparable across
    timeframes, but "high for this timeframe" is.
    """
    if len(close) < window + 2 or np.any(close <= 0):
        return None
    returns = np.diff(np.log(close))
    rv = _rolling_std(returns, window) * np.sqrt(252 * 390)
    return percentile_rank(rv, rank_window)


def rv_expansion(
    close: Floats, short_window: int = 5, long_window: int = RV_WINDOW
) -> float | None:
    """Short-window realized vol over long-window, minus 1 (> 0 = expanding)."""
    if len(close) < long_window + 2 or np.any(close <= 0):
        return None
    returns = np.diff(np.log(close))
    short = _rolling_std(returns, short_window)
    long = _rolling_std(returns, long_window)
    if not np.isfinite(short[-1]) or not np.isfinite(long[-1]) or long[-1] <= 0:
        return None
    return _finite(short[-1] / long[-1] - 1.0)


# --------------------------------------------------------------------------- #
# Price geometry and flow                                                     #
# --------------------------------------------------------------------------- #
def vwap_roll(
    close: Floats, volume: Floats, window: int = VWAP_WINDOW
) -> tuple[float | None, float | None]:
    """``(distance_pct, slope_pct)`` of close against a rolling VWAP."""
    if len(close) < 2:
        return None, None
    n = min(window, len(close))
    c, v = close[-n:], volume[-n:]
    total = float(v.sum())
    if total <= 0 or close[-1] <= 0:
        return None, None
    vwap = float((c * v).sum() / total)
    distance = (close[-1] - vwap) / close[-1] * 100.0
    slope = 0.0
    prior_total = float(v[:-1].sum())
    if n >= 2 and prior_total > 0:
        prior_vwap = float((c[:-1] * v[:-1]).sum() / prior_total)
        if prior_vwap > 0:
            slope = (vwap - prior_vwap) / prior_vwap * 100.0
    return _finite(distance), _finite(slope)


def range_position(
    high: Floats, low: Floats, close: Floats, window: int = VWAP_WINDOW
) -> float | None:
    """Where close sits in the recent range: 0 at the low, 1 at the high."""
    if not len(close):
        return None
    n = min(window, len(close))
    hi, lo = float(high[-n:].max()), float(low[-n:].min())
    if hi <= lo:
        return None
    return _finite((close[-1] - lo) / (hi - lo))


def signed_volume_proxy(
    high: Floats, low: Floats, close: Floats, volume: Floats
) -> Floats:
    """Close-location-value proxy for signed volume.

    Neither Tradier nor Massive publishes signed volume, so buying and selling
    pressure is inferred from where each bar closed inside its own range: a bar
    closing on its high is treated as fully bought, on its low as fully sold. A
    zero-range bar contributes nothing rather than an arbitrary sign.
    """
    span = high - low
    with np.errstate(invalid="ignore", divide="ignore"):
        clv = np.where(span > 0, 2.0 * (close - low) / span - 1.0, 0.0)
    return np.nan_to_num(clv) * volume


def cvd_slope(
    signed_volume: Floats, volume: Floats, lookback: int = SLOPE_LOOKBACK
) -> float | None:
    """Cumulative-volume-delta slope, normalized by average bar volume."""
    if len(signed_volume) < lookback + 1:
        return None
    cvd = np.cumsum(signed_volume)
    recent = volume[-lookback:]
    average = float(np.mean(recent)) if len(recent) and float(np.mean(recent)) > 0 else 1.0
    return _finite((cvd[-1] - cvd[-1 - lookback]) / (average * lookback))


# --------------------------------------------------------------------------- #
# Volatility channels                                                         #
# --------------------------------------------------------------------------- #
def channel_features(
    high: Floats, low: Floats, close: Floats
) -> dict[str, float | None]:
    """Bollinger / Keltner / Donchian state for the last bar.

    Every key in :data:`CHANNEL_KEYS` is always present; a key is ``None`` when
    this timeframe has too little history for that indicator.
    """
    out: dict[str, float | None] = dict.fromkeys(CHANNEL_KEYS)
    n = len(close)

    bb_width_last = _bollinger(out, close, n)
    atr_last = _keltner(out, high, low, close, n, bb_width_last)
    _donchian(out, high, low, close, n, atr_last)
    return out


def _bollinger(
    out: dict[str, float | None], close: Floats, n: int
) -> float | None:
    """Fill the Bollinger keys; return the current normalized width."""
    if n < BB_PERIOD:
        return None
    mid = _rolling_mean(close, BB_PERIOD)
    sd = _rolling_std(close, BB_PERIOD)
    upper = mid + BB_STDDEV * sd
    lower = mid - BB_STDDEV * sd
    with np.errstate(invalid="ignore", divide="ignore"):
        width = (upper - lower) / mid
    if not np.isfinite(width[-1]) or mid[-1] <= 0:
        return None

    out["bb_width"] = percentile_rank(width, CHANNEL_RANK_WINDOW)
    span = float(upper[-1] - lower[-1])
    if span > 0:
        out["bb_position"] = _finite((close[-1] - lower[-1]) / span)
    if n >= BB_PERIOD + BB_EXPANSION_LOOKBACK:
        prior = width[-1 - BB_EXPANSION_LOOKBACK]
        if np.isfinite(prior) and prior > 0:
            out["bb_expansion"] = _finite(width[-1] / prior - 1.0)
    return float(width[-1])


def _keltner(
    out: dict[str, float | None],
    high: Floats,
    low: Floats,
    close: Floats,
    n: int,
    bb_width_last: float | None,
) -> float | None:
    """Fill the Keltner keys and the squeeze; return the current ATR."""
    atr = atr_series(high, low, close, KELTNER_PERIOD)
    if atr is None or not np.isfinite(atr[-1]) or atr[-1] <= 0 or n < KELTNER_PERIOD:
        return None
    atr_last = float(atr[-1])

    # ATR is aligned to bars 1..n-1, so the EMA basis drops its first bar to match.
    mid = ema_series(close, KELTNER_PERIOD)[1:]
    half = KELTNER_MULTIPLIER * atr
    upper, lower = mid + half, mid - half
    with np.errstate(invalid="ignore", divide="ignore"):
        width = np.where(mid > 0, 2.0 * half / mid, np.nan)
    out["keltner_width"] = percentile_rank(width, CHANNEL_RANK_WINDOW)

    span = float(upper[-1] - lower[-1])
    if span > 0:
        out["keltner_position"] = _finite((close[-1] - lower[-1]) / span)

    # Signed persistence above/below the midline: riding the upper half for
    # several bars is a trend, touching it once is noise.
    m = min(KELTNER_TREND_LOOKBACK, len(upper))
    spans = upper[-m:] - lower[-m:]
    with np.errstate(invalid="ignore", divide="ignore"):
        positions = np.where(spans > 0, (close[-m:] - lower[-m:]) / spans, np.nan)
    positions = positions[np.isfinite(positions)]
    if len(positions):
        out["keltner_trend_strength"] = _finite(float(np.mean(positions - 0.5)))

    width_last = float(width[-1]) if np.isfinite(width[-1]) else None
    if bb_width_last is not None and width_last and width_last > 0:
        ratio = bb_width_last / width_last
        graded = (SQUEEZE_ON - ratio) / (SQUEEZE_ON - SQUEEZE_FULL)
        out["bb_squeeze"] = float(np.clip(graded, 0.0, 1.0))
    return atr_last


def _donchian(
    out: dict[str, float | None],
    high: Floats,
    low: Floats,
    close: Floats,
    n: int,
    atr_last: float | None,
) -> None:
    """Fill the Donchian keys, including ATR-scaled breakout penetration."""
    if n < DONCHIAN_PERIOD + 1:
        return
    highest = np.full(n, np.nan)
    lowest = np.full(n, np.nan)
    highest[DONCHIAN_PERIOD - 1 :] = _rolling(high, DONCHIAN_PERIOD).max(axis=1)
    lowest[DONCHIAN_PERIOD - 1 :] = _rolling(low, DONCHIAN_PERIOD).min(axis=1)

    span = float(highest[-1] - lowest[-1])
    if span > 0 and close[-1] > 0:
        with np.errstate(invalid="ignore", divide="ignore"):
            normalized = (highest - lowest) / close
        out["donchian_width"] = percentile_rank(normalized, CHANNEL_RANK_WINDOW)
        out["donchian_position"] = _finite((close[-1] - lowest[-1]) / span)

    # Measured against the *prior* bar's channel, so a new extreme registers as
    # penetration instead of being absorbed into its own channel and reading 0.
    prior_high, prior_low = highest[-2], lowest[-2]
    if atr_last and np.isfinite(prior_high) and np.isfinite(prior_low):
        out["donchian_breakout_up"] = max(0.0, float((close[-1] - prior_high) / atr_last))
        out["donchian_breakout_down"] = max(0.0, float((prior_low - close[-1]) / atr_last))
