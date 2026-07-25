"""Driver-variable Markov chains for synthetic universes.

Ported from 0DTE ``matrix_universe.VariableChain`` (DGator86/0DTE @ 6393cd1).

Each driver variable (gex / rv / vrp / skew / drift) is a 3-state Markov chain
with an Ornstein-Uhlenbeck relaxation toward the numeric target of its current
state. Every variable gets its own ``numpy.random.Generator`` stream, so the
variables are genuinely independent rather than shared draws off one stream.
"""

from __future__ import annotations

import numpy as np

from spy_der.synthetic.archetypes import (
    VAR_OU_NOISE,
    VAR_OU_THETA,
    VAR_PREFERENCE,
    VAR_PULL,
    VAR_STATES,
    VAR_STAY,
    VAR_TARGETS,
)

__all__ = ["VariableChain", "normalize_row"]


def normalize_row(row: dict[str, float] | object) -> list[float]:
    """Normalize a transition row's values to a probability vector.

    A row whose mass is non-positive degrades to uniform rather than raising —
    a jittered or calibrated row must never be able to halt world generation.
    """
    if not isinstance(row, dict):
        raise TypeError("normalize_row expects a mapping of state -> weight")
    values = [max(float(v), 0.0) for v in row.values()]
    total = sum(values)
    if total <= 0.0:
        share = 1.0 / max(len(values), 1)
        return [share for _ in values]
    return [v / total for v in values]


class VariableChain:
    """One driver variable: 3-state Markov chain + OU relaxation to a target.

    When a caller supplies ``prefer_override`` (skew does, per breakout/drift
    direction) the OU target follows the OVERRIDE immediately rather than the
    discrete Markov ``state``, whose expected switch time
    (``1/(1 - VAR_STAY) ~= 58`` minutes) is far longer than a breakout lasts.
    Without an override the target is the current discrete state, so
    gex/rv/vrp/drift keep their slower cadence.
    """

    __slots__ = ("name", "rng", "scale", "state", "value")

    def __init__(self, name: str, rng: np.random.Generator, scale: float = 1.0) -> None:
        if name not in VAR_TARGETS:
            msg = f"unknown driver variable: {name!r}"
            raise KeyError(msg)
        self.name = name
        self.rng = rng
        self.scale = scale
        self.state = "mid"
        self.value = VAR_TARGETS[name]["mid"] * scale

    def step(self, regime: str, prefer_override: str | None = None) -> float:
        """Advance one minute and return the new numeric value."""
        prefer = prefer_override or VAR_PREFERENCE[regime][self.name]
        others = [s for s in VAR_STATES if s != self.state]
        p_move = 1.0 - VAR_STAY
        probs: dict[str, float] = {s: p_move / len(others) for s in others}
        probs[self.state] = VAR_STAY
        probs[prefer] = probs.get(prefer, 0.0) + VAR_PULL
        total = sum(probs.values())
        states = list(probs)
        self.state = str(
            self.rng.choice(states, p=[probs[s] / total for s in states])
        )
        # OU target: relax toward the override when one is given (fast,
        # direction-coherent skew), else toward the discrete Markov state.
        target_state = prefer_override if prefer_override is not None else self.state
        target = VAR_TARGETS[self.name][target_state] * self.scale
        theta = VAR_OU_THETA[self.name]
        noise = VAR_OU_NOISE[self.name] * self.scale
        self.value += theta * (target - self.value) + noise * float(
            self.rng.standard_normal()
        )
        return self.value
