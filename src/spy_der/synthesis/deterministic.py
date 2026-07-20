"""Deterministic decision agent / synthesis (spec §36 Phase 9)."""

from __future__ import annotations

from spy_der.contracts import (
    CandidateUniverse,
    LegacyDecisionView,
    MarketForecastBundle,
    RiskEnvelope,
    SystemAction,
    SystemDecision,
    V3DecisionView,
)
from spy_der.contracts.policies import (
    PolicyAction,
    PolicyDecisionView,
    PolicyDisagreement,
    PolicyInputPacket,
    PolicyMode,
)
from spy_der.policies.disagreement import compute_policy_disagreement
from spy_der.policies.ensemble import EnsemblePolicy, EnsemblePolicyConfig

__all__ = [
    "DeterministicDecisionAgent",
    "build_policy_packet",
    "synthesize_from_policies",
]


def build_policy_packet(
    *,
    snapshot_id: str,
    legacy: object,
    forecast: object | None,
    universe: object | None,
    envelope: object | None,
    ranking: object | None = None,
    economics: tuple[object, ...] = (),
    value_forecasts: tuple[object, ...] = (),
    meta_decision: object | None = None,
    required_inputs_present: bool = True,
) -> PolicyInputPacket:
    return PolicyInputPacket(
        snapshot_id=snapshot_id,
        legacy_view=legacy,
        market_forecast=forecast,
        candidate_universe=universe,
        economics=economics,
        value_forecasts=value_forecasts,
        ranking=ranking,
        risk_envelope=envelope,
        meta_decision=meta_decision,
        required_inputs_present=required_inputs_present,
    )


def _to_system_action(action: PolicyAction) -> SystemAction:
    if action is PolicyAction.SELECT_CANDIDATE:
        return SystemAction.SELECT_CANDIDATE
    if action is PolicyAction.ABSTAIN:
        return SystemAction.ABSTAIN
    return SystemAction.ABSTAIN


class DeterministicDecisionAgent:
    """Policy-ensemble synthesis without AI dependency."""

    def __init__(self, cfg: EnsemblePolicyConfig | None = None) -> None:
        self.ensemble = EnsemblePolicy(cfg)

    def decide(
        self,
        packet: PolicyInputPacket,
    ) -> tuple[SystemDecision, tuple[PolicyDecisionView, ...], PolicyDisagreement]:
        views = self.ensemble.evaluate_all(packet)
        disagreement = compute_policy_disagreement(views)
        authoritative = self.ensemble.evaluate(packet)
        forecast = packet.market_forecast
        model_version = str(getattr(forecast, "model_version", "") or "")
        universe = packet.candidate_universe
        universe_id = str(getattr(universe, "universe_id", "") or "")
        decision = SystemDecision(
            action=_to_system_action(authoritative.action),
            selected_candidate_id=authoritative.candidate_id,
            reason=";".join(authoritative.reason_codes) or authoritative.action.value,
            forecast_model_version=model_version,
            candidate_universe_id=universe_id,
            veto_codes=authoritative.hard_vetoes,
        )
        # NO_EDGE maps to ABSTAIN at SystemDecision layer (Phase-0 action set).
        if authoritative.action is PolicyAction.NO_EDGE:
            decision = SystemDecision(
                action=SystemAction.ABSTAIN,
                selected_candidate_id=None,
                reason=";".join(authoritative.reason_codes) or "no_edge",
                forecast_model_version=model_version,
                candidate_universe_id=universe_id,
                veto_codes=authoritative.hard_vetoes,
            )
        return decision, views, disagreement


def synthesize_from_policies(
    *,
    legacy: LegacyDecisionView,
    forecast: MarketForecastBundle,
    universe: CandidateUniverse,
    v3_view: V3DecisionView,
    envelope: RiskEnvelope,
    required_inputs_present: bool,
    mode: PolicyMode = PolicyMode.SHADOW,
) -> SystemDecision:
    """Drop-in synthesis path using Phase 9 policy ensemble."""
    packet = build_policy_packet(
        snapshot_id=getattr(forecast, "snapshot_id", "") or "unknown",
        legacy=legacy,
        forecast=forecast,
        universe=universe,
        envelope=envelope,
        ranking=v3_view.ranking,
        required_inputs_present=required_inputs_present,
    )
    agent = DeterministicDecisionAgent(EnsemblePolicyConfig(mode=mode))
    decision, _views, _disagreement = agent.decide(packet)
    return decision
