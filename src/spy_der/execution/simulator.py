"""Deterministic paper fill simulator (spec §51)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from spy_der.contracts.common import content_hash
from spy_der.contracts.execution import OrderFill, OrderIntent, OrderState, OrderStatus
from spy_der.execution.accounts import IsolatedAccountBook
from spy_der.execution.state_machine import is_terminal_order, validate_order_transition

__all__ = ["PaperExecutionSimulator", "SimulatorConfig"]


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    seed: str = "paper-sim-v1"
    default_fill_probability: float = 1.0
    allow_partials: bool = True
    fee_per_contract: Decimal = Decimal("0.65")
    reject_if_account_blocked: bool = True


@dataclass
class PaperExecutionSimulator:
    """Limit-order paper simulator with deterministic fill RNG."""

    accounts: IsolatedAccountBook = field(default_factory=IsolatedAccountBook)
    config: SimulatorConfig = field(default_factory=SimulatorConfig)
    _orders: dict[str, OrderState] = field(default_factory=dict)

    def submit(self, intent: OrderIntent, *, now: datetime | None = None) -> OrderState:
        now = now or datetime.now(tz=UTC)
        order_id = intent.intent_id or content_hash(
            {"account": intent.account_id, "candidate": intent.candidate_id, "ts": now.isoformat()}
        )
        state = OrderState(
            order_id=order_id,
            intent_id=intent.intent_id or order_id,
            account_id=intent.account_id,
            candidate_id=intent.candidate_id,
            status=OrderStatus.CREATED,
            requested_contracts=intent.contracts,
            created_at=intent.created_at or now,
            updated_at=now,
            limit_price=intent.limit_price,
        )
        self._orders[order_id] = state

        acct = self.accounts.ensure(intent.account_id)
        if self.config.reject_if_account_blocked and acct.blocked:
            return self._transition(
                order_id,
                OrderStatus.REJECTED,
                now=now,
                reason=f"account_blocked:{acct.block_reason}",
            )

        state = self._transition(
            order_id, OrderStatus.VALIDATED, now=now, reason="validated"
        )
        if intent.expires_at is not None and now >= intent.expires_at:
            return self._transition(
                order_id, OrderStatus.EXPIRED, now=now, reason="expired_at_submit"
            )

        state = self._transition(
            order_id,
            OrderStatus.SUBMITTED_SIMULATED,
            now=now,
            reason="submitted_simulated",
        )
        state = self._transition(
            order_id, OrderStatus.ACKNOWLEDGED, now=now, reason="acked"
        )
        return self._maybe_fill(
            intent, state, market_price=intent.limit_price, now=now
        )

    def on_quote(
        self,
        order_id: str,
        *,
        market_price: Decimal,
        now: datetime | None = None,
        intent: OrderIntent | None = None,
    ) -> OrderState:
        now = now or datetime.now(tz=UTC)
        state = self._orders[order_id]
        if is_terminal_order(state.status):
            return state
        if intent is None:
            intent = OrderIntent(
                intent_id=state.intent_id,
                account_id=state.account_id,
                candidate_id=state.candidate_id,
                contracts=max(1, state.requested_contracts - state.filled_contracts),
                limit_price=state.limit_price,
                created_at=state.created_at,
            )
        if intent.expires_at is not None and now >= intent.expires_at:
            return self._transition(order_id, OrderStatus.EXPIRED, now=now, reason="timeout")
        return self._maybe_fill(intent, state, market_price=market_price, now=now)

    def cancel(self, order_id: str, *, now: datetime | None = None) -> OrderState:
        now = now or datetime.now(tz=UTC)
        state = self._orders[order_id]
        if is_terminal_order(state.status):
            return state
        state = self._transition(
            order_id, OrderStatus.CANCEL_PENDING, now=now, reason="cancel_requested"
        )
        return self._transition(
            order_id, OrderStatus.CANCELED, now=now, reason="canceled"
        )

    def get(self, order_id: str) -> OrderState:
        return self._orders[order_id]

    def all_orders(self) -> tuple[OrderState, ...]:
        return tuple(self._orders.values())

    def restore_orders(self, orders: list[OrderState] | tuple[OrderState, ...]) -> None:
        self._orders = {o.order_id: o for o in orders}

    def _maybe_fill(
        self,
        intent: OrderIntent,
        state: OrderState,
        *,
        market_price: Decimal,
        now: datetime,
    ) -> OrderState:
        remaining = state.requested_contracts - state.filled_contracts
        if remaining <= 0:
            return state

        # Limit buy: fill when market <= limit; sell: market >= limit.
        side = intent.side
        can_fill = (
            market_price <= intent.limit_price
            if side.startswith("buy")
            else market_price >= intent.limit_price
        )
        if not can_fill:
            return state

        p_fill = intent.fill_probability
        if p_fill < 0:
            p_fill = self.config.default_fill_probability
        roll = self._unit_random(state.order_id, state.filled_contracts, now)
        if roll > p_fill:
            return state

        qty = remaining
        if self.config.allow_partials and remaining > 1 and roll > (p_fill * 0.5):
            qty = max(1, remaining // 2)

        fee = (intent.fee_per_contract or self.config.fee_per_contract) * Decimal(qty)
        fill = OrderFill(
            fill_id=content_hash(
                {
                    "order": state.order_id,
                    "qty": qty,
                    "price": str(market_price),
                    "n": state.filled_contracts,
                }
            ),
            order_id=state.order_id,
            account_id=state.account_id,
            quantity=qty,
            price=Decimal(str(market_price)),
            fees=fee,
            filled_at=now,
            simulated=True,
        )
        self.accounts.apply_fill(fill, debit=side.startswith("buy"))

        filled = state.filled_contracts + qty
        fills = (*state.fills, fill)
        total_notional = sum(
            (Decimal(f.quantity) * Decimal(str(f.price)) for f in fills),
            Decimal("0"),
        )
        avg = (total_notional / Decimal(filled)).quantize(Decimal("0.0001"))
        fees = state.fees + fee
        if filled >= state.requested_contracts:
            nxt = OrderStatus.FILLED
        else:
            nxt = OrderStatus.PARTIALLY_FILLED
        validate_order_transition(state.status, nxt)
        updated = OrderState(
            order_id=state.order_id,
            intent_id=state.intent_id,
            account_id=state.account_id,
            candidate_id=state.candidate_id,
            status=nxt,
            requested_contracts=state.requested_contracts,
            filled_contracts=filled,
            avg_fill_price=avg,
            fees=fees,
            fills=fills,
            reason="filled" if nxt is OrderStatus.FILLED else "partial_fill",
            created_at=state.created_at,
            updated_at=now,
            limit_price=state.limit_price,
        )
        self._orders[state.order_id] = updated
        return updated

    def _transition(
        self,
        order_id: str,
        nxt: OrderStatus,
        *,
        now: datetime,
        reason: str,
    ) -> OrderState:
        state = self._orders[order_id]
        if state.status is nxt:
            return state
        validate_order_transition(state.status, nxt)
        updated = OrderState(
            order_id=state.order_id,
            intent_id=state.intent_id,
            account_id=state.account_id,
            candidate_id=state.candidate_id,
            status=nxt,
            requested_contracts=state.requested_contracts,
            filled_contracts=state.filled_contracts,
            avg_fill_price=state.avg_fill_price,
            fees=state.fees,
            fills=state.fills,
            reason=reason,
            created_at=state.created_at,
            updated_at=now,
            limit_price=state.limit_price,
        )
        self._orders[order_id] = updated
        return updated

    def _unit_random(self, order_id: str, filled: int, now: datetime) -> float:
        material = f"{self.config.seed}|{order_id}|{filled}|{now.isoformat()}".encode()
        digest = hashlib.sha256(material).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF
