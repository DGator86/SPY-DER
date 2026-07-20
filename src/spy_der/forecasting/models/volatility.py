"""Volatility / realized-move model (System A prediction/models/volatility.py)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from spy_der.forecasting.models.base import RANDOM_STATE, FeatureVectorizer

__all__ = ["VolatilityModel", "VolatilityModelConfig"]


@dataclass
class VolatilityModelConfig:
    target: str = "remaining_realized_move"
    epsilon: float = 1e-6
    learning_rate: float = 0.05
    max_leaf_nodes: int = 15
    max_depth: int | None = 3
    min_samples_leaf: int = 20
    l2_regularization: float = 1.0
    max_iter: int = 100
    random_state: int = RANDOM_STATE
    implied_feature: str | None = "implied_remaining_move"


@dataclass
class VolatilityModel:
    config: VolatilityModelConfig = field(default_factory=VolatilityModelConfig)
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    point_estimator: Any = None
    quantile_estimators: dict[float, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    fitted: bool = False

    def fit(
        self,
        rows: Sequence[dict[str, Any]],
        y: Sequence[float],
        sessions: Sequence[str] | None = None,
    ) -> VolatilityModel:
        del sessions
        y_arr = np.asarray(y, dtype=float)
        x = self.vectorizer.fit_transform(rows)
        self.point_estimator = HistGradientBoostingRegressor(
            learning_rate=self.config.learning_rate,
            max_leaf_nodes=self.config.max_leaf_nodes,
            max_depth=self.config.max_depth,
            min_samples_leaf=self.config.min_samples_leaf,
            l2_regularization=self.config.l2_regularization,
            max_iter=self.config.max_iter,
            random_state=self.config.random_state,
        )
        self.point_estimator.fit(x, y_arr)
        self.quantile_estimators = {}
        for q in (0.1, 0.9):
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
            self.quantile_estimators[q] = est
        self.metadata = {"target": self.config.target, "n_train": len(y_arr)}
        self.fitted = True
        return self

    def predict(self, rows: Sequence[dict[str, Any]]) -> dict[str, np.ndarray]:
        if not self.fitted or self.point_estimator is None:
            raise RuntimeError("VolatilityModel.predict before fit")
        x = self.vectorizer.transform(rows)
        expected = np.maximum(self.point_estimator.predict(x), self.config.epsilon)
        q10 = self.quantile_estimators[0.1].predict(x)
        q90 = self.quantile_estimators[0.9].predict(x)
        uncertainty = np.clip((q90 - q10) / np.maximum(expected, self.config.epsilon), 0.0, 1.0)
        rv_iv = np.full(len(rows), np.nan)
        if self.config.implied_feature:
            for i, row in enumerate(rows):
                implied = row.get(self.config.implied_feature)
                if isinstance(implied, (int, float)) and float(implied) > 0:
                    rv_iv[i] = float(expected[i] / float(implied))
        return {
            "expected_move": expected,
            "move_q10": q10,
            "move_q90": q90,
            "uncertainty": uncertainty,
            "rv_iv_ratio": rv_iv,
        }
