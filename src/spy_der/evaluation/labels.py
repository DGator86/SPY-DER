"""Underlying outcome labels (master spec §54 / System A prediction/labels.py).

Labels are computed from one completed session path, strictly forward of the
observation timestamp. Structural levels are frozen at observation-time values.
Horizons past the session close are missing (never truncated). Same-bar
target+stop is adverse-first.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from spy_der.training.asof import to_naive_utc

__all__ = [
    "DEFAULT_MIN_RETURN",
    "DEFAULT_MOVE_FRACTION",
    "HORIZONS",
    "HORIZON_MINUTES",
    "SessionLabeler",
    "direction_label",
    "first_passage",
    "range_survival",
]

HORIZON_MINUTES: dict[str, int] = {"5m": 5, "15m": 15, "30m": 30, "60m": 60}
HORIZONS: tuple[str, ...] = ("5m", "15m", "30m", "60m", "close")
DEFAULT_MIN_RETURN = 0.0002
DEFAULT_MOVE_FRACTION = 0.05


def _ln(a: float, b: float) -> float:
    return math.log(a / b)


def direction_label(
    forward_return: float | None,
    implied_remaining_move: float | None = None,
    min_return: float = DEFAULT_MIN_RETURN,
    move_fraction: float = DEFAULT_MOVE_FRACTION,
    cost_equivalent: float = 0.0,
) -> int | None:
    """+1 / -1 / 0 actionable direction; None when the return is unknown."""
    if forward_return is None:
        return None
    threshold = max(
        min_return,
        (implied_remaining_move or 0.0) * move_fraction,
        cost_equivalent,
    )
    if forward_return > threshold:
        return 1
    if forward_return < -threshold:
        return -1
    return 0


def first_passage(
    highs: Sequence[float] | np.ndarray,
    lows: Sequence[float] | np.ndarray,
    minutes: Sequence[float],
    target: float,
    stop: float,
    direction: str = "up",
) -> dict[str, object]:
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    for hi, lo, m in zip(highs, lows, minutes, strict=False):
        if direction == "up":
            hit_target = hi >= target
            hit_stop = lo <= stop
        else:
            hit_target = lo <= target
            hit_stop = hi >= stop
        if hit_target and hit_stop:
            return {
                "first_event": "ambiguous",
                "first_event_conservative": "stop",
                "ambiguous_same_bar": 1,
                "time_to_first_event": float(m),
            }
        if hit_target:
            return {
                "first_event": "target",
                "first_event_conservative": "target",
                "ambiguous_same_bar": 0,
                "time_to_first_event": float(m),
            }
        if hit_stop:
            return {
                "first_event": "stop",
                "first_event_conservative": "stop",
                "ambiguous_same_bar": 0,
                "time_to_first_event": float(m),
            }
    return {
        "first_event": "neither",
        "first_event_conservative": "neither",
        "ambiguous_same_bar": 0,
        "time_to_first_event": None,
    }


def range_survival(
    highs: Sequence[float] | np.ndarray,
    lows: Sequence[float] | np.ndarray,
    lower: float,
    upper: float,
) -> int:
    """1 if every bar stays STRICTLY within (lower, upper), else 0."""
    for hi, lo in zip(highs, lows, strict=False):
        if hi >= upper or lo <= lower:
            return 0
    return 1


@dataclass
class SessionLabeler:
    """Label observations against one session's completed bar path."""

    ts: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    base_bar_minutes: int = 1

    def __post_init__(self) -> None:
        self.ts = np.asarray(self.ts, dtype="datetime64[ns]")
        if len(self.ts) == 0:
            raise ValueError("SessionLabeler needs at least one bar")
        if len(self.ts) > 1 and np.any(np.diff(self.ts) <= np.timedelta64(0, "ns")):
            raise ValueError("bar timestamps must be strictly increasing")
        self.high = np.asarray(self.high, dtype=float)
        self.low = np.asarray(self.low, dtype=float)
        self.close = np.asarray(self.close, dtype=float)

    def _obs64(self, observation_ts: dt.datetime) -> np.datetime64:
        return np.datetime64(to_naive_utc(observation_ts))

    def _horizon_end_idx(self, obs: np.datetime64, horizon: str) -> int | None:
        session_close = self.ts[-1]
        if horizon == "close":
            return len(self.ts) - 1 if obs < session_close else None
        boundary = obs + np.timedelta64(HORIZON_MINUTES[horizon], "m")
        if boundary > session_close:
            return None
        i = int(np.searchsorted(self.ts, boundary, side="left"))
        if i >= len(self.ts):
            return None
        tolerance = np.timedelta64(self.base_bar_minutes, "m")
        if self.ts[i] - boundary > tolerance:
            return None
        return i

    def _future_start_idx(self, obs: np.datetime64) -> int:
        return int(np.searchsorted(self.ts, obs, side="right"))

    def _minutes_from(self, obs: np.datetime64, idx: int) -> float:
        return float((self.ts[idx] - obs) / np.timedelta64(1, "m"))

    def label_observation(
        self,
        observation_ts: dt.datetime,
        spot: float,
        *,
        call_wall: float | None = None,
        put_wall: float | None = None,
        gamma_flip: float | None = None,
        implied_remaining_move: float | None = None,
        min_return: float = DEFAULT_MIN_RETURN,
        move_fraction: float = DEFAULT_MOVE_FRACTION,
        cost_equivalent: float = 0.0,
    ) -> dict[str, object]:
        obs = self._obs64(observation_ts)
        start = self._future_start_idx(obs)
        out: dict[str, object] = {}

        for h in HORIZONS:
            end = self._horizon_end_idx(obs, h)
            valid = end is not None and end >= start
            fwd = _ln(float(self.close[end]), spot) if valid and end is not None else None
            out[f"fwd_return_{h}"] = fwd
            out[f"up_{h}"] = (1 if fwd > 0 else 0) if fwd is not None else None
            out[f"direction_{h}"] = direction_label(
                fwd, implied_remaining_move, min_return, move_fraction, cost_equivalent
            )

            if not valid or end is None:
                for name in (
                    "up_mfe",
                    "up_mae",
                    "down_mfe",
                    "down_mae",
                    "realized_variance",
                    "realized_volatility",
                    "abs_return",
                    "high_low_range",
                    "max_intrahorizon_move",
                    "touch_call_wall",
                    "touch_put_wall",
                    "touch_gamma_flip",
                    "cross_gamma_flip",
                    "range_survive",
                ):
                    out[f"{name}_{h}"] = None
                continue

            hi = self.high[start : end + 1]
            lo = self.low[start : end + 1]
            cl = self.close[start : end + 1]

            up_mfe = _ln(float(hi.max()), spot)
            up_mae = _ln(float(lo.min()), spot)
            out[f"up_mfe_{h}"] = up_mfe
            out[f"up_mae_{h}"] = up_mae
            out[f"down_mfe_{h}"] = -up_mae
            out[f"down_mae_{h}"] = -up_mfe

            path = np.concatenate(([spot], cl))
            rets = np.diff(np.log(path))
            var = float(np.sum(rets**2))
            out[f"realized_variance_{h}"] = var
            out[f"realized_volatility_{h}"] = math.sqrt(var)
            out[f"abs_return_{h}"] = abs(fwd) if fwd is not None else None
            out[f"high_low_range_{h}"] = _ln(float(hi.max()), float(lo.min()))
            out[f"max_intrahorizon_move_{h}"] = max(abs(up_mfe), abs(up_mae))

            out[f"touch_call_wall_{h}"] = (
                int(bool(np.any(hi >= call_wall))) if call_wall is not None else None
            )
            out[f"touch_put_wall_{h}"] = (
                int(bool(np.any(lo <= put_wall))) if put_wall is not None else None
            )
            if gamma_flip is not None:
                out[f"touch_gamma_flip_{h}"] = int(
                    bool(np.any((lo <= gamma_flip) & (hi >= gamma_flip)))
                )
                side0 = 1.0 if spot > gamma_flip else (-1.0 if spot < gamma_flip else 0.0)
                if side0:
                    crossed = bool(np.any(np.sign(cl - gamma_flip) == -side0))
                else:
                    crossed = bool(np.any(cl != gamma_flip))
                out[f"cross_gamma_flip_{h}"] = int(crossed)
            else:
                out[f"touch_gamma_flip_{h}"] = None
                out[f"cross_gamma_flip_{h}"] = None

            out[f"range_survive_{h}"] = (
                range_survival(hi, lo, put_wall, call_wall)
                if call_wall is not None and put_wall is not None
                else None
            )

        if start < len(self.ts):
            hi_rem = float(self.high[start:].max())
            lo_rem = float(self.low[start:].min())
            out["remaining_realized_move"] = max(
                abs(hi_rem / spot - 1.0), abs(lo_rem / spot - 1.0)
            )
        else:
            out["remaining_realized_move"] = None

        out.update(self._wall_first_passage(obs, start, call_wall, put_wall, gamma_flip))
        return out

    def _wall_first_passage(
        self,
        obs: np.datetime64,
        start: int,
        call_wall: float | None,
        put_wall: float | None,
        gamma_flip: float | None,
    ) -> dict[str, object]:
        out: dict[str, object] = {
            "call_wall_first": None,
            "put_wall_first": None,
            "neither_wall": None,
            "wall_first_ambiguous": None,
            "time_to_call_wall": None,
            "time_to_put_wall": None,
            "time_to_flip": None,
        }
        hi = self.high[start:]
        lo = self.low[start:]

        if call_wall is not None:
            idx = np.nonzero(hi >= call_wall)[0]
            if len(idx):
                out["time_to_call_wall"] = self._minutes_from(obs, start + int(idx[0]))
        if put_wall is not None:
            idx = np.nonzero(lo <= put_wall)[0]
            if len(idx):
                out["time_to_put_wall"] = self._minutes_from(obs, start + int(idx[0]))
        if gamma_flip is not None:
            idx = np.nonzero((lo <= gamma_flip) & (hi >= gamma_flip))[0]
            if len(idx):
                out["time_to_flip"] = self._minutes_from(obs, start + int(idx[0]))

        if call_wall is not None and put_wall is not None:
            fp = first_passage(
                hi,
                lo,
                [self._minutes_from(obs, start + i) for i in range(len(hi))],
                target=call_wall,
                stop=put_wall,
                direction="up",
            )
            ev = fp["first_event"]
            out["wall_first_ambiguous"] = fp["ambiguous_same_bar"]
            if ev == "ambiguous":
                out["neither_wall"] = 0
            else:
                out["call_wall_first"] = 1 if ev == "target" else 0
                out["put_wall_first"] = 1 if ev == "stop" else 0
                out["neither_wall"] = 1 if ev == "neither" else 0
        return out
