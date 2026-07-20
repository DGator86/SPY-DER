"""Execution contracts (master spec §51)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "EXECUTION_SCHEMA",
    "ISOLATED_ACCOUNTS",
    "OrderFill",
    "OrderIntent",
    "OrderState",
    "OrderStatus",
    "PaperAccount",
    "assert_account_id",
    "is_isolated_account",
]

EXECUTION_SCHEMA = "execution.v1"

ISOLATED_ACCOUNTS: frozenset[str] = frozenset(
    {
        "system_a_legacy",
        "system_b_legacy",
        "system_b_v2",
        "system_b_v3",
        "system_b_ensemble",
        "system_b_grok",
    }
)


def is_isolated_account(account_id: str) -> bool:
    if account_id in ISOLATED_ACCOUNTS:
        return True
    return account_id.startswith("system_b_challenger_")


def assert_account_id(account_id: str) -> None:
    if not account_id or not is_isolated_account(account_id):
        raise ValueError(f"unknown isolated account_id: {account_id!r}")


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED_SIMULATED = "SUBMITTED_SIMULATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"
    # Phase-0 alias kept for older callers/tests.
    ROUTED = "ROUTED"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    schema_version: str = EXECUTION_SCHEMA
    intent_id: str = ""
    account_id: str = ""
    candidate_id: str = ""
    side: str = "buy_to_open"
    contracts: int = 1
    limit_price: Decimal = Decimal("0")
    created_at: datetime | None = None
    expires_at: datetime | None = None
    risk_decision_id: str = ""
    exit_policy_id: str = ""
    fill_probability: float = 1.0
    fee_per_contract: Decimal = Decimal("0.65")
    geometry_hash: str = ""
    snapshot_id: str = ""

    def __post_init__(self) -> None:
        if self.account_id:
            assert_account_id(self.account_id)
        if self.contracts <= 0:
            raise ValueError("contracts must be positive")
        if not 0.0 <= self.fill_probability <= 1.0:
            raise ValueError("fill_probability must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class OrderFill:
    fill_id: str
    order_id: str
    account_id: str
    quantity: int
    price: Decimal
    fees: Decimal = Decimal("0")
    filled_at: datetime | None = None
    simulated: bool = True

    def __post_init__(self) -> None:
        assert_account_id(self.account_id)
        if self.quantity <= 0:
            raise ValueError("fill quantity must be positive")


@dataclass(frozen=True, slots=True)
class OrderState:
    schema_version: str = EXECUTION_SCHEMA
    order_id: str = ""
    intent_id: str = ""
    account_id: str = ""
    candidate_id: str = ""
    status: OrderStatus = OrderStatus.CREATED
    requested_contracts: int = 0
    filled_contracts: int = 0
    avg_fill_price: Decimal | None = None
    fees: Decimal = Decimal("0")
    fills: tuple[OrderFill, ...] = ()
    reason: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    limit_price: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.account_id:
            assert_account_id(self.account_id)
        if self.filled_contracts < 0:
            raise ValueError("filled_contracts cannot be negative")
        if self.requested_contracts < 0:
            raise ValueError("requested_contracts cannot be negative")


@dataclass(frozen=True, slots=True)
class PaperAccount:
    """Isolated paper ledger for one account_id."""

    account_id: str
    cash: Decimal
    equity: Decimal
    starting_cash: Decimal
    open_position_ids: tuple[str, ...] = ()
    daily_realized_pnl: Decimal = Decimal("0")
    trade_count: int = 0
    blocked: bool = False
    block_reason: str = ""
    events: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        assert_account_id(self.account_id)
        if self.cash < 0 or self.equity < 0:
            raise ValueError("cash/equity cannot be negative")
