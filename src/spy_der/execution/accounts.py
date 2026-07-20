"""Isolated paper account ledgers (spec §51)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from spy_der.contracts.execution import (
    ISOLATED_ACCOUNTS,
    OrderFill,
    PaperAccount,
    assert_account_id,
    is_isolated_account,
)

__all__ = ["IsolatedAccountBook", "default_account_ids"]


def default_account_ids() -> tuple[str, ...]:
    return tuple(sorted(ISOLATED_ACCOUNTS))


@dataclass
class IsolatedAccountBook:
    """Per-account cash/equity ledgers. Accounts cannot mutate each other."""

    starting_cash: Decimal = Decimal("10000")
    _accounts: dict[str, PaperAccount] = field(default_factory=dict)

    def ensure(self, account_id: str) -> PaperAccount:
        assert_account_id(account_id)
        if account_id not in self._accounts:
            cash = Decimal(str(self.starting_cash))
            self._accounts[account_id] = PaperAccount(
                account_id=account_id,
                cash=cash,
                equity=cash,
                starting_cash=cash,
            )
        return self._accounts[account_id]

    def get(self, account_id: str) -> PaperAccount:
        return self.ensure(account_id)

    def block(self, account_id: str, reason: str) -> PaperAccount:
        acct = self.ensure(account_id)
        updated = PaperAccount(
            account_id=acct.account_id,
            cash=acct.cash,
            equity=acct.equity,
            starting_cash=acct.starting_cash,
            open_position_ids=acct.open_position_ids,
            daily_realized_pnl=acct.daily_realized_pnl,
            trade_count=acct.trade_count,
            blocked=True,
            block_reason=reason,
            events=(*acct.events, f"blocked:{reason}"),
        )
        self._accounts[account_id] = updated
        return updated

    def apply_fill(self, fill: OrderFill, *, debit: bool = True) -> PaperAccount:
        acct = self.ensure(fill.account_id)
        if acct.blocked:
            raise ValueError(f"account blocked: {acct.block_reason}")
        notional = Decimal(fill.quantity) * Decimal(str(fill.price)) + Decimal(str(fill.fees))
        cash = acct.cash - notional if debit else acct.cash + notional
        if cash < 0:
            raise ValueError("insufficient cash for fill")
        updated = PaperAccount(
            account_id=acct.account_id,
            cash=cash,
            equity=cash,  # marks applied by position manager
            starting_cash=acct.starting_cash,
            open_position_ids=acct.open_position_ids,
            daily_realized_pnl=acct.daily_realized_pnl,
            trade_count=acct.trade_count + (1 if debit else 0),
            blocked=acct.blocked,
            block_reason=acct.block_reason,
            events=(*acct.events, f"fill:{fill.fill_id}"),
        )
        self._accounts[fill.account_id] = updated
        return updated

    def register_position(self, account_id: str, position_id: str) -> PaperAccount:
        acct = self.ensure(account_id)
        if position_id in acct.open_position_ids:
            return acct
        updated = PaperAccount(
            account_id=acct.account_id,
            cash=acct.cash,
            equity=acct.equity,
            starting_cash=acct.starting_cash,
            open_position_ids=(*acct.open_position_ids, position_id),
            daily_realized_pnl=acct.daily_realized_pnl,
            trade_count=acct.trade_count,
            blocked=acct.blocked,
            block_reason=acct.block_reason,
            events=acct.events,
        )
        self._accounts[account_id] = updated
        return updated

    def close_position(
        self,
        account_id: str,
        position_id: str,
        realized_pnl: Decimal,
    ) -> PaperAccount:
        acct = self.ensure(account_id)
        remaining = tuple(p for p in acct.open_position_ids if p != position_id)
        cash = acct.cash + Decimal(str(realized_pnl))
        updated = PaperAccount(
            account_id=acct.account_id,
            cash=cash,
            equity=cash,
            starting_cash=acct.starting_cash,
            open_position_ids=remaining,
            daily_realized_pnl=acct.daily_realized_pnl + Decimal(str(realized_pnl)),
            trade_count=acct.trade_count,
            blocked=acct.blocked,
            block_reason=acct.block_reason,
            events=(*acct.events, f"close:{position_id}"),
        )
        self._accounts[account_id] = updated
        return updated

    def snapshot(self) -> dict[str, PaperAccount]:
        return dict(self._accounts)

    def assert_same_account(self, account_id: str, other_account_id: str) -> None:
        if account_id != other_account_id:
            raise ValueError(
                f"cross-account mutation forbidden: {account_id} != {other_account_id}"
            )
        if not is_isolated_account(account_id):
            assert_account_id(account_id)
