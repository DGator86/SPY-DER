"""Parity golden for Phase 11 risk envelope + firewall decision."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    CandidateUniverse,
    DebitCredit,
    OptionType,
    RiskLimits,
    SystemAction,
    SystemDecision,
)
from spy_der.contracts.serialization import to_canonical_json
from spy_der.risk import (
    FirewallContext,
    apply_risk_firewall,
    build_operational_state,
    build_portfolio_state,
    build_risk_envelope,
)

GOLDEN = Path("baseline/expected_outputs/phase11/risk_decision.json")


def _candidate() -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id="cand-risk-1",
        snapshot_id="snap-phase11",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("500"),
                quantity=1,
                expiration=exp,
                contract_id="SPY250105C00500000",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal("35"),
        breakevens=(),
        capital_required=Decimal("35"),
        terminal_payoff_hash="sha256:pay-phase11",
        geometry_hash="sha256:geo-phase11",
    )


def _artifact() -> dict[str, object]:
    now = datetime(2026, 1, 5, 16, 30, tzinfo=UTC)  # 11:30 ET
    cand = _candidate()
    universe = CandidateUniverse(universe_id="u-phase11", candidates=(cand,))
    portfolio = build_portfolio_state(
        account_id="system_b_ensemble",
        equity=Decimal("10000"),
        cash=Decimal("10000"),
    )
    operational = build_operational_state(
        now=now,
        market_open=True,
        data_valid=True,
        broker_available=True,
        journal_available=True,
    )
    envelope = build_risk_envelope(
        limits=RiskLimits(
            max_risk_dollars=Decimal("100"),
            max_contracts=4,
            max_positions=2,
            max_daily_loss=Decimal("250"),
            risk_per_trade_frac=0.02,
            decision_ttl_seconds=60,
        ),
        portfolio=portfolio,
        operational=operational,
    )
    decision = SystemDecision(
        action=SystemAction.SELECT_CANDIDATE,
        selected_candidate_id=cand.candidate_id,
        market_snapshot_id="snap-phase11",
        reason="parity",
    )
    risk = apply_risk_firewall(
        decision,
        envelope,
        universe,
        FirewallContext(
            portfolio=portfolio,
            operational=operational,
            decided_at=now,
            now=now,
            spot_age_seconds=1.0,
            bar_age_seconds=30.0,
            quote_age_seconds=1.0,
            fill_probability=0.7,
            requested_contracts=2,
            size_scalar=1.0,
            exit_policy_approved=True,
        ),
    )
    return {
        "envelope": {
            "approved": envelope.approved,
            "max_risk_dollars": str(envelope.max_risk_dollars),
            "max_size_scalar": envelope.max_size_scalar,
            "remaining_daily_loss_budget": str(envelope.remaining_daily_loss_budget),
            "remaining_position_slots": envelope.remaining_position_slots,
            "hard_vetoes": list(envelope.hard_vetoes),
        },
        "decision": {
            "approved": risk.approved,
            "approved_contracts": risk.approved_contracts,
            "approved_risk_dollars": str(risk.approved_risk_dollars),
            "veto_codes": [v.code for v in risk.vetoes],
            "check_names": [c.name for c in risk.checks],
            "candidate_hash": risk.candidate_hash,
            "market_snapshot_id": risk.market_snapshot_id,
            "account_state_id": risk.account_state_id,
            "expires_at": risk.expires_at.isoformat() if risk.expires_at else None,
            "allowed": risk.allowed,
            "reason": risk.reason,
        },
    }


def test_phase11_risk_parity() -> None:
    artifact = json.loads(to_canonical_json(_artifact()))
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert artifact == expected
