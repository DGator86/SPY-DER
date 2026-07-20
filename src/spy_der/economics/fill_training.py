"""Fill-model support / fallback helpers (System A prediction/fill_training.py)."""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.economics import FillRecord

__all__ = [
    "FillSupportThresholds",
    "blend_with_prior",
    "empirical_weight",
    "fallback_level",
    "stage1_attempts",
    "stage2_fills",
]


@dataclass(frozen=True, slots=True)
class FillSupportThresholds:
    minimum_sessions: int = 40
    minimum_attempts: int = 200
    minimum_fills: int = 100
    minimum_family_attempts: int = 50
    minimum_family_fills: int = 25
    prior_equivalent_support: int = 100


def empirical_weight(support: int, prior_equivalent_support: int = 100) -> float:
    s = max(int(support), 0)
    p = max(int(prior_equivalent_support), 1)
    return float(s / (s + p))


def blend_with_prior(
    empirical: float,
    prior: float,
    support: int,
    prior_equivalent_support: int = 100,
) -> tuple[float, float]:
    w = empirical_weight(support, prior_equivalent_support)
    blended = w * float(empirical) + (1.0 - w) * float(prior)
    return float(blended), float(w)


def fallback_level(
    *,
    family_attempts: int,
    family_fills: int,
    global_attempts: int,
    global_fills: int,
    thresholds: FillSupportThresholds | None = None,
) -> str:
    thr = thresholds or FillSupportThresholds()
    if family_attempts >= thr.minimum_family_attempts and family_fills >= thr.minimum_family_fills:
        return "exact_family"
    if global_attempts >= thr.minimum_attempts and global_fills >= thr.minimum_fills:
        return "global_empirical"
    return "deterministic_prior"


def stage1_attempts(records: list[FillRecord] | tuple[FillRecord, ...]) -> list[FillRecord]:
    """All attempts, including cancels/rejects/unfilled."""
    return list(records)


def stage2_fills(records: list[FillRecord] | tuple[FillRecord, ...]) -> list[FillRecord]:
    """Completed fills only."""
    return [r for r in records if r.filled and r.fill_credit is not None]
