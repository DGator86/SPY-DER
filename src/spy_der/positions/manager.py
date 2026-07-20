"""Position manager: open/mark/exit from order states."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from spy_der.contracts.common import content_hash
from spy_der.contracts.execution import OrderState, OrderStatus
from spy_der.contracts.positions import ExitPolicy, PositionState, PositionStatus
from spy_der.execution.accounts import IsolatedAccountBook
from spy_der.positions.exits import ExitSignal, evaluate_exit
from spy_der.positions.state_machine import validate_position_transition

__all__ = ["PositionManager"]


@dataclass
class PositionManager:
    accounts: IsolatedAccountBook = field(default_factory=IsolatedAccountBook)
    default_policy: ExitPolicy = field(default_factory=ExitPolicy)
    _positions: dict[str, PositionState] = field(default_factory=dict)

    def on_order_state(
        self,
        order: OrderState,
        *,
        max_loss: Decimal = Decimal("0"),
        exit_policy_id: str = "",
        now: datetime | None = None,
    ) -> PositionState | None:
        now = now or datetime.now(tz=UTC)
        if order.status not in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}:
            return None
        if order.filled_contracts <= 0 or order.avg_fill_price is None:
            return None

        position_id = content_hash(
            {
                "account": order.account_id,
                "candidate": order.candidate_id,
                "order": order.order_id,
            }
        )
        existing = self._positions.get(position_id)
        if existing is None:
            pending = PositionState(
                position_id=position_id,
                account_id=order.account_id,
                candidate_id=order.candidate_id,
                status=PositionStatus.PENDING_OPEN,
                opened_contracts=order.filled_contracts,
                open_contracts=order.filled_contracts,
                entry_price=order.avg_fill_price,
                mark_price=order.avg_fill_price,
                max_loss=max_loss,
                exit_policy_id=exit_policy_id or self.default_policy.policy_id,
                opened_at=now,
                order_ids=(order.order_id,),
            )
            validate_position_transition(PositionStatus.PENDING_OPEN, PositionStatus.OPEN)
            opened = PositionState(
                position_id=pending.position_id,
                account_id=pending.account_id,
                candidate_id=pending.candidate_id,
                status=PositionStatus.OPEN,
                opened_contracts=pending.opened_contracts,
                open_contracts=pending.open_contracts,
                entry_price=pending.entry_price,
                mark_price=pending.mark_price,
                max_loss=pending.max_loss,
                exit_policy_id=pending.exit_policy_id,
                opened_at=pending.opened_at,
                order_ids=pending.order_ids,
            )
            self._positions[position_id] = opened
            self.accounts.register_position(order.account_id, position_id)
            return opened

        # Additional partial fill on same order identity.
        if order.filled_contracts > existing.opened_contracts:
            validate_position_transition(existing.status, PositionStatus.OPEN)
            updated = PositionState(
                position_id=existing.position_id,
                account_id=existing.account_id,
                candidate_id=existing.candidate_id,
                status=PositionStatus.OPEN,
                opened_contracts=order.filled_contracts,
                open_contracts=order.filled_contracts,
                entry_price=order.avg_fill_price,
                mark_price=order.avg_fill_price,
                max_loss=existing.max_loss,
                realized_pnl=existing.realized_pnl,
                unrealized_pnl=existing.unrealized_pnl,
                peak_pnl=existing.peak_pnl,
                exit_policy_id=existing.exit_policy_id,
                opened_at=existing.opened_at,
                order_ids=tuple(dict.fromkeys((*existing.order_ids, order.order_id))),
            )
            self._positions[position_id] = updated
            return updated
        return existing

    def mark(
        self,
        position_id: str,
        mark_price: Decimal,
        *,
        now: datetime | None = None,
    ) -> PositionState:
        pos = self._positions[position_id]
        if pos.entry_price is None or pos.open_contracts <= 0:
            return pos
        pnl_ratio = (Decimal(str(mark_price)) - Decimal(str(pos.entry_price))) / Decimal(
            str(pos.entry_price)
        )
        peak = max(pos.peak_pnl, pnl_ratio)
        updated = PositionState(
            position_id=pos.position_id,
            account_id=pos.account_id,
            candidate_id=pos.candidate_id,
            status=pos.status,
            opened_contracts=pos.opened_contracts,
            open_contracts=pos.open_contracts,
            entry_price=pos.entry_price,
            mark_price=Decimal(str(mark_price)),
            max_loss=pos.max_loss,
            realized_pnl=pos.realized_pnl,
            unrealized_pnl=pnl_ratio * Decimal(pos.open_contracts),
            peak_pnl=peak,
            exit_policy_id=pos.exit_policy_id,
            opened_at=pos.opened_at,
            closed_at=pos.closed_at,
            exit_reason=pos.exit_reason,
            order_ids=pos.order_ids,
            geometry_hash=pos.geometry_hash,
        )
        self._positions[position_id] = updated
        return updated

    def evaluate_exit(
        self,
        position_id: str,
        *,
        mark_price: Decimal,
        now: datetime | None = None,
        ras_exit: bool = False,
        emergency: bool = False,
        expired: bool = False,
        policy: ExitPolicy | None = None,
    ) -> ExitSignal:
        now = now or datetime.now(tz=UTC)
        pos = self.mark(position_id, mark_price, now=now)
        return evaluate_exit(
            pos,
            mark_price=mark_price,
            now=now,
            policy=policy,
            ras_exit=ras_exit,
            emergency=emergency,
            expired=expired,
        )

    def close(
        self,
        position_id: str,
        *,
        exit_price: Decimal,
        reason: str,
        now: datetime | None = None,
    ) -> PositionState:
        now = now or datetime.now(tz=UTC)
        pos = self._positions[position_id]
        if pos.status in {PositionStatus.CLOSED, PositionStatus.SETTLED}:
            return pos
        validate_position_transition(pos.status, PositionStatus.CLOSE_PENDING)
        validate_position_transition(PositionStatus.CLOSE_PENDING, PositionStatus.CLOSED)
        entry = pos.entry_price or Decimal("0")
        pnl = (Decimal(str(exit_price)) - entry) * Decimal(pos.open_contracts)
        closed = PositionState(
            position_id=pos.position_id,
            account_id=pos.account_id,
            candidate_id=pos.candidate_id,
            status=PositionStatus.CLOSED,
            opened_contracts=pos.opened_contracts,
            open_contracts=0,
            entry_price=pos.entry_price,
            mark_price=Decimal(str(exit_price)),
            max_loss=pos.max_loss,
            realized_pnl=pos.realized_pnl + pnl,
            unrealized_pnl=Decimal("0"),
            peak_pnl=pos.peak_pnl,
            exit_policy_id=pos.exit_policy_id,
            opened_at=pos.opened_at,
            closed_at=now,
            exit_reason=reason,
            order_ids=pos.order_ids,
            geometry_hash=pos.geometry_hash,
        )
        self._positions[position_id] = closed
        self.accounts.close_position(pos.account_id, position_id, pnl)
        return closed

    def settle(self, position_id: str) -> PositionState:
        pos = self._positions[position_id]
        validate_position_transition(pos.status, PositionStatus.SETTLED)
        settled = PositionState(
            position_id=pos.position_id,
            account_id=pos.account_id,
            candidate_id=pos.candidate_id,
            status=PositionStatus.SETTLED,
            opened_contracts=pos.opened_contracts,
            open_contracts=pos.open_contracts,
            entry_price=pos.entry_price,
            mark_price=pos.mark_price,
            max_loss=pos.max_loss,
            realized_pnl=pos.realized_pnl,
            unrealized_pnl=pos.unrealized_pnl,
            peak_pnl=pos.peak_pnl,
            exit_policy_id=pos.exit_policy_id,
            opened_at=pos.opened_at,
            closed_at=pos.closed_at,
            exit_reason=pos.exit_reason,
            order_ids=pos.order_ids,
            geometry_hash=pos.geometry_hash,
        )
        self._positions[position_id] = settled
        return settled

    def get(self, position_id: str) -> PositionState:
        return self._positions[position_id]

    def open_positions(self, account_id: str | None = None) -> tuple[PositionState, ...]:
        vals = self._positions.values()
        if account_id is None:
            return tuple(p for p in vals if p.open_contracts > 0)
        return tuple(
            p for p in vals if p.account_id == account_id and p.open_contracts > 0
        )

    def all_positions(self) -> tuple[PositionState, ...]:
        return tuple(self._positions.values())

    def restore(self, positions: list[PositionState] | tuple[PositionState, ...]) -> None:
        self._positions = {p.position_id: p for p in positions}
