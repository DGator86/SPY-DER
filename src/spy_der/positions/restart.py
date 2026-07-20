"""Restart reconstruction from persisted order/position snapshots (spec §52)."""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.execution import OrderState
from spy_der.contracts.positions import PositionState, ReconciliationResult
from spy_der.execution.accounts import IsolatedAccountBook
from spy_der.execution.reconciliation import reconcile_account
from spy_der.execution.simulator import PaperExecutionSimulator
from spy_der.positions.manager import PositionManager

__all__ = ["RestartBundle", "restart_runtime"]


@dataclass(frozen=True, slots=True)
class RestartBundle:
    simulator: PaperExecutionSimulator
    positions: PositionManager
    reconciliation: tuple[ReconciliationResult, ...]


def restart_runtime(
    *,
    orders: tuple[OrderState, ...] | list[OrderState],
    positions: tuple[PositionState, ...] | list[PositionState],
    accounts: IsolatedAccountBook | None = None,
    starting_cash: str = "10000",
) -> RestartBundle:
    """Load orders/positions, rebuild books, reconcile, block on mismatch."""
    from decimal import Decimal

    book = accounts or IsolatedAccountBook(starting_cash=Decimal(starting_cash))
    sim = PaperExecutionSimulator(accounts=book)
    sim.restore_orders(orders)
    mgr = PositionManager(accounts=book)
    mgr.restore(positions)

    # Re-register open positions into account ledgers.
    for pos in positions:
        book.ensure(pos.account_id)
        if pos.open_contracts > 0:
            book.register_position(pos.account_id, pos.position_id)

    account_ids = sorted({o.account_id for o in orders} | {p.account_id for p in positions})
    results: list[ReconciliationResult] = []
    for account_id in account_ids:
        acct_orders = tuple(o for o in orders if o.account_id == account_id)
        acct_positions = tuple(p for p in positions if p.account_id == account_id)
        results.append(
            reconcile_account(
                account_id=account_id,
                book=book,
                orders=acct_orders,
                positions=acct_positions,
            )
        )
    return RestartBundle(
        simulator=sim,
        positions=mgr,
        reconciliation=tuple(results),
    )
