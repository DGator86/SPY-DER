"""Read models rebuilt from the append-only event stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from spy_der.contracts.events import JournalEvent, JournalEventType

__all__ = [
    "JournalProjections",
    "OrderProjection",
    "OutcomeProjection",
    "PositionProjection",
    "SessionProjection",
    "project_events",
]


@dataclass
class OrderProjection:
    order_id: str
    status: str = ""
    account_id: str = ""
    candidate_id: str = ""
    filled_contracts: int = 0


@dataclass
class PositionProjection:
    position_id: str
    status: str = ""
    account_id: str = ""
    candidate_id: str = ""
    open_contracts: int = 0


@dataclass
class OutcomeProjection:
    record_id: str
    candidate_id: str = ""
    realized_pnl: Decimal = Decimal("0")
    counterfactual: bool = False


@dataclass
class SessionProjection:
    session_date: str
    event_count: int = 0
    outcomes: int = 0
    counterfactuals: int = 0
    realized_pnl_total: Decimal = Decimal("0")


@dataclass
class JournalProjections:
    orders: dict[str, OrderProjection] = field(default_factory=dict)
    positions: dict[str, PositionProjection] = field(default_factory=dict)
    outcomes: dict[str, OutcomeProjection] = field(default_factory=dict)
    sessions: dict[str, SessionProjection] = field(default_factory=dict)
    latest_event_hash: str | None = None


def project_events(events: tuple[JournalEvent, ...] | list[JournalEvent]) -> JournalProjections:
    proj = JournalProjections()
    for event in events:
        proj.latest_event_hash = event.event_hash or proj.latest_event_hash
        payload: dict[str, Any] = dict(event.payload)
        et = event.event_type

        if et in {
            JournalEventType.ORDER_SUBMITTED_SIMULATED.value,
            JournalEventType.ORDER_PARTIALLY_FILLED.value,
            JournalEventType.ORDER_FILLED.value,
            JournalEventType.ORDER_CANCELED.value,
            JournalEventType.ORDER_REJECTED.value,
            JournalEventType.ORDER_INTENT_CREATED.value,
        }:
            oid = str(payload.get("order_id") or event.aggregate_id)
            order = proj.orders.get(oid) or OrderProjection(order_id=oid)
            order.status = et.removeprefix("order_").upper() if et.startswith("order_") else et
            order.account_id = str(payload.get("account_id") or order.account_id)
            order.candidate_id = str(payload.get("candidate_id") or order.candidate_id)
            if "filled_contracts" in payload:
                order.filled_contracts = int(payload["filled_contracts"])
            proj.orders[oid] = order

        if et in {
            JournalEventType.POSITION_OPENED.value,
            JournalEventType.POSITION_MARKED.value,
            JournalEventType.POSITION_REDUCED.value,
            JournalEventType.POSITION_CLOSED.value,
        }:
            pid = str(payload.get("position_id") or event.aggregate_id)
            pos = proj.positions.get(pid) or PositionProjection(position_id=pid)
            pos.status = et.removeprefix("position_").upper()
            pos.account_id = str(payload.get("account_id") or pos.account_id)
            pos.candidate_id = str(payload.get("candidate_id") or pos.candidate_id)
            if "open_contracts" in payload:
                pos.open_contracts = int(payload["open_contracts"])
            if et == JournalEventType.POSITION_CLOSED.value:
                pos.open_contracts = 0
                pos.status = "CLOSED"
            proj.positions[pid] = pos

        if et in {
            JournalEventType.OUTCOME_SETTLED.value,
            JournalEventType.COUNTERFACTUAL_SETTLED.value,
        }:
            rid = str(payload.get("record_id") or event.event_id)
            pnl = Decimal(str(payload.get("realized_pnl", "0")))
            proj.outcomes[rid] = OutcomeProjection(
                record_id=rid,
                candidate_id=str(payload.get("candidate_id") or ""),
                realized_pnl=pnl,
                counterfactual=et == JournalEventType.COUNTERFACTUAL_SETTLED.value,
            )
            session_date = str(payload.get("session_date") or "unknown")
            sess = proj.sessions.get(session_date) or SessionProjection(session_date=session_date)
            sess.event_count += 1
            if et == JournalEventType.OUTCOME_SETTLED.value:
                sess.outcomes += 1
                sess.realized_pnl_total += pnl
            else:
                sess.counterfactuals += 1
            proj.sessions[session_date] = sess

    return proj
