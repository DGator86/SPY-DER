"""Recorded-tape Dojo phase — consumes MarketExperienceProvider only."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, cast

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.decisions import DecisionRole, decide_on_packets
from spy_der.dojo.evaluator import default_evaluator
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionRecord,
    MarketExperienceProvider,
)

__all__ = ["RecordedDecision", "run_recorded_phase"]


class RecordedDecision:
    def __init__(
        self, snapshot_id: str, action: str, candidate_id: str | None
    ) -> None:
        self.snapshot_id = snapshot_id
        self.action = action
        self.candidate_id = candidate_id


def _filter_sessions(sessions: list[date], recent_days: int) -> list[date]:
    if recent_days <= 0 or not sessions:
        return sessions
    cutoff = max(sessions) - timedelta(days=recent_days - 1)
    return [s for s in sessions if s >= cutoff]


def _collect(
    provider: MarketExperienceProvider, sessions: list[date]
) -> tuple[list[MarketPacket], list[OutcomePacket]]:
    packets: list[MarketPacket] = []
    outcomes: list[OutcomePacket] = []
    for session in sessions:
        for packet in provider.snapshots(session):
            packets.append(packet)
            outcome = provider.outcome(packet.snapshot_id)
            if outcome is not None:
                outcomes.append(outcome)
    return packets, outcomes


def _lane_score(
    packets: list[MarketPacket],
    outcomes: list[OutcomePacket],
    evaluator: CandidateEvaluator,
    role: DecisionRole,
    *,
    decisions: Sequence[DecisionRecord] | None = None,
) -> dict[str, Any]:
    if decisions is None:
        decided = cast(Sequence[DecisionRecord], decide_on_packets(packets, role=role))
    else:
        decided = decisions
    report = evaluator.evaluate(decided, outcomes)
    payload = report.to_dict()
    payload["role"] = role.value
    payload["n_decisions_produced"] = len(decided)
    return payload


def run_recorded_phase(
    cfg: DojoConfig,
    provider: MarketExperienceProvider | None,
    *,
    decisions: list[DecisionRecord] | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    """Walk recorded experience, run decision lanes, score vs outcomes."""
    if cfg.skip_recorded:
        return {"status": "skipped", "note": "skip_recorded"}
    if provider is None:
        return {
            "status": "insufficient_data",
            "note": "no MarketExperienceProvider — wire 0DTE recorded feed",
        }

    sessions = _filter_sessions(list(provider.sessions()), cfg.recent_days)
    if len(sessions) < cfg.min_sessions:
        return {
            "status": "insufficient_data",
            "note": (
                f"{len(sessions)} sessions recorded "
                f"(< {cfg.min_sessions}) — let 0DTE accumulate tape"
            ),
            "n_sessions": len(sessions),
        }

    packets, outcomes = _collect(provider, sessions)
    if len(packets) < cfg.min_ticks:
        return {
            "status": "insufficient_data",
            "note": (
                f"{len(packets)} snapshots "
                f"(< {cfg.min_ticks}) — insufficient recorded ticks"
            ),
            "n_sessions": len(sessions),
            "n_snapshots": len(packets),
        }

    scorer: CandidateEvaluator = evaluator or default_evaluator()

    if decisions is not None:
        # Caller-supplied decisions: score as champion lane only.
        champion = _lane_score(
            packets, outcomes, scorer, DecisionRole.CHAMPION, decisions=decisions
        )
        baseline = None
        challenger = None
    else:
        champion = _lane_score(packets, outcomes, scorer, DecisionRole.CHAMPION)
        baseline = _lane_score(packets, outcomes, scorer, DecisionRole.BASELINE)
        challenger = _lane_score(packets, outcomes, scorer, DecisionRole.CHALLENGER)

    produced = int(champion.get("n_decisions_produced") or 0)
    matched = int(champion.get("n_matched") or 0)
    if produced == 0 and matched == 0:
        status = "baseline_only"
    else:
        status = "ok"

    return {
        "status": status,
        "n_sessions": len(sessions),
        "n_snapshots": len(packets),
        "n_outcomes": len(outcomes),
        "wf_folds": cfg.wf_folds,
        "evaluation": champion,
        "lanes": {
            "champion": champion,
            "baseline": baseline,
            "challenger": challenger,
        },
        "note": (
            "recorded tape scored via CandidateEvaluator over champion / "
            "challenger / deterministic baseline decision lanes"
        ),
    }
