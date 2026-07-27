"""Adaptive learning cycle — diagnose → hypothesize → optimize → stage.

This module never writes champion.json; it stages a challenger and hands the
change on. Whether that challenger is *enacted* is decided by the promotion
trial in :mod:`spy_der.learning.promotion_trial`, which re-runs the system with
the change installed. Staging itself is gated on retention / forward-transfer
when those scores exist, and a hypothesis that changes no live decision knob is
not staged at all — there would be nothing to promote.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spy_der.decisions.knobs import actionable_knobs
from spy_der.learning.hypotheses import (
    WEAK_ARCHETYPE_PREFIX,
    diagnose,
    generate_hypotheses,
)
from spy_der.learning.optimization import optimize_with_holdout
from spy_der.learning.promotion import stage_pending_review

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spy_der.dojo.protocols import (
        CandidateEvaluator,
        DecisionAuthority,
        MarketExperienceProvider,
    )

# spy_der.dojo imports this module through its package __init__, so importing
# any dojo module at learning's import time makes `import spy_der.learning`
# fail depending on which side is imported first. Dojo pieces are pulled in
# where they are used instead.

__all__ = ["run_learning_cycle", "staging_gates_pass"]


def staging_gates_pass(
    *,
    recorded_result: dict[str, Any] | None,
    sequential_result: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    """Return whether a challenger may be staged after complete gates."""
    notes: list[str] = []
    ok = True

    sequential = sequential_result or {}
    retention = sequential.get("retention")
    if isinstance(retention, dict) and retention.get("ok") is False:
        ok = False
        notes.append(str(retention.get("detail") or "retention_failed"))

    mean_ft = sequential.get("mean_forward_transfer")
    if mean_ft is not None and float(mean_ft) < 0:
        ok = False
        notes.append(f"mean_forward_transfer {float(mean_ft):+.4f} < 0")

    recorded = recorded_result or {}
    evaluation = recorded.get("evaluation") or {}
    # Only gate on catastrophic recorded failure when we actually scored trades.
    trades = int(evaluation.get("trades") or evaluation.get("n_matched") or 0)
    total_pnl = evaluation.get("total_pnl")
    if trades >= 5 and total_pnl is not None and float(total_pnl) < 0:
        win_rate = evaluation.get("win_rate")
        if win_rate is not None and float(win_rate) < 0.35:
            ok = False
            notes.append(
                f"recorded win_rate {float(win_rate):.2f} with pnl {float(total_pnl):+.4f}"
            )

    if ok and not notes:
        notes.append("gates_ok")
    return ok, tuple(notes)


def run_learning_cycle(
    *,
    mode: str = "dojo",
    configs_dir: str,
    experience: MarketExperienceProvider | None = None,
    trials: int = 15,
    holdout: float = 0.25,
    authorities: dict[str, DecisionAuthority] | None = None,
    evaluator: CandidateEvaluator | None = None,
    sequential_result: dict[str, Any] | None = None,
    recorded_result: dict[str, Any] | None = None,
    weak_archetypes: Sequence[str] = (),
) -> dict[str, Any]:
    """One adaptive-learning cycle. Stages pending_review only after gates pass.

    ``weak_archetypes`` are the robustness gaps earlier runs recorded (worst
    first). They are diagnoses in their own right: a tape that is profitable
    overall while the panel shows crash underwater is not a system with nothing
    to learn, and treating it as one is how the gaps stayed open.
    """
    from spy_der.dojo.config import DojoConfig
    from spy_der.dojo.recorded import run_recorded_phase

    cfg = DojoConfig(
        configs_dir=configs_dir,
        skip_learner=True,  # avoid recursion if recorded phase ever called learner
        learn_trials=trials,
        learn_holdout=holdout,
    )
    summary = recorded_result or run_recorded_phase(
        cfg,
        experience,
        authorities=authorities,
        evaluator=evaluator,
    )
    diagnoses = diagnose(
        summary if summary.get("status") != "skipped" else None,
        weak_archetypes=weak_archetypes,
    )
    hypotheses = generate_hypotheses(diagnoses)
    optimization = optimize_with_holdout(
        hypotheses,
        trials=trials,
        holdout=holdout,
        experience_summary=summary,
    )

    gates_ok, gate_notes = staging_gates_pass(
        recorded_result=summary,
        sequential_result=sequential_result,
    )

    staged_path: str | None = None
    staged_candidate_id: str | None = None
    staged_changes: dict[str, Any] = {}
    staged_target: str | None = None
    outcome = "no_change"
    if optimization.status == "ok" and optimization.selected is not None:
        knobs = actionable_knobs(optimization.selected.change)
        if not gates_ok:
            outcome = "gated"
        elif not knobs:
            # e.g. the "stable_baseline" diagnosis, whose hypothesis is
            # literally "hold_champion". Calling that a promotion recommendation
            # asks a promoter to enact nothing.
            outcome = "no_change"
        else:
            candidate_id = (
                f"{mode}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{optimization.selected.hypothesis_id}"
            )
            staged_candidate_id = candidate_id
            staged_changes = knobs
            staged_target = optimization.selected.target_archetype
            path = stage_pending_review(
                configs_dir,
                candidate_id=candidate_id,
                auto_promote=True,
                payload={
                    "knobs": knobs,
                    "mode": mode,
                    "target_archetype": staged_target,
                    "hypothesis": optimization.selected.to_dict(),
                    "diagnoses": diagnoses,
                    "optimization": optimization.to_dict(),
                    "gates": list(gate_notes),
                    "experience_summary": {
                        "status": summary.get("status"),
                        "n_sessions": summary.get("n_sessions"),
                        "n_snapshots": summary.get("n_snapshots"),
                        "forward_transfer": summary.get("forward_transfer"),
                    },
                    "sequential": {
                        "status": (sequential_result or {}).get("status"),
                        "mean_forward_transfer": (sequential_result or {}).get(
                            "mean_forward_transfer"
                        ),
                        "retention": (sequential_result or {}).get("retention"),
                    },
                },
            )
            staged_path = str(path)
            outcome = "promotion_recommended"

    return {
        "status": "ok",
        "mode": mode,
        "outcome": outcome,
        "diagnoses": diagnoses,
        "hypotheses": [h.to_dict() for h in hypotheses],
        "optimization": optimization.to_dict(),
        "gates": list(gate_notes),
        "staged_path": staged_path,
        "staged_candidate_id": staged_candidate_id,
        "staged_changes": staged_changes,
        "staged_target_archetype": staged_target,
        "weak_archetypes": list(weak_archetypes),
        "configs_dir": str(Path(configs_dir)),
        "note": _outcome_note(outcome, staged_target, optimization, diagnoses),
    }


def _outcome_note(
    outcome: str,
    target: str | None,
    optimization: Any,
    diagnoses: Sequence[str],
) -> str:
    if outcome == "promotion_recommended":
        if target:
            return (
                f"challenger staged to repair {target} — the promotion trial "
                "re-runs the system with it and holds it to that archetype"
            )
        return "challenger staged — promotion trial re-runs the system with it"
    if outcome == "gated":
        return "staging blocked by promotion gates"
    # "no challenger staged" on its own reads as "nothing to fix" even when the
    # panel just diagnosed four weak archetypes. Say which wall it hit.
    gaps = [d for d in diagnoses if d.startswith(WEAK_ARCHETYPE_PREFIX)]
    reason = "; ".join(getattr(optimization, "notes", ()) or ())
    status = getattr(optimization, "status", "")
    if gaps:
        # Including status == "ok": the optimizer can succeed and still select a
        # hypothesis with no live knob, which is the case most likely to read as
        # "nothing to fix" while the panel says otherwise.
        return (
            f"{len(gaps)} gap(s) diagnosed but nothing staged — "
            f"{reason or status or 'no actionable knob'}"
        )
    if status != "ok" and reason:
        return f"no challenger staged — {reason}"
    return "no challenger staged"
