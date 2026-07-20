"""Within-snapshot ranking and expected regret (spec §35)."""

from __future__ import annotations

from spy_der.contracts.candidates import Candidate
from spy_der.contracts.value import CANDIDATE_VALUE_VERSION, CandidateValueForecast, SnapshotRanking

__all__ = ["rank_snapshot", "ranking_regret", "tie_break_key"]


def ranking_regret(
    selected_id: str | None,
    realized_utilities: dict[str, float],
) -> float:
    """hindsight_best - selected (best if no selection)."""
    if not realized_utilities:
        return 0.0
    best = max(realized_utilities.values())
    if selected_id is None or selected_id not in realized_utilities:
        return float(best)
    return float(best - realized_utilities[selected_id])


def tie_break_key(
    candidate_id: str,
    *,
    absolute_utility: float,
    uncertainty: float,
    capital: float,
) -> tuple[float, float, float, str]:
    """Deterministic: higher utility, lower unc, lower capital, id."""
    return (-float(absolute_utility), float(uncertainty), float(capital), str(candidate_id))


def rank_snapshot(
    *,
    snapshot_id: str,
    candidates: list[Candidate] | tuple[Candidate, ...],
    forecasts: dict[str, CandidateValueForecast],
    vetoed_ids: set[str] | frozenset[str] | None = None,
) -> SnapshotRanking:
    """Rank by utility with deterministic tie-break; compute score-gap regret."""
    vetoed = set(vetoed_ids or ())
    scored: list[tuple[str, float, float, float]] = []
    for cand in candidates:
        fc = forecasts.get(cand.candidate_id)
        if fc is None:
            continue
        util = float(fc.utility if fc.utility is not None else 0.0)
        unc = float(
            max(
                fc.model_uncertainty,
                fc.forecast_uncertainty,
                fc.execution_uncertainty,
                fc.ood_score,
            )
        )
        capital = float(cand.capital_required)
        scored.append((cand.candidate_id, util, unc, capital))

    # Sort selectable first by tie-break; vetoed scored but not selected.
    selectable = [s for s in scored if s[0] not in vetoed]
    selectable.sort(
        key=lambda item: tie_break_key(
            item[0],
            absolute_utility=item[1],
            uncertainty=item[2],
            capital=item[3],
        )
    )
    ordered = [cid for cid, *_ in selectable]
    # Append vetoed after, also deterministically sorted.
    vetoed_scored = [s for s in scored if s[0] in vetoed]
    vetoed_scored.sort(
        key=lambda item: tie_break_key(
            item[0],
            absolute_utility=item[1],
            uncertainty=item[2],
            capital=item[3],
        )
    )
    ordered.extend(cid for cid, *_ in vetoed_scored)

    util_map = {cid: util for cid, util, *_ in scored}
    top = ordered[0] if selectable else None
    second = ordered[1] if len(selectable) > 1 else None
    margin = None
    if top is not None and second is not None:
        margin = float(util_map[top] - util_map[second])

    regrets = {cid: ranking_regret(cid, util_map) for cid in util_map}
    # Expected regret relative to best selectable utility.
    return SnapshotRanking(
        snapshot_id=snapshot_id,
        ordered_candidate_ids=tuple(ordered),
        combined_scores=tuple((cid, util_map[cid]) for cid in ordered),
        expected_regret=tuple((cid, regrets[cid]) for cid in ordered),
        top_candidate_id=top,
        second_candidate_id=second,
        top_score_margin=margin,
        model_version=CANDIDATE_VALUE_VERSION,
    )
