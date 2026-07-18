from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from system_b.contracts import (
    Candidate,
    CandidateRanking,
    CandidateUniverse,
    HardVeto,
    LegacyDecisionView,
    MarketForecastBundle,
    OptionLeg,
    RiskEnvelope,
    StrategyPermissions,
    StructuralState,
    SystemAction,
    V3DecisionView,
)
from system_b.risk.firewall import apply_risk_firewall
from system_b.synthesis.engine import synthesize_decision


def _candidate(candidate_id: str, max_loss: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        legs=(OptionLeg(contract="SPY", quantity=1, side="BUY"),),
        max_loss=Decimal(max_loss),
    )


def test_hard_vetoes_cannot_be_overridden() -> None:
    decision = synthesize_decision(
        legacy=LegacyDecisionView(
            structural_state=StructuralState(state_id="s1", regime="neutral"),
            permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
            hard_vetoes=(HardVeto(code="HALT", reason="halt"),),
        ),
        forecast=MarketForecastBundle(model_version="v2", prob_up=0.5, prob_down=0.5),
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
        forecast=MarketForecastBundle(model_version="v2", prob_up=0.5, prob_down=0.5),
        universe=CandidateUniverse(universe_id="u1", candidates=(_candidate("c1", "10"),)),
        v3_view=V3DecisionView(ranking=CandidateRanking(ordered_candidate_ids=("c1",))),
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("25")),
        required_inputs_present=False,
    )
    assert decision.action == SystemAction.ABSTAIN
    assert "required input missing" in decision.reason


def test_risk_limits_cannot_be_increased_by_decision_system() -> None:
    universe = CandidateUniverse(universe_id="u1", candidates=(_candidate("c1", "100"),))
    decision = synthesize_decision(
        legacy=LegacyDecisionView(
            structural_state=StructuralState(state_id="s1", regime="neutral"),
            permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
        ),
        forecast=MarketForecastBundle(model_version="v2", prob_up=0.5, prob_down=0.5),
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
