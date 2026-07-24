"""Holdout-gated optimization over staged hypotheses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spy_der.learning.hypotheses import Hypothesis

__all__ = ["OptimizationResult", "optimize_with_holdout"]


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    status: str
    selected: Hypothesis | None
    holdout_fraction: float
    trials: int
    score: float | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "selected": None if self.selected is None else self.selected.to_dict(),
            "holdout_fraction": self.holdout_fraction,
            "trials": self.trials,
            "score": self.score,
            "notes": list(self.notes),
        }


def optimize_with_holdout(
    hypotheses: Sequence[Hypothesis],
    *,
    trials: int,
    holdout: float,
    experience_summary: dict[str, Any] | None = None,
) -> OptimizationResult:
    """Select a hypothesis without writing champion state.

    Full search against 0DTE CandidateEvaluator is Phase-4 work. Until then,
    pick the highest-priority hypothesis when enough experience exists.
    """
    if holdout <= 0.0 or holdout >= 1.0:
        return OptimizationResult(
            status="invalid_holdout",
            selected=None,
            holdout_fraction=holdout,
            trials=trials,
            score=None,
            notes=("holdout must be in (0, 1)",),
        )
    if not hypotheses:
        return OptimizationResult(
            status="no_hypotheses",
            selected=None,
            holdout_fraction=holdout,
            trials=trials,
            score=None,
            notes=("no diagnoses produced actionable hypotheses",),
        )
    n_sessions = int((experience_summary or {}).get("n_sessions") or 0)
    if n_sessions < 3:
        return OptimizationResult(
            status="insufficient_data",
            selected=None,
            holdout_fraction=holdout,
            trials=trials,
            score=None,
            notes=(f"n_sessions={n_sessions} too thin for holdout optimization",),
        )
    selected = max(hypotheses, key=lambda h: h.priority)
    return OptimizationResult(
        status="ok",
        selected=selected,
        holdout_fraction=holdout,
        trials=trials,
        score=selected.priority,
        notes=("staged_only", "champion_untouched"),
    )
