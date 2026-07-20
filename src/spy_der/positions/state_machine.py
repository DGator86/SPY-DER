"""Position state machine (master spec §52)."""

from __future__ import annotations

from spy_der.contracts.positions import PositionStatus

__all__ = ["is_terminal_position", "validate_position_transition"]

_ALLOWED: dict[PositionStatus, set[PositionStatus]] = {
    PositionStatus.PENDING_OPEN: {
        PositionStatus.OPEN,
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.OPEN: {
        PositionStatus.PARTIALLY_REDUCED,
        PositionStatus.CLOSE_PENDING,
        PositionStatus.CLOSING,
        PositionStatus.CLOSED,
        PositionStatus.EXPIRED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.PARTIALLY_REDUCED: {
        PositionStatus.PARTIALLY_REDUCED,
        PositionStatus.CLOSE_PENDING,
        PositionStatus.CLOSING,
        PositionStatus.CLOSED,
        PositionStatus.EXPIRED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.CLOSE_PENDING: {
        PositionStatus.OPEN,
        PositionStatus.PARTIALLY_REDUCED,
        PositionStatus.CLOSED,
        PositionStatus.EXPIRED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.CLOSING: {
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.CLOSED: {
        PositionStatus.SETTLED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.EXPIRED: {
        PositionStatus.SETTLED,
        PositionStatus.RECONCILIATION_ERROR,
    },
    PositionStatus.SETTLED: set(),
    PositionStatus.RECONCILIATION_ERROR: set(),
}


def validate_position_transition(current: PositionStatus, nxt: PositionStatus) -> None:
    allowed = _ALLOWED.get(current, set())
    if nxt not in allowed:
        msg = f"invalid position transition: {current.value} -> {nxt.value}"
        raise ValueError(msg)


def is_terminal_position(status: PositionStatus) -> bool:
    return status in {
        PositionStatus.SETTLED,
        PositionStatus.RECONCILIATION_ERROR,
    }
