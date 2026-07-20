from __future__ import annotations

from spy_der.contracts import (
    CandidateUniverse,
    LegacyDecisionView,
    MarketForecastBundle,
    RiskEnvelope,
    SystemDecision,
    V3DecisionView,
)
from spy_der.contracts.policies import PolicyMode
from spy_der.synthesis.deterministic import synthesize_from_policies


def synthesize_decision(
    *,
    legacy: LegacyDecisionView,
    forecast: MarketForecastBundle,
    universe: CandidateUniverse,
    v3_view: V3DecisionView,
    envelope: RiskEnvelope,
    required_inputs_present: bool,
) -> SystemDecision:
    """Synthesize via Phase 9 policy ensemble (Legacy-authoritative shadow mode)."""
    return synthesize_from_policies(
        legacy=legacy,
        forecast=forecast,
        universe=universe,
        v3_view=v3_view,
        envelope=envelope,
        required_inputs_present=required_inputs_present,
        mode=PolicyMode.SHADOW,
    )
