"""Candidate-value, ranking, and meta-action contracts (master spec §35)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from spy_der.contracts.common import require_probability

__all__ = [
    "CANDIDATE_VALUE_VERSION",
    "META_MODEL_VERSION",
    "CandidateValueForecast",
    "MetaAction",
    "MetaDecision",
    "SnapshotRanking",
]

CANDIDATE_VALUE_VERSION = "candidate-value.v1"
META_MODEL_VERSION = "trade-meta.v1"


class MetaAction(StrEnum):
    TRADE = "TRADE"
    NO_EDGE = "NO_EDGE"
    ABSTAIN = "ABSTAIN"
    HARD_VETO = "HARD_VETO"


@dataclass(frozen=True, slots=True)
class CandidateValueForecast:
    """Spec §35 candidate forecast (executable economics + value heads)."""

    candidate_id: str
    model_id: str = CANDIDATE_VALUE_VERSION
    expected_net_pnl: Decimal | None = None
    p_positive_net_pnl: float | None = None
    p_positive_utility: float | None = None
    pnl_q05: Decimal | None = None
    pnl_q10: Decimal | None = None
    pnl_q25: Decimal | None = None
    pnl_q50: Decimal | None = None
    pnl_q75: Decimal | None = None
    pnl_q90: Decimal | None = None
    pnl_q95: Decimal | None = None
    expected_shortfall: Decimal | None = None
    p_target_first: float | None = None
    p_stop_first: float | None = None
    p_neither: float | None = None
    expected_time_in_trade_minutes: float | None = None
    fill_probability: float | None = None
    fill_concession: Decimal | None = None
    model_uncertainty: float = 0.5
    forecast_uncertainty: float = 0.5
    execution_uncertainty: float = 0.5
    ood_score: float = 0.0
    utility: float | None = None
    capital_required: Decimal | None = None
    maximum_loss: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        for name in (
            "p_positive_net_pnl",
            "p_positive_utility",
            "p_target_first",
            "p_stop_first",
            "p_neither",
            "fill_probability",
            "model_uncertainty",
            "forecast_uncertainty",
            "execution_uncertainty",
            "ood_score",
        ):
            value = getattr(self, name)
            if value is not None:
                require_probability(float(value), name)
        if self.expected_shortfall is not None and self.expected_shortfall < 0:
            raise ValueError("expected_shortfall must be non-negative")

    @property
    def probability_positive_utility(self) -> float:
        """Compatibility with Phase-0 CandidateForecast stub / interfaces."""
        if self.p_positive_utility is None:
            return 0.0
        return float(self.p_positive_utility)


@dataclass(frozen=True, slots=True)
class SnapshotRanking:
    snapshot_id: str
    ordered_candidate_ids: tuple[str, ...] = ()
    combined_scores: tuple[tuple[str, float], ...] = ()
    expected_regret: tuple[tuple[str, float], ...] = ()
    top_candidate_id: str | None = None
    second_candidate_id: str | None = None
    top_score_margin: float | None = None
    model_version: str = CANDIDATE_VALUE_VERSION
    diagnostics: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class MetaDecision:
    action: MetaAction
    p_positive_utility: float
    expected_order_value: float
    selected_candidate_id: str | None
    composite_uncertainty: float
    threshold_used: float
    reasons: tuple[str, ...] = ()
    model_version: str = META_MODEL_VERSION
    diagnostics: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        require_probability(self.p_positive_utility, "p_positive_utility")
        require_probability(self.composite_uncertainty, "composite_uncertainty")
