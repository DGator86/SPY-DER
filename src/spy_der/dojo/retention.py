"""Retention / anti-forgetting checks for promotion gates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from spy_der.dojo.evaluation import forgetting_penalty

__all__ = ["RetentionResult", "check_retention"]


@dataclass(frozen=True, slots=True)
class RetentionResult:
    ok: bool
    penalty: float
    threshold: float
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "penalty": self.penalty,
            "threshold": self.threshold,
            "detail": self.detail,
        }


def check_retention(
    before_scores: Sequence[float],
    after_scores: Sequence[float],
    *,
    max_penalty: float = 0.05,
) -> RetentionResult:
    """Fail closed when a candidate regresses the retention panel too far."""
    penalty = forgetting_penalty(before_scores, after_scores)
    ok = penalty <= max_penalty
    return RetentionResult(
        ok=ok,
        penalty=penalty,
        threshold=max_penalty,
        detail=(
            "retention_ok"
            if ok
            else f"forgetting_penalty {penalty:.4f} exceeds {max_penalty:.4f}"
        ),
    )
