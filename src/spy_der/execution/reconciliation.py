"""Execution/account reconciliation helpers."""

from __future__ import annotations

from spy_der.contracts.execution import OrderState, PaperAccount
from spy_der.contracts.positions import PositionState, ReconciliationResult
from spy_der.execution.accounts import IsolatedAccountBook

__all__ = ["reconcile_account"]


def reconcile_account(
    *,
    account_id: str,
    book: IsolatedAccountBook,
    orders: tuple[OrderState, ...] | list[OrderState],
    positions: tuple[PositionState, ...] | list[PositionState],
    expected: PaperAccount | None = None,
) -> ReconciliationResult:
    """Compare reconstructed account state vs expected ledger.

    On mismatch, block new entries for the account.
    """
    acct = book.get(account_id)
    discrepancies: list[str] = []

    for order in orders:
        if order.account_id != account_id:
            discrepancies.append(f"order_cross_account:{order.order_id}")

    open_ids = {p.position_id for p in positions if p.open_contracts > 0}
    for pid in open_ids:
        if pid not in acct.open_position_ids:
            discrepancies.append(f"missing_open_position:{pid}")
    for pid in acct.open_position_ids:
        if pid not in open_ids:
            discrepancies.append(f"orphan_open_position:{pid}")

    if expected is not None:
        if expected.account_id != account_id:
            discrepancies.append("expected_account_mismatch")
        if expected.cash != acct.cash:
            discrepancies.append(f"cash:{acct.cash}!={expected.cash}")
        if set(expected.open_position_ids) != set(acct.open_position_ids):
            discrepancies.append("open_position_set_mismatch")

    matched = not discrepancies
    if not matched:
        book.block(account_id, "reconciliation_mismatch")
    return ReconciliationResult(
        account_id=account_id,
        matched=matched,
        discrepancies=tuple(discrepancies),
        blocked_entries=not matched,
    )
