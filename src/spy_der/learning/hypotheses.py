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


#: Candidate deltas per diagnosis, most-promising variant first.
#:
#: One knob per hypothesis, deliberately. These changes are no longer only
#: staged for a human to read — the promotion trial re-runs the tape with the
#: selected one installed and promotes it if it wins, so a hypothesis has to be
#: attributable. Bundling a confidence floor with an OOD stand-down made the
#: pair untestable: the floor alone silences every tick on tape where the
#: deterministic agent reports a flat 0.5 confidence, and the bundle then scores
#: zero trades no matter what the other knob would have done.
_VARIANTS: dict[str, tuple[tuple[str, dict[str, Any]], ...]] = {
    "low_win_rate": (
        ("confidence_floor", {"min_confidence": 0.55}),
        ("size_derate", {"risk_max_size_scalar": 0.75}),
    ),
    "negative_pnl": (
        ("ood_abstain", {"prefer_abstain_on_ood": True}),
        ("confidence_floor", {"min_confidence": 0.6}),
    ),
    "thin_history": (("wider_holdout", {"learn_holdout": 0.35}),),
}
_HOLD = (("hold_champion", {"note": "hold_champion"}),)


def generate_hypotheses(diagnoses: Sequence[str]) -> list[Hypothesis]:
    """Map diagnoses to candidate config deltas (never applied to champion)."""
    out: list[Hypothesis] = []
    for idx, diagnosis in enumerate(diagnoses):
        if diagnosis == "insufficient_data":
            continue
        variants = _VARIANTS.get(diagnosis, _HOLD)
        for variant_idx, (variant, change) in enumerate(variants):
            suffix = f"-{variant}" if len(variants) > 1 else ""
            out.append(
                Hypothesis(
                    hypothesis_id=f"h-{idx}-{diagnosis}{suffix}",
                    diagnosis=diagnosis,
                    change=dict(change),
                    # Diagnosis order dominates; variant order breaks ties.
                    priority=1.0 - (idx * 0.1) - (variant_idx * 0.01),
                )
            )
    return out
