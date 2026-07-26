"""Weak-archetype evolution — spend the next generation where the agent is worst.

Migrated into SPY-DER from the 0DTE synthetic stack. A Dojo generation reports
per-archetype performance; this module turns that report into the *next*
generation's catalog weights, so sampling concentrates on the archetypes the
current champion handles badly and on lattice cells that have never been
visited.

Two pressures combine:

* **Weakness** — archetypes with the worst score get proportionally more draws.
* **Coverage** — cells with zero minutes get a floor weight regardless of score,
  because "never seen" is not the same as "handled well".

When a prior curriculum exists, the new plan is blended with it
(``w = (1-a)*w_hat + a*w_prior``) so weekly full-lattice measurement cannot wipe
accumulated gap pressure. Weights are floored so no archetype is ever fully
abandoned: a regime the agent looks strong on today is exactly where silent
regression hides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from spy_der.synthetic.archetypes import ARCHETYPES
from spy_der.synthetic.universe import CoverageMatrix, UniverseCatalog

__all__ = [
    "CURRICULUM_INERTIA",
    "ArchetypeScore",
    "EvolutionPlan",
    "blend_weights",
    "evolve_catalog",
    "focus_from_plan",
    "next_generation_weights",
    "scores_from_archetype_matrix",
]

#: No archetype ever drops below this share of the mean weight.
_WEIGHT_FLOOR = 0.25
#: An unvisited (archetype, regime) cell contributes this much extra weight.
_COVERAGE_BONUS = 0.5
#: Ceiling so one catastrophic archetype cannot consume an entire generation.
_WEIGHT_CEILING = 4.0
#: Share of the prior curriculum kept when forming the next plan.
#: ``w = (1-a)*w_hat + a*w_prior`` — enough momentum that a full-lattice
#: measurement cannot erase last night's gap pressure.
CURRICULUM_INERTIA = 0.35

_ARCHETYPE_LABELS: dict[str, str] = {
    "calm_pin": "calm pin",
    "grind_up": "grind up",
    "grind_down": "grind down",
    "range_chop": "range chop",
    "vol_expansion": "vol expansion",
    "squeeze_melt_up": "squeeze melt-up",
    "crash": "crash",
    "gap_shock": "gap shock",
}


@dataclass(frozen=True, slots=True)
class ArchetypeScore:
    """One archetype's performance in the generation that just ran."""

    archetype: str
    mean_session_pnl: float | None = None
    session_win_rate: float | None = None
    dir_hit: float | None = None
    n_sessions: int = 0

    @property
    def is_scored(self) -> bool:
        return self.n_sessions > 0 and self.mean_session_pnl is not None

    def weakness(self) -> float:
        """``[0, 1]``-ish weakness score; higher means the agent did worse.

        Combines the three signals that are actually comparable across
        archetypes. An unscored archetype returns the neutral 0.5 rather than 0,
        so "no data" pulls sampling toward it instead of away.
        """
        if not self.is_scored:
            return 0.5
        parts: list[float] = []
        pnl = self.mean_session_pnl
        if pnl is not None:
            # Squash P&L into [0, 1]: losses -> >0.5, gains -> <0.5.
            parts.append(0.5 - 0.5 * _squash(pnl))
        if self.session_win_rate is not None:
            parts.append(1.0 - min(max(self.session_win_rate, 0.0), 1.0))
        if self.dir_hit is not None:
            parts.append(1.0 - min(max(self.dir_hit, 0.0), 1.0))
        return sum(parts) / len(parts) if parts else 0.5


def _squash(value: float, scale: float = 100.0) -> float:
    """Map ``(-inf, inf)`` onto ``(-1, 1)`` — bounded, monotone, sign-preserving."""
    return value / (scale + abs(value))


