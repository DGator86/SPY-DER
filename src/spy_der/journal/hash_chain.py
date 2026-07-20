"""Per-event hash-chain helpers for the append-only journal."""

from __future__ import annotations

from spy_der.contracts.events import JournalEvent, event_content_hash

__all__ = ["compute_event_hash", "verify_chain"]


def compute_event_hash(event: JournalEvent) -> str:
    if event.occurred_at is None or event.recorded_at is None:
        raise ValueError("event timestamps required for hash")
    return event_content_hash(
        event_id=event.event_id,
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        sequence_number=event.sequence_number,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        schema_version=event.schema_version,
        payload_hash=event.payload_hash,
        previous_event_hash=event.previous_event_hash,
        deployment_id=event.deployment_id,
        snapshot_id=event.snapshot_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
    )


def verify_chain(events: tuple[JournalEvent, ...] | list[JournalEvent]) -> bool:
    prev_hash: str | None = None
    expected_seq = 1
    for event in events:
        if event.sequence_number != expected_seq:
            return False
        if event.previous_event_hash != prev_hash:
            return False
        digest = compute_event_hash(event)
        if event.event_hash and event.event_hash != digest:
            return False
        prev_hash = digest
        expected_seq += 1
    return True
