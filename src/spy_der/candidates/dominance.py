"""Deterministic pre-ranking dominance (master spec §32).

Removes duplicates / payoff-dominated candidates without using model scores,
EV, AI, or policy preference.
"""

from __future__ import annotations

from decimal import Decimal

from spy_der.candidates.payoff import terminal_payoff
from spy_der.contracts.candidates import Candidate

__all__ = ["apply_deterministic_dominance"]


def _payoff_vector(candidate: Candidate, spots: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    return tuple(
        terminal_payoff(candidate.legs, entry_credit=candidate.entry_credit, spot=spot)
        for spot in spots
    )


def _strictly_dominates(
    a_payoffs: tuple[Decimal, ...],
    b_payoffs: tuple[Decimal, ...],
) -> bool:
    if len(a_payoffs) != len(b_payoffs) or not a_payoffs:
        return False
    ge_all = all(a >= b for a, b in zip(a_payoffs, b_payoffs, strict=True))
    gt_any = any(a > b for a, b in zip(a_payoffs, b_payoffs, strict=True))
    return ge_all and gt_any


def apply_deterministic_dominance(
    candidates: list[Candidate],
) -> list[Candidate]:
    """Return a deterministically pruned, ID-sorted universe.

    Removal reasons (spec §32):
    - duplicate geometry (keep better quote_quality, then lower candidate_id)
    - identical payoff with higher cost (worse entry_credit for same payoff hash)
    - strict payoff dominance at equal-or-better entry cost
    """
    if not candidates:
        return []

    # 1) Duplicate geometry
    by_geom: dict[str, Candidate] = {}
    for cand in candidates:
        prior = by_geom.get(cand.geometry_hash)
        if prior is None:
            by_geom[cand.geometry_hash] = cand
            continue
        # Prefer higher quote quality; tie-break by candidate_id.
        if cand.quote_quality > prior.quote_quality or (
            cand.quote_quality == prior.quote_quality
            and cand.candidate_id < prior.candidate_id
        ):
            by_geom[cand.geometry_hash] = cand
    unique = list(by_geom.values())

    # Shared evaluation grid for dominance comparisons.
    strike_spots = sorted({leg.strike for c in unique for leg in c.legs})
    spots = (Decimal("0"), *strike_spots)
    if strike_spots:
        spots = (*spots, strike_spots[-1] + Decimal("50"))

    # 2) Identical payoff with higher cost
    by_payoff: dict[str, Candidate] = {}
    for cand in unique:
        prior = by_payoff.get(cand.terminal_payoff_hash)
        if prior is None:
            by_payoff[cand.terminal_payoff_hash] = cand
            continue
        # Higher entry_credit is better (more credit / less debit).
        if cand.entry_credit > prior.entry_credit or (
            cand.entry_credit == prior.entry_credit
            and cand.candidate_id < prior.candidate_id
        ):
            by_payoff[cand.terminal_payoff_hash] = cand
    unique = list(by_payoff.values())

    # 3) Strict payoff dominance
    vectors = {c.candidate_id: _payoff_vector(c, spots) for c in unique}
    dominated: set[str] = set()
    for a in unique:
        if a.candidate_id in dominated:
            continue
        for b in unique:
            if a.candidate_id == b.candidate_id or b.candidate_id in dominated:
                continue
            if a.entry_credit < b.entry_credit:
                continue
            if _strictly_dominates(vectors[a.candidate_id], vectors[b.candidate_id]):
                dominated.add(b.candidate_id)

    survivors = [c for c in unique if c.candidate_id not in dominated]
    survivors.sort(key=lambda c: c.candidate_id)
    return survivors
