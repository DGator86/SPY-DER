from __future__ import annotations

import pytest

from spy_der.contracts.common import ValidationError
from spy_der.contracts.verification import (
    ForecastMaturityStatus,
    MarketForecastVerification,
    MarketOutcome,
)


def _outcome() -> MarketOutcome:
    return MarketOutcome(
        forecast_ts="2026-08-27T14:30:00-04:00",
        maturity_ts="2026-08-27T15:00:00-04:00",
        horizon_minutes=30,
        start_price=650.0,
        end_price=650.65,
        realized_log_return=0.0009995,
        realized_move=0.001,
        realized_mfe=0.0018,
        realized_mae=-0.0004,
        regime_survived=False,
        realized_next_regime="VOLATILITY_EXPANSION",
        transition_minutes=22.0,
        data_quality=0.99,
    )


def test_market_outcome_identity_is_deterministic() -> None:
    first = _outcome()
    second = _outcome()
    assert first.outcome_id == second.outcome_id


def test_matured_verification_requires_market_outcome() -> None:
    with pytest.raises(ValidationError, match="requires outcome_id"):
        MarketForecastVerification(
            forecast_id="fcst-1",
            snapshot_id="snap-1",
            horizon="30m",
            maturity_status=ForecastMaturityStatus.MATURED,
        )


def test_pending_verification_cannot_include_realized_outcome() -> None:
    with pytest.raises(ValidationError, match="cannot reference"):
        MarketForecastVerification(
            forecast_id="fcst-1",
            snapshot_id="snap-1",
            horizon="30m",
            maturity_status=ForecastMaturityStatus.PENDING,
            outcome_id="mktout-1",
        )


def test_matured_verification_is_trade_independent() -> None:
    outcome = _outcome()
    verification = MarketForecastVerification(
        forecast_id="fcst-1",
        snapshot_id="snap-1",
        horizon="30m",
        maturity_status=ForecastMaturityStatus.MATURED,
        outcome_id=outcome.outcome_id,
        p_up=0.57,
        expected_return=0.0007,
        return_q10=-0.0011,
        return_q50=0.0003,
        return_q90=0.0016,
        predicted_regime_survival=0.48,
        predicted_next_regime="VOLATILITY_EXPANSION",
        predicted_next_regime_probability=0.61,
        predicted_median_transition_minutes=22.0,
        realized_up=True,
        realized_log_return=outcome.realized_log_return,
        realized_regime_survival=False,
        realized_next_regime=outcome.realized_next_regime,
        realized_transition_minutes=outcome.transition_minutes,
        brier_direction=(0.57 - 1.0) ** 2,
        brier_regime_survival=(0.48 - 0.0) ** 2,
        direction_correct=True,
        next_regime_correct=True,
    )
    assert verification.verification_id.startswith("verify-")
    assert verification.next_regime_correct is True


def test_return_quantiles_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="return_q10 cannot exceed return_q50"):
        MarketForecastVerification(
            forecast_id="fcst-1",
            snapshot_id="snap-1",
            horizon="30m",
            maturity_status=ForecastMaturityStatus.UNAVAILABLE,
            return_q10=0.1,
            return_q50=0.0,
        )
