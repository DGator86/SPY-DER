"""Shared V2 model plumbing (System A prediction/models/base.py).

FeatureVectorizer emits value columns plus explicit missingness flags so
estimators never confuse a median-imputed placeholder with an observed value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "RANDOM_STATE",
    "FeatureVectorizer",
    "brier_score",
    "brier_skill",
    "clip_probability",
    "interval_coverage",
    "log_loss_score",
    "pinball_loss",
    "rearrange_quantiles",
]

RANDOM_STATE = 7


def _as_float(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        return f if math.isfinite(f) else float("nan")
    return float("nan")


@dataclass
class FeatureVectorizer:
    """dict-rows -> fixed-width numeric matrix ``[values | missing flags]``."""

    feature_names: list[str] = field(default_factory=list)
    medians: dict[str, float] = field(default_factory=dict)
    fitted: bool = False

    def fit(self, rows: Sequence[dict[str, Any]]) -> FeatureVectorizer:
        names: set[str] = set()
        for row in rows:
            names.update(row.keys())
        self.feature_names = sorted(names)
        cols: dict[str, list[float]] = {n: [] for n in self.feature_names}
        for row in rows:
            for name in self.feature_names:
                cols[name].append(_as_float(row.get(name)))
        self.medians = {}
        for name in self.feature_names:
            arr = np.asarray(cols[name], dtype=float)
            finite = arr[np.isfinite(arr)]
            self.medians[name] = float(np.median(finite)) if len(finite) else 0.0
        self.fitted = True
        return self

    def transform(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("FeatureVectorizer.transform before fit")
        n_feat = len(self.feature_names)
        out = np.empty((len(rows), 2 * n_feat), dtype=float)
        for i, row in enumerate(rows):
            for j, name in enumerate(self.feature_names):
                v = _as_float(row.get(name))
                missing = not math.isfinite(v)
                out[i, j] = self.medians[name] if missing else v
                out[i, n_feat + j] = 1.0 if missing else 0.0
        return out

    def fit_transform(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        return self.fit(rows).transform(rows)

    @property
    def n_columns(self) -> int:
        return 2 * len(self.feature_names)


def clip_probability(p: Any) -> np.ndarray:
    return np.asarray(np.clip(np.asarray(p, dtype=float), 0.0, 1.0), dtype=float)


def rearrange_quantiles(
    q10: Any, q50: Any, q90: Any
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    stacked = np.sort(
        np.vstack(
            [
                np.asarray(q10, dtype=float),
                np.asarray(q50, dtype=float),
                np.asarray(q90, dtype=float),
            ]
        ),
        axis=0,
    )
    return stacked[0], stacked[1], stacked[2]


def pinball_loss(y_true: Any, y_pred: Any, quantile: float) -> float:
    y_true_a = np.asarray(y_true, dtype=float)
    y_pred_a = np.asarray(y_pred, dtype=float)
    diff = y_true_a - y_pred_a
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1.0) * diff)))


def interval_coverage(y_true: Any, lower: Any, upper: Any) -> float:
    y = np.asarray(y_true, dtype=float)
    return float(
        np.mean((y >= np.asarray(lower, dtype=float)) & (y <= np.asarray(upper, dtype=float)))
    )


def brier_score(y_true: Any, p: Any) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y_true, dtype=float)) ** 2))


def brier_skill(y_true: Any, p: Any) -> float | None:
    y = np.asarray(y_true, dtype=float)
    base = float(np.mean(y))
    ref = brier_score(y, np.full_like(y, base))
    if ref <= 0.0:
        return None
    return 1.0 - brier_score(y, p) / ref


def log_loss_score(y_true: Any, p: Any, eps: float = 1e-12) -> float:
    y = np.asarray(y_true, dtype=float)
    p_c = np.clip(np.asarray(p, dtype=float), eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p_c) + (1.0 - y) * np.log(1.0 - p_c)))
