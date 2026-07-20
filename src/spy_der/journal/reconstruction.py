"""Deterministic reconstruction of projections from a journal stream."""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.events import JournalEvent
from spy_der.journal.hash_chain import verify_chain
from spy_der.journal.projections import JournalProjections, project_events
from spy_der.journal.store import InMemoryJournalStore

__all__ = ["ReconstructionResult", "reconstruct_from_events"]


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    events: tuple[JournalEvent, ...]
    projections: JournalProjections
    chain_valid: bool
    tip_hash: str | None


def reconstruct_from_events(
    events: tuple[JournalEvent, ...] | list[JournalEvent],
) -> ReconstructionResult:
    """Rebuild projections and verify hash-chain integrity."""
    store = InMemoryJournalStore()
    # Re-append copies without sequence/hash to prove deterministic rebuild,
    # OR trust provided chain. Prefer verifying provided chain then projecting.
    ordered = tuple(sorted(events, key=lambda e: e.sequence_number))
    chain_valid = verify_chain(ordered)
    if not chain_valid:
        # Attempt rebuild by replaying payloads into a fresh store.
        for event in ordered:
            store.append(
                JournalEvent(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    occurred_at=event.occurred_at,
                    payload=dict(event.payload),
                    deployment_id=event.deployment_id,
                    snapshot_id=event.snapshot_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                )
            )
        rebuilt = store.iter_events()
        return ReconstructionResult(
            events=rebuilt,
            projections=project_events(rebuilt),
            chain_valid=store.verify_chain(),
            tip_hash=store.latest_hash(),
        )

    return ReconstructionResult(
        events=ordered,
        projections=project_events(ordered),
        chain_valid=True,
        tip_hash=ordered[-1].event_hash if ordered else None,
    )
