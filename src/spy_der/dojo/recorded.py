"""Recorded-tape Dojo phase — consumes MarketExperienceProvider only."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.evaluation import SimpleEvaluationReport, evaluate_decisions
from spy_der.dojo.protocols import DecisionRecord, MarketExperienceProvider

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


def run_recorded_phase(
    cfg: DojoConfig,
    provider: MarketExperienceProvider | None,
    *,
    decisions: list[DecisionRecord] | None = None,
) -> dict[str, Any]:
    """Walk recorded experience and score available decisions vs outcomes."""
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

    packets: list[MarketPacket] = []
    outcomes: list[OutcomePacket] = []
    for session in sessions:
        for packet in provider.snapshots(session):
            packets.append(packet)
            outcome = provider.outcome(packet.snapshot_id)
            if outcome is not None:
                outcomes.append(outcome)

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

    decision_list: list[DecisionRecord] = list(decisions or [])
    report: SimpleEvaluationReport = evaluate_decisions(decision_list, outcomes)
    return {
        "status": "ok" if report.status == "ok" or decision_list else "baseline_only",
        "n_sessions": len(sessions),
        "n_snapshots": len(packets),
        "n_outcomes": len(outcomes),
        "wf_folds": cfg.wf_folds,
        "evaluation": report.to_dict(),
        "note": (
            "recorded baseline assembled via MarketExperienceProvider; "
            "full walk-forward scoring uses CandidateEvaluator when wired"
        ),
    }
