"""Session-safe evaluation metrics (master spec §55-§56)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

__all__ = [
    "EVALUATION_SCHEMA",
    "EvaluationResult",
    "TradeOutcome",
    "evaluate_placeholder",
    "evaluate_trades",
]

EVALUATION_SCHEMA = "evaluation.v1"


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    """One settled or counterfactual trade for metric aggregation."""

    session_date: str
    realized_pnl: Decimal
    max_loss: Decimal = Decimal("0")
    was_traded: bool = True
    abstained: bool = False
    vetoed: bool = False
    false_positive: bool = False
    brier: float | None = None
    log_loss: float | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    schema_version: str = EVALUATION_SCHEMA
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
    trade_count: int = 0
    session_count: int = 0


def evaluate_placeholder() -> EvaluationResult:
    return EvaluationResult()


def evaluate_trades(
    outcomes: Sequence[TradeOutcome],
    *,
    regret: float = 0.0,
    runtime_failures: int = 0,
    stale_data_decisions: int = 0,
    fallback_usage: int = 0,
    reproducibility: float = 1.0,
) -> EvaluationResult:
    """Aggregate trade/decision outcomes into EvaluationResult."""
    if not outcomes:
        return EvaluationResult(
            runtime_failures=runtime_failures,
            stale_data_decisions=stale_data_decisions,
            fallback_usage=fallback_usage,
            deterministic_replay_reproducibility=reproducibility,
        )

    traded = [o for o in outcomes if o.was_traded and not o.abstained]
    pnls = [float(o.realized_pnl) for o in traded]
    net = sum((o.realized_pnl for o in traded), Decimal("0"))
    n = len(traded)
    expectancy = (float(net) / n) if n else 0.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = (len(wins) / n) if n else 0.0
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # CVaR at 5% (mean of worst 5% outcomes); empty -> 0.
    if pnls:
        ordered = sorted(pnls)
        k = max(1, int(len(ordered) * 0.05 + 0.9999))
        cvar = float(sum(ordered[:k]) / k)
    else:
        cvar = 0.0

    risk = sum((float(o.max_loss) for o in traded if o.max_loss > 0), 0.0)
    rod = (float(net) / risk) if risk > 0 else 0.0
    capital_efficiency = rod

    total_decisions = len(outcomes)
    abstentions = sum(1 for o in outcomes if o.abstained)
    abstention_rate = abstentions / total_decisions if total_decisions else 0.0
    false_pos = sum(1 for o in traded if o.false_positive)
    false_positive_rate = false_pos / n if n else 0.0
    vetoes = sum(1 for o in outcomes if o.vetoed)
    bad_trades = sum(1 for o in traded if float(o.realized_pnl) < 0)
    # Veto effectiveness: share of non-traded vetoes vs losing trades avoided (proxy).
    veto_effectiveness = (
        vetoes / (vetoes + bad_trades) if (vetoes + bad_trades) else 0.0
    )

    briers = [o.brier for o in outcomes if o.brier is not None]
    logs = [o.log_loss for o in outcomes if o.log_loss is not None]
    brier = sum(briers) / len(briers) if briers else 0.0
    logloss = sum(logs) / len(logs) if logs else 0.0

    sessions = {o.session_date for o in outcomes}
    # Simple calibration error proxy: |win_rate - mean predicted| unavailable -> 0
    # Use std of pnl normalized as placeholder realized_move_error.
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        move_err = sqrt(var)
    else:
        move_err = 0.0

    return EvaluationResult(
        net_pnl=net,
        expectancy=expectancy,
        return_on_defined_risk=rod,
        maximum_drawdown=max_dd,
        cvar=cvar,
        win_rate=win_rate,
        profit_factor=profit_factor if profit_factor != float("inf") else 999.0,
        capital_efficiency=capital_efficiency,
        candidate_selection_regret=float(regret),
        brier_score=brier,
        log_loss=logloss,
        calibration_error=0.0,
        quantile_coverage_error=0.0,
        realized_move_error=move_err,
        abstention_rate=abstention_rate,
        false_positive_trade_rate=false_positive_rate,
        veto_effectiveness=veto_effectiveness,
        deterministic_replay_reproducibility=reproducibility,
        runtime_failures=runtime_failures,
        stale_data_decisions=stale_data_decisions,
        fallback_usage=fallback_usage,
        trade_count=n,
        session_count=len(sessions),
    )
