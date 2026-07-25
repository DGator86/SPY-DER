"""Sequential Dojo — prequential / forward-transfer measurement spine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from spy_der.decisions.shadow import reset_shadow_tick_cache
from spy_der.dojo.evaluation import (
    OutcomeCandidateEvaluator,
    forward_transfer,
)
from spy_der.dojo.outcomes import collect_outcomes
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionAuthority,
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
    retention_panel_size: int = 2
    max_forgetting_penalty: float = 0.05


def _session_score(
    provider: MarketExperienceProvider,
    session: date,
    authority: DecisionAuthority,
    evaluator: CandidateEvaluator,
) -> float:
    packets = list(provider.snapshots(session))
    if not packets:
        return 0.0
    outcomes = collect_outcomes(provider, packets)
    reset_shadow_tick_cache()
    decisions = [authority.decide(p) for p in packets]
    report = evaluator.evaluate(decisions, outcomes)
    payload = report.to_dict()
    total = payload.get("total_pnl")
    if isinstance(total, (int, float)):
        return float(total)
    if isinstance(total, str):
        try:
            return float(total)
        except ValueError:
            return 0.0
    return 0.0


def run_sequential_dojo(
    provider: MarketExperienceProvider | None,
    *,
    cfg: SequentialDojoConfig | None = None,
    champion_scores: Sequence[float] | None = None,
    baseline_scores: Sequence[float] | None = None,
    retention_before: Sequence[float] | None = None,
    retention_after: Sequence[float] | None = None,
    authorities: dict[str, DecisionAuthority] | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    """Measure prequential forward transfer without learning from Day t first.

    When ``authorities`` are supplied, champion and baseline are scored on each
    blind day directly (leak-free). External score sequences remain supported
    for callers that pre-compute them.
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

    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()
    champion = (authorities or {}).get("champion")
    baseline = (authorities or {}).get("baseline")
    compute_inline = champion is not None and baseline is not None

    computed_champion: list[float] = []
    computed_baseline: list[float] = []
    retention_panel = sessions[: max(0, cfg.retention_panel_size)]
    retention_before_scores: list[float] = []
    if compute_inline and retention_panel and champion is not None:
        retention_before_scores = [
            _session_score(provider, s, champion, scorer) for s in retention_panel
        ]

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

        champ_score: float | None = None
        base_score: float | None = None
        if compute_inline:
            assert champion is not None and baseline is not None
            champ_score = _session_score(provider, session, champion, scorer)
            base_score = _session_score(provider, session, baseline, scorer)
            computed_champion.append(champ_score)
            computed_baseline.append(base_score)
        elif (
            champion_scores is not None
            and baseline_scores is not None
            and len(champion_scores) > len(curriculum)
            and len(baseline_scores) > len(curriculum)
        ):
            champ_score = float(champion_scores[len(curriculum)])
            base_score = float(baseline_scores[len(curriculum)])

        if champ_score is not None and base_score is not None:
            ft = forward_transfer(champ_score, base_score)
            entry["forward_transfer"] = ft
            entry["champion_score"] = champ_score
            entry["baseline_score"] = base_score
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
    }
    if compute_inline:
        result["champion_scores"] = computed_champion
        result["baseline_scores"] = computed_baseline
        result["note"] = (
            "sequential scoring ran DecisionAuthority over blind days "
            "(leak-free warm/test split)"
        )

    # Retention panel: re-score early sessions after the curriculum walk.
    before = (
        list(retention_before)
        if retention_before is not None
        else retention_before_scores
    )
    after: list[float] | None
    if retention_after is not None:
        after = list(retention_after)
    elif compute_inline and retention_panel and champion is not None:
        after = [_session_score(provider, s, champion, scorer) for s in retention_panel]
    else:
        after = None

    if before and after is not None:
        retention = check_retention(
            before,
            after,
            max_penalty=cfg.max_forgetting_penalty,
        )
        result["forgetting_penalty"] = retention.penalty
        result["retention"] = retention.to_dict()
    return result
