"""Recorded-tape Dojo phase — consumes MarketExperienceProvider only."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.evaluation import (
    OutcomeCandidateEvaluator,
    evaluate_decisions_rich,
    forward_transfer,
)
from spy_der.dojo.outcomes import collect_outcomes
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionAuthority,
    DecisionRecord,
    MarketExperienceProvider,
)
from spy_der.integrations.zerodte.provider import reset_shadow_tick_cache

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


def _run_authority(
    authority: DecisionAuthority,
    packets: list[MarketPacket],
) -> list[DecisionRecord]:
    return [authority.decide(packet) for packet in packets]


def run_recorded_phase(
    cfg: DojoConfig,
    provider: MarketExperienceProvider | None,
    *,
    decisions: list[DecisionRecord] | None = None,
    authorities: dict[str, DecisionAuthority] | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    """Walk recorded experience; score champion / challenger / baseline vs outcomes."""
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
    for session in sessions:
        packets.extend(list(provider.snapshots(session)))

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

    outcomes: list[OutcomePacket] = collect_outcomes(provider, packets)
    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()

    authority_reports: dict[str, dict[str, Any]] = {}
    if authorities:
        for name, authority in authorities.items():
            # Shadow-tick cache keys omit agent identity — clear between authorities.
            reset_shadow_tick_cache()
            decided = _run_authority(authority, packets)
            report = scorer.evaluate(decided, outcomes)
            authority_reports[name] = report.to_dict()

    decision_list: list[DecisionRecord] = list(decisions or [])
    if not decision_list and authorities and "champion" in authorities:
        reset_shadow_tick_cache()
        decision_list = _run_authority(authorities["champion"], packets)

    if decision_list:
        primary = scorer.evaluate(decision_list, outcomes)
        report_dict = primary.to_dict()
        status = str(report_dict.get("status") or "ok")
        if status != "ok" and decision_list:
            # Decisions present but no matched trades still counts as scored.
            status = "ok" if outcomes else status
    else:
        empty = evaluate_decisions_rich([], outcomes)
        report_dict = empty.to_dict()
        status = "baseline_only"

    result: dict[str, Any] = {
        "status": status if decision_list or authorities else "baseline_only",
        "n_sessions": len(sessions),
        "n_snapshots": len(packets),
        "n_outcomes": len(outcomes),
        "wf_folds": cfg.wf_folds,
        "evaluation": report_dict,
        "note": (
            "recorded tape scored via DecisionAuthority + CandidateEvaluator"
            if authorities or decision_list
            else (
                "recorded baseline assembled via MarketExperienceProvider; "
                "attach DecisionAuthority to score AI decisions"
            )
        ),
    }
    if authority_reports:
        result["authorities"] = authority_reports
        champ = authority_reports.get("champion") or {}
        base = authority_reports.get("baseline") or {}
        if champ.get("total_pnl") is not None and base.get("total_pnl") is not None:
            result["forward_transfer"] = forward_transfer(
                float(champ["total_pnl"]),
                float(base["total_pnl"]),
            )
    return result
