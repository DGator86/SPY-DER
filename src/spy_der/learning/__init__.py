"""SPY-DER adaptive learning — diagnoses, staging, evidence-gated promotion."""

from spy_der.learning.learner import run_learning_cycle, staging_gates_pass
from spy_der.learning.promotion import (
    auto_promote_pending,
    current_champion,
    list_pending,
    promote_pending,
    reject_pending,
    rollback_champion,
    stage_pending_review,
)
from spy_der.learning.promotion_trial import (
    PromotionThresholds,
    PromotionTrial,
    run_promotion_trial,
)

__all__ = [
    "PromotionThresholds",
    "PromotionTrial",
    "auto_promote_pending",
    "current_champion",
    "list_pending",
    "promote_pending",
    "reject_pending",
    "rollback_champion",
    "run_learning_cycle",
    "run_promotion_trial",
    "stage_pending_review",
    "staging_gates_pass",
]
