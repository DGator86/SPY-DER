"""Adaptive learning cycle — diagnose → hypothesize → optimize → stage.

Never writes champion.json. Promotion stays human via ``learning.promotion``.
Staging requires complete Dojo gates when scored summaries are supplied.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.dojo.config import DojoConfig
from spy_der.dojo.protocols import CandidateEvaluator, MarketExperienceProvider
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.learning.hypotheses import diagnose, generate_hypotheses
from spy_der.learning.optimization import optimize_with_holdout
from spy_der.learning.promotion import stage_pending_review

__all__ = ["gates_pass", "run_learning_cycle"]


def gates_pass(
    *,
    recorded_summary: dict[str, Any] | None,
    sequential_summary: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    """Return whether staging gates pass and the blocking reasons."""
    notes: list[str] = []
    recorded = recorded_summary or {}
    sequential = sequential_summary or {}

    champ = (recorded.get("lanes") or {}).get("champion") or recorded.get("evaluation") or {}
    baseline = (recorded.get("lanes") or {}).get("baseline") or {}

    if recorded.get("status") not in {"ok", "baseline_only"}:
        notes.append(f"recorded_status={recorded.get('status')}")

    if champ.get("status") == "ok" and baseline.get("status") == "ok":
        champ_pnl = float(champ.get("total_pnl") or 0.0)
        base_pnl = float(baseline.get("total_pnl") or 0.0)
        if champ_pnl + 1e-9 < base_pnl:
            notes.append(
                f"champion_pnl {champ_pnl:.4f} < baseline_pnl {base_pnl:.4f}"
            )

    ft = sequential.get("mean_forward_transfer")
    if sequential.get("status") == "ok" and ft is not None and float(ft) < 0:
        notes.append(f"mean_forward_transfer {float(ft):+.4f} < 0")

    retention = sequential.get("retention") or {}
    if retention and not retention.get("ok", True):
        notes.append(str(retention.get("detail") or "retention_failed"))

    return (len(notes) == 0), tuple(notes)


def run_learning_cycle(
    *,
    mode: str = "dojo",
    configs_dir: str,
    experience: MarketExperienceProvider | None = None,
    trials: int = 15,
    holdout: float = 0.25,
    evaluator: CandidateEvaluator | None = None,
    sequential_summary: dict[str, Any] | None = None,
    recorded_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One adaptive-learning cycle. Stages pending_review only after gates."""
    cfg = DojoConfig(
        configs_dir=configs_dir,
        skip_learner=True,  # avoid recursion if recorded phase ever called learner
        learn_trials=trials,
        learn_holdout=holdout,
    )
    summary = recorded_summary or run_recorded_phase(
        cfg, experience, evaluator=evaluator
    )
    diagnoses = diagnose(summary if summary.get("status") != "skipped" else None)
    hypotheses = generate_hypotheses(diagnoses)
    optimization = optimize_with_holdout(
        hypotheses,
        trials=trials,
        holdout=holdout,
        experience_summary=summary,
        evaluator=evaluator,
    )

    ok, gate_notes = gates_pass(
        recorded_summary=summary,
        sequential_summary=sequential_summary,
    )

    staged_path: str | None = None
    outcome = "no_change"
    if optimization.status == "ok" and optimization.selected is not None:
        if not ok:
            outcome = "gates_blocked"
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
                    "gates": {"passed": True, "notes": []},
                    "experience_summary": {
                        "status": summary.get("status"),
                        "n_sessions": summary.get("n_sessions"),
                        "n_snapshots": summary.get("n_snapshots"),
                    },
                    "sequential_summary": {
                        "status": (sequential_summary or {}).get("status"),
                        "mean_forward_transfer": (sequential_summary or {}).get(
                            "mean_forward_transfer"
                        ),
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
        "gates": {"passed": ok, "notes": list(gate_notes)},
        "staged_path": staged_path,
        "configs_dir": str(Path(configs_dir)),
        "note": (
            "champion.json untouched — human promoter required"
            if outcome != "gates_blocked"
            else f"staging blocked: {'; '.join(gate_notes)}"
        ),
    }
