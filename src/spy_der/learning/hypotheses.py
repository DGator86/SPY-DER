"""Hypothesis generation from recorded experience diagnoses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Hypothesis", "diagnose", "generate_hypotheses"]


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    diagnosis: str
    change: dict[str, Any]
    priority: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "diagnosis": self.diagnosis,
            "change": dict(self.change),
            "priority": self.priority,
        }


def diagnose(experience_summary: dict[str, Any] | None) -> list[str]:
    """Produce coarse failure-mode labels from an experience summary."""
    if not experience_summary:
        return ["insufficient_data"]
    diagnoses: list[str] = []
    n_sessions = int(experience_summary.get("n_sessions") or 0)
    if n_sessions < 3:
        diagnoses.append("thin_history")
    evaluation = experience_summary.get("evaluation") or {}
    win_rate = evaluation.get("win_rate")
    if win_rate is not None and float(win_rate) < 0.45:
        diagnoses.append("low_win_rate")
    total_pnl = evaluation.get("total_pnl")
    if total_pnl is not None and float(total_pnl) < 0:
        diagnoses.append("negative_pnl")
    if not diagnoses:
        diagnoses.append("stable_baseline")
    return diagnoses


def generate_hypotheses(diagnoses: Sequence[str]) -> list[Hypothesis]:
    """Map diagnoses to staged config deltas (never applied to champion)."""
    out: list[Hypothesis] = []
    for idx, diagnosis in enumerate(diagnoses):
        if diagnosis == "insufficient_data":
            continue
        change: dict[str, Any]
        if diagnosis == "low_win_rate":
            change = {"risk_max_size_scalar": 0.75, "min_confidence": 0.55}
        elif diagnosis == "negative_pnl":
            change = {"prefer_abstain_on_ood": True, "min_confidence": 0.6}
        elif diagnosis == "thin_history":
            change = {"learn_holdout": 0.35}
        else:
            change = {"note": "hold_champion"}
        out.append(
            Hypothesis(
                hypothesis_id=f"h-{idx}-{diagnosis}",
                diagnosis=diagnosis,
                change=change,
                priority=1.0 - (idx * 0.1),
            )
        )
    return out
