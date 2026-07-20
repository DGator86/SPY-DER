"""Risk firewall - final pre-order-intent gate (spec §50)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from spy_der.contracts import (
    CandidateUniverse,
    SystemAction,
    SystemDecision,
)
from spy_der.contracts.risk import (
    OperationalState,
    PortfolioState,
    RiskCheck,
    RiskDecision,
    RiskEnvelope,
    RiskVeto,
)
from spy_der.risk.duplicates import DuplicateGuard, is_stale_decision
from spy_der.risk.sizing import contracts_for_risk

__all__ = [
    "FirewallContext",
    "RiskFirewallService",
    "apply_risk_firewall",
]


@dataclass(frozen=True, slots=True)
class FirewallContext:
    """Optional richer inputs for full §50 checks."""

    portfolio: PortfolioState | None = None
    operational: OperationalState | None = None
    decided_at: datetime | None = None
    now: datetime | None = None
    spot_age_seconds: float | None = None
    bar_age_seconds: float | None = None
    quote_age_seconds: float | None = None
    max_spot_age_seconds: float = 5.0
    max_bar_age_seconds: float = 120.0
    max_quote_age_seconds: float = 5.0
    fill_probability: float | None = None
    min_fill_probability: float = 0.0
    requested_contracts: int = 1
    size_scalar: float = 1.0
    exit_policy_approved: bool = True
    duplicate_guard: DuplicateGuard | None = None
    expected_geometry_hash: str | None = None


def apply_risk_firewall(
    decision: SystemDecision,
    envelope: RiskEnvelope,
    universe: CandidateUniverse,
    context: FirewallContext | None = None,
) -> RiskDecision:
    """Evaluate the risk firewall. Fail-closed on any hard veto."""
    ctx = context or FirewallContext()
    now = ctx.now or datetime.now(tz=UTC)
    checks: list[RiskCheck] = []
    vetoes: list[RiskVeto] = []

    def _check(name: str, passed: bool, detail: str = "") -> None:
        checks.append(RiskCheck(name=name, passed=passed, detail=detail))
        if not passed:
            vetoes.append(RiskVeto(code=name, reason=detail or name, severity="hard"))

    # --- non-executable decisions ---
    if (
        decision.action is not SystemAction.SELECT_CANDIDATE
        or decision.selected_candidate_id is None
    ):
        _check("order_intent", False, "no executable order intent")
        return _decision(
            False,
            vetoes=vetoes,
            checks=checks,
            market_snapshot_id=decision.market_snapshot_id,
            account_state_id=_account_state_id(ctx),
            now=now,
            ttl=envelope.decision_ttl_seconds,
        )

    candidate = next(
        (c for c in universe.candidates if c.candidate_id == decision.selected_candidate_id),
        None,
    )
    if candidate is None:
        _check("candidate_id", False, "candidate not found in approved universe")
        return _decision(
            False,
            vetoes=vetoes,
            checks=checks,
            market_snapshot_id=decision.market_snapshot_id,
            account_state_id=_account_state_id(ctx),
            now=now,
            ttl=envelope.decision_ttl_seconds,
        )

    candidate_hash = getattr(candidate, "geometry_hash", "") or ""
    max_loss = Decimal(
        str(
            getattr(candidate, "max_loss", None)
            or getattr(candidate, "maximum_loss", 0)
            or 0
        )
    )

    # --- envelope approval / deployment / lockouts ---
    _check(
        "deployment_mode",
        envelope.deployment_permission and envelope.approved,
        "deployment or envelope rejected" if not envelope.approved else "ok",
    )
    if envelope.lockout_active or envelope.hard_vetoes:
        _check(
            "emergency_lockout",
            False,
            ",".join(envelope.hard_vetoes) or "lockout_active",
        )
    else:
        _check("emergency_lockout", True, "ok")

    ops = ctx.operational
    if ops is not None:
        _check("session_status", ops.market_open and ops.data_valid, "session invalid")
        _check("entry_cutoff", not ops.entry_locked and not ops.session_warmup, "entry locked")
        _check("journal_availability", ops.journal_available, "journal unavailable")
        if ops.hard_vetoes:
            _check("operational_vetoes", False, ",".join(ops.hard_vetoes))
        else:
            _check("operational_vetoes", True, "ok")
    else:
        _check("session_status", True, "not_provided")
        _check("entry_cutoff", True, "not_provided")
        _check("journal_availability", True, "not_provided")
        _check("operational_vetoes", True, "not_provided")

    # --- freshness ---
    _check(
        "spot_freshness",
        ctx.spot_age_seconds is None or ctx.spot_age_seconds <= ctx.max_spot_age_seconds,
        f"age={ctx.spot_age_seconds}",
    )
    _check(
        "bar_freshness",
        ctx.bar_age_seconds is None or ctx.bar_age_seconds <= ctx.max_bar_age_seconds,
        f"age={ctx.bar_age_seconds}",
    )
    _check(
        "quote_freshness",
        ctx.quote_age_seconds is None or ctx.quote_age_seconds <= ctx.max_quote_age_seconds,
        f"age={ctx.quote_age_seconds}",
    )

    # --- candidate identity / geometry ---
    _check("candidate_id", bool(candidate.candidate_id), "missing candidate_id")
    expected_geo = ctx.expected_geometry_hash
    if expected_geo is not None:
        _check(
            "geometry_hash",
            candidate_hash == expected_geo,
            f"{candidate_hash}!={expected_geo}",
        )
    else:
        _check("geometry_hash", bool(candidate_hash), "missing geometry_hash")

    # --- max-loss recalculation vs envelope ---
    envelope_cap = envelope.max_risk_dollars
    if envelope.max_defined_risk_per_trade is not None:
        envelope_cap = envelope.max_defined_risk_per_trade
    if max_loss <= 0:
        _check("maximum_loss", False, "non_positive_max_loss")
    elif envelope_cap > 0 and max_loss > envelope_cap:
        _check(
            "maximum_loss",
            False,
            f"risk envelope exceeded: {max_loss}>{envelope_cap}",
        )
    else:
        _check("maximum_loss", True, f"max_loss={max_loss}")

    # --- fill estimate ---
    if ctx.fill_probability is not None:
        _check(
            "fill_estimate",
            ctx.fill_probability >= ctx.min_fill_probability,
            f"p_fill={ctx.fill_probability}",
        )
    else:
        _check("fill_estimate", True, "not_provided")

    # --- portfolio / account ---
    portfolio = ctx.portfolio
    if portfolio is not None:
        _check("account_equity", portfolio.equity > 0, f"equity={portfolio.equity}")
        if envelope.max_daily_loss > 0:
            remaining = envelope.remaining_daily_loss_budget
            _check(
                "daily_realized_loss",
                remaining >= max_loss,
                f"remaining={remaining}",
            )
            _check(
                "open_risk",
                portfolio.open_risk_dollars + max_loss <= envelope.max_daily_loss,
                f"open_risk={portfolio.open_risk_dollars}",
            )
        else:
            _check("daily_realized_loss", True, "unlimited")
            _check("open_risk", True, f"open_risk={portfolio.open_risk_dollars}")
        if envelope.max_positions > 0:
            _check(
                "position_count",
                portfolio.open_positions < envelope.max_positions,
                f"open={portfolio.open_positions}",
            )
        else:
            _check("position_count", True, "unlimited")

        family = str(getattr(candidate, "family", "") or "")
        if envelope.max_family_positions > 0 and family:
            current = dict(portfolio.family_counts).get(family, 0)
            _check(
                "family_concentration",
                current < envelope.max_family_positions,
                f"{family}:{current}",
            )
        else:
            _check("family_concentration", True, "ok")

        expiration = str(getattr(candidate, "expiration", "") or "")
        if envelope.max_expiration_positions > 0 and expiration:
            current = dict(portfolio.expiration_counts).get(expiration, 0)
            _check(
                "expiration_concentration",
                current < envelope.max_expiration_positions,
                f"{expiration}:{current}",
            )
        else:
            _check("expiration_concentration", True, "ok")

        if envelope.delta_limit is not None and envelope.delta_limit > 0:
            _check(
                "delta",
                abs(portfolio.portfolio_delta) <= envelope.delta_limit,
                f"delta={portfolio.portfolio_delta}",
            )
        else:
            _check("delta", True, "unlimited")

        if envelope.gamma_limit is not None and envelope.gamma_limit > 0:
            cand_gamma = abs(Decimal(str(getattr(candidate, "gamma", 0) or 0)))
            _check(
                "gamma",
                portfolio.portfolio_gamma + cand_gamma <= envelope.gamma_limit,
                f"gamma={portfolio.portfolio_gamma}+{cand_gamma}",
            )
        else:
            _check("gamma", True, "unlimited")
    else:
        for name in (
            "account_equity",
            "daily_realized_loss",
            "open_risk",
            "position_count",
            "family_concentration",
            "expiration_concentration",
            "delta",
            "gamma",
        ):
            _check(name, True, "not_provided")

    # --- duplicates / equivalent positions ---
    if portfolio is not None:
        equiv = (
            candidate.candidate_id in portfolio.open_candidate_ids
            or (bool(candidate_hash) and candidate_hash in portfolio.open_geometry_hashes)
        )
        _check("equivalent_position", not equiv, "open equivalent position")
        if ctx.duplicate_guard is not None:
            dup, why = ctx.duplicate_guard.is_duplicate(
                candidate_id=candidate.candidate_id,
                geometry_hash=candidate_hash,
                account_id=portfolio.account_id,
                open_candidate_ids=(),
                open_geometry_hashes=(),
                now=now,
            )
            _check("duplicate_order", not dup, why or "ok")
        else:
            _check("duplicate_order", True, "no_guard")
    else:
        _check("duplicate_order", True, "not_provided")
        _check("equivalent_position", True, "not_provided")

    # --- stale decision ---
    freshness = is_stale_decision(
        decided_at=ctx.decided_at,
        now=now,
        ttl_seconds=envelope.decision_ttl_seconds,
    )
    # If no decided_at provided, do not fail closed on staleness (compat path).
    if ctx.decided_at is None:
        _check("stale_decision", True, "not_provided")
    else:
        _check("stale_decision", not freshness.stale, freshness.reason)

    _check("exit_policy", ctx.exit_policy_approved, "exit policy not approved")

    # --- sizing ---
    size = contracts_for_risk(
        equity=portfolio.equity if portfolio is not None else envelope_cap,
        risk_frac=1.0 if portfolio is None else float(
            min(1.0, float(envelope_cap / portfolio.equity)) if portfolio.equity > 0 else 0.0
        ),
        max_loss_per_contract=max_loss,
        max_contracts=envelope.max_contracts,
        max_risk_dollars=envelope_cap if envelope_cap > 0 else None,
        size_scalar=min(float(ctx.size_scalar), float(envelope.max_size_scalar)),
    )
    # Compat path without portfolio: approve 1 contract when max_loss within envelope.
    if portfolio is None:
        within_cap = envelope_cap <= 0 or max_loss <= envelope_cap
        approved_contracts = 1 if max_loss > 0 and within_cap else 0
        approved_risk = max_loss if approved_contracts else Decimal("0")
    else:
        # Honor explicit requested contracts when within budget.
        approved_contracts = size.contracts
        if ctx.requested_contracts > 0 and approved_contracts > 0:
            approved_contracts = min(approved_contracts, ctx.requested_contracts)
        if envelope.max_contracts > 0:
            approved_contracts = min(approved_contracts, envelope.max_contracts)
        if approved_contracts:
            approved_risk = max_loss * Decimal(approved_contracts)
        else:
            approved_risk = Decimal("0")
        if approved_contracts < 1:
            _check("sizing", False, size.reason or "insufficient_budget")
        else:
            _check("sizing", True, f"contracts={approved_contracts}")

    hard = [v for v in vetoes if v.severity == "hard"]
    approved = not hard and (portfolio is None or approved_contracts >= 1)
    if portfolio is None:
        # Preserve Phase-0 semantics: envelope max-loss gate only.
        approved = not hard

    return _decision(
        approved,
        approved_contracts=approved_contracts if approved else 0,
        approved_risk_dollars=approved_risk if approved else Decimal("0"),
        vetoes=tuple(hard),
        checks=tuple(checks),
        candidate_hash=candidate_hash,
        market_snapshot_id=decision.market_snapshot_id or getattr(candidate, "snapshot_id", ""),
        account_state_id=_account_state_id(ctx),
        now=now,
        ttl=envelope.decision_ttl_seconds,
    )


@dataclass
class RiskFirewallService:
    """Stateful firewall with duplicate memory."""

    duplicate_guard: DuplicateGuard | None = None

    def evaluate(
        self,
        decision: SystemDecision,
        envelope: RiskEnvelope,
        universe: CandidateUniverse,
        context: FirewallContext | None = None,
    ) -> RiskDecision:
        ctx = context or FirewallContext()
        if self.duplicate_guard is not None and ctx.duplicate_guard is None:
            ctx = FirewallContext(
                portfolio=ctx.portfolio,
                operational=ctx.operational,
                decided_at=ctx.decided_at,
                now=ctx.now,
                spot_age_seconds=ctx.spot_age_seconds,
                bar_age_seconds=ctx.bar_age_seconds,
                quote_age_seconds=ctx.quote_age_seconds,
                max_spot_age_seconds=ctx.max_spot_age_seconds,
                max_bar_age_seconds=ctx.max_bar_age_seconds,
                max_quote_age_seconds=ctx.max_quote_age_seconds,
                fill_probability=ctx.fill_probability,
                min_fill_probability=ctx.min_fill_probability,
                requested_contracts=ctx.requested_contracts,
                size_scalar=ctx.size_scalar,
                exit_policy_approved=ctx.exit_policy_approved,
                duplicate_guard=self.duplicate_guard,
                expected_geometry_hash=ctx.expected_geometry_hash,
            )
        result = apply_risk_firewall(decision, envelope, universe, ctx)
        if result.approved and ctx.portfolio is not None and decision.selected_candidate_id:
            cand = next(
                (
                    c
                    for c in universe.candidates
                    if c.candidate_id == decision.selected_candidate_id
                ),
                None,
            )
            if cand is not None:
                self.duplicate_guard = self.duplicate_guard or DuplicateGuard()
                self.duplicate_guard.remember_decision(
                    cand,
                    account_id=ctx.portfolio.account_id,
                    at=ctx.now or datetime.now(tz=UTC),
                )
        return result


def _account_state_id(ctx: FirewallContext) -> str:
    if ctx.portfolio is not None:
        return ctx.portfolio.state_id
    return ""


def _decision(
    approved: bool,
    *,
    approved_contracts: int = 0,
    approved_risk_dollars: Decimal = Decimal("0"),
    vetoes: list[RiskVeto] | tuple[RiskVeto, ...] = (),
    checks: list[RiskCheck] | tuple[RiskCheck, ...] = (),
    candidate_hash: str = "",
    market_snapshot_id: str = "",
    account_state_id: str = "",
    now: datetime,
    ttl: int,
) -> RiskDecision:
    return RiskDecision(
        approved=approved,
        approved_contracts=approved_contracts,
        approved_risk_dollars=approved_risk_dollars,
        vetoes=tuple(vetoes),
        checks=tuple(checks),
        candidate_hash=candidate_hash,
        market_snapshot_id=market_snapshot_id,
        account_state_id=account_state_id,
        expires_at=now + timedelta(seconds=max(1, ttl)),
    )

