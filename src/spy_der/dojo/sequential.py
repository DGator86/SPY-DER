"""Sequential Dojo — prequential / forward-transfer measurement spine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from spy_der.dojo.evaluation import forgetting_penalty, forward_transfer
from spy_der.dojo.protocols import MarketExperienceProvider

__all__ = ["SequentialDojoConfig", "run_sequential_dojo"]


@dataclass
class SequentialDojoConfig:
    min_warm_sessions: int = 2
    sealed_sessions: tuple[str, ...] = ()
    embargo_sessions: int = 0
    reports_dir: str = ""


def run_sequential_dojo(
    provider: MarketExperienceProvider | None,
    *,
    cfg: SequentialDojoConfig | None = None,
    champion_scores: Sequence[float] | None = None,
    baseline_scores: Sequence[float] | None = None,
    retention_before: Sequence[float] | None = None,
    retention_after: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Measure prequential forward transfer without learning from Day t first.

    When score sequences are supplied (from an attached CandidateEvaluator),
    FT_t and forgetting penalty are computed. Without scores, the curriculum
    session list is still assembled leak-free for later wiring.
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
        if (
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
    }
    if retention_before is not None and retention_after is not None:
        result["forgetting_penalty"] = forgetting_penalty(
            retention_before, retention_after
        )
    return result
