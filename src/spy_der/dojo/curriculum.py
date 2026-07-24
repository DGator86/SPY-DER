"""AI curriculum scheduling helpers for Dojo cadence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["CurriculumPlan", "DojoCadence", "plan_for_cadence"]


class DojoCadence(StrEnum):
    DAILY = "daily"
    RECENT = "recent"
    WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class CurriculumPlan:
    cadence: DojoCadence
    recent_days: int
    universe_days: int
    universes_per_gen: int
    generations: int
    learn_trials: int
    full_lattice: bool
    calibrate_from_recorded: bool


def plan_for_cadence(cadence: DojoCadence | str) -> CurriculumPlan:
    """Map systemd timer cadence to Dojo knobs (mirrors former 0DTE timers)."""
    value = DojoCadence(cadence)
    if value is DojoCadence.DAILY:
        return CurriculumPlan(
            cadence=value,
            recent_days=3,
            universe_days=3,
            universes_per_gen=6,
            generations=1,
            learn_trials=10,
            full_lattice=False,
            calibrate_from_recorded=False,
        )
    if value is DojoCadence.RECENT:
        return CurriculumPlan(
            cadence=value,
            recent_days=10,
            universe_days=10,
            universes_per_gen=8,
            generations=2,
            learn_trials=15,
            full_lattice=False,
            calibrate_from_recorded=False,
        )
    return CurriculumPlan(
        cadence=DojoCadence.WEEKLY,
        recent_days=0,
        universe_days=8,
        universes_per_gen=72,
        generations=2,
        learn_trials=25,
        full_lattice=True,
        calibrate_from_recorded=True,
    )
