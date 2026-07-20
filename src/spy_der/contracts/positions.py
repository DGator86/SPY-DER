"""Position contracts (master spec §52)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from spy_der.contracts.execution import assert_account_id

__all__ = [
    "POSITION_SCHEMA",
    "ApprovedExitPolicyId",
    "ExitPolicy",
    "PositionState",
    "PositionStatus",
    "ReconciliationResult",
]

POSITION_SCHEMA = "position.v1"


class PositionStatus(StrEnum):
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PARTIALLY_REDUCED = "PARTIALLY_REDUCED"
    CLOSE_PENDING = "CLOSE_PENDING"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    SETTLED = "SETTLED"
    RECONCILIATION_ERROR = "RECONCILIATION_ERROR"
    # Phase-0 alias.
    CLOSING = "CLOSING"


class ApprovedExitPolicyId(StrEnum):
    FIXED_TARGET = "fixed_target"
    FIXED_STOP = "fixed_stop"
    TARGET_AND_STOP = "target_and_stop"
    TRAILING = "trailing"
    TIME_EXIT = "time_exit"
    EOD_EXIT = "eod_exit"
    STRUCTURAL_RAS_EXIT = "structural_ras_exit"
    EMERGENCY_EXIT = "emergency_exit"
    EXPIRATION_SETTLEMENT = "expiration_settlement"


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    schema_version: str = POSITION_SCHEMA
    policy_id: str = ApprovedExitPolicyId.TARGET_AND_STOP.value
    take_profit_ratio: float = 0.5
    stop_loss_ratio: float = 0.35
    trailing_arm_ratio: float = 0.25
    trailing_giveback_ratio: float = 0.15
    max_holding_minutes: int = 0
    eod_close: bool = True

    def __post_init__(self) -> None:
        allowed = {p.value for p in ApprovedExitPolicyId}
        if self.policy_id not in allowed:
            raise ValueError(f"exit policy not approved: {self.policy_id}")


@dataclass(frozen=True, slots=True)
class PositionState:
    schema_version: str = POSITION_SCHEMA
    position_id: str = ""
    account_id: str = ""
    candidate_id: str = ""
    status: PositionStatus = PositionStatus.PENDING_OPEN
    opened_contracts: int = 0
    open_contracts: int = 0
    entry_price: Decimal | None = None
    mark_price: Decimal | None = None
    max_loss: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    peak_pnl: Decimal = Decimal("0")
    exit_policy_id: str = ApprovedExitPolicyId.TARGET_AND_STOP.value
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_reason: str = ""
    order_ids: tuple[str, ...] = ()
    geometry_hash: str = ""

    def __post_init__(self) -> None:
        if self.account_id:
            assert_account_id(self.account_id)
        if self.open_contracts < 0 or self.opened_contracts < 0:
            raise ValueError("contracts cannot be negative")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    account_id: str
    matched: bool
    discrepancies: tuple[str, ...] = ()
    blocked_entries: bool = False

    def __post_init__(self) -> None:
        assert_account_id(self.account_id)
        if self.discrepancies and self.matched:
            raise ValueError("matched reconciliation cannot carry discrepancies")
