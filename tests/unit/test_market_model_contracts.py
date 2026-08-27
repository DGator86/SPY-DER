from __future__ import annotations

from dataclasses import fields

import pytest

from spy_der.contracts.common import ValidationError
from spy_der.contracts.forecasts import MarketForecastBundle
from spy_der.contracts.market_model import (
    PROHIBITED_MODEL_FIELD_TOKENS,
    MarketState,
    MarketStateAxis,
    RegimeLifecycleForecast,
    RegimePosterior,
    RegimeProbability,
)


def _posterior() -> RegimePosterior:
    return RegimePosterior(
        market_state_id="mstate-1",
        probabilities=(
            RegimeProbability("COMPRESSION", 0.70),
            RegimeProbability("TRANSITION", 0.20),
            RegimeProbability("VOLATILITY_EXPANSION", 0.10),
        ),
        current_age_minutes=17.0,
    )


def test_missing_direction_remains_missing() -> None:
    bundle = MarketForecastBundle(
        snapshot_id="snap-1",
        ts="2026-08-27T14:30:00-04:00",
        session_date="2026-08-27",
    )
    assert bundle.prob_up is None
    assert bundle.prob_down is None


def test_market_state_identity_is_deterministic() -> None:
    kwargs = {
        "snapshot_id": "snap-1",
        "measurement_bundle_id": "measure-1",
        "ts": "2026-08-27T14:30:00-04:00",
        "axes": (
            MarketStateAxis("trend", 0.62, confidence=0.80, support=11),
            MarketStateAxis("breadth_participation", 0.57, confidence=0.90, support=498),
        ),
        "data_quality": 0.95,
    }
    first = MarketState(**kwargs)
    second = MarketState(**kwargs)
    assert first.state_id == second.state_id
    assert first.axis("trend") == 0.62
    assert first.axis("not_present") is None


def test_regime_posterior_requires_normalized_probability_vector() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        RegimePosterior(
            market_state_id="mstate-1",
            probabilities=(
                RegimeProbability("COMPRESSION", 0.8),
                RegimeProbability("TRANSITION", 0.3),
            ),
        )


def test_regime_posterior_exposes_uncertainty_without_collapsing_vector() -> None:
    posterior = _posterior()
    assert posterior.dominant_regime == "COMPRESSION"
    assert posterior.dominant_probability == 0.70
    assert posterior.normalized_entropy is not None
    assert 0.0 < posterior.normalized_entropy < 1.0


def test_survival_curve_must_be_monotone_nonincreasing() -> None:
    posterior = _posterior()
    with pytest.raises(ValidationError, match="cannot increase"):
        RegimeLifecycleForecast(
            regime_posterior_id=posterior.posterior_id,
            current_regime="COMPRESSION",
            survival_probabilities=((5, 0.70), (15, 0.80)),
        )


def test_lifecycle_contract_keeps_persistence_transition_and_timing_together() -> None:
    posterior = _posterior()
    lifecycle = RegimeLifecycleForecast(
        regime_posterior_id=posterior.posterior_id,
        current_regime="COMPRESSION",
        survival_probabilities=((5, 0.91), (15, 0.76), (30, 0.48)),
        next_regime_probabilities=(
            RegimeProbability("VOLATILITY_EXPANSION", 0.61),
            RegimeProbability("MEAN_REVERTING_AUCTION", 0.24),
            RegimeProbability("QUIET_BULLISH_TREND", 0.15),
        ),
        conditional_p_up_if_transition=0.57,
        expected_remaining_minutes=24.0,
        median_transition_minutes=22.0,
        transition_q25_minutes=13.0,
        transition_q75_minutes=37.0,
        calibration_version="cal-2026-08",
    )
    assert lifecycle.survival_probability(15) == 0.76
    assert lifecycle.survival_probability(60) is None
    assert lifecycle.lifecycle_forecast_id.startswith("life-")


def test_market_model_contracts_do_not_admit_trading_fields() -> None:
    model_types = (MarketState, RegimePosterior, RegimeLifecycleForecast)
    for model_type in model_types:
        names = {item.name.lower() for item in fields(model_type)}
        for name in names:
            assert not any(token in name for token in PROHIBITED_MODEL_FIELD_TOKENS)
