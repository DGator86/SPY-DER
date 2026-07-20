"""Multi-timeframe resampling and indicators (master spec §20).

Bounded migration of System A ``resample.py`` / ``mtf_matrix.py``: resample the
1-minute bars into higher timeframes and compute a core indicator set (return,
EMA slope, RSI, realized volatility) per timeframe. Timeframes without enough
history report ``None`` for history-dependent indicators — an explicit cold
start, never a fabricated value (spec §20 cold-start behavior).

The full ~110-variable MTF matrix (ADX/DI, Bollinger, VWAP, CVD, keltner, etc.)
is layered on later; this establishes the resample + indicator contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from spy_der.contracts.market import Bar

__all__ = ["DEFAULT_TIMEFRAMES", "TimeframeFeatures", "compute_mtf"]

DEFAULT_TIMEFRAMES: tuple[int, ...] = (1, 5, 15)
_EMA_PERIOD = 9
_RSI_PERIOD = 14
_RV_WINDOW = 20


@dataclass(frozen=True, slots=True)
class TimeframeFeatures:
    timeframe_minutes: int
    n_bars: int
    last_return: float | None
    ema_slope: float | None
    rsi: float | None
    realized_vol: float | None


def _resample_closes(bars: Sequence[Bar], minutes: int) -> list[float]:
    closes = [float(b.close) for b in bars]
    if minutes <= 1:
        return closes
    grouped: list[float] = []
    for start in range(0, len(closes), minutes):
        window = closes[start : start + minutes]
        if window:
            grouped.append(window[-1])  # close of the resampled bar
    return grouped


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(closes: np.ndarray, period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.clip(deltas, 0.0, None)
    losses = np.clip(-deltas, 0.0, None)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _features(minutes: int, closes: list[float]) -> TimeframeFeatures:
    arr = np.asarray(closes, dtype=float)
    n = len(arr)

    last_return: float | None = None
    if n >= 2 and arr[-2] != 0.0:
        last_return = float(arr[-1] / arr[-2] - 1.0)

    ema_slope: float | None = None
    if n >= _EMA_PERIOD:
        ema = _ema(arr, _EMA_PERIOD)
        if ema[-2] != 0.0:
            ema_slope = float(ema[-1] / ema[-2] - 1.0)

    rsi = _rsi(arr, _RSI_PERIOD)

    realized_vol: float | None = None
    if n >= 3:
        rets = np.diff(arr) / arr[:-1]
        window = rets[-_RV_WINDOW:]
        if len(window) >= 2:
            realized_vol = float(np.std(window, ddof=1))

    return TimeframeFeatures(
        timeframe_minutes=minutes,
        n_bars=n,
        last_return=last_return,
        ema_slope=ema_slope,
        rsi=rsi,
        realized_vol=realized_vol,
    )


def compute_mtf(
    bars: Sequence[Bar],
    timeframes: Sequence[int] = DEFAULT_TIMEFRAMES,
) -> tuple[TimeframeFeatures, ...]:
    """Resample ``bars`` and compute per-timeframe indicators."""
    return tuple(_features(tf, _resample_closes(bars, tf)) for tf in timeframes)
