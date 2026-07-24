"""Adaptive learning cycle — diagnose → hypothesize → optimize → stage.

Never writes champion.json. Promotion stays human via ``learning.promotion``.
Staging is gated on retention / forward-transfer when those scores exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.dojo.config import DojoConfig
from spy_der.dojo.protocols import CandidateEvaluator, DecisionAuthority, MarketExperienceProvider
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.learning.hypotheses import diagnose, generate_hypotheses
from spy_der.learning.optimization import optimize_with_holdout
from spy_der.learning.promotion import stage_pending_review

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
) -> dict[str, Any]:
    """One adaptive-learning cycle. Stages pending_review only after gates pass."""
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
    diagnoses = diagnose(summary if summary.get("status") != "skipped" else None)
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
    outcome = "no_change"
    if optimization.status == "ok" and optimization.selected is not None:
        if not gates_ok:
            outcome = "gated"
        else:
            candidate_id = (
                f"{mode}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{optimization.selected.hypothesis_id}"
            )
            path = stage_pending_review(
                configs_dir,
                candidate_id=candidate_id,
                payload={
                    "mode": mode,
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
        "configs_dir": str(Path(configs_dir)),
        "note": (
            "champion.json untouched — human promoter required"
            if outcome == "promotion_recommended"
            else (
                "staging blocked by promotion gates"
                if outcome == "gated"
                else "no challenger staged"
            )
        ),
    }