@dataclass(frozen=True, slots=True)
class EvolutionPlan:
    """The next generation's sampling weights, with the reasoning attached."""

    generation: int
    weights: dict[str, float]
    weakness: dict[str, float]
    coverage_bonus: dict[str, float]
    unvisited_cells: tuple[tuple[str, str], ...]
    #: Fresh weakness/coverage weights before curriculum inertia.
    proposed_weights: dict[str, float] = field(default_factory=dict)
    #: Inertia coefficient used when blending with a prior curriculum (0 = none).
    inertia: float = 0.0
    blended_from_prior: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "weights": dict(sorted(self.weights.items())),
            "weakness": dict(sorted(self.weakness.items())),
            "coverage_bonus": dict(sorted(self.coverage_bonus.items())),
            "unvisited_cells": [list(c) for c in self.unvisited_cells],
            "proposed_weights": dict(sorted(self.proposed_weights.items())),
            "inertia": self.inertia,
            "blended_from_prior": self.blended_from_prior,
        }


def blend_weights(
    proposed: Mapping[str, float],
    prior: Mapping[str, float] | None,
    *,
    inertia: float = CURRICULUM_INERTIA,
) -> dict[str, float]:
    """``w = (1-a)*proposed + a*prior``, floored/ceiled like a fresh plan.

    When ``prior`` is missing or ``inertia`` is 0, returns a floored copy of
    ``proposed``. Missing prior keys are filled with the prior mean (or the
    proposed value) so a partial curriculum cannot starve a new archetype.
    """
    alpha = min(max(float(inertia), 0.0), 1.0)
    proposed_full = {a: float(proposed.get(a, 0.0)) for a in ARCHETYPES}
    if not prior or alpha <= 0.0:
        return _floor_ceiling(proposed_full)

    prior_vals = [float(prior[a]) for a in ARCHETYPES if a in prior and float(prior[a]) > 0]
    prior_mean = (sum(prior_vals) / len(prior_vals)) if prior_vals else 1.0
    blended = {}
    for archetype in ARCHETYPES:
        p = proposed_full[archetype]
        q = float(prior[archetype]) if archetype in prior else prior_mean
        if q <= 0.0:
            q = prior_mean
        blended[archetype] = (1.0 - alpha) * p + alpha * q
    return _floor_ceiling(blended)


def _floor_ceiling(raw: Mapping[str, float]) -> dict[str, float]:
    mean = sum(raw.values()) / len(raw) if raw else 1.0
    floor = _WEIGHT_FLOOR * mean if mean > 0 else _WEIGHT_FLOOR
    ceiling = _WEIGHT_CEILING * mean if mean > 0 else _WEIGHT_CEILING
    return {a: min(max(float(raw[a]), floor), ceiling) for a in ARCHETYPES}


def next_generation_weights(
    scores: Mapping[str, ArchetypeScore],
    coverage: CoverageMatrix | None = None,
    *,
    generation: int = 0,
    prior_weights: Mapping[str, float] | None = None,
    inertia: float = CURRICULUM_INERTIA,
) -> EvolutionPlan:
    """Turn per-archetype scores plus coverage gaps into sampling weights.

    ``prior_weights`` (when present) are blended in with ``inertia`` so a
    full-lattice measurement cannot erase accumulated curriculum pressure.
    """
    weakness = {a: scores.get(a, ArchetypeScore(a)).weakness() for a in ARCHETYPES}

    bonus = dict.fromkeys(ARCHETYPES, 0.0)
    unvisited: tuple[tuple[str, str], ...] = ()
    if coverage is not None:
        unvisited = coverage.unvisited()
        for archetype, _regime in unvisited:
            if archetype in bonus:
                bonus[archetype] += _COVERAGE_BONUS

    proposed = _floor_ceiling({a: weakness[a] + bonus[a] for a in ARCHETYPES})
    use_prior = bool(prior_weights) and float(inertia) > 0.0
    weights = (
        blend_weights(proposed, prior_weights, inertia=inertia)
        if use_prior
        else proposed
    )

    return EvolutionPlan(
        generation=generation,
        weights=weights,
        weakness=weakness,
        coverage_bonus=bonus,
        unvisited_cells=unvisited,
        proposed_weights=dict(proposed),
        inertia=float(inertia) if use_prior else 0.0,
        blended_from_prior=use_prior,
    )


