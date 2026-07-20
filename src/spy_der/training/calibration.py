"""Probability calibration (master spec §25 / System A prediction/calibration.py).

Default is sigmoid/Platt scaling. Calibration must be fit on train-only
cross-fitted (OOF) scores — never on outer test labels.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from spy_der.forecasting.models.base import brier_score, clip_probability, log_loss_score

__all__ = [
    "CalibrationArtifact",
    "IdentityCalibrator",
    "IsotonicCalibrator",
    "SigmoidCalibrator",
    "build_calibration_artifact",
    "fit_calibrator",
    "reliability_bins",
]


def _logit(p: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), eps, 1.0 - eps)
    return np.log(p / (1.0 - p))


@dataclass
class SigmoidCalibrator:
    """Platt scaling: logistic regression on the raw score's log-odds."""

    a: float = 1.0
    b: float = 0.0
    fitted: bool = False

    def fit(self, p_raw: Any, y: Any) -> SigmoidCalibrator:
        from sklearn.linear_model import LogisticRegression

        x = _logit(np.asarray(p_raw, dtype=float)).reshape(-1, 1)
        y_arr = np.asarray(y, dtype=int)
        if len(np.unique(y_arr)) < 2:
            self.a, self.b = 1.0, 0.0
            self.fitted = True
            return self
        lr = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        lr.fit(x, y_arr)
        self.a = float(lr.coef_[0][0])
        self.b = float(lr.intercept_[0])
        self.fitted = True
        return self

    def transform(self, p_raw: Any) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("calibrator used before fit")
        z = self.a * _logit(np.asarray(p_raw, dtype=float)) + self.b
        out = np.where(
            z >= 0,
            1.0 / (1.0 + np.exp(-np.abs(z))),
            np.exp(-np.abs(z)) / (1.0 + np.exp(-np.abs(z))),
        )
        return clip_probability(out)

    def to_dict(self) -> dict[str, Any]:
        return {"method": "sigmoid", "a": self.a, "b": self.b}


@dataclass
class IsotonicCalibrator:
    """Isotonic regression p_raw -> p_cal; monotone, clipped to [0, 1]."""

    _iso: Any = field(default=None, repr=False)
    fitted: bool = False

    def fit(self, p_raw: Any, y: Any) -> IsotonicCalibrator:
        from sklearn.isotonic import IsotonicRegression

        self._iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        self._iso.fit(np.asarray(p_raw, dtype=float), np.asarray(y, dtype=float))
        self.fitted = True
        return self

    def transform(self, p_raw: Any) -> np.ndarray:
        if not self.fitted or self._iso is None:
            raise RuntimeError("calibrator used before fit")
        return clip_probability(self._iso.predict(np.asarray(p_raw, dtype=float)))

    def to_dict(self) -> dict[str, Any]:
        return {"method": "isotonic"}


@dataclass
class IdentityCalibrator:
    """No-op fallback (still clips to [0, 1])."""

    fitted: bool = True

    def fit(self, p_raw: Any, y: Any) -> IdentityCalibrator:
        return self

    def transform(self, p_raw: Any) -> np.ndarray:
        return clip_probability(p_raw)

    def to_dict(self) -> dict[str, Any]:
        return {"method": "identity"}


@dataclass
class CalibrationArtifact:
    """Auditable record of an independent calibrator fit."""

    method: str
    calibrator: Any
    training_sessions: tuple[str, ...]
    oof_n: int
    oof_session_n: int
    brier_before: float
    brier_after: float
    log_loss_before: float
    log_loss_after: float
    slope: float | None
    intercept: float | None
    reliability_bins: list[dict[str, float]]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "training_sessions": list(self.training_sessions),
            "oof_n": self.oof_n,
            "oof_session_n": self.oof_session_n,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "log_loss_before": self.log_loss_before,
            "log_loss_after": self.log_loss_after,
            "slope": self.slope,
            "intercept": self.intercept,
            "reliability_bins": self.reliability_bins,
            "diagnostics": self.diagnostics,
            "calibrator": (
                self.calibrator.to_dict() if hasattr(self.calibrator, "to_dict") else {}
            ),
        }


def fit_calibrator(p_raw: Any, y: Any, method: str = "sigmoid") -> Any:
    mapping = {
        "sigmoid": SigmoidCalibrator,
        "isotonic": IsotonicCalibrator,
        "identity": IdentityCalibrator,
    }
    cls = mapping.get(method)
    if cls is None:
        raise ValueError(f"unknown calibration method {method!r}")
    return cls().fit(p_raw, y)


def reliability_bins(p: Any, y: Any, n_bins: int = 10) -> list[dict[str, float]]:
    p_arr = np.asarray(p, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    if len(p_arr) == 0:
        return []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[dict[str, float]] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p_arr >= lo) & (p_arr < hi if i < n_bins - 1 else p_arr <= hi)
        if not np.any(mask):
            continue
        out.append(
            {
                "bin_left": float(lo),
                "bin_right": float(hi),
                "count": float(np.sum(mask)),
                "mean_p": float(np.mean(p_arr[mask])),
                "mean_y": float(np.mean(y_arr[mask])),
            }
        )
    return out


def build_calibration_artifact(
    p_raw: Any,
    y: Any,
    sessions: Sequence[str],
    *,
    method: str = "sigmoid",
) -> CalibrationArtifact:
    """Fit a calibrator on provided OOF scores and emit an audit artifact."""
    p_arr = np.asarray(p_raw, dtype=float)
    y_arr = np.asarray(y, dtype=int)
    sessions_t = tuple(sessions)
    cal = fit_calibrator(p_arr, y_arr, method=method)
    p_cal = cal.transform(p_arr)
    return CalibrationArtifact(
        method=method,
        calibrator=cal,
        training_sessions=tuple(sorted(set(sessions_t))),
        oof_n=len(y_arr),
        oof_session_n=len(set(sessions_t)),
        brier_before=brier_score(y_arr, p_arr),
        brier_after=brier_score(y_arr, p_cal),
        log_loss_before=log_loss_score(y_arr, p_arr),
        log_loss_after=log_loss_score(y_arr, p_cal),
        slope=None,
        intercept=None,
        reliability_bins=reliability_bins(p_cal, y_arr),
    )
