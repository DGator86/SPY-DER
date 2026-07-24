"""Adaptive learning cycle — diagnose → hypothesize → optimize → stage.

Never writes champion.json. Promotion stays human via ``learning.promotion``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.dojo.config import DojoConfig
from spy_der.dojo.protocols import MarketExperienceProvider
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.learning.hypotheses import diagnose, generate_hypotheses
from spy_der.learning.optimization import optimize_with_holdout
from spy_der.learning.promotion import stage_pending_review

__all__ = ["run_learning_cycle"]


def run_learning_cycle(
    *,
    mode: str = "dojo",
    configs_dir: str,
    experience: MarketExperienceProvider | None = None,
    trials: int = 15,
    holdout: float = 0.25,
) -> dict[str, Any]:
    """One adaptive-learning cycle. Stages pending_review only."""
    cfg = DojoConfig(
        configs_dir=configs_dir,
        skip_learner=True,  # avoid recursion if recorded phase ever called learner
        learn_trials=trials,
        learn_holdout=holdout,
    )
    summary = run_recorded_phase(cfg, experience)
    diagnoses = diagnose(summary if summary.get("status") != "skipped" else None)
    hypotheses = generate_hypotheses(diagnoses)
    optimization = optimize_with_holdout(
        hypotheses,
        trials=trials,
        holdout=holdout,
        experience_summary=summary,
    )

    staged_path: str | None = None
    outcome = "no_change"
    if optimization.status == "ok" and optimization.selected is not None:
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
                "experience_summary": {
                    "status": summary.get("status"),
                    "n_sessions": summary.get("n_sessions"),
                    "n_snapshots": summary.get("n_snapshots"),
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
        "staged_path": staged_path,
        "configs_dir": str(Path(configs_dir)),
        "note": "champion.json untouched — human promoter required",
    }
