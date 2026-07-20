"""Phase 11 — risk envelope, firewall, sizing, portfolio, lockouts, duplicates."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    CandidateUniverse,
    DebitCredit,
    OptionType,
    RiskEnvelope,
    RiskLimits,
    SystemAction,
    SystemDecision,
)
from spy_der.risk import (
    DuplicateGuard,
    FirewallContext,
    PortfolioTracker,
    apply_risk_firewall,
    build_operational_state,
    build_portfolio_state,
    build_risk_envelope,
    contracts_for_risk,
    is_stale_decision,
    scale_risk,
)


def _candidate(
    candidate_id: str = "c1",
    max_loss: str = "25",
    family: str = "long_call",
    geometry_hash: str = "sha256:geo1",
) -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=candidate_id,
        snapshot_id="snap-risk",
        family=family,
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("100"),
                quantity=1,
                expiration=exp,
                contract_id="SPY",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal(max_loss),
        breakevens=(),
        capital_required=Decimal(max_loss),
        terminal_payoff_hash="sha256:pay",
        geometry_hash=geometry_hash,
    )


def _decision(candidate_id: str = "c1") -> SystemDecision:
    return SystemDecision(
        action=SystemAction.SELECT_CANDIDATE,
        selected_candidate_id=candidate_id,
        market_snapshot_id="snap-risk",
        reason="test",
    )


def test_scale_risk_floor_until_enough_trades() -> None:
    frac, note = scale_risk(10, 0.6, 2.0, 1.0)
    assert frac == 0.02
    assert "floor" in note


def test_contracts_for_risk_floors_budget() -> None:
    sized = contracts_for_risk(
        equity=Decimal("1000"),
        risk_frac=0.05,
        max_loss_per_contract=Decimal("25"),
        max_contracts=10,
    )
    assert sized.contracts == 2
    assert sized.risk_dollars == Decimal("50.0000")


def test_lockout_session_warmup_and_cutoff() -> None:
    warmup = build_operational_state(
        now=datetime(2026, 1, 5, 9, 30, tzinfo=UTC),  # 4:30 ET
        market_open=True,
    )
    assert not warmup.entries_allowed
    assert "session_warmup" in warmup.hard_vetoes

    cutoff = build_operational_state(
        now=datetime(2026, 1, 5, 20, 45, tzinfo=UTC),  # 15:45 ET
        market_open=True,
    )
    assert not cutoff.entries_allowed
    assert "entry_cutoff" in cutoff.hard_vetoes


def test_portfolio_tracker_daily_loss_and_positions() -> None:
    tracker = PortfolioTracker(
        limits=RiskLimits(max_daily_loss=Decimal("50"), max_positions=1)
    )
    cand = _candidate(max_loss="40")
    ok, vetoes = tracker.check(cand, "2026-01-05")
    assert ok and not vetoes
    tracker.record_trade(cand, "2026-01-05")
    ok2, vetoes2 = tracker.check(_candidate("c2", "20"), "2026-01-05")
    assert not ok2
    assert any(v.startswith("max_positions") for v in vetoes2)


def test_build_risk_envelope_rejects_on_lockout() -> None:
    portfolio = build_portfolio_state(
        account_id="acct-1",
        equity=Decimal("1000"),
        cash=Decimal("1000"),
    )
    ops = build_operational_state(
        now=datetime(2026, 1, 5, 9, 0, tzinfo=UTC),
        market_open=True,
        emergency_lockout=True,
    )
    envelope = build_risk_envelope(
        limits=RiskLimits(max_risk_dollars=Decimal("100"), max_daily_loss=Decimal("200")),
        portfolio=portfolio,
        operational=ops,
    )
    assert not envelope.approved
    assert envelope.max_size_scalar == 0.0
    assert envelope.hard_vetoes


def test_firewall_rejects_over_envelope() -> None:
    universe = CandidateUniverse(universe_id="u1", candidates=(_candidate(max_loss="100"),))
    risk = apply_risk_firewall(
        _decision(),
        RiskEnvelope(max_defined_risk_per_trade=Decimal("50")),
        universe,
    )
    assert not risk.approved
    assert not risk.allowed
    assert any(v.code == "maximum_loss" for v in risk.vetoes)


def test_firewall_approves_within_envelope() -> None:
    cand = _candidate(max_loss="25")
    universe = CandidateUniverse(universe_id="u1", candidates=(cand,))
    risk = apply_risk_firewall(
        _decision(),
        RiskEnvelope(max_defined_risk_per_trade=Decimal("50"), max_size_scalar=1.0),
        universe,
    )
    assert risk.approved
    assert risk.allowed
    assert risk.approved_contracts == 1


def test_firewall_stale_decision_and_duplicate() -> None:
    cand = _candidate()
    universe = CandidateUniverse(universe_id="u1", candidates=(cand,))
    portfolio = build_portfolio_state(
        account_id="acct-1",
        equity=Decimal("5000"),
        cash=Decimal("5000"),
    )
    envelope = build_risk_envelope(
        limits=RiskLimits(
            max_risk_dollars=Decimal("100"),
            max_daily_loss=Decimal("500"),
            max_positions=3,
            decision_ttl_seconds=30,
        ),
        portfolio=portfolio,
        operational=build_operational_state(
            now=datetime(2026, 1, 5, 16, 0, tzinfo=UTC),  # 11:00 ET
            market_open=True,
        ),
    )
    now = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
    stale = apply_risk_firewall(
        _decision(),
        envelope,
        universe,
        FirewallContext(
            portfolio=portfolio,
            decided_at=now - timedelta(seconds=120),
            now=now,
        ),
    )
    assert not stale.approved
    assert any(v.code == "stale_decision" for v in stale.vetoes)

    guard = DuplicateGuard()
    guard.remember_decision(cand, account_id="acct-1", at=now)
    dup = apply_risk_firewall(
        _decision(),
        envelope,
        universe,
        FirewallContext(
            portfolio=portfolio,
            decided_at=now,
            now=now,
            duplicate_guard=guard,
        ),
    )
    assert not dup.approved
    assert any(v.code == "duplicate_order" for v in dup.vetoes)


def test_is_stale_decision_helper() -> None:
    now = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
    fresh = is_stale_decision(decided_at=now - timedelta(seconds=10), now=now, ttl_seconds=60)
    assert not fresh.stale
    old = is_stale_decision(decided_at=now - timedelta(seconds=90), now=now, ttl_seconds=60)
    assert old.stale
