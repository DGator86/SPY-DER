"""Trade / no-edge / abstain meta-action (spec §35; System A trade_meta.py)."""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.value import META_MODEL_VERSION, MetaAction, MetaDecision

__all__ = [
    "MetaThresholdConfig",
    "apply_hard_vetoes",
    "decide_meta_action",
]


@dataclass(frozen=True, slots=True)
class MetaThresholdConfig:
    uncertainty_abstain_threshold: float = 0.75
    ood_abstain_threshold: float = 0.95
    minimum_data_quality: float = 0.60
    minimum_candidate_utility: float = 0.0
    minimum_expected_order_value: float = 0.0
    minimum_trade_probability: float = 0.58


def decide_meta_action(
    *,
    p_positive_utility: float,
    expected_order_value: float,
    selected_candidate_id: str | None,
    selected_candidate_utility: float | None,
    composite_uncertainty: float,
    ood_score: float,
    data_quality: float,
    cfg: MetaThresholdConfig | None = None,
) -> MetaDecision:
    """Pure threshold logic — hard vetoes applied separately."""
    config = cfg or MetaThresholdConfig()
    reasons: list[str] = []
    action = MetaAction.TRADE

    if composite_uncertainty >= config.uncertainty_abstain_threshold:
        action = MetaAction.ABSTAIN
        reasons.append("uncertainty_abstain")
    if ood_score >= config.ood_abstain_threshold:
        action = MetaAction.ABSTAIN
        reasons.append("ood_abstain")
    if data_quality < config.minimum_data_quality:
        action = MetaAction.ABSTAIN
        reasons.append("low_data_quality")

    if action is MetaAction.TRADE:
        if selected_candidate_id is None:
            action = MetaAction.NO_EDGE
            reasons.append("no_selected_candidate")
        elif p_positive_utility < config.minimum_trade_probability:
            action = MetaAction.NO_EDGE
            reasons.append("low_trade_probability")
        elif expected_order_value < config.minimum_expected_order_value:
            action = MetaAction.NO_EDGE
            reasons.append("non_positive_order_value")
        elif (
            selected_candidate_utility is not None
            and selected_candidate_utility < config.minimum_candidate_utility
        ):
            action = MetaAction.NO_EDGE
            reasons.append("utility_below_minimum")

    if action is not MetaAction.TRADE:
        selected_candidate_id = None

    return MetaDecision(
        action=action,
        p_positive_utility=float(p_positive_utility),
        expected_order_value=float(expected_order_value),
        selected_candidate_id=selected_candidate_id,
        composite_uncertainty=float(composite_uncertainty),
        threshold_used=config.minimum_trade_probability,
        reasons=tuple(reasons),
        model_version=META_MODEL_VERSION,
    )


def apply_hard_vetoes(
    decision: MetaDecision,
    hard_vetoes: tuple[str, ...] | list[str],
) -> MetaDecision:
    """Hard vetoes always override statistical action."""
    if not hard_vetoes:
        return decision
    return MetaDecision(
        action=MetaAction.HARD_VETO,
        p_positive_utility=decision.p_positive_utility,
        expected_order_value=decision.expected_order_value,
        selected_candidate_id=None,
        composite_uncertainty=decision.composite_uncertainty,
        threshold_used=decision.threshold_used,
        reasons=tuple([*decision.reasons, *(f"hard_veto:{v}" for v in hard_vetoes)]),
        model_version=decision.model_version,
        diagnostics=decision.diagnostics,
    )
