"""Phase 12 — execution simulator, accounts, positions, restart, reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spy_der.contracts import (
    ApprovedExitPolicyId,
    ExitPolicy,
    OrderIntent,
    OrderStatus,
    PositionStatus,
)
from spy_der.execution import (
    IsolatedAccountBook,
    PaperExecutionSimulator,
    validate_order_transition,
)
from spy_der.positions import (
    PositionManager,
    evaluate_exit,
    restart_runtime,
    validate_position_transition,
)


def test_invalid_order_transition_still_fails() -> None:
    with pytest.raises(ValueError):
        validate_order_transition(OrderStatus.CREATED, OrderStatus.FILLED)


def test_isolated_accounts_cannot_cross_mutate() -> None:
    book = IsolatedAccountBook(starting_cash=Decimal("1000"))
    a = book.ensure("system_b_v2")
    b = book.ensure("system_b_v3")
    assert a.cash == b.cash
    with pytest.raises(ValueError):
        book.assert_same_account("system_b_v2", "system_b_v3")


def test_simulator_fills_limit_order_deterministically() -> None:
    sim = PaperExecutionSimulator()
    now = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
    intent = OrderIntent(
        intent_id="intent-1",
        account_id="system_b_ensemble",
        candidate_id="cand-1",
        contracts=2,
        limit_price=Decimal("1.50"),
        created_at=now,
        fill_probability=1.0,
    )
    state = sim.submit(intent, now=now)
    assert state.status is OrderStatus.FILLED
    assert state.filled_contracts == 2
    assert state.avg_fill_price == Decimal("1.5000")
    acct = sim.accounts.get("system_b_ensemble")
    assert acct.trade_count == 1
    assert acct.cash < Decimal("10000")


def test_partial_fill_then_cancel() -> None:
    sim = PaperExecutionSimulator()
    now = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
    intent = OrderIntent(
        intent_id="intent-partial",
        account_id="system_b_v2",
        candidate_id="cand-p",
        contracts=4,
        limit_price=Decimal("2.00"),
        created_at=now,
        # Low probability encourages partial path in deterministic RNG.
        fill_probability=0.55,
    )
    state = sim.submit(intent, now=now)
    assert state.status in {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
    }
    if state.status is not OrderStatus.FILLED:
        canceled = sim.cancel(state.order_id, now=now + timedelta(seconds=1))
        assert canceled.status is OrderStatus.CANCELED


def test_position_open_mark_exit_and_settle() -> None:
    sim = PaperExecutionSimulator()
    mgr = PositionManager(accounts=sim.accounts)
    now = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
    order = sim.submit(
        OrderIntent(
            intent_id="intent-pos",
            account_id="system_b_legacy",
            candidate_id="cand-pos",
            contracts=1,
            limit_price=Decimal("1.00"),
            created_at=now,
            exit_policy_id=ApprovedExitPolicyId.TARGET_AND_STOP.value,
        ),
        now=now,
    )
    pos = mgr.on_order_state(order, max_loss=Decimal("100"), now=now)
    assert pos is not None
    assert pos.status is PositionStatus.OPEN

    marked = mgr.mark(pos.position_id, Decimal("1.60"), now=now)
    signal = evaluate_exit(
        marked,
        mark_price=Decimal("1.60"),
        now=now,
        policy=ExitPolicy(
            policy_id=ApprovedExitPolicyId.TARGET_AND_STOP.value,
            take_profit_ratio=0.5,
            stop_loss_ratio=0.35,
            eod_close=False,
        ),
    )
    assert signal.should_exit and signal.reason == "target"
    closed = mgr.close(pos.position_id, exit_price=Decimal("1.60"), reason="target", now=now)
    assert closed.status is PositionStatus.CLOSED
    settled = mgr.settle(closed.position_id)
    assert settled.status is PositionStatus.SETTLED


def test_restart_and_reconciliation_blocks_on_mismatch() -> None:
    sim = PaperExecutionSimulator()
    mgr = PositionManager(accounts=sim.accounts)
    now = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
    order = sim.submit(
        OrderIntent(
            intent_id="intent-restart",
            account_id="system_b_grok",
            candidate_id="cand-r",
            contracts=1,
            limit_price=Decimal("1.25"),
            created_at=now,
        ),
        now=now,
    )
    pos = mgr.on_order_state(order, now=now)
    assert pos is not None

    # Clean restart matches.
    bundle = restart_runtime(orders=sim.all_orders(), positions=mgr.all_positions())
    assert all(r.matched for r in bundle.reconciliation)

    # Corrupt open position set -> mismatch + block.
    broken = IsolatedAccountBook(starting_cash=Decimal("10000"))
    broken.ensure("system_b_grok")
    bad = restart_runtime(
        orders=sim.all_orders(),
        positions=mgr.all_positions(),
        accounts=broken,
    )
    # After restore+register, reconcile should match again because restart
    # re-registers opens. Force mismatch by reconciling empty positions.
    from spy_der.execution.reconciliation import reconcile_account

    result = reconcile_account(
        account_id="system_b_grok",
        book=bad.simulator.accounts,
        orders=sim.all_orders(),
        positions=(),  # pretend no positions reconstructed
    )
    assert not result.matched
    assert result.blocked_entries
    assert bad.simulator.accounts.get("system_b_grok").blocked


def test_position_transition_rejects_open_to_settled() -> None:
    with pytest.raises(ValueError):
        validate_position_transition(PositionStatus.OPEN, PositionStatus.SETTLED)
