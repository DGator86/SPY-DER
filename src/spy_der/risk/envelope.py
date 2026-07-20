"""Build deterministic pre-trade risk envelopes."""

from __future__ import annotations

from decimal import Decimal

from spy_der.contracts.risk import (
    OperationalState,
    PortfolioState,
    RiskEnvelope,
    RiskLimits,
)

__all__ = ["build_risk_envelope"]


def build_risk_envelope(
    *,
    limits: RiskLimits,
    portfolio: PortfolioState,
    operational: OperationalState | None = None,
    size_scalar_cap: float = 1.0,
) -> RiskEnvelope:
    """Compose remaining budgets / slots into a RiskEnvelope.

    Fail-closed: any operational hard veto or exhausted budget rejects the
    envelope with max_size_scalar=0.
    """
    hard: list[str] = []
    warnings: list[str] = []
    ops = operational

    if ops is not None:
        hard.extend(ops.hard_vetoes)
        if not ops.entries_allowed and not ops.hard_vetoes:
            hard.append("entries_not_allowed")
        if not ops.deployment_permission:
            hard.append("deployment_denied")
        if not ops.journal_available:
            hard.append("journal_unavailable")

    remaining_daily = limits.max_daily_loss
    if limits.max_daily_loss > 0:
        # Committed open risk + adverse realized PnL consume budget.
        consumed = portfolio.open_risk_dollars
        if portfolio.daily_realized_pnl < 0:
            consumed += abs(portfolio.daily_realized_pnl)
        remaining_daily = max(Decimal("0"), limits.max_daily_loss - consumed)
        if remaining_daily <= 0:
            hard.append("daily_loss_exhausted")

    remaining_slots = 10**9
    if limits.max_positions > 0:
        remaining_slots = max(0, limits.max_positions - portfolio.open_positions)
        if remaining_slots <= 0:
            hard.append("max_positions")

    max_risk = limits.max_risk_dollars
    if limits.max_daily_loss > 0:
        if max_risk <= 0:
            max_risk = remaining_daily
        else:
            max_risk = min(max_risk, remaining_daily)

    # Equity-fraction ceiling when static max_risk is unset.
    if max_risk <= 0 and portfolio.equity > 0 and limits.risk_per_trade_frac > 0:
        max_risk = (
            Decimal(str(portfolio.equity)) * Decimal(str(limits.risk_per_trade_frac))
        ).quantize(Decimal("0.0001"))

    if max_risk <= 0:
        hard.append("zero_risk_budget")

    if limits.gamma_limit is not None and limits.gamma_limit > 0:
        if portfolio.portfolio_gamma > limits.gamma_limit:
            hard.append("portfolio_gamma")

    if limits.delta_limit is not None and limits.delta_limit > 0:
        if abs(portfolio.portfolio_delta) > limits.delta_limit:
            hard.append("portfolio_delta")

    hard = list(dict.fromkeys(hard))
    approved = not hard
    size_scalar = 0.0 if not approved else max(0.0, min(1.0, float(size_scalar_cap)))
    lockout = bool(ops and (ops.session_warmup or ops.entry_locked or ops.hard_vetoes))

    if not approved:
        return RiskEnvelope.rejected(*hard, account_id=portfolio.account_id)

    return RiskEnvelope(
        account_id=portfolio.account_id,
        approved=True,
        max_risk_dollars=max_risk,
        max_contracts=limits.max_contracts,
        max_positions=limits.max_positions,
        max_daily_loss=limits.max_daily_loss,
        max_size_scalar=size_scalar,
        remaining_daily_loss_budget=remaining_daily
        if limits.max_daily_loss > 0
        else max_risk,
        remaining_position_slots=remaining_slots if limits.max_positions > 0 else 10**9,
        delta_limit=limits.delta_limit,
        gamma_limit=limits.gamma_limit,
        max_family_positions=limits.max_family_positions,
        max_expiration_positions=limits.max_expiration_positions,
        lockout_active=lockout,
        deployment_permission=True if ops is None else ops.deployment_permission,
        decision_ttl_seconds=limits.decision_ttl_seconds,
        hard_vetoes=(),
        warnings=tuple(warnings),
    )
