"""AI-specific evaluation primitives owned by SPY-DER."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from spy_der.contracts.integration import OutcomePacket
from spy_der.dojo.protocols import DecisionRecord

__all__ = [
    "RichEvaluationReport",
    "SimpleEvaluationReport",
    "WalkForwardFoldResult",
    "evaluate_decisions",
    "evaluate_decisions_rich",
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


@dataclass(frozen=True, slots=True)
class RichEvaluationReport:
    """Full Dojo scorecard: P&L, win rate, directional hit, regret, sessions."""

    status: str
    n_decisions: int
    n_outcomes: int
    n_matched: int
    total_pnl: float
    win_rate: float | None
    dir_hit: float | None
    mean_session_pnl: float | None
    session_win_rate: float | None
    n_sessions: int
    trades: int
    regret: float | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "n_decisions": self.n_decisions,
            "n_outcomes": self.n_outcomes,
            "n_matched": self.n_matched,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "dir_hit": self.dir_hit,
            "mean_session_pnl": self.mean_session_pnl,
            "session_win_rate": self.session_win_rate,
            "n_sessions": self.n_sessions,
            "trades": self.trades,
            "regret": self.regret,
            "notes": list(self.notes),
        }


@dataclass
class _Match:
    snapshot_id: str
    session_date: str
    pnl: float
    win: bool
    dir_hit: bool | None


def _decision_direction(decision: DecisionRecord) -> str | None:
    direction = getattr(decision, "direction", None)
    if isinstance(direction, str) and direction:
        return direction.lower()
    return None


def _realized_direction(outcome: OutcomePacket) -> str | None:
    labels = outcome.labels or {}
    for key in ("realized_direction", "direction", "move_direction"):
        value = labels.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    if outcome.realized_pnl is None:
        return None
    pnl = float(outcome.realized_pnl)
    if pnl > 0:
        return "up"
    if pnl < 0:
        return "down"
    return None


def _normalize_dir(value: str) -> str:
    v = value.lower()
    if v in {"up", "bull", "bullish", "long", "call"}:
        return "up"
    if v in {"down", "bear", "bearish", "short", "put"}:
        return "down"
    return v


def _score_matches(
    decisions: Sequence[DecisionRecord],
    outcomes: Sequence[OutcomePacket],
) -> tuple[list[_Match], list[str]]:
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
        pred = _decision_direction(decision)
        realized = _realized_direction(outcome)
        dir_ok: bool | None = None
        if pred is not None and realized is not None:
            dir_ok = _normalize_dir(pred) == _normalize_dir(realized)
        matches.append(
            _Match(
                snapshot_id=decision.snapshot_id,
                session_date=outcome.session_date.isoformat(),
                pnl=pnl,
                win=pnl > 0,
                dir_hit=dir_ok,
            )
        )
    return matches, notes


def evaluate_decisions(
    decisions: Sequence[DecisionRecord],
    outcomes: Sequence[OutcomePacket],
) -> SimpleEvaluationReport:
    """Score decided trades against outcome packets (no 0DTE imports)."""
    rich = evaluate_decisions_rich(decisions, outcomes)
    return SimpleEvaluationReport(
        status=rich.status,
        n_decisions=rich.n_decisions,
        n_outcomes=rich.n_outcomes,
        n_matched=rich.n_matched,
        total_pnl=rich.total_pnl,
        win_rate=rich.win_rate,
        notes=rich.notes,
    )


def evaluate_decisions_rich(
    decisions: Sequence[DecisionRecord],
    outcomes: Sequence[OutcomePacket],
) -> RichEvaluationReport:
    """Full scorecard used by CandidateEvaluator and Dojo phases."""
    matches, notes = _score_matches(decisions, outcomes)
    if not matches:
        return RichEvaluationReport(
            status="insufficient_data",
            n_decisions=len(decisions),
            n_outcomes=len(outcomes),
            n_matched=0,
            total_pnl=0.0,
            win_rate=None,
            dir_hit=None,
            mean_session_pnl=None,
            session_win_rate=None,
            n_sessions=0,
            trades=0,
            regret=None,
            notes=tuple(notes) or ("no_matched_trades",),
        )

    wins = sum(1 for m in matches if m.win)
    dir_known = [m for m in matches if m.dir_hit is not None]
    dir_hits = sum(1 for m in dir_known if m.dir_hit)

    by_session: dict[str, list[float]] = defaultdict(list)
    for m in matches:
        by_session[m.session_date].append(m.pnl)
    session_pnls = [sum(v) for v in by_session.values()]
    session_wins = sum(1 for p in session_pnls if p > 0)

    # Regret vs always-take-best-outcome-per-snapshot oracle (upper bound).
    best_by_snap = {
        o.snapshot_id: float(o.realized_pnl)
        for o in outcomes
        if o.realized_pnl is not None
    }
    oracle = sum(max(0.0, best_by_snap.get(m.snapshot_id, 0.0)) for m in matches)
    realized = sum(m.pnl for m in matches)
    regret = round(max(0.0, oracle - realized), 6) if oracle > 0 else 0.0

    return RichEvaluationReport(
        status="ok",
        n_decisions=len(decisions),
        n_outcomes=len(outcomes),
        n_matched=len(matches),
        total_pnl=round(realized, 6),
        win_rate=wins / len(matches),
        dir_hit=(dir_hits / len(dir_known)) if dir_known else None,
        mean_session_pnl=(
            round(sum(session_pnls) / len(session_pnls), 6) if session_pnls else None
        ),
        session_win_rate=(
            session_wins / len(session_pnls) if session_pnls else None
        ),
        n_sessions=len(session_pnls),
        trades=len(matches),
        regret=regret,
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
    deltas = [
        float(b) - float(a) for b, a in zip(before_scores, after_scores, strict=True)
    ]
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
