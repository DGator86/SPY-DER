"""Append-only event journal, hash chain, projections, reconstruction."""

from spy_der.journal.hash_chain import compute_event_hash, verify_chain
from spy_der.journal.projections import JournalProjections, project_events
from spy_der.journal.reconstruction import ReconstructionResult, reconstruct_from_events
from spy_der.journal.store import InMemoryJournalStore, SqliteJournalStore

__all__ = [
    "InMemoryJournalStore",
    "JournalProjections",
    "ReconstructionResult",
    "SqliteJournalStore",
    "compute_event_hash",
    "project_events",
    "reconstruct_from_events",
    "verify_chain",
]
