from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from spy_der.forecasting.witnesses.beta import BetaWitnessError, parse_beta_state


def _state(now: datetime) -> dict:
    return {
        "status": "LIVE",
        "timestamp": now.isoformat(),
        "snapshot": {
            "timestamp": now.isoformat(),
            "factors": {
                "coverage_ratio": 0.98,
                "covered_weight": 0.97,
            },
            "forecasts": [
                {
                    "horizon_minutes": 5,
                    "probability_up": 0.54,
                    "expected_return_bps": 1.5,
                    "confidence": 0.62,
                    "model_ready": True,
                    "sample_count": 800,
                },
                {
                    "horizon_minutes": 15,
                    "probability_up": 0.61,
                    "expected_return_bps": 4.2,
                    "confidence": 0.73,
                    "model_ready": True,
                    "sample_count": 650,
                },
                {
                    "horizon_minutes": 30,
                    "probability_up": 0.47,
                    "expected_return_bps": -3.0,
                    "confidence": 0.66,
                    "model_ready": False,
                    "sample_count": 99,
                },
            ],
            "decision": {
                "action": "TRADE",
                "direction": "UP",
                "risk_multiplier": 2.0,
            },
        },
        "option_plan": {"structure": "CALL_DEBIT_SPREAD"},
        "ledger": {"realized_pnl": 9999.0},
    }


def test_beta_witness_parses_forecasts_and_converts_bps() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    witness = parse_beta_state(_state(now), as_of=now + timedelta(seconds=10))

    horizon = witness.horizon(15)
    assert horizon is not None
    assert horizon.probability_up == 0.61
    assert horizon.expected_return == pytest.approx(0.00042)
    assert witness.stale_seconds == 10.0
    assert witness.horizon(30) is None  # not model-ready


def test_beta_downstream_decision_and_pnl_cannot_change_witness() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    first = _state(now)
    second = deepcopy(first)
    second["snapshot"]["decision"] = {
        "action": "NO_TRADE",
        "direction": "DOWN",
        "risk_multiplier": 0.0,
    }
    second["option_plan"] = {"structure": "LONG_PUT"}
    second["ledger"] = {"realized_pnl": -9999.0}

    left = parse_beta_state(first, as_of=now)
    right = parse_beta_state(second, as_of=now)
    assert left == right


def test_beta_witness_rejects_stale_state() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    with pytest.raises(BetaWitnessError, match="stale"):
        parse_beta_state(
            _state(now - timedelta(minutes=3)),
            as_of=now,
            max_age_seconds=90,
        )


def test_beta_witness_rejects_future_state() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    with pytest.raises(BetaWitnessError, match="future"):
        parse_beta_state(_state(now + timedelta(seconds=20)), as_of=now)


def test_beta_witness_rejects_low_market_coverage() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    payload = _state(now)
    payload["snapshot"]["factors"]["coverage_ratio"] = 0.50
    with pytest.raises(BetaWitnessError, match="coverage ratio"):
        parse_beta_state(payload, as_of=now)


def test_beta_witness_rejects_invalid_probability() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    payload = _state(now)
    payload["snapshot"]["forecasts"][0]["probability_up"] = 1.2
    with pytest.raises(BetaWitnessError, match="probability_up"):
        parse_beta_state(payload, as_of=now)


def test_beta_witness_rejects_non_live_status() -> None:
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    payload = _state(now)
    payload["status"] = "DEGRADED"
    with pytest.raises(BetaWitnessError, match="not LIVE"):
        parse_beta_state(payload, as_of=now)
