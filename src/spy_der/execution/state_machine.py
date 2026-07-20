"""Order state machine (master spec §51)."""

from __future__ import annotations

from spy_der.contracts.execution import OrderStatus

__all__ = ["is_terminal_order", "validate_order_transition"]

_ALLOWED: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {
        OrderStatus.VALIDATED,
        OrderStatus.REJECTED,
        OrderStatus.ERROR,
        # Phase-0 compatibility path.
        OrderStatus.ROUTED,
        OrderStatus.CANCELED,
    },
    OrderStatus.VALIDATED: {
        OrderStatus.SUBMITTED_SIMULATED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.ERROR,
        OrderStatus.ROUTED,
    },
    OrderStatus.SUBMITTED_SIMULATED: {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.REJECTED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.EXPIRED,
        OrderStatus.ERROR,
    },
    OrderStatus.ROUTED: {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.EXPIRED,
        OrderStatus.ERROR,
    },
    OrderStatus.ACKNOWLEDGED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.ERROR,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.EXPIRED,
        OrderStatus.ERROR,
        OrderStatus.CANCELED,
    },
    OrderStatus.CANCEL_PENDING: {
        OrderStatus.CANCELED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.ERROR,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.ERROR: set(),
}


def validate_order_transition(current: OrderStatus, nxt: OrderStatus) -> None:
    allowed = _ALLOWED.get(current, set())
    if nxt not in allowed:
        msg = f"invalid order transition: {current.value} -> {nxt.value}"
        raise ValueError(msg)


def is_terminal_order(status: OrderStatus) -> bool:
    return status in {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.ERROR,
    }
