"""Sequential Dojo — prequential / forward-transfer measurement spine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.dojo.decisions import DecisionRole, decide_on_packets
from spy_der.dojo.evaluation import forgetting_penalty, forward_transfer
from spy_der.dojo.evaluator import default_evaluator
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionRecord,
    MarketExperienceProvider,
)
from spy_der.dojo.retention import check_retention

__all__ = ["SequentialDojoConfig", "run_sequential_dojo"]


@dataclass
class SequentialDojoConfig:
    min_warm_sessions: int = 2
    sealed_sessions: tuple[str, ...] = ()
    embargo_sessions: int = 0
    reports_dir: str = ""
    run_decisions: bool = True
    retention_max_penalty: float = 0.05


def _session_packets(
    provider: MarketExperienceProvider, session: date
) -> tuple[list[MarketPacket], list[OutcomePacket]]:
    packets = list(provider.snapshots(session))
    outcomes: list[OutcomePacket] = []
    for packet in packets:
        outcome = provider.outcome(packet.snapshot_id)
        if outcome is not None:
            outcomes.append(outcome)
    return packets, outcomes


def _score_session(
    packets: list[MarketPacket],
    outcomes: list[OutcomePacket],
    evaluator: CandidateEvaluator,
    role: DecisionRole,
) -> float:
    if not packets or not outcomes:
        return 0.0
    decisions = decide_on_packets(packets, role=role)
    report = evaluator.evaluate(cast(Sequence[DecisionRecord], decisions), outcomes)
    payload = report.to_dict()
    mean = payload.get("mean_session_pnl")
    if mean is not None:
        return float(cast(float | int | str, mean))
    total = payload.get("total_pnl") or 0.0
    return float(cast(float | int | str, total))


def run_sequential_dojo(
    provider: MarketExperienceProvider | None,
    *,
    cfg: SequentialDojoConfig | None = None,
    evaluator: CandidateEvaluator | None = None,
    champion_scores: Sequence[float] | None = None,
    baseline_scores: Sequence[float] | None = None,
    retention_before: Sequence[float] | None = None,
    retention_after: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Blind-day curriculum with optional in-loop decision scoring.

    When ``run_decisions`` is true and an experience provider is attached, each
    blind day runs:

        warm sessions (sealed) → SPY-DER champion / baseline decisions on day t
        → outcome scoring → forward transfer → retention panel

    Externally supplied ``champion_scores`` / ``baseline_scores`` still work for
    callers that score elsewhere.
    """
    cfg = cfg or SequentialDojoConfig()
    if provider is None:
        return {
            "status": "insufficient_data",
            "note": "no MarketExperienceProvider",
        }

    sealed = {date.fromisoformat(s) for s in cfg.sealed_sessions}
    sessions = [s for s in provider.sessions() if s not in sealed]
    if len(sessions) <= cfg.min_warm_sessions:
        return {
            "status": "insufficient_data",
            "note": (
                f"{len(sessions)} learnable sessions "
                f"(need > {cfg.min_warm_sessions} warm sessions)"
            ),
            "n_sessions": len(sessions),
        }

    scorer = evaluator or default_evaluator()
    computed_champion: list[float] = []
    computed_baseline: list[float] = []
    curriculum: list[dict[str, Any]] = []

    for idx, session in enumerate(sessions):
        warm = sessions[: max(0, idx - cfg.embargo_sessions)]
        if len(warm) < cfg.min_warm_sessions:
            continue

        entry: dict[str, Any] = {
            "session": session.isoformat(),
            "warm_sessions": [s.isoformat() for s in warm],
            "scored_blind": True,
        }

        if cfg.run_decisions and champion_scores is None and baseline_scores is None:
            packets, outcomes = _session_packets(provider, session)
            champ = _score_session(
                packets, outcomes, scorer, DecisionRole.CHAMPION
            )
            base = _score_session(
                packets, outcomes, scorer, DecisionRole.BASELINE
            )
            computed_champion.append(champ)
            computed_baseline.append(base)
            ft = forward_transfer(champ, base)
            entry["forward_transfer"] = ft
            entry["champion_score"] = champ
            entry["baseline_score"] = base
            entry["n_packets"] = len(packets)
            entry["n_outcomes"] = len(outcomes)
        elif (
            champion_scores is not None
            and baseline_scores is not None
            and len(champion_scores) > len(curriculum)
            and len(baseline_scores) > len(curriculum)
        ):
            ft = forward_transfer(
                champion_scores[len(curriculum)],
                baseline_scores[len(curriculum)],
            )
            entry["forward_transfer"] = ft
            entry["champion_score"] = champion_scores[len(curriculum)]
            entry["baseline_score"] = baseline_scores[len(curriculum)]

        curriculum.append(entry)

    ft_values = [
        float(row["forward_transfer"])
        for row in curriculum
        if "forward_transfer" in row
    ]
    result: dict[str, Any] = {
        "status": "ok",
        "n_sessions": len(sessions),
        "n_scored": len(curriculum),
        "mean_forward_transfer": (
            sum(ft_values) / len(ft_values) if ft_values else None
        ),
        "curriculum": curriculum,
        "sealed_sessions": list(cfg.sealed_sessions),
        "note": (
            "sequential blind-day spine with champion/baseline scoring and "
            "forward transfer"
        ),
    }

    # Retention panel: compare early vs late champion scores when available.
    champ_series = (
        list(champion_scores)
        if champion_scores is not None
        else computed_champion
    )
    if retention_before is not None and retention_after is not None:
        retention = check_retention(
            retention_before,
            retention_after,
            max_penalty=cfg.retention_max_penalty,
        )
        result["forgetting_penalty"] = retention.penalty
        result["retention"] = retention.to_dict()
    elif len(champ_series) >= 4:
        mid = len(champ_series) // 2
        before = champ_series[:mid]
        after = champ_series[-len(before) :]
        retention = check_retention(
            before,
            after,
            max_penalty=cfg.retention_max_penalty,
        )
        result["forgetting_penalty"] = retention.penalty
        result["retention"] = retention.to_dict()
    else:
        result["forgetting_penalty"] = forgetting_penalty([], [])

    return result
