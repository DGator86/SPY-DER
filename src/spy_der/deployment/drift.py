"""Model/data drift detection gates for promotion and freeze."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["DriftLevel", "DriftReport", "evaluate_drift"]


class DriftLevel(StrEnum):
    OK = "ok"
    WATCH = "watch"
    FREEZE = "freeze"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class DriftReport:
    level: DriftLevel
    reasons: tuple[str, ...] = ()
    psi: float = 0.0
    brier_skill: float | None = None
    expectancy_delta: float | None = None


def evaluate_drift(
    *,
    psi: float = 0.0,
    brier_skill: float | None = None,
    expectancy_delta: float | None = None,
    psi_watch: float = 0.1,
    psi_freeze: float = 0.25,
    min_brier_skill: float = 0.0,
    max_expectancy_drawdown: float = 0.5,
) -> DriftReport:
    """Deterministic drift thresholds for ops freeze/rollback recommendations."""
    reasons: list[str] = []
    level = DriftLevel.OK

    if psi >= psi_freeze:
        reasons.append(f"psi_freeze:{psi:.4f}>={psi_freeze}")
        level = DriftLevel.FREEZE
    elif psi >= psi_watch:
        reasons.append(f"psi_watch:{psi:.4f}>={psi_watch}")
        level = DriftLevel.WATCH

    if brier_skill is not None and brier_skill < min_brier_skill:
        reasons.append(f"brier_skill:{brier_skill:.4f}<{min_brier_skill}")
        level = DriftLevel.FREEZE if level is DriftLevel.OK else level
        if brier_skill < min_brier_skill - 0.1:
            level = DriftLevel.ROLLBACK

    if expectancy_delta is not None and expectancy_delta < -abs(max_expectancy_drawdown):
        reasons.append(f"expectancy_delta:{expectancy_delta:.4f}")
        level = DriftLevel.ROLLBACK

    return DriftReport(
        level=level,
        reasons=tuple(reasons),
        psi=psi,
        brier_skill=brier_skill,
        expectancy_delta=expectancy_delta,
    )
