"""Causal orientation calibration and promotion evidence for forecast witnesses.

A witness is allowed to be anti-oriented. Calibration learns that relationship
from *matured historical forecasts only* rather than hard-coding an inversion.
The caller owns the as-of cut: observations supplied here must already satisfy
``realized_at <= forecast_as_of`` for the forecast being produced.

Training a calibrator never grants blend authority. A separate, later held-out
sample must demonstrate improvement before a witness can receive non-zero
ensemble weight. This module has no knowledge of candidates, trades, fills or
P&L.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sklearn.linear_model import LogisticRegression

from spy_der.contracts.common import (
    ErrorCode,
    ValidationError,
    require_probability,
    require_tz_aware,
)

ORIENTATION_CALIBRATION_VERSION = "alpha-v2-witness-orientation.v2"

__all__ = [
    "ORIENTATION_CALIBRATION_VERSION",
    "WitnessCalibration",
    "WitnessCalibrationEvidence",
    "WitnessObservation",
    "evaluate_witness_calibration",
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
    """Frozen calibration learned only from historical matured observations."""

    witness_name: str
    horizon_minutes: int
    fitted_through: datetime
    sample_count: int
    session_count: int
    probability_intercept: float
    probability_slope: float
    return_intercept: float
    return_slope: float
    raw_brier_training: float
    calibrated_brier_training: float
    raw_return_correlation_training: float | None
    calibrated_return_correlation_training: float | None
    orientation: str
    version: str = ORIENTATION_CALIBRATION_VERSION

    def __post_init__(self) -> None:
        require_tz_aware(self.fitted_through, "WitnessCalibration.fitted_through")
        if self.horizon_minutes <= 0:
            raise ValidationError(ErrorCode.NEGATIVE_VALUE, "horizon_minutes must be positive")
        if self.sample_count < 0 or self.session_count < 0:
            raise ValidationError(
                ErrorCode.NEGATIVE_VALUE,
                "sample/session counts must be non-negative",
            )
        numeric = (
            ("probability_intercept", self.probability_intercept),
            ("probability_slope", self.probability_slope),
            ("return_intercept", self.return_intercept),
            ("return_slope", self.return_slope),
            ("raw_brier_training", self.raw_brier_training),
            ("calibrated_brier_training", self.calibrated_brier_training),
        )
        for name, value in numeric:
            if not math.isfinite(value):
                raise ValidationError(ErrorCode.NON_FINITE_NUMBER, f"{name} must be finite")
        if self.orientation not in {"aligned", "inverted", "weak"}:
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "invalid orientation")

    def calibrate_probability(self, probability_up: float) -> float:
        require_probability(probability_up, "probability_up")
        linear = self.probability_intercept + self.probability_slope * _logit(probability_up)
        return _sigmoid(linear)

    def calibrate_expected_return(self, expected_return: float) -> float:
        if not math.isfinite(expected_return):
            raise ValidationError(ErrorCode.NON_FINITE_NUMBER, "expected_return must be finite")
        return self.return_intercept + self.return_slope * expected_return


@dataclass(frozen=True, slots=True)
class WitnessCalibrationEvidence:
    """Later-session evidence that decides whether a calibration may be blended."""

    witness_name: str
    horizon_minutes: int
    calibration_version: str
    validation_samples: int
    validation_sessions: int
    raw_brier: float
    calibrated_brier: float
    calibrated_accuracy: float
    brier_improvement: float
    blend_eligible: bool

    def __post_init__(self) -> None:
        for name, value in (
            ("raw_brier", self.raw_brier),
            ("calibrated_brier", self.calibrated_brier),
            ("calibrated_accuracy", self.calibrated_accuracy),
            ("brier_improvement", self.brier_improvement),
        ):
            if not math.isfinite(value):
                raise ValidationError(ErrorCode.NON_FINITE_NUMBER, f"{name} must be finite")
        require_probability(self.calibrated_accuracy, "calibrated_accuracy")


def _correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 3 or np.std(left) <= 1e-12 or np.std(right) <= 1e-12:
        return None
    value = float(np.corrcoef(left, right)[0, 1])
    return value if math.isfinite(value) else None


def _validate_as_of(rows: tuple[WitnessObservation, ...], as_of: datetime) -> None:
    require_tz_aware(as_of, "as_of")
    if any(row.realized_at > as_of for row in rows):
        raise ValidationError(
            ErrorCode.MALFORMED_RECORD,
            "witness calibration received an outcome unavailable at as_of",
        )


def fit_witness_calibration(
    *,
    witness_name: str,
    horizon_minutes: int,
    observations: Iterable[WitnessObservation],
    as_of: datetime,
) -> WitnessCalibration:
    """Fit an unconstrained orientation transform from prior matured outcomes.

    A negative learned slope is legal and diagnostic. The returned object has no
    authority flag; only :func:`evaluate_witness_calibration` can qualify it on
    a later held-out sample.
    """

    rows = tuple(observations)
    _validate_as_of(rows, as_of)
    if not witness_name.strip():
        raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "witness_name is required")
    if horizon_minutes <= 0:
        raise ValidationError(ErrorCode.NEGATIVE_VALUE, "horizon_minutes must be positive")
    if not rows:
        raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "no matured observations")

    x_prob = np.asarray([_logit(row.probability_up) for row in rows], dtype=float).reshape(-1, 1)
    y_up = np.asarray([1 if row.realized_up else 0 for row in rows], dtype=int)
    raw_prob = np.asarray([row.probability_up for row in rows], dtype=float)

    if len(set(int(value) for value in y_up)) < 2:
        base_rate = float(np.mean(y_up))
        probability_intercept = _logit(base_rate)
        probability_slope = 0.0
        calibrated_prob = np.full(len(rows), base_rate, dtype=float)
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

    x_return = np.asarray([row.expected_return for row in rows], dtype=float)
    y_return = np.asarray([row.realized_return for row in rows], dtype=float)
    x_mean = float(np.mean(x_return))
    y_mean = float(np.mean(y_return))
    centered = x_return - x_mean
    denominator = float(np.dot(centered, centered)) + 1e-12
    return_slope = float(np.dot(centered, y_return - y_mean) / denominator)
    return_intercept = y_mean - return_slope * x_mean
    calibrated_return = return_intercept + return_slope * x_return

    raw_brier = float(np.mean((raw_prob - y_up) ** 2))
    calibrated_brier = float(np.mean((calibrated_prob - y_up) ** 2))
    raw_return_corr = _correlation(x_return, y_return)
    calibrated_return_corr = _correlation(calibrated_return, y_return)

    if probability_slope < -0.05 or return_slope < -0.05:
        orientation = "inverted"
    elif probability_slope > 0.05 or return_slope > 0.05:
        orientation = "aligned"
    else:
        orientation = "weak"

    return WitnessCalibration(
        witness_name=witness_name,
        horizon_minutes=horizon_minutes,
        fitted_through=as_of,
        sample_count=len(rows),
        session_count=len({row.session_date for row in rows}),
        probability_intercept=probability_intercept,
        probability_slope=probability_slope,
        return_intercept=return_intercept,
        return_slope=return_slope,
        raw_brier_training=raw_brier,
        calibrated_brier_training=calibrated_brier,
        raw_return_correlation_training=raw_return_corr,
        calibrated_return_correlation_training=calibrated_return_corr,
        orientation=orientation,
    )


def evaluate_witness_calibration(
    calibration: WitnessCalibration,
    observations: Iterable[WitnessObservation],
    *,
    as_of: datetime,
    minimum_samples: int = 250,
    minimum_sessions: int = 10,
    maximum_brier: float = 0.245,
    minimum_accuracy: float = 0.53,
    minimum_brier_improvement: float = 0.0025,
) -> WitnessCalibrationEvidence:
    """Evaluate a frozen calibration on later, held-out matured observations."""

    rows = tuple(observations)
    _validate_as_of(rows, as_of)
    if not rows:
        raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "no validation observations")
    if any(row.forecast_at <= calibration.fitted_through for row in rows):
        raise ValidationError(
            ErrorCode.MALFORMED_RECORD,
            "validation observations must be later than the calibration fit window",
        )

    raw = np.asarray([row.probability_up for row in rows], dtype=float)
    calibrated = np.asarray(
        [calibration.calibrate_probability(row.probability_up) for row in rows],
        dtype=float,
    )
    actual = np.asarray([1 if row.realized_up else 0 for row in rows], dtype=int)
    raw_brier = float(np.mean((raw - actual) ** 2))
    calibrated_brier = float(np.mean((calibrated - actual) ** 2))
    accuracy = float(np.mean((calibrated >= 0.5) == actual))
    improvement = raw_brier - calibrated_brier
    sessions = len({row.session_date for row in rows})
    eligible = (
        len(rows) >= minimum_samples
        and sessions >= minimum_sessions
        and calibrated_brier <= maximum_brier
        and accuracy >= minimum_accuracy
        and improvement >= minimum_brier_improvement
    )
    return WitnessCalibrationEvidence(
        witness_name=calibration.witness_name,
        horizon_minutes=calibration.horizon_minutes,
        calibration_version=calibration.version,
        validation_samples=len(rows),
        validation_sessions=sessions,
        raw_brier=raw_brier,
        calibrated_brier=calibrated_brier,
        calibrated_accuracy=accuracy,
        brier_improvement=improvement,
        blend_eligible=eligible,
    )
