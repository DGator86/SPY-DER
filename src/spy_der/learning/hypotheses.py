"""Hypothesis generation from recorded experience and synthetic gaps."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "WEAK_ARCHETYPE_PREFIX",
    "Hypothesis",
    "diagnose",
    "generate_hypotheses",
    "target_archetype_of",
]

#: Diagnosis prefix for "the system loses money in this market archetype".
WEAK_ARCHETYPE_PREFIX = "weak_archetype:"


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    diagnosis: str
    change: dict[str, Any]
    priority: float
    #: Archetype this change is meant to repair, when it came from a gap.
    #: The promotion trial holds it to that: a change staged to fix crash has
    #: to beat the incumbent *on crash*, not merely on the average.
    target_archetype: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "diagnosis": self.diagnosis,
            "change": dict(self.change),
            "priority": self.priority,
            "target_archetype": self.target_archetype,
        }


def target_archetype_of(diagnosis: str) -> str | None:
    """``weak_archetype:crash`` → ``crash``; anything else → None."""
    if diagnosis.startswith(WEAK_ARCHETYPE_PREFIX):
        return diagnosis[len(WEAK_ARCHETYPE_PREFIX) :] or None
    return None


def diagnose(
    experience_summary: dict[str, Any] | None,
    *,
    weak_archetypes: Sequence[str] = (),
) -> list[str]:
    """Failure-mode labels from recorded tape *and* synthetic robustness gaps.

    ``weak_archetypes`` (worst first) is what the universe panel found the
    system losing money in. Without them a profitable overall tape reads as
    ``stable_baseline`` and the Dojo concludes there is nothing to learn —
    while the robustness matrix is showing five archetypes underwater.
    """
    diagnoses: list[str] = []
    if experience_summary:
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

    for archetype in weak_archetypes:
        if archetype:
            diagnoses.append(f"{WEAK_ARCHETYPE_PREFIX}{archetype}")

    if not diagnoses:
        diagnoses.append("insufficient_data" if not experience_summary else "stable_baseline")
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

#: What to try when an archetype is losing money. The knobs are the ones the
#: live path can enact, ordered by how well the trial can measure them: an OOD
#: stand-down changes behaviour on exactly the ticks a violent archetype
#: produces, a confidence floor is coarser, and a size derate is invisible to a
#: P&L-per-trade evaluator. Repairing an archetype means "stop paying for the
#: ticks you cannot read" — the Dojo cannot invent a strategy for crash, but it
#: can learn to stand down in it.
_WEAK_ARCHETYPE_VARIANTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("ood_abstain", {"prefer_abstain_on_ood": True}),
    ("confidence_floor", {"min_confidence": 0.6}),
    ("size_derate", {"risk_max_size_scalar": 0.5}),
)


def generate_hypotheses(diagnoses: Sequence[str]) -> list[Hypothesis]:
    """Map diagnoses to candidate config deltas (never applied to champion)."""
    out: list[Hypothesis] = []
    for idx, diagnosis in enumerate(diagnoses):
        if diagnosis == "insufficient_data":
            continue
        target = target_archetype_of(diagnosis)
        variants = (
            _WEAK_ARCHETYPE_VARIANTS if target else _VARIANTS.get(diagnosis, _HOLD)
        )
        label = f"weak-{target}" if target else diagnosis
        for variant_idx, (variant, change) in enumerate(variants):
            suffix = f"-{variant}" if len(variants) > 1 else ""
            out.append(
                Hypothesis(
                    hypothesis_id=f"h-{idx}-{label}{suffix}",
                    diagnosis=diagnosis,
                    change=dict(change),
                    # Diagnosis order dominates; variant order breaks ties.
                    priority=1.0 - (idx * 0.1) - (variant_idx * 0.01),
                    target_archetype=target,
                )
            )
    return out
