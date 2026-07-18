from __future__ import annotations

from system_b.contracts import (
    CandidateUniverse,
    LegacyDecisionView,
    MarketForecastBundle,
    RiskEnvelope,
    SystemAction,
    SystemDecision,
    V3DecisionView,
)


def synthesize_decision(
    *,
    legacy: LegacyDecisionView,
    forecast: MarketForecastBundle,
    universe: CandidateUniverse,
    v3_view: V3DecisionView,
    envelope: RiskEnvelope,
    required_inputs_present: bool,
) -> SystemDecision:
    if not required_inputs_present:
        return SystemDecision(
            action=SystemAction.ABSTAIN,
            reason="required input missing",
            forecast_model_version=forecast.model_version,
            candidate_universe_id=universe.universe_id,
            veto_codes=tuple(v.code for v in legacy.hard_vetoes),
        )

    if legacy.hard_vetoes:
        return SystemDecision(
            action=SystemAction.ABSTAIN,
            reason="hard veto present",
            forecast_model_version=forecast.model_version,
            candidate_universe_id=universe.universe_id,
            veto_codes=tuple(v.code for v in legacy.hard_vetoes),
        )

    if not (legacy.permissions.options_allowed and legacy.permissions.new_positions_allowed):
        return SystemDecision(
            action=SystemAction.ABSTAIN,
            reason="strategy permissions deny action",
            forecast_model_version=forecast.model_version,
            candidate_universe_id=universe.universe_id,
        )

    by_id = {c.candidate_id: c for c in universe.candidates}
    chosen_id = next((cid for cid in v3_view.ranking.ordered_candidate_ids if cid in by_id), None)
    if chosen_id is None:
        return SystemDecision(
            action=SystemAction.ABSTAIN,
            reason="no approved candidates",
            forecast_model_version=forecast.model_version,
            candidate_universe_id=universe.universe_id,
        )

    chosen = by_id[chosen_id]
    if chosen.max_loss is None or chosen.max_loss > envelope.max_defined_risk_per_trade:
        return SystemDecision(
            action=SystemAction.ABSTAIN,
            reason="candidate exceeds deterministic risk envelope",
            forecast_model_version=forecast.model_version,
            candidate_universe_id=universe.universe_id,
        )

    return SystemDecision(
        action=SystemAction.SELECT_CANDIDATE,
        selected_candidate_id=chosen_id,
        reason="selected top-ranked approved candidate",
        forecast_model_version=forecast.model_version,
        candidate_universe_id=universe.universe_id,
    )