def evolve_catalog(
    catalog: UniverseCatalog,
    scores: Mapping[str, ArchetypeScore],
    coverage: CoverageMatrix | None = None,
    *,
    prior_weights: Mapping[str, float] | None = None,
    inertia: float = CURRICULUM_INERTIA,
) -> tuple[UniverseCatalog, EvolutionPlan]:
    """Advance ``catalog`` one generation with re-weighted archetype sampling.

    The returned catalog carries ``generation + 1``, which also advances the
    Dirichlet transition jitter via
    :func:`~spy_der.synthetic.universe.jitter_for_generation` — so later
    generations both concentrate on weak archetypes *and* explore nearby
    dynamics.

    ``prior_weights`` defaults to the catalog's current archetype weights so
    intra-run evolution carries curriculum inertia forward.
    """
    prior = prior_weights if prior_weights is not None else catalog.archetype_weights
    plan = next_generation_weights(
        scores,
        coverage,
        generation=catalog.generation + 1,
        prior_weights=prior,
        inertia=inertia,
    )
    evolved = UniverseCatalog(
        seed=catalog.seed,
        days=catalog.days,
        generation=catalog.generation + 1,
        tilts=catalog.tilts,
        vol_mults=catalog.vol_mults,
        gap_mults=catalog.gap_mults,
        base_spot=catalog.base_spot,
        tick_stride=catalog.tick_stride,
        archetype_weights=dict(plan.weights),
    )
    return evolved, plan


def focus_from_plan(
    plan: EvolutionPlan,
    scores: Mapping[str, ArchetypeScore] | None = None,
    *,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Top-weighted archetypes with human reasons drawn from the plan itself."""
    if not plan.weights:
        return []
    mean = sum(plan.weights.values()) / len(plan.weights)
    ranked = sorted(plan.weights.items(), key=lambda item: item[1], reverse=True)
    focus: list[dict[str, Any]] = []
    score_map = scores or {}
    for archetype, weight in ranked:
        # Always surface the heaviest draws; skip only clearly below-average
        # weights once we already have at least one focus row.
        if focus and mean > 0 and weight < mean:
            continue
        score = score_map.get(archetype) or ArchetypeScore(archetype)
        reasons = _focus_reasons(archetype, plan, score)
        focus.append(
            {
                "archetype": archetype,
                "label": _ARCHETYPE_LABELS.get(archetype, archetype.replace("_", " ")),
                "weight": round(float(weight), 4),
                "reasons": reasons,
            }
        )
        if len(focus) >= top_n:
            break
    return focus


def _focus_reasons(
    archetype: str,
    plan: EvolutionPlan,
    score: ArchetypeScore,
) -> list[str]:
    reasons: list[str] = []
    if score.is_scored:
        pnl = score.mean_session_pnl
        if pnl is not None and pnl < 0.0:
            reasons.append("negative mean P&L")
        if score.session_win_rate is not None and score.session_win_rate < 0.45:
            reasons.append("low session win rate")
        if score.dir_hit is not None and score.dir_hit < 0.5:
            reasons.append("low directional accuracy")
    else:
        reasons.append("no scored sessions")

    unvisited_for = [regime for arch, regime in plan.unvisited_cells if arch == archetype]
    if unvisited_for:
        n = len(unvisited_for)
        reasons.append(f"{n} unvisited regime{'s' if n != 1 else ''}")
    elif plan.coverage_bonus.get(archetype, 0.0) > 0.0:
        reasons.append("coverage gap")

    if plan.blended_from_prior and plan.proposed_weights:
        proposed = float(plan.proposed_weights.get(archetype, 0.0))
        final = float(plan.weights.get(archetype, 0.0))
        if final > proposed * 1.05:
            reasons.append("carried from prior curriculum")

    if not reasons:
        reasons.append("elevated sampling weight")
    return reasons


def scores_from_archetype_matrix(
    matrix: Mapping[str, Mapping[str, Any]],
) -> dict[str, ArchetypeScore]:
    """Adapt a Dojo universe-phase ``archetype_matrix`` into scores."""
    out: dict[str, ArchetypeScore] = {}
    for archetype, row in matrix.items():
        out[archetype] = ArchetypeScore(
            archetype=archetype,
            mean_session_pnl=_maybe_float(row.get("mean_session_pnl")),
            session_win_rate=_maybe_float(row.get("session_win_rate")),
            dir_hit=_maybe_float(row.get("dir_hit")),
            n_sessions=int(row.get("n_sessions") or 0),
        )
    return out


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
