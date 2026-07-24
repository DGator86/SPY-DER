"""SPY-DER adaptive learning — diagnoses, staging, human-gated promotion."""

from spy_der.learning.learner import gates_pass, run_learning_cycle
from spy_der.learning.memories import append_failure_episode, append_lesson
from spy_der.learning.promotion import (
    list_pending,
    promote_pending,
    reject_pending,
    stage_pending_review,
)

__all__ = [
    "append_failure_episode",
    "append_lesson",
    "gates_pass",
    "list_pending",
    "promote_pending",
    "reject_pending",
    "run_learning_cycle",
    "stage_pending_review",
]
