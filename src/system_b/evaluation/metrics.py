from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    schema_version: str = "1.0.0"
    net_pnl: Decimal = Decimal("0")
    expectancy: float = 0.0
    return_on_defined_risk: float = 0.0
    maximum_drawdown: float = 0.0
    cvar: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    capital_efficiency: float = 0.0
    candidate_selection_regret: float = 0.0
    brier_score: float = 0.0
    log_loss: float = 0.0
    calibration_error: float = 0.0
    quantile_coverage_error: float = 0.0
    realized_move_error: float = 0.0
    abstention_rate: float = 0.0
    false_positive_trade_rate: float = 0.0
    veto_effectiveness: float = 0.0
    deterministic_replay_reproducibility: float = 1.0
    runtime_failures: int = 0
    stale_data_decisions: int = 0
    fallback_usage: int = 0


def evaluate_placeholder() -> EvaluationResult:
    return EvaluationResult()
