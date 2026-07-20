"""Settlement and counterfactual outcome contracts (spec §53-§54)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "OUTCOME_SCHEMA",
    "CounterfactualOutcome",
    "OutcomeRecord",
    "SettlementSource",
    "SettlementStatus",
]

OUTCOME_SCHEMA = "outcome.v1"


class SettlementStatus(StrEnum):
    UNSETTLED = "unsettled"
    SETTLED = "settled"
    COUNTERFACTUAL = "counterfactual"


class SettlementSource(StrEnum):
    SESSION_CLOSE = "session_close"
    EXPIRATION = "expiration"
    MANUAL = "manual"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    schema_version: str = OUTCOME_SCHEMA
    record_id: str = ""
    account_id: str = ""
    candidate_id: str = ""
    position_id: str = ""
    snapshot_id: str = ""
    session_date: str = ""
    settlement_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    contracts: int = 0
    status: SettlementStatus = SettlementStatus.SETTLED
    source: SettlementSource = SettlementSource.SESSION_CLOSE
    settled_at: datetime | None = None
    entry_credit: Decimal = Decimal("0")
    was_traded: bool = True


@dataclass(frozen=True, slots=True)
class CounterfactualOutcome:
    schema_version: str = OUTCOME_SCHEMA
    record_id: str = ""
    candidate_id: str = ""
    snapshot_id: str = ""
    session_date: str = ""
    settlement_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    reason_not_taken: str = ""
    policy_id: str = ""
    would_have_filled: bool = True
    settled_at: datetime | None = None
