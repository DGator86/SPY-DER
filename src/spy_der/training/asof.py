"""Point-in-time (as-of) source rules (master spec §25 / System A prediction/asof.py).

A feature may enter an observation only when its source timestamp is <= the
observation timestamp. Future-stamped sources raise rather than silently drop.
Missing values are recorded as missing — never replaced with neutral defaults.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from spy_der.contracts.market import Bar

__all__ = [
    "AsOfFeatureBuilder",
    "AsOfViolation",
    "bars_asof",
    "ensure_asof",
    "to_naive_utc",
]

UTC = dt.UTC


class AsOfViolation(ValueError):
    """A source timestamped AFTER the observation tried to enter it."""


def to_naive_utc(ts: dt.datetime) -> dt.datetime:
    """Aware -> naive UTC; naive passes through (System A bar-clock convention)."""
    if ts.tzinfo is not None:
        return ts.astimezone(UTC).replace(tzinfo=None)
    return ts


def ensure_asof(name: str, source_ts: dt.datetime, observation_ts: dt.datetime) -> float:
    """Assert ``source_ts <= observation_ts``; return source age in seconds."""
    src = to_naive_utc(source_ts)
    obs = to_naive_utc(observation_ts)
    age = (obs - src).total_seconds()
    if age < 0:
        raise AsOfViolation(
            f"source {name!r} is {-age:.3f}s in the FUTURE of the "
            f"observation ({source_ts.isoformat()} > {observation_ts.isoformat()})"
        )
    return age


def bars_asof(bars: tuple[Bar, ...] | list[Bar], observation_ts: dt.datetime) -> tuple[Bar, ...]:
    """Return only bars whose END timestamp is <= the observation timestamp."""
    cutoff = to_naive_utc(observation_ts)
    kept: list[Bar] = []
    for bar in bars:
        end = to_naive_utc(bar.timestamp)
        if end <= cutoff:
            kept.append(bar)
    return tuple(kept)


def bars_asof_arrays(
    ts: np.ndarray,
    observation_ts: dt.datetime,
) -> int:
    """Return the exclusive end index of bars with end-ts <= observation_ts."""
    cutoff = np.datetime64(to_naive_utc(observation_ts))
    arr = np.asarray(ts, dtype="datetime64[ns]")
    return int(np.searchsorted(arr, cutoff, side="right"))


@dataclass
class AsOfFeatureBuilder:
    """Collect one observation's raw features under the as-of rule."""

    observation_ts: dt.datetime
    features: dict[str, float | None] = field(default_factory=dict)
    missingness: dict[str, int] = field(default_factory=dict)
    source_ages: dict[str, float | None] = field(default_factory=dict)

    def add(
        self,
        name: str,
        value: float | None,
        source_ts: dt.datetime | None = None,
    ) -> None:
        if value is None or (isinstance(value, float) and value != value):
            self.add_missing(name)
            return
        age: float | None = None
        if source_ts is not None:
            age = ensure_asof(name, source_ts, self.observation_ts)
        self.features[name] = float(value)
        self.missingness[name] = 0
        self.source_ages[name] = age

    def add_missing(self, name: str) -> None:
        self.features[name] = None
        self.missingness[name] = 1
        self.source_ages[name] = None

    def coverage(self) -> float:
        if not self.missingness:
            return 0.0
        present = sum(1 for m in self.missingness.values() if m == 0)
        return present / len(self.missingness)

    def build(self) -> dict[str, object]:
        return {
            "features": dict(self.features),
            "missingness": dict(self.missingness),
            "source_ages": dict(self.source_ages),
            "coverage": round(self.coverage(), 6),
        }
