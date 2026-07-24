"""AI-specific evaluation primitives owned by SPY-DER."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from spy_der.contracts.integration import OutcomePacket
from spy_der.dojo.protocols import DecisionRecord

__all__ = [
    "SimpleEvaluationReport",
    "evaluate_decisions",
    "forgetting_penalty",
    "forward_transfer",
]


@dataclass(frozen=True, slots=True)
class SimpleEvaluationReport:
    status: str
    n_decisions: int
    n_outcomes: int
    n_matched: int
    total_pnl: float
    win_rate: float | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "n_decisions": self.n_decisions,
            "n_outcomes": self.n_outcomes,
            "n_matched": self.n_matched,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "notes": list(self.notes),
        }


@dataclass
class _Match:
    snapshot_id: str
    pnl: float
    win: bool


def evaluate_decisions(
    decisions: Sequence[DecisionRecord],
    outcomes: Sequence[OutcomePacket],
) -> SimpleEvaluationReport:
    """Score decided trades against outcome packets (no 0DTE imports)."""
    by_snap = {o.snapshot_id: o for o in outcomes}
    matches: list[_Match] = []
    notes: list[str] = []
    for decision in decisions:
        outcome = by_snap.get(decision.snapshot_id)
        if outcome is None:
            continue
        if decision.action not in {"TRADE", "SELECT_CANDIDATE"}:
            continue
        if outcome.realized_pnl is None:
            notes.append(f"missing_pnl:{decision.snapshot_id}")
            continue
        pnl = float(outcome.realized_pnl)
        matches.append(_Match(decision.snapshot_id, pnl, pnl > 0))
    if not matches:
        return SimpleEvaluationReport(
            status="insufficient_data",
            n_decisions=len(decisions),
            n_outcomes=len(outcomes),
            n_matched=0,
            total_pnl=0.0,
            win_rate=None,
            notes=tuple(notes) or ("no_matched_trades",),
        )
    wins = sum(1 for m in matches if m.win)
    return SimpleEvaluationReport(
        status="ok",
        n_decisions=len(decisions),
        n_outcomes=len(outcomes),
        n_matched=len(matches),
        total_pnl=round(sum(m.pnl for m in matches), 6),
        win_rate=wins / len(matches),
        notes=tuple(notes),
    )


def forward_transfer(champion_score: float, baseline_score: float) -> float:
    """FT = J(champion) - J(baseline). Positive means learning helped."""
    return float(champion_score) - float(baseline_score)


def forgetting_penalty(
    before_scores: Sequence[float],
    after_scores: Sequence[float],
) -> float:
    """Mean degradation on a retention panel (anti-forgetting penalty F)."""
    if not before_scores or len(before_scores) != len(after_scores):
        return 0.0
    deltas = [float(b) - float(a) for b, a in zip(before_scores, after_scores, strict=True)]
    # Only count regressions (positive delta = forgot).
    regressions = [d for d in deltas if d > 0]
    if not regressions:
        return 0.0
    return sum(regressions) / len(regressions)


@dataclass
class WalkForwardFoldResult:
    fold_id: int
    score: float
    n_sessions: int
    notes: dict[str, Any] = field(default_factory=dict)
