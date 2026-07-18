from __future__ import annotations

from system_b.contracts import OrderStatus, PositionStatus

_ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.ROUTED, OrderStatus.CANCELED, OrderStatus.REJECTED},
    OrderStatus.ROUTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELED: set(),
    OrderStatus.REJECTED: set(),
}

_ALLOWED_POSITION_TRANSITIONS: dict[PositionStatus, set[PositionStatus]] = {
    PositionStatus.OPEN: {PositionStatus.CLOSING, PositionStatus.CLOSED},
    PositionStatus.CLOSING: {PositionStatus.CLOSED},
    PositionStatus.CLOSED: set(),
}


def validate_order_transition(current: OrderStatus, nxt: OrderStatus) -> None:
    if nxt not in _ALLOWED_ORDER_TRANSITIONS[current]:
        msg = f"invalid order transition: {current.value} -> {nxt.value}"
        raise ValueError(msg)


def validate_position_transition(current: PositionStatus, nxt: PositionStatus) -> None:
    if nxt not in _ALLOWED_POSITION_TRANSITIONS[current]:
        msg = f"invalid position transition: {current.value} -> {nxt.value}"
        raise ValueError(msg)
