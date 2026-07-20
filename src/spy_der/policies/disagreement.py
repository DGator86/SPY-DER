"""Deterministic policy disagreement model (spec §36 / §41)."""

from __future__ import annotations

from spy_der.contracts.policies import PolicyAction, PolicyDecisionView, PolicyDisagreement

__all__ = ["compute_policy_disagreement"]


def compute_policy_disagreement(
    views: tuple[PolicyDecisionView, ...] | list[PolicyDecisionView],
) -> PolicyDisagreement:
    """Compare policy actions/candidates without scores or AI preference."""
    if len(views) < 2:
        names = tuple(v.policy_name for v in views)
        return PolicyDisagreement(
            disagree=False,
            action_conflict=False,
            candidate_conflict=False,
            agreeing_policies=names,
        )

    actions = {v.action for v in views}
    action_conflict = len(actions) > 1
    selects = [v for v in views if v.action is PolicyAction.SELECT_CANDIDATE]
    cand_ids = {v.candidate_id for v in selects if v.candidate_id}
    candidate_conflict = len(cand_ids) > 1

    disagree = action_conflict or candidate_conflict
    reasons: list[str] = []
    if action_conflict:
        reasons.append("action_conflict")
    if candidate_conflict:
        reasons.append("candidate_conflict")

    # Agreeing = share the modal action (and candidate when selecting).
    from collections import Counter

    action_counts = Counter(v.action for v in views)
    modal_action, _ = action_counts.most_common(1)[0]
    agreeing: list[str] = []
    disagreeing: list[str] = []
    modal_candidate = None
    if modal_action is PolicyAction.SELECT_CANDIDATE and cand_ids:
        # Modal candidate among selectors.
        cand_counts = Counter(v.candidate_id for v in selects if v.candidate_id)
        modal_candidate = cand_counts.most_common(1)[0][0]

    for view in views:
        if view.action != modal_action:
            disagreeing.append(view.policy_name)
            continue
        if (
            modal_action is PolicyAction.SELECT_CANDIDATE
            and modal_candidate is not None
            and view.candidate_id != modal_candidate
        ):
            disagreeing.append(view.policy_name)
            continue
        agreeing.append(view.policy_name)

    return PolicyDisagreement(
        disagree=disagree,
        action_conflict=action_conflict,
        candidate_conflict=candidate_conflict,
        agreeing_policies=tuple(agreeing),
        disagreeing_policies=tuple(disagreeing),
        reasons=tuple(reasons),
    )
