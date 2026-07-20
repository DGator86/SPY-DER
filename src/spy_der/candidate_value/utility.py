"""Distributional candidate utility (System A candidate_ranker.candidate_utility)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from spy_der.contracts.value import CandidateValueForecast

__all__ = ["UtilityConfig", "candidate_utility"]


@dataclass(frozen=True, slots=True)
class UtilityConfig:
    lambda_shortfall: float = 0.50
    lambda_tail: float = 0.25
    lambda_fill: float = 0.25
    lambda_model: float = 0.25
    lambda_forecast: float = 0.20
    lambda_ood: float = 0.20
    lambda_capital: float = 0.10
    minimum_utility: float = 0.0
    portfolio_risk_budget: float = 1.0


def candidate_utility(
    forecast: CandidateValueForecast,
    *,
    capital: Decimal | float | None = None,
    cfg: UtilityConfig | None = None,
) -> float:
    """Higher shortfall/tail/fill/model/forecast/OOD/capital → lower utility."""
    config = cfg or UtilityConfig()
    budget = max(config.portfolio_risk_budget, 1e-9)
    if capital is None:
        capital = (
            forecast.capital_required if forecast.capital_required is not None else Decimal("0")
        )
    cap = float(capital)
    ev = float(forecast.expected_net_pnl or 0.0)
    es = float(forecast.expected_shortfall or 0.0)
    # Tail proxy: distance from median to q05 (or q10).
    q_lo = forecast.pnl_q05 if forecast.pnl_q05 is not None else forecast.pnl_q10
    q_med = forecast.pnl_q50
    tail = 0.0
    if q_lo is not None and q_med is not None:
        tail = max(float(q_med - q_lo), 0.0)
    fill_u = float(forecast.execution_uncertainty)
    model_u = float(forecast.model_uncertainty)
    forecast_u = float(forecast.forecast_uncertainty)
    ood = float(forecast.ood_score)
    utility = (
        ev
        - config.lambda_shortfall * es
        - config.lambda_tail * tail
        - config.lambda_fill * fill_u
        - config.lambda_model * model_u
        - config.lambda_forecast * forecast_u
        - config.lambda_ood * ood
        - config.lambda_capital * (cap / budget)
    )
    return float(utility)
