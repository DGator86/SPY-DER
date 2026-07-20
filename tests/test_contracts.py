from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from spy_der.contracts import (
    Candidate,
    MarketForecastBundle,
    OptionLeg,
    to_canonical_json,
)


def test_contract_serialization_is_deterministic() -> None:
    bundle = MarketForecastBundle(model_version="v1", prob_up=0.6, prob_down=0.4)
    assert to_canonical_json(bundle) == to_canonical_json(bundle)


def test_invalid_probabilities_rejected() -> None:
    with pytest.raises(ValueError):
        MarketForecastBundle(model_version="v1", prob_up=1.2, prob_down=0.0)


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
        candidate.legs = ()
