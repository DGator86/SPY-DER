from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from spy_der.contracts import (
    Candidate,
    MarketForecastBundle,
    OptionLeg,
    ValidationError,
    to_canonical_json,
)


def _bundle(**kwargs: object) -> MarketForecastBundle:
    base = {
        "snapshot_id": "snap-1",
        "ts": "2026-01-05T10:30:00-05:00",
        "session_date": "2026-01-05",
        "symbol": "SPY",
        "model_version": "v1",
        "p_up_30m": 0.6,
    }
    base.update(kwargs)
    return MarketForecastBundle(**base)  # type: ignore[arg-type]


def test_contract_serialization_is_deterministic() -> None:
    bundle = _bundle()
    assert to_canonical_json(bundle) == to_canonical_json(bundle)


def test_invalid_probabilities_rejected() -> None:
    with pytest.raises(ValidationError):
        _bundle(p_up_30m=1.2)


def test_undefined_risk_candidate_rejected() -> None:
    with pytest.raises(ValueError):
        Candidate(candidate_id="c1", legs=(), max_loss=None)


def test_stock_dependent_candidate_rejected() -> None:
    with pytest.raises(ValueError):
        Candidate(candidate_id="c1", legs=(), max_loss=Decimal("10"), requires_stock_ownership=True)


def test_option_legs_immutable_after_creation() -> None:
    candidate = Candidate(
        candidate_id="c1",
        legs=(OptionLeg(contract="SPY240101C00500000", quantity=1, side="BUY"),),
        max_loss=Decimal("5"),
    )
    with pytest.raises(FrozenInstanceError):
        candidate.legs = ()  # type: ignore[misc]


def test_forecast_bundle_prob_aliases() -> None:
    bundle = _bundle(p_up_30m=0.6)
    assert bundle.prob_up == 0.6
    assert bundle.prob_down == pytest.approx(0.4)
    assert bundle.forecast_id.startswith("fcst-")
    assert bundle.content_hash.startswith("sha256:")
