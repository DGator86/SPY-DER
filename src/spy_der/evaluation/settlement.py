"""Session settlement and counterfactual outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from spy_der.candidates.payoff import terminal_payoff
from spy_der.contracts.candidates import Candidate, CandidateLeg
from spy_der.contracts.common import content_hash
from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.contracts.outcomes import (
    CounterfactualOutcome,
    OutcomeRecord,
    SettlementSource,
    SettlementStatus,
)
from spy_der.journal.store import InMemoryJournalStore

__all__ = [
    "SettlementBatch",
    "settle_candidate",
    "settle_session",
]


@dataclass(frozen=True, slots=True)
class SettlementBatch:
    outcomes: tuple[OutcomeRecord, ...]
    counterfactuals: tuple[CounterfactualOutcome, ...]
    events: tuple[JournalEvent, ...]


def settle_candidate(
    *,
    legs: tuple[CandidateLeg, ...],
    entry_credit: Decimal,
    settlement_price: Decimal,
    contracts: int = 1,
) -> Decimal:
    """Per-share terminal PnL * contracts (System A realized_pnl semantics)."""
    per_share = terminal_payoff(
        legs, entry_credit=entry_credit, spot=Decimal(str(settlement_price))
    )
    return (per_share * Decimal(contracts)).quantize(Decimal("0.0001"))


def settle_session(
    *,
    session_date: str,
    settlement_price: Decimal,
    traded: Sequence[tuple[Candidate, Decimal, int, str]] = (),
    blocked: Sequence[tuple[Candidate, Decimal, str, str]] = (),
    journal: InMemoryJournalStore | None = None,
    account_id: str = "system_b_ensemble",
    deployment_id: str = "phase13",
    now: datetime | None = None,
    source: SettlementSource = SettlementSource.SESSION_CLOSE,
) -> SettlementBatch:
    """Settle traded positions and counterfactual blocked candidates.

    traded items: (candidate, entry_credit, contracts, position_id)
    blocked items: (candidate, entry_credit, reason_not_taken, policy_id)
    """
    now = now or datetime.now(tz=UTC)
    # InMemoryJournalStore defines __len__; empty stores are falsy under bool().
    store = journal if journal is not None else InMemoryJournalStore()
    outcomes: list[OutcomeRecord] = []
    counterfactuals: list[CounterfactualOutcome] = []
    events: list[JournalEvent] = []

    for candidate, entry_credit, contracts, position_id in traded:
        pnl = settle_candidate(
            legs=candidate.legs,
            entry_credit=entry_credit,
            settlement_price=settlement_price,
            contracts=contracts,
        )
        record = OutcomeRecord(
            record_id=content_hash(
                {
                    "session": session_date,
                    "candidate": candidate.candidate_id,
                    "position": position_id,
                    "settle": str(settlement_price),
                }
            ),
            account_id=account_id,
            candidate_id=candidate.candidate_id,
            position_id=position_id,
            snapshot_id=candidate.snapshot_id,
            session_date=session_date,
            settlement_price=Decimal(str(settlement_price)),
            realized_pnl=pnl,
            contracts=contracts,
            status=SettlementStatus.SETTLED,
            source=source,
            settled_at=now,
            entry_credit=entry_credit,
            was_traded=True,
        )
        outcomes.append(record)
        ev = store.append(
            JournalEvent(
                event_type=JournalEventType.OUTCOME_SETTLED.value,
                aggregate_type=AggregateType.POSITION.value,
                aggregate_id=position_id or candidate.candidate_id,
                occurred_at=now,
                payload={
                    "record_id": record.record_id,
                    "candidate_id": record.candidate_id,
                    "realized_pnl": str(record.realized_pnl),
                    "settlement_price": str(record.settlement_price),
                    "session_date": session_date,
                    "was_traded": True,
                },
                deployment_id=deployment_id,
                snapshot_id=candidate.snapshot_id,
            )
        )
        events.append(ev)

    for candidate, entry_credit, reason, policy_id in blocked:
        pnl = settle_candidate(
            legs=candidate.legs,
            entry_credit=entry_credit,
            settlement_price=settlement_price,
            contracts=1,
        )
        cf = CounterfactualOutcome(
            record_id=content_hash(
                {
                    "session": session_date,
                    "candidate": candidate.candidate_id,
                    "cf": reason,
                    "settle": str(settlement_price),
                }
            ),
            candidate_id=candidate.candidate_id,
            snapshot_id=candidate.snapshot_id,
            session_date=session_date,
            settlement_price=Decimal(str(settlement_price)),
            realized_pnl=pnl,
            reason_not_taken=reason,
            policy_id=policy_id,
            would_have_filled=True,
            settled_at=now,
        )
        counterfactuals.append(cf)
        ev = store.append(
            JournalEvent(
                event_type=JournalEventType.COUNTERFACTUAL_SETTLED.value,
                aggregate_type=AggregateType.CANDIDATE.value,
                aggregate_id=candidate.candidate_id,
                occurred_at=now,
                payload={
                    "record_id": cf.record_id,
                    "candidate_id": cf.candidate_id,
                    "realized_pnl": str(cf.realized_pnl),
                    "reason_not_taken": reason,
                    "policy_id": policy_id,
                    "session_date": session_date,
                },
                deployment_id=deployment_id,
                snapshot_id=candidate.snapshot_id,
            )
        )
        events.append(ev)

    return SettlementBatch(
        outcomes=tuple(outcomes),
        counterfactuals=tuple(counterfactuals),
        events=tuple(events),
    )
