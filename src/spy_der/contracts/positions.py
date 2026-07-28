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
    "profit_ratio",
]

POSITION_SCHEMA = "position.v1"


def profit_ratio(
    entry_price: Decimal | float | str,
    mark_price: Decimal | float | str,
    *,
    opened_for_credit: bool,
) -> Decimal:
    """Fraction of the entry price gained, signed so positive is always profit.

    The direction is the whole point. ``avg_fill_price`` is a positive price
    magnitude — a structure sold for a 1.00 credit and one bought for a 1.00
    debit both record ``entry_price = 1.00``, with the direction living in the
    order's ``side``. So the naive ``(mark - entry) / entry`` is right for a
    debit and exactly backwards for a credit:

    ===================================  ==========  ==================
    Iron fly sold at 1.00                Reality     Naive ratio
    ===================================  ==========  ==================
    price falls to 0.50 (buy back cheap) winning     -0.50, reads as a loss
    price rises to 1.60 (buy back dear)  losing      +0.60, reads as a win
    ===================================  ==========  ==================

    Fed to :func:`~spy_der.positions.exits.evaluate_exit` that stopped out
    winners and took profit on losers — both locking in a loss, on exactly the
    credit families (iron fly, iron condor, credit spreads) the system trades
    most. Every consumer of a profit ratio must come through here.

    For a credit structure the convention this produces is the conventional
    one: ``take_profit_ratio = 0.5`` means buying the structure back for half
    the credit received.
    """
    entry = Decimal(str(entry_price))
    if entry == 0:
        raise ValueError("profit_ratio needs a non-zero entry price")
    move = (Decimal(str(mark_price)) - entry) / entry
    return -move if opened_for_credit else move


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
    #: True when the structure was opened for a net credit. Load-bearing:
    #: ``avg_fill_price`` is a positive price magnitude and carries direction in
    #: the order's ``side``, not in its sign, so without this a credit structure
    #: has its profit direction backwards. See :func:`profit_ratio`.
    opened_for_credit: bool = False

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
