"""Barrier-touch model (System A prediction/models/barrier_touch.py)."""

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

__all__ = [
    "BARRIER_TARGETS",
    "BarrierTouchConfig",
    "BarrierTouchModel",
    "barrier_feature_row",
]

BARRIER_TARGETS: tuple[str, ...] = (
    "touch_call_wall",
    "touch_put_wall",
    "cross_gamma_flip",
    "call_wall_first",
    "put_wall_first",
)


def barrier_feature_row(
    *,
    spot: float,
    call_wall: float | None = None,
    put_wall: float | None = None,
    gamma_flip: float | None = None,
    minutes_to_close: float | None = None,
    expected_realized_move: float | None = None,
    net_gex: float | None = None,
) -> dict[str, float | None]:
    return {
        "dist_call_wall": ((call_wall - spot) / spot) if call_wall and spot else None,
        "dist_put_wall": ((spot - put_wall) / spot) if put_wall and spot else None,
        "dist_gamma_flip": ((gamma_flip - spot) / spot) if gamma_flip and spot else None,
        "barrier_width": (
            (call_wall - put_wall) / spot if call_wall and put_wall and spot else None
        ),
        "minutes_to_close": minutes_to_close,
        "expected_realized_move": expected_realized_move,
        "net_gex_sign": (
            1.0 if net_gex and net_gex > 0 else (-1.0 if net_gex and net_gex < 0 else 0.0)
        ),
        "net_gex": net_gex,
    }


@dataclass
class BarrierTouchConfig:
    target: str = "touch_call_wall"
    c: float = 0.1
    l1_ratio: float = 0.5
    class_weight: str | None = "balanced"
    max_iter: int = 1500
    calibration: str = "sigmoid"
    random_state: int = RANDOM_STATE


@dataclass
class BarrierTouchModel:
    config: BarrierTouchConfig = field(default_factory=BarrierTouchConfig)
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
    ) -> BarrierTouchModel:
        del sessions
        if self.config.target not in BARRIER_TARGETS:
            raise ValueError(f"unsupported barrier target {self.config.target!r}")
        y_arr = np.asarray(y, dtype=int)
        self._base_rate = float(np.mean(y_arr)) if len(y_arr) else 0.5
        x = self.vectorizer.fit_transform(rows)
        if len(np.unique(y_arr)) < 2:
            self.estimator = None
            self.calibrator = IdentityCalibrator()
            self.fitted = True
            self.metadata = {"target": self.config.target, "degenerate": True}
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
        self.metadata = {"target": self.config.target, "n_train": len(y_arr)}
        self.fitted = True
        return self

    def predict_raw(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("BarrierTouchModel.predict_raw before fit")
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
