from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    DebitCredit,
    MarketForecastBundle,
    OptionType,
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


def _candidate(*, max_loss: Decimal | None = Decimal("5"), stock: bool = False) -> Candidate:
    exp = date(2026, 1, 1)
    return Candidate(
        candidate_id="c1",
        snapshot_id="snap-1",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("500"),
                quantity=1,
                expiration=exp,
                contract_id="SPY240101C00500000",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=max_loss if max_loss is not None else Decimal("0"),  # overwritten below
        breakevens=(),
        capital_required=Decimal("5"),
        terminal_payoff_hash="sha256:pay",
        geometry_hash="sha256:geom",
        requires_stock_ownership=stock,
    )


def test_contract_serialization_is_deterministic() -> None:
    bundle = _bundle()
    assert to_canonical_json(bundle) == to_canonical_json(bundle)


def test_invalid_probabilities_rejected() -> None:
    with pytest.raises(ValidationError):
        _bundle(p_up_30m=1.2)


def test_undefined_risk_candidate_rejected() -> None:
    exp = date(2026, 1, 1)
    with pytest.raises(ValueError, match="max_loss must be defined"):
        Candidate(
            candidate_id="c1",
            snapshot_id="s",
            family="long_call",
            direction="bullish",
            expiration=exp,
            legs=(
                CandidateLeg(
                    option_type=OptionType.CALL,
                    strike=Decimal("100"),
                    quantity=1,
                    expiration=exp,
                ),
            ),
            entry_type=DebitCredit.DEBIT,
            maximum_profit=None,
            maximum_loss=None,  # type: ignore[arg-type]
            breakevens=(),
            capital_required=Decimal("0"),
            terminal_payoff_hash="sha256:x",
            geometry_hash="sha256:y",
        )


def test_negative_max_loss_rejected() -> None:
    exp = date(2026, 1, 1)
    with pytest.raises(ValueError, match="non-negative"):
        Candidate(
            candidate_id="c1",
            snapshot_id="s",
            family="long_call",
            direction="bullish",
            expiration=exp,
            legs=(
                CandidateLeg(
                    option_type=OptionType.CALL,
                    strike=Decimal("100"),
                    quantity=1,
                    expiration=exp,
                ),
            ),
            entry_type=DebitCredit.DEBIT,
            maximum_profit=None,
            maximum_loss=Decimal("-1"),
            breakevens=(),
            capital_required=Decimal("0"),
            terminal_payoff_hash="sha256:x",
            geometry_hash="sha256:y",
        )


def test_stock_dependent_candidate_rejected() -> None:
    with pytest.raises(ValueError, match="stock-dependent"):
        _candidate(stock=True)


def test_option_legs_immutable_after_creation() -> None:
    candidate = _candidate()
    with pytest.raises(FrozenInstanceError):
        candidate.legs = ()  # type: ignore[misc]


def test_forecast_bundle_prob_aliases() -> None:
    bundle = _bundle(p_up_30m=0.6)
    assert bundle.prob_up == 0.6
    assert bundle.prob_down == pytest.approx(0.4)
    assert bundle.forecast_id.startswith("fcst-")
    assert bundle.content_hash.startswith("sha256:")
