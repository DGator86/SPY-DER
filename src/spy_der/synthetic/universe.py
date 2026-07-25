"""Universe specification, combinatoric catalog, and coverage matrix.

Ported from 0DTE ``matrix_universe.UniverseSpec`` / ``UniverseCatalog`` /
``merge_coverage`` (DGator86/0DTE @ 6393cd1).

A :class:`UniverseSpec` is the complete, deterministic address of one synthetic
world: the seed plus the lattice coordinates that condition the chains. The same
spec always reproduces the same world.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from spy_der.synthetic.archetypes import ARCHETYPES, GEN_PARAMS, REGIMES

__all__ = [
    "CoverageMatrix",
    "UniverseCatalog",
    "UniverseSpec",
    "dirichlet_rows",
    "jitter_for_generation",
    "merge_coverage",
    "spec_id",
]


@dataclass(frozen=True, slots=True)
class UniverseSpec:
    """One fully deterministic universe.

    Satisfies the ``spy_der.dojo.protocols.UniverseSpec`` protocol via
    :attr:`archetype`, which aliases :attr:`start_archetype`.
    """

    universe_id: str
    seed: int
    days: int
    start_archetype: str
    persistence_tilt: float = 1.0  # >1 = regimes stickier, <1 = choppier
    vol_mult: float = 1.0
    gap_mult: float = 1.0
    tick_stride: int = 1
    base_spot: float = 600.0
    generation: int = 0
    #: Seeded Dirichlet perturbation of BOTH transition layers (archetype and
    #: regime rows). 0 = the canonical matrices verbatim; >0 draws each row from
    #: ``Dirichlet(row / jitter)``, so later generations explore nearby dynamics
    #: deterministically per seed.
    transition_jitter: float = 0.0

    def __post_init__(self) -> None:
        if self.days < 1:
            msg = "UniverseSpec.days must be >= 1"
            raise ValueError(msg)
        if self.tick_stride < 1:
            msg = "UniverseSpec.tick_stride must be >= 1"
            raise ValueError(msg)
        if self.start_archetype not in ARCHETYPES:
            msg = (
                f"unknown archetype {self.start_archetype!r}; "
                f"expected one of {ARCHETYPES}"
            )
            raise ValueError(msg)
        if self.transition_jitter < 0.0:
            msg = "UniverseSpec.transition_jitter must be >= 0"
            raise ValueError(msg)

    @property
    def archetype(self) -> str:
        """Protocol alias for :attr:`start_archetype`."""
        return self.start_archetype

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "seed": self.seed,
            "days": self.days,
            "start_archetype": self.start_archetype,
            "persistence_tilt": self.persistence_tilt,
            "vol_mult": self.vol_mult,
            "gap_mult": self.gap_mult,
            "tick_stride": self.tick_stride,
            "base_spot": self.base_spot,
            "generation": self.generation,
            "transition_jitter": self.transition_jitter,
        }


def spec_id(seed: int, archetype: str, tilt: float, vol: float, generation: int) -> str:
    """Stable 10-hex-char universe id for a lattice coordinate."""
    raw = f"{seed}|{archetype}|{tilt}|{vol}|{generation}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def dirichlet_rows(
    rows: Mapping[str, Mapping[str, float]],
    rng: np.random.Generator,
    jitter: float,
) -> dict[str, dict[str, float]]:
    """Redraw each row from ``Dirichlet(row / jitter)``.

    The row stays a proper distribution centered on the canonical one, with
    spread growing in ``jitter``. Deterministic given the caller's seeded
    generator.
    """
    out: dict[str, dict[str, float]] = {}
    kappa = 1.0 / max(jitter, 1e-6)
    for state, row in rows.items():
        keys = list(row)
        alpha = np.array([max(row[k], 1e-4) for k in keys]) * kappa
        sample = rng.dirichlet(alpha)
        out[state] = {k: float(v) for k, v in zip(keys, sample, strict=True)}
    return out


def jitter_for_generation(generation: int) -> float:
    """Jitter schedule: ``per_generation * generation``, capped.

    Generation 0 is always the canonical matrices verbatim (jitter 0.0), so a
    baseline universe reproduces pre-migration worlds exactly.
    """
    schedule = GEN_PARAMS["jitter"]
    per_gen = float(schedule["per_generation"])
    cap = float(schedule["cap"])
    return min(per_gen * max(generation, 0), cap)


@dataclass
class UniverseCatalog:
    """The combinatoric lattice: archetypes x persistence tilts x vol mults.

    ``archetype_weights`` biases the shallow sample toward archetypes the Dojo
    is currently weak on (see :mod:`spy_der.synthetic.evolution`); the full
    lattice ignores weights and enumerates every cell.
    """

    seed: int = 1
    days: int = 1
    generation: int = 0
    tilts: tuple[float, ...] = (0.85, 1.0, 1.15)
    vol_mults: tuple[float, ...] = (0.7, 1.0, 1.4, 2.0)
    gap_mults: tuple[float, ...] = (1.0,)
    base_spot: float = 600.0
    tick_stride: int = 1
    archetype_weights: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(ARCHETYPES, 1.0)
    )

    @property
    def lattice_size(self) -> int:
        return len(ARCHETYPES) * len(self.tilts) * len(self.vol_mults) * len(self.gap_mults)

    def full_lattice(self) -> list[UniverseSpec]:
        """Every ``(archetype, tilt, vol, gap)`` cell, deterministically ordered."""
        jitter = jitter_for_generation(self.generation)
        specs: list[UniverseSpec] = []
        cells = itertools.product(ARCHETYPES, self.tilts, self.vol_mults, self.gap_mults)
        for index, (archetype, tilt, vol, gap) in enumerate(cells):
            specs.append(
                UniverseSpec(
                    universe_id=spec_id(self.seed + index, archetype, tilt, vol, self.generation),
                    seed=self.seed + index,
                    days=self.days,
                    start_archetype=archetype,
                    persistence_tilt=tilt,
                    vol_mult=vol,
                    gap_mult=gap,
                    tick_stride=self.tick_stride,
                    base_spot=self.base_spot,
                    generation=self.generation,
                    transition_jitter=jitter,
                )
            )
        return specs

    def sample(self, count: int) -> list[UniverseSpec]:
        """A weighted shallow sample of ``count`` universes, deterministic per seed.

        Archetypes are drawn in proportion to :attr:`archetype_weights`; tilt,
        vol and gap coordinates are drawn uniformly from the lattice axes.
        """
        if count < 1:
            return []
        rng = np.random.default_rng(self.seed * 1_000_003 + self.generation)
        jitter = jitter_for_generation(self.generation)
        names = list(ARCHETYPES)
        weights = np.array([max(self.archetype_weights.get(a, 0.0), 0.0) for a in names])
        if weights.sum() <= 0.0:
            weights = np.ones(len(names))
        probs = weights / weights.sum()

        specs: list[UniverseSpec] = []
        for index in range(count):
            archetype = str(rng.choice(names, p=probs))
            tilt = float(rng.choice(np.asarray(self.tilts)))
            vol = float(rng.choice(np.asarray(self.vol_mults)))
            gap = float(rng.choice(np.asarray(self.gap_mults)))
            specs.append(
                UniverseSpec(
                    universe_id=spec_id(self.seed + index, archetype, tilt, vol, self.generation),
                    seed=self.seed + index,
                    days=self.days,
                    start_archetype=archetype,
                    persistence_tilt=tilt,
                    vol_mult=vol,
                    gap_mult=gap,
                    tick_stride=self.tick_stride,
                    base_spot=self.base_spot,
                    generation=self.generation,
                    transition_jitter=jitter,
                )
            )
        return specs


@dataclass(frozen=True, slots=True)
class CoverageMatrix:
    """(archetype x regime) minute occupancy across a set of generated worlds."""

    cells: Mapping[str, Mapping[str, int]]

    @property
    def total_cells(self) -> int:
        return len(ARCHETYPES) * len(REGIMES)

    @property
    def visited_cells(self) -> int:
        return sum(
            1
            for archetype in ARCHETYPES
            for regime in REGIMES
            if self.cells.get(archetype, {}).get(regime, 0) > 0
        )

    @property
    def coverage_fraction(self) -> float:
        return self.visited_cells / self.total_cells if self.total_cells else 0.0

    def unvisited(self) -> tuple[tuple[str, str], ...]:
        """Cells with zero minutes — the gaps a curriculum should target."""
        return tuple(
            (archetype, regime)
            for archetype in ARCHETYPES
            for regime in REGIMES
            if self.cells.get(archetype, {}).get(regime, 0) == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cells": {a: dict(r) for a, r in sorted(self.cells.items())},
            "visited_cells": self.visited_cells,
            "total_cells": self.total_cells,
            "coverage_fraction": self.coverage_fraction,
            "unvisited": [list(cell) for cell in self.unvisited()],
        }


def merge_coverage(
    occupancies: Iterable[Mapping[str, Mapping[str, int]]],
) -> CoverageMatrix:
    """Sum per-world ``archetype -> regime -> minutes`` occupancy tables."""
    out: dict[str, dict[str, int]] = {
        archetype: dict.fromkeys(REGIMES, 0) for archetype in ARCHETYPES
    }
    for occupancy in occupancies:
        for archetype, rows in occupancy.items():
            target = out.setdefault(archetype, dict.fromkeys(REGIMES, 0))
            for regime, minutes in rows.items():
                target[regime] = target.get(regime, 0) + int(minutes)
    return CoverageMatrix(cells=out)


def specs_for(
    archetypes: Sequence[str],
    *,
    seed: int = 1,
    days: int = 1,
    generation: int = 0,
) -> list[UniverseSpec]:
    """Convenience: one baseline universe per named archetype."""
    jitter = jitter_for_generation(generation)
    return [
        UniverseSpec(
            universe_id=spec_id(seed + i, archetype, 1.0, 1.0, generation),
            seed=seed + i,
            days=days,
            start_archetype=archetype,
            generation=generation,
            transition_jitter=jitter,
        )
        for i, archetype in enumerate(archetypes)
    ]
