"""Direction probability model (System A prediction/models/direction.py).

Bounded Phase 5 form: elastic-net logistic regression with an independent
session-holdout sigmoid calibrator. Nested HGB challenger search is deferred.
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

__all__ = ["DIRECTION_HORIZONS", "DirectionModel", "DirectionModelConfig"]

DIRECTION_HORIZONS: tuple[str, ...] = ("5m", "15m", "30m", "60m", "close")


@dataclass
class DirectionModelConfig:
    horizon: str = "30m"
    c: float = 0.1
    l1_ratio: float = 0.5
    class_weight: str | None = "balanced"
    max_iter: int = 2000
    calibration_frac: float = 0.25
    embargo_sessions: int = 1
    calibration: str = "sigmoid"
    random_state: int = RANDOM_STATE


@dataclass
class DirectionModel:
    config: DirectionModelConfig = field(default_factory=DirectionModelConfig)
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    estimator: Any = None
    calibrator: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    fitted: bool = False
    calibration_artifact: dict[str, Any] = field(default_factory=dict)

    def fit(
        self,
        rows: Sequence[dict[str, Any]],
        y: Sequence[int],
        sessions: Sequence[str],
    ) -> DirectionModel:
        if self.config.horizon not in DIRECTION_HORIZONS:
            raise ValueError(f"unsupported horizon {self.config.horizon!r}")
        y_arr = np.asarray(y, dtype=int)
        sessions_l = list(sessions)
        if len(rows) != len(y_arr) or len(rows) != len(sessions_l):
            raise ValueError("rows/y/sessions length mismatch")
        if len(np.unique(y_arr)) < 2:
            raise ValueError("direction labels must contain both classes")

        uniq = sorted(set(sessions_l))
        n_cal = max(1, round(len(uniq) * self.config.calibration_frac))
        n_fit = len(uniq) - n_cal - self.config.embargo_sessions
        if n_fit < 2:
            fit_sessions = uniq
            cal_sessions: list[str] = []
        else:
            fit_sessions = uniq[:n_fit]
            cal_sessions = uniq[n_fit + self.config.embargo_sessions :]

        fit_mask = np.asarray([s in set(fit_sessions) for s in sessions_l], dtype=bool)
        cal_mask = np.asarray([s in set(cal_sessions) for s in sessions_l], dtype=bool)
        if not np.any(cal_mask):
            cal_mask = fit_mask

        fit_rows = [rows[i] for i in range(len(rows)) if fit_mask[i]]
        x_fit = self.vectorizer.fit_transform(fit_rows)
        y_fit = y_arr[fit_mask]
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
        pipe.fit(x_fit, y_fit)
        self.estimator = pipe

        cal_rows = [rows[i] for i in range(len(rows)) if cal_mask[i]]
        p_raw = self.predict_raw(cal_rows)
        y_cal = y_arr[cal_mask]
        try:
            self.calibrator = fit_calibrator(p_raw, y_cal, method=self.config.calibration)
        except Exception:
            self.calibrator = IdentityCalibrator().fit(p_raw, y_cal)
        if hasattr(self.calibrator, "to_dict"):
            self.calibration_artifact = {
                "method": self.calibrator.to_dict().get("method", self.config.calibration),
                "calibrator": self.calibrator.to_dict(),
                "training_sessions": cal_sessions or fit_sessions,
                "oof_n": len(y_cal),
                "oof_session_n": len(set(cal_sessions or fit_sessions)),
            }
        self.metadata = {
            "horizon": self.config.horizon,
            "n_train": len(y_fit),
            "n_cal": len(y_cal),
            "train_sessions": fit_sessions,
            "calibration_sessions": cal_sessions,
            "feature_names": list(self.vectorizer.feature_names),
        }
        self.fitted = True
        return self

    def predict_raw(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("DirectionModel.predict_raw before fit")
        x = self.vectorizer.transform(rows)
        proba = self.estimator.predict_proba(x)
        # positive class column
        classes = list(self.estimator.named_steps["clf"].classes_)
        pos_idx = classes.index(1) if 1 in classes else -1
        return clip_probability(proba[:, pos_idx])

    def predict_proba(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        raw = self.predict_raw(rows)
        if self.calibrator is None:
            return raw
        return clip_probability(self.calibrator.transform(raw))
