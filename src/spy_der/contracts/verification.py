"""Alpha V2 forecast-verification contracts.

Verification is intentionally independent of whether the Trader acted on a
forecast.  Every matured market forecast can therefore be scored, including
forecasts that produced WAIT, NO_EDGE, or ABSTAIN downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spy_der.contracts.common import (
    SCHEMA_VERSION,
    ErrorCode,
    ValidationError,
    deterministic_id,
    require_finite,
    require_non_negative,
    require_probability,
)

FORECAST_VERIFICATION_VERSION = "alpha-v2-forecast-verification.v1"

__all__ = [
    "FORECAST_VERIFICATION_VERSION",
    "ForecastMaturityStatus",
    "MarketForecastVerification",
    "MarketOutcome",
]


class ForecastMaturityStatus(StrEnum):
    PENDING = "pending"
    MATURED = "matured"
    UNAVAILABLE = "unavailable"


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValidationError(
            ErrorCode.MISSING_REQUIRED_INPUT,
            f"'{field_name}' is required",
        )


@dataclass(frozen=True, slots=True)
class MarketOutcome:
    """Realized market path for one forecast horizon, independent of trading."""

    forecast_ts: str
    maturity_ts: str
    horizon_minutes: int
    start_price: float
    end_price: float
    realized_log_return: float
    realized_move: float
    realized_mfe: float | None = None
    realized_mae: float | None = None
    regime_survived: bool | None = None
    realized_next_regime: str | None = None
    transition_minutes: float | None = None
    data_quality: float | None = None
    outcome_id: str = ""

    def __post_init__(self) -> None:
        _required(self.forecast_ts, "MarketOutcome.forecast_ts")
        _required(self.maturity_ts, "MarketOutcome.maturity_ts")
        if self.horizon_minutes <= 0:
            raise ValidationError(
                ErrorCode.NEGATIVE_VALUE,
                "MarketOutcome.horizon_minutes must be positive",
            )
        for field_name in (
            "start_price",
            "end_price",
            "realized_log_return",
            "realized_move",
        ):
            require_finite(getattr(self, field_name), f"MarketOutcome.{field_name}")
        if self.start_price <= 0.0 or self.end_price <= 0.0:
            raise ValidationError(
                ErrorCode.NON_POSITIVE_PRICE,
                "MarketOutcome prices must be positive",
            )
        for field_name in ("realized_mfe", "realized_mae", "transition_minutes"):
            value = getattr(self, field_name)
            if value is not None:
                require_finite(value, f"MarketOutcome.{field_name}")
        if self.transition_minutes is not None:
            require_non_negative(self.transition_minutes, "MarketOutcome.transition_minutes")
        if self.data_quality is not None:
            require_probability(self.data_quality, "MarketOutcome.data_quality")
        if not self.outcome_id:
            object.__setattr__(
                self,
                "outcome_id",
                deterministic_id(
                    "mktout",
                    self.forecast_ts,
                    self.maturity_ts,
                    self.horizon_minutes,
                    self.start_price,
                    self.end_price,
                    self.realized_log_return,
                    self.realized_move,
                    self.realized_mfe,
                    self.realized_mae,
                    self.regime_survived,
                    self.realized_next_regime,
                    self.transition_minutes,
                ),
            )


@dataclass(frozen=True, slots=True)
class MarketForecastVerification:
    """A matured comparison between one frozen market forecast and reality."""

    forecast_id: str
    snapshot_id: str
    horizon: str
    maturity_status: ForecastMaturityStatus
    outcome_id: str | None = None
    p_up: float | None = None
    expected_return: float | None = None
    return_q10: float | None = None
    return_q50: float | None = None
    return_q90: float | None = None
    predicted_regime_survival: float | None = None
    predicted_next_regime: str | None = None
    predicted_next_regime_probability: float | None = None
    predicted_median_transition_minutes: float | None = None
    realized_up: bool | None = None
    realized_log_return: float | None = None
    realized_regime_survival: bool | None = None
    realized_next_regime: str | None = None
    realized_transition_minutes: float | None = None
    brier_direction: float | None = None
    brier_regime_survival: float | None = None
    direction_correct: bool | None = None
    next_regime_correct: bool | None = None
    verification_version: str = FORECAST_VERIFICATION_VERSION
    schema_version: str = SCHEMA_VERSION
    verification_id: str = ""

    def __post_init__(self) -> None:
        _required(self.forecast_id, "MarketForecastVerification.forecast_id")
        _required(self.snapshot_id, "MarketForecastVerification.snapshot_id")
        _required(self.horizon, "MarketForecastVerification.horizon")
        for field_name in (
            "p_up",
            "predicted_regime_survival",
            "predicted_next_regime_probability",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_probability(value, f"MarketForecastVerification.{field_name}")
        for field_name in (
            "expected_return",
            "return_q10",
            "return_q50",
            "return_q90",
            "realized_log_return",
            "realized_transition_minutes",
            "brier_direction",
            "brier_regime_survival",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_finite(value, f"MarketForecastVerification.{field_name}")
        if self.predicted_median_transition_minutes is not None:
            require_non_negative(
                self.predicted_median_transition_minutes,
                "MarketForecastVerification.predicted_median_transition_minutes",
            )
        if self.realized_transition_minutes is not None:
            require_non_negative(
                self.realized_transition_minutes,
                "MarketForecastVerification.realized_transition_minutes",
            )
        if self.brier_direction is not None:
            require_probability(self.brier_direction, "MarketForecastVerification.brier_direction")
        if self.brier_regime_survival is not None:
            require_probability(
                self.brier_regime_survival,
                "MarketForecastVerification.brier_regime_survival",
            )
        if (
            self.return_q10 is not None
            and self.return_q50 is not None
            and self.return_q10 > self.return_q50
        ):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "return_q10 cannot exceed return_q50",
            )
        if (
            self.return_q50 is not None
            and self.return_q90 is not None
            and self.return_q50 > self.return_q90
        ):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "return_q50 cannot exceed return_q90",
            )
        if self.maturity_status == ForecastMaturityStatus.MATURED and not self.outcome_id:
            raise ValidationError(
                ErrorCode.MISSING_REQUIRED_INPUT,
                "matured verification requires outcome_id",
            )
        if self.maturity_status == ForecastMaturityStatus.PENDING and self.outcome_id is not None:
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "pending verification cannot reference a realized outcome",
            )
        if not self.verification_id:
            object.__setattr__(
                self,
                "verification_id",
                deterministic_id(
                    "verify",
                    self.forecast_id,
                    self.snapshot_id,
                    self.horizon,
                    self.maturity_status.value,
                    self.outcome_id,
                    self.verification_version,
                ),
            )
