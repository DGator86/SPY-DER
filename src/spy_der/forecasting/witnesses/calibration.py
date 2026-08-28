"""Causal orientation calibration and promotion evidence for forecast witnesses.

A witness is allowed to be anti-oriented.  Calibration learns that relationship
from *matured historical forecasts only* rather than hard-coding an inversion.
The caller owns the as-of cut: observations supplied here must already satisfy
``realized_at <= forecast_as_of`` for the forecast being produced.

This module has no knowledge of option candidates, trades, fills or P&L.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge

from spy_der.contracts.common import ErrorCode, ValidationError, require_probability, require_tz_aware

ORIENTATION_CALIBRATION_VERSION = "alpha-v2-witness-orientation.v1"

__all__ = [
    "ORIENTATION_CALIBRATION_VERSION",
    "WitnessCalibration",
    "WitnessObservation",
    "fit_witness_calibration",
]

_EPS = 1e-6


def _logit(probability: float) -> float:
    p = min(1.0 - _EPS, max(_EPS, probability))
    return math.log(p / (1.0 - p))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp_neg = math.exp(-value)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(value)
    return exp_pos / (1.0 + exp_pos)


@dataclass(frozen=True, slots=True)
class WitnessObservation:
    """One matured, point-in-time witness forecast and its realized outcome."""

    session_date: str
    forecast_at: datetime
    realized_at: datetime
    probability_up: float
    expected_return: float
    realized_up: bool
    realized_return: float

    def __post_init__(self) -> None:
        if not self.session_date:
            raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "session_date is required")
        require_tz_aware(self.forecast_at, "WitnessObservation.forecast_at")
        require_tz_aware(self.realized_at, "WitnessObservation.realized_at")
        if self.realized_at <= self.forecast_at:
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "realized_at must be after forecast_at",
            )
        require_probability(self.probability_up, "WitnessObservation.probability_up")
        for name, value in (
            ("expected_return", self.expected_return),
            ("realized_return", self.realized_return),
        ):
            if not math.isfinite(value):
                raise ValidationError(ErrorCode.NON_FINITE_NUMBER, f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class WitnessCalibration:
    """Frozen calibration learned from matured historical witness observations."""

    witness_name: str
    horizon_minutes: int
    fitted_through: datetime
    sample_count: int
    session_count: int
    probability_intercept: float
    probability_slope: float
    return_intercept: float
    return_slope: float
    raw_brier: float
    calibrated_brier: float
    raw_return_correlation: float | None
    calibrated_return_correlation: float | None
    orientation: str
    blend_eligible: bool
    version: str = ORIENTATION_CALIBRATION_VERSION

    def __post_init__(self) -> None:
        require_tz_aware(self.fitted_through, "WitnessCalibration.fitted_through")
        if self.horizon_minutes <= 0:
            raise ValidationError(ErrorCode.NEGATIVE_VALUE, "horizon_minutes must be positive")
        if self.sample_count < 0 or self.session_count < 0:
            raise ValidationError(ErrorCode.NEGATIVE_VALUE, "sample/session counts must be non-negative")
        for name, value in (
            ("probability_intercept", self.probability_intercept),
            ("probability_slope", self.probability_slope),
            ("return_intercept", self.return_intercept),
            ("return_slope", self.return_slope),
            ("raw_brier", self.raw_brier),
            ("calibrated_brier", self.calibrated_brier),
        ):
            if not math.isfinite(value):
                raise ValidationError(ErrorCode.NON_FINITE_NUMBER, f"{name} must be finite")
        if self.orientation not in {"aligned", "inverted", "weak"}:
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "invalid orientation")

    def calibrate_probability(self, probability_up: float) -> float:
        require_probability(probability_up, "probability_up")
        return _sigmoid(self.probability_intercept + self.probability_slope * _logit(probability_up))

    def calibrate_expected_return(self, expected_return: float) -> float:
        if not math.isfinite(expected_return):
            raise ValidationError(ErrorCode.NON_FINITE_NUMBER, "expected_return must be finite")
        return self.return_intercept + self.return_slope * expected_return


def _correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 3 or np.std(left) <= 1e-12 or np.std(right) <= 1e-12:
        return None
    value = float(np.corrcoef(left, right)[0, 1])
    return value if math.isfinite(value) else None


def fit_witness_calibration(
    *,
    witness_name: str,
    horizon_minutes: int,
    observations: Iterable[WitnessObservation],
    as_of: datetime,
    minimum_samples: int = 500,
    minimum_sessions: int = 20,
    minimum_brier_improvement: float = 0.0025,
) -> WitnessCalibration:
    """Fit an unconstrained orientation calibration from prior matured outcomes.

    Observations whose realization is after ``as_of`` are rejected rather than
    dropped silently.  ``blend_eligible`` is deliberately conservative: enough
    independent sessions and samples are required *and* calibration must improve
    Brier score by a pre-registered amount.  A negative learned slope is legal.
    """

    require_tz_aware(as_of, "as_of")
    rows = tuple(observations)
    if not witness_name.strip():
        raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "witness_name is required")
    if horizon_minutes <= 0:
        raise ValidationError(ErrorCode.NEGATIVE_VALUE, "horizon_minutes must be positive")
    if any(row.realized_at > as_of for row in rows):
        raise ValidationError(
            ErrorCode.LOOKAHEAD_ATTEMPT,
            "witness calibration received an outcome unavailable at as_of",
        )
    if not rows:
        raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "no matured observations")

    x_prob = np.asarray([_logit(row.probability_up) for row in rows], dtype=float).reshape(-1, 1)
    y_up = np.asarray([1 if row.realized_up else 0 for row in rows], dtype=int)
    raw_prob = np.asarray([row.probability_up for row in rows], dtype=float)

    if len(set(int(value) for value in y_up)) < 2:
        probability_intercept = _logit(float(np.mean(y_up)))
        probability_slope = 0.0
        calibrated_prob = np.full(len(rows), float(np.mean(y_up)), dtype=float)
    else:
        classifier = LogisticRegression(
            C=1.0,
            penalty="l2",
            solver="lbfgs",
            max_iter=1000,
            random_state=86,
        )
        classifier.fit(x_prob, y_up)
        probability_intercept = float(classifier.intercept_[0])
        probability_slope = float(classifier.coef_[0, 0])
        calibrated_prob = classifier.predict_proba(x_prob)[:, 1]

    x_return = np.asarray([row.expected_return for row in rows], dtype=float).reshape(-1, 1)
    y_return = np.asarray([row.realized_return for row in rows], dtype=float)
    regressor = Ridge(alpha=1.0, fit_intercept=True)
    regressor.fit(x_return, y_return)
    return_intercept = float(regressor.intercept_)
    return_slope = float(regressor.coef_[0])
    calibrated_return = regressor.predict(x_return)

    raw_brier = float(np.mean((raw_prob - y_up) ** 2))
    calibrated_brier = float(np.mean((calibrated_prob - y_up) ** 2))
    raw_return_corr = _correlation(x_return[:, 0], y_return)
    calibrated_return_corr = _correlation(calibrated_return, y_return)

    if probability_slope < -0.05 or return_slope < -0.05:
        orientation = "inverted"
    elif probability_slope > 0.05 or return_slope > 0.05:
        orientation = "aligned"
    else:
        orientation = "weak"

    session_count = len({row.session_date for row in rows})
    brier_improvement = raw_brier - calibrated_brier
    blend_eligible = (
        len(rows) >= minimum_samples
        and session_count >= minimum_sessions
        and brier_improvement >= minimum_brier_improvement
        and calibrated_brier < 0.25
    )

    return WitnessCalibration(
        witness_name=witness_name,
        horizon_minutes=horizon_minutes,
        fitted_through=as_of,
        sample_count=len(rows),
        session_count=session_count,
        probability_intercept=probability_intercept,
        probability_slope=probability_slope,
        return_intercept=return_intercept,
        return_slope=return_slope,
        raw_brier=raw_brier,
        calibrated_brier=calibrated_brier,
        raw_return_correlation=raw_return_corr,
        calibrated_return_correlation=calibrated_return_corr,
        orientation=orientation,
        blend_eligible=blend_eligible,
    )
