"""Range-survival model (System A prediction/models/range_survival.py).

Bounded to wall-channel survival with elastic-net logistic + sigmoid calibration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from spy_der.forecasting.models.base import RANDOM_STATE, FeatureVectorizer, clip_probability
from spy_der.training.calibration import IdentityCalibrator, fit_calibrator

__all__ = ["RANGE_HORIZONS", "RangeSurvivalConfig", "RangeSurvivalModel", "range_feature_row"]

RANGE_HORIZONS: tuple[str, ...] = ("15m", "30m", "60m", "close")


def range_feature_row(
    *,
    spot: float,
    lower: float,
    upper: float,
    minutes_to_close: float | None = None,
    expected_realized_move: float | None = None,
    net_gex: float | None = None,
) -> dict[str, float | None]:
    width = upper - lower
    return {
        "dist_lower": (spot - lower) / spot if spot else None,
        "dist_upper": (upper - spot) / spot if spot else None,
        "barrier_width": width / spot if spot else None,
        "barrier_width_over_vol": (
            width / expected_realized_move
            if expected_realized_move and expected_realized_move > 0
            else None
        ),
        "minutes_to_close": minutes_to_close,
        "expected_realized_move": expected_realized_move,
        "net_gex_sign": (
            1.0 if net_gex and net_gex > 0 else (-1.0 if net_gex and net_gex < 0 else 0.0)
        ),
        "net_gex": net_gex,
    }


@dataclass
class RangeSurvivalConfig:
    horizon: str = "close"
    c: float = 0.1
    l1_ratio: float = 0.5
    class_weight: str | None = "balanced"
    max_iter: int = 1500
    calibration: str = "sigmoid"
    random_state: int = RANDOM_STATE


@dataclass
class RangeSurvivalModel:
    config: RangeSurvivalConfig = field(default_factory=RangeSurvivalConfig)
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    estimator: Any = None
    calibrator: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    fitted: bool = False
    _base_rate: float = 0.5

    def fit(
        self,
        rows: Sequence[dict[str, Any]],
        y: Sequence[int],
        sessions: Sequence[str],
    ) -> RangeSurvivalModel:
        del sessions
        y_arr = np.asarray(y, dtype=int)
        self._base_rate = float(np.mean(y_arr)) if len(y_arr) else 0.5
        x = self.vectorizer.fit_transform(rows)
        if len(np.unique(y_arr)) < 2:
            self.estimator = None
            self.calibrator = IdentityCalibrator()
            self.fitted = True
            self.metadata = {"horizon": self.config.horizon, "degenerate": True}
            return self
        pipe = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        solver="saga",
                        l1_ratio=self.config.l1_ratio,
                        C=self.config.c,
                        class_weight=self.config.class_weight,
                        max_iter=self.config.max_iter,
                        random_state=self.config.random_state,
                    ),
                ),
            ]
        )
        pipe.fit(x, y_arr)
        self.estimator = pipe
        raw = self.predict_raw(rows)
        try:
            self.calibrator = fit_calibrator(raw, y_arr, method=self.config.calibration)
        except Exception:
            self.calibrator = IdentityCalibrator().fit(raw, y_arr)
        self.metadata = {"horizon": self.config.horizon, "n_train": len(y_arr)}
        self.fitted = True
        return self

    def predict_raw(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("RangeSurvivalModel.predict_raw before fit")
        if self.estimator is None:
            return np.full(len(rows), self._base_rate, dtype=float)
        x = self.vectorizer.transform(rows)
        proba = self.estimator.predict_proba(x)
        classes = list(self.estimator.named_steps["clf"].classes_)
        pos_idx = classes.index(1) if 1 in classes else -1
        return clip_probability(proba[:, pos_idx])

    def predict_proba(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        raw = self.predict_raw(rows)
        if self.calibrator is None:
            return raw
        return clip_probability(self.calibrator.transform(raw))
