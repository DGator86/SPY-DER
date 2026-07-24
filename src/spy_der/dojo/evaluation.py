"""AI-specific evaluation primitives owned by SPY-DER."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from spy_der.contracts.integration import OutcomePacket
from spy_der.dojo.protocols import DecisionRecord

__all__ = [
    "OutcomeCandidateEvaluator",
    "RichEvaluationReport",
    "SimpleEvaluationReport",
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
    """Full Dojo evaluation: P&L, calibration, regret, directional hit."""

    status: str
    n_decisions: int
    n_outcomes: int
    n_matched: int
    total_pnl: float
    win_rate: float | None
    mean_session_pnl: float | None
    session_win_rate: float | None
    dir_hit: float | None
    regret: float | None
    calibration_error: float | None
    n_sessions: int
    trades: int
    notes: tuple[str, ...] = ()
    per_session: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "n_decisions": self.n_decisions,
            "n_outcomes": self.n_outcomes,
            "n_matched": self.n_matched,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "mean_session_pnl": self.mean_session_pnl,
            "session_win_rate": self.session_win_rate,
            "dir_hit": self.dir_hit,
            "regret": self.regret,
            "calibration_error": self.calibration_error,
            "n_sessions": self.n_sessions,
            "trades": self.trades,
            "notes": list(self.notes),
            "per_session": list(self.per_session),
        }


@dataclass
class _Match:
    snapshot_id: str
    session_key: str
    pnl: float
    win: bool
    direction_hit: bool | None
    regret: float | None
    confidence: float


def _session_key(outcome: OutcomePacket) -> str:
    return outcome.session_date.isoformat()


def _direction_hit(decision: DecisionRecord, outcome: OutcomePacket) -> bool | None:
    true_dir = outcome.labels.get("true_direction") if outcome.labels else None
    decided_dir = getattr(decision, "direction", None)
    if true_dir is None or decided_dir is None:
        return None
    true_norm = str(true_dir).strip().lower()
    decided_norm = str(decided_dir).strip().lower()
    # Normalize common aliases.
    aliases = {
        "up": "bullish",
        "down": "bearish",
        "bull": "bullish",
        "bear": "bearish",
        "long": "bullish",
        "short": "bearish",
    }
    true_norm = aliases.get(true_norm, true_norm)
    decided_norm = aliases.get(decided_norm, decided_norm)
    return true_norm == decided_norm


def _selection_regret(decision: DecisionRecord, outcome: OutcomePacket) -> float | None:
    by_cand = (outcome.labels or {}).get("realized_pnl_by_candidate")
    if not isinstance(by_cand, dict) or not by_cand:
        return None
    try:
        values = {str(k): float(v) for k, v in by_cand.items()}
    except (TypeError, ValueError):
        return None
    if not values:
        return None
    best = max(values.values())
    chosen_id = decision.candidate_id
    if chosen_id is None or chosen_id not in values:
        # Abstained / missed — regret vs best available.
        return best if best > 0 else 0.0
    return best - values[chosen_id]


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
    """Score decisions with P&L, directional hit, regret, and calibration."""
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
        # If outcome is tied to a different candidate than selected, use
        # per-candidate map when available; otherwise keep settlement pnl.
        by_cand = (outcome.labels or {}).get("realized_pnl_by_candidate")
        if (
            isinstance(by_cand, dict)
            and decision.candidate_id is not None
            and decision.candidate_id in by_cand
        ):
            try:
                pnl = float(by_cand[decision.candidate_id])
            except (TypeError, ValueError):
                pass
        conf = float(getattr(decision, "confidence", 0.0) or 0.0)
        matches.append(
            _Match(
                snapshot_id=decision.snapshot_id,
                session_key=_session_key(outcome),
                pnl=pnl,
                win=pnl > 0,
                direction_hit=_direction_hit(decision, outcome),
                regret=_selection_regret(decision, outcome),
                confidence=conf,
            )
        )
    if not matches:
        return RichEvaluationReport(
            status="insufficient_data",
            n_decisions=len(decisions),
            n_outcomes=len(outcomes),
            n_matched=0,
            total_pnl=0.0,
            win_rate=None,
            mean_session_pnl=None,
            session_win_rate=None,
            dir_hit=None,
            regret=None,
            calibration_error=None,
            n_sessions=0,
            trades=0,
            notes=tuple(notes) or ("no_matched_trades",),
        )

    wins = sum(1 for m in matches if m.win)
    dir_vals = [m.direction_hit for m in matches if m.direction_hit is not None]
    dir_hit = (sum(1 for d in dir_vals if d) / len(dir_vals)) if dir_vals else None
    regrets = [m.regret for m in matches if m.regret is not None]
    regret = (sum(regrets) / len(regrets)) if regrets else None

    # Calibration proxy: mean |confidence - outcome| where outcome is 1/0 win.
    if matches:
        calibration_error = sum(
            abs(m.confidence - (1.0 if m.win else 0.0)) for m in matches
        ) / len(matches)
    else:
        calibration_error = None

    by_session: dict[str, list[_Match]] = {}
    for match in matches:
        by_session.setdefault(match.session_key, []).append(match)
    per_session: list[dict[str, object]] = []
    session_pnls: list[float] = []
    for session, rows in sorted(by_session.items()):
        spnl = sum(r.pnl for r in rows)
        session_pnls.append(spnl)
        per_session.append(
            {
                "session": session,
                "trades": len(rows),
                "total_pnl": round(spnl, 6),
                "win_rate": sum(1 for r in rows if r.win) / len(rows),
            }
        )
    mean_session_pnl = sum(session_pnls) / len(session_pnls) if session_pnls else None
    session_win_rate = (
        sum(1 for p in session_pnls if p > 0) / len(session_pnls) if session_pnls else None
    )

    return RichEvaluationReport(
        status="ok",
        n_decisions=len(decisions),
        n_outcomes=len(outcomes),
        n_matched=len(matches),
        total_pnl=round(sum(m.pnl for m in matches), 6),
        win_rate=wins / len(matches),
        mean_session_pnl=(
            round(mean_session_pnl, 6) if mean_session_pnl is not None else None
        ),
        session_win_rate=session_win_rate,
        dir_hit=dir_hit,
        regret=(round(regret, 6) if regret is not None else None),
        calibration_error=(
            round(calibration_error, 6) if calibration_error is not None else None
        ),
        n_sessions=len(by_session),
        trades=len(matches),
        notes=tuple(notes),
        per_session=tuple(per_session),
    )


class OutcomeCandidateEvaluator:
    """Default CandidateEvaluator — scores decisions against OutcomePackets."""

    def evaluate(
        self,
        decisions: Sequence[DecisionRecord],
        outcomes: Sequence[OutcomePacket],
    ) -> RichEvaluationReport:
        return evaluate_decisions_rich(decisions, outcomes)


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
