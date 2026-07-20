"""Return quantile model (System A prediction/models/return_quantiles.py)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from spy_der.forecasting.models.base import (
    RANDOM_STATE,
    FeatureVectorizer,
    pinball_loss,
    rearrange_quantiles,
)

__all__ = ["QUANTILES", "QUANTILE_HORIZONS", "ReturnQuantileConfig", "ReturnQuantileModel"]

QUANTILE_HORIZONS: tuple[str, ...] = ("30m", "60m", "close")
QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)


@dataclass
class ReturnQuantileConfig:
    horizon: str = "30m"
    quantiles: tuple[float, ...] = QUANTILES
    learning_rate: float = 0.05
    max_leaf_nodes: int = 15
    max_depth: int | None = 3
    min_samples_leaf: int = 20
    l2_regularization: float = 1.0
    max_iter: int = 100
    random_state: int = RANDOM_STATE


@dataclass
class ReturnQuantileModel:
    config: ReturnQuantileConfig = field(default_factory=ReturnQuantileConfig)
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    estimators: dict[float, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    fitted: bool = False

    def fit(
        self,
        rows: Sequence[dict[str, Any]],
        y: Sequence[float],
        sessions: Sequence[str] | None = None,
    ) -> ReturnQuantileModel:
        del sessions  # reserved for grouped evaluation
        y_arr = np.asarray(y, dtype=float)
        x = self.vectorizer.fit_transform(rows)
        self.estimators = {}
        for q in self.config.quantiles:
            est = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=q,
                learning_rate=self.config.learning_rate,
                max_leaf_nodes=self.config.max_leaf_nodes,
                max_depth=self.config.max_depth,
                min_samples_leaf=self.config.min_samples_leaf,
                l2_regularization=self.config.l2_regularization,
                max_iter=self.config.max_iter,
                random_state=self.config.random_state,
            )
            est.fit(x, y_arr)
            self.estimators[q] = est
        self.metadata = {
            "horizon": self.config.horizon,
            "n_train": len(y_arr),
            "quantiles": list(self.config.quantiles),
        }
        self.fitted = True
        return self

    def predict(self, rows: Sequence[dict[str, Any]]) -> dict[str, np.ndarray]:
        if not self.fitted:
            raise RuntimeError("ReturnQuantileModel.predict before fit")
        x = self.vectorizer.transform(rows)
        preds = {q: est.predict(x) for q, est in self.estimators.items()}
        q10, q50, q90 = rearrange_quantiles(preds[0.1], preds[0.5], preds[0.9])
        return {"q10": q10, "q50": q50, "q90": q90}

    def evaluate(self, rows: Sequence[dict[str, Any]], y: Sequence[float]) -> dict[str, float]:
        pred = self.predict(rows)
        y_arr = np.asarray(y, dtype=float)
        return {
            "pinball_q10": pinball_loss(y_arr, pred["q10"], 0.1),
            "pinball_q50": pinball_loss(y_arr, pred["q50"], 0.5),
            "pinball_q90": pinball_loss(y_arr, pred["q90"], 0.9),
        }
