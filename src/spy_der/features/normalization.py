"""Robust rolling normalization (master spec §20).

Migrated in spirit from System A ``regime_classifier.ScaleBook`` / the MTF
normalization: standardize a feature against a rolling window of its own recent
values using median and MAD (robust to outliers), scoring an observation
*before* updating state, and reading neutral until warm. State persists across
restarts via atomic JSON so a redeploy does not cold-start the scales.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict, deque

__all__ = ["RobustStandardizer"]

_MAD_TO_STD = 1.4826  # MAD -> standard-deviation-equivalent for normal data


class RobustStandardizer:
    """Per-key robust z-score with a bounded rolling window, optionally persisted."""

    def __init__(
        self,
        window: int = 500,
        min_samples: int = 20,
        path: str | None = None,
    ) -> None:
        self.window = window
        self.min_samples = min_samples
        self.path = path
        self._values: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))
        self._load()

    def is_warm(self, key: str) -> bool:
        return len(self._values[key]) >= self.min_samples

    def score(self, key: str, value: float) -> float | None:
        """Robust z-score of ``value`` against history; ``None`` until warm.

        The observation is scored against existing history, then recorded — an
        observation never standardizes against itself.
        """
        history = self._values[key]
        result: float | None = None
        if len(history) >= self.min_samples:
            ordered = sorted(history)
            median = _median(ordered)
            mad = _median(sorted(abs(v - median) for v in ordered))
            scale = _MAD_TO_STD * mad
            result = (value - median) / scale if scale > 0 else 0.0
        history.append(value)
        self._save()
        return result

    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            for key, values in data.get("values", {}).items():
                self._values[key] = deque(
                    (float(v) for v in values), maxlen=self.window
                )
        except (OSError, ValueError, TypeError):
            self._values.clear()  # corrupt state re-warms; never crash

    def _save(self) -> None:
        if not self.path:
            return
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            payload = {"values": {k: list(v) for k, v in self._values.items()}}
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".norm_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, self.path)
        except OSError:
            pass  # persistence is best-effort


def _median(ordered: list[float]) -> float:
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
