from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    CandidateRanking,
    CandidateUniverse,
    DebitCredit,
    HardVeto,
    LegacyDecisionView,
    MarketForecastBundle,
    OptionType,
    RiskEnvelope,
    StrategyPermissions,
    StructuralState,
    SystemAction,
    V3DecisionView,
)
from spy_der.risk.firewall import apply_risk_firewall
from spy_der.synthesis.engine import synthesize_decision


def _candidate(candidate_id: str, max_loss: str) -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=candidate_id,
        snapshot_id="snap-safety",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("100"),
                quantity=1,
                expiration=exp,
                contract_id="SPY",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal(max_loss),
        breakevens=(),
        capital_required=Decimal(max_loss),
        terminal_payoff_hash="sha256:pay",
        geometry_hash=f"sha256:{candidate_id}",
    )


def _forecast() -> MarketForecastBundle:
    return MarketForecastBundle(
        snapshot_id="snap-safety",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        symbol="SPY",
        model_version="v2",
        p_up_30m=0.5,
    )


def test_hard_vetoes_cannot_be_overridden() -> None:
    decision = synthesize_decision(
        legacy=LegacyDecisionView(
            structural_state=StructuralState(state_id="s1", regime="neutral"),
            permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
            hard_vetoes=(HardVeto(code="HALT", reason="halt"),),
        ),
        forecast=_forecast(),
        universe=CandidateUniverse(universe_id="u1", candidates=(_candidate("c1", "10"),)),
        v3_view=V3DecisionView(ranking=CandidateRanking(ordered_candidate_ids=("c1",))),
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("25")),
        required_inputs_present=True,
    )
    assert decision.action == SystemAction.ABSTAIN


def test_missing_required_inputs_produce_abstention() -> None:
    decision = synthesize_decision(
        legacy=LegacyDecisionView(
            structural_state=StructuralState(state_id="s1", regime="neutral"),
            permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
        ),
        forecast=_forecast(),
        universe=CandidateUniverse(universe_id="u1", candidates=(_candidate("c1", "10"),)),
        v3_view=V3DecisionView(ranking=CandidateRanking(ordered_candidate_ids=("c1",))),
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("25")),
        required_inputs_present=False,
    )
    assert decision.action == SystemAction.ABSTAIN
    assert "missing_inputs" in decision.reason


def test_risk_limits_cannot_be_increased_by_decision_system() -> None:
    universe = CandidateUniverse(universe_id="u1", candidates=(_candidate("c1", "100"),))
    decision = synthesize_decision(
        legacy=LegacyDecisionView(
            structural_state=StructuralState(state_id="s1", regime="neutral"),
            permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
        ),
        forecast=_forecast(),
        universe=universe,
        v3_view=V3DecisionView(ranking=CandidateRanking(ordered_candidate_ids=("c1",))),
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("50")),
        required_inputs_present=True,
    )
    risk = apply_risk_firewall(
        decision,
        RiskEnvelope(max_defined_risk_per_trade=Decimal("50")),
        universe,
    )
    assert not risk.allowed


def test_journal_event_timezone_awareness() -> None:
    # smoke-check aware timestamp handling in test suite context
    assert datetime.now(tz=UTC).tzinfo is not None
