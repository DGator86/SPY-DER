from __future__ import annotations

from spy_der.contracts import (
    CandidateUniverse,
    RiskDecision,
    RiskEnvelope,
    SystemAction,
    SystemDecision,
)


def apply_risk_firewall(
    decision: SystemDecision,
    envelope: RiskEnvelope,
    universe: CandidateUniverse,
) -> RiskDecision:
    if (
        decision.action is not SystemAction.SELECT_CANDIDATE
        or decision.selected_candidate_id is None
    ):
        return RiskDecision(allowed=False, reason="no executable order intent")

    candidate = next(
        (c for c in universe.candidates if c.candidate_id == decision.selected_candidate_id),
        None,
    )
    if candidate is None:
        return RiskDecision(allowed=False, reason="candidate not found in approved universe")

    if candidate.max_loss is None or candidate.max_loss > envelope.max_defined_risk_per_trade:
        return RiskDecision(allowed=False, reason="risk envelope exceeded")

    return RiskDecision(allowed=True, reason="within deterministic risk envelope")
