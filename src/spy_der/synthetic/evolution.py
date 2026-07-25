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

Weights are floored so no archetype is ever fully abandoned: a regime the agent
looks strong on today is exactly where silent regression hides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from spy_der.synthetic.archetypes import ARCHETYPES
from spy_der.synthetic.universe import CoverageMatrix, UniverseCatalog

__all__ = [
    "ArchetypeScore",
    "EvolutionPlan",
    "evolve_catalog",
    "next_generation_weights",
]

#: No archetype ever drops below this share of the mean weight.
_WEIGHT_FLOOR = 0.25
#: An unvisited (archetype, regime) cell contributes this much extra weight.
_COVERAGE_BONUS = 0.5
#: Ceiling so one catastrophic archetype cannot consume an entire generation.
_WEIGHT_CEILING = 4.0


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "weights": dict(sorted(self.weights.items())),
            "weakness": dict(sorted(self.weakness.items())),
            "coverage_bonus": dict(sorted(self.coverage_bonus.items())),
            "unvisited_cells": [list(c) for c in self.unvisited_cells],
        }


def next_generation_weights(
    scores: Mapping[str, ArchetypeScore],
    coverage: CoverageMatrix | None = None,
    *,
    generation: int = 0,
) -> EvolutionPlan:
    """Turn per-archetype scores plus coverage gaps into sampling weights."""
    weakness = {a: scores.get(a, ArchetypeScore(a)).weakness() for a in ARCHETYPES}

    bonus = dict.fromkeys(ARCHETYPES, 0.0)
    unvisited: tuple[tuple[str, str], ...] = ()
    if coverage is not None:
        unvisited = coverage.unvisited()
        for archetype, _regime in unvisited:
            if archetype in bonus:
                bonus[archetype] += _COVERAGE_BONUS

    raw = {a: weakness[a] + bonus[a] for a in ARCHETYPES}
    mean = sum(raw.values()) / len(raw) if raw else 1.0
    floor = _WEIGHT_FLOOR * mean if mean > 0 else _WEIGHT_FLOOR
    ceiling = _WEIGHT_CEILING * mean if mean > 0 else _WEIGHT_CEILING
    weights = {a: min(max(raw[a], floor), ceiling) for a in ARCHETYPES}

    return EvolutionPlan(
        generation=generation,
        weights=weights,
        weakness=weakness,
        coverage_bonus=bonus,
        unvisited_cells=unvisited,
    )


def evolve_catalog(
    catalog: UniverseCatalog,
    scores: Mapping[str, ArchetypeScore],
    coverage: CoverageMatrix | None = None,
) -> tuple[UniverseCatalog, EvolutionPlan]:
    """Advance ``catalog`` one generation with re-weighted archetype sampling.

    The returned catalog carries ``generation + 1``, which also advances the
    Dirichlet transition jitter via
    :func:`~spy_der.synthetic.universe.jitter_for_generation` — so later
    generations both concentrate on weak archetypes *and* explore nearby
    dynamics.
    """
    plan = next_generation_weights(scores, coverage, generation=catalog.generation + 1)
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
