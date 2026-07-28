"""Append-only in-memory and SQLite WAL journal stores."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from spy_der.contracts.common import content_hash
from spy_der.contracts.events import JournalEvent
from spy_der.journal.hash_chain import compute_event_hash, verify_chain

__all__ = ["AppendOnlyJournal", "InMemoryJournalStore", "SqliteJournalStore"]


class AppendOnlyJournal(Protocol):
    """What a writer needs from a journal store.

    Narrower than `interfaces.JournalStore`, which returns `None`: callers that
    chain events need the finalized event back, with its sequence number and
    hash filled in. Both stores below satisfy this, so a writer can take either
    without naming a concrete class.
    """

    def append(self, event: JournalEvent) -> JournalEvent: ...


def _finalize(
    event: JournalEvent,
    *,
    seq: int,
    previous_hash: str | None,
) -> JournalEvent:
    """Stamp sequence + hash-chain fields onto ``event`` for persistence."""
    now = datetime.now(tz=UTC)
    occurred = event.occurred_at or event.timestamp or now
    recorded = event.recorded_at or now
    event_id = event.event_id or content_hash(
        {
            "seq": seq,
            "type": event.event_type,
            "aggregate": event.aggregate_id,
            "occurred": occurred.isoformat(),
        }
    )
    staged = JournalEvent(
        event_id=event_id,
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        sequence_number=seq,
        occurred_at=occurred,
        recorded_at=recorded,
        schema_version=event.schema_version,
        payload=dict(event.payload),
        payload_hash=event.payload_hash,
        previous_event_hash=previous_hash,
        event_hash="",
        deployment_id=event.deployment_id,
        snapshot_id=event.snapshot_id,
        correlation_id=event.correlation_id or event_id,
        causation_id=event.causation_id,
    )
    return replace(staged, event_hash=compute_event_hash(staged))


@dataclass
class InMemoryJournalStore:
    """Fail-closed append-only journal (primary implementation for tests)."""

    _events: list[JournalEvent] = field(default_factory=list)

    def append(self, event: JournalEvent) -> JournalEvent:
        seq = len(self._events) + 1
        prev = self._events[-1].event_hash if self._events else None
        finalized = _finalize(event, seq=seq, previous_hash=prev)
        self._events.append(finalized)
        return finalized

    def iter_events(
        self,
        *,
        aggregate_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[JournalEvent, ...]:
        out = self._events
        if aggregate_id is not None:
            out = [e for e in out if e.aggregate_id == aggregate_id]
        if event_type is not None:
            out = [e for e in out if e.event_type == event_type]
        return tuple(out)

    def latest_hash(self) -> str | None:
        if not self._events:
            return None
        return self._events[-1].event_hash

    def verify_chain(self) -> bool:
        return verify_chain(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __bool__(self) -> bool:
        # Prevent empty stores from being falsy (len==0) in `x or default`.
        return True


@dataclass
class SqliteJournalStore:
    """SQLite WAL-backed append-only journal behind the same interface.

    Engine and settlement are separate processes that both append. Sequence
    assignment therefore happens under ``BEGIN IMMEDIATE`` so two writers cannot
    both read the same tip and collide on ``sequence_number``. The tip is read
    with ``ORDER BY … DESC LIMIT 1`` — never by loading the whole journal into
    memory on every append.
    """

    path: str | Path
    _conn: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # check_same_thread=False: the runtime services are single-threaded, but
        # tests exercise concurrent connections from worker threads against one
        # file. Cross-process writers each open their own connection.
        self._conn = sqlite3.connect(str(self.path), timeout=60.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=60000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_events (
                sequence_number INTEGER PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                previous_event_hash TEXT,
                event_hash TEXT NOT NULL,
                deployment_id TEXT NOT NULL,
                snapshot_id TEXT,
                correlation_id TEXT NOT NULL,
                causation_id TEXT
            )
            """
        )
        self._conn.commit()

    def _tip(self) -> tuple[int, str | None]:
        """``(next_sequence, previous_event_hash)`` at the current tip."""
        row = self._conn.execute(
            "SELECT sequence_number, event_hash FROM journal_events "
            "ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return 1, None
        return int(row["sequence_number"]) + 1, str(row["event_hash"])

    def append(self, event: JournalEvent) -> JournalEvent:
        # IMMEDIATE takes the reserved lock before we read the tip, so a second
        # writer blocks here instead of both computing the same next sequence.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            seq, previous_hash = self._tip()
            finalized = _finalize(event, seq=seq, previous_hash=previous_hash)
            self._conn.execute(
                """
                INSERT INTO journal_events (
                    sequence_number, event_id, event_type, aggregate_type, aggregate_id,
                    occurred_at, recorded_at, schema_version, payload_json, payload_hash,
                    previous_event_hash, event_hash, deployment_id, snapshot_id,
                    correlation_id, causation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finalized.sequence_number,
                    finalized.event_id,
                    finalized.event_type,
                    finalized.aggregate_type,
                    finalized.aggregate_id,
                    finalized.occurred_at.isoformat() if finalized.occurred_at else "",
                    finalized.recorded_at.isoformat() if finalized.recorded_at else "",
                    finalized.schema_version,
                    finalized.payload_json,
                    finalized.payload_hash,
                    finalized.previous_event_hash,
                    finalized.event_hash,
                    finalized.deployment_id,
                    finalized.snapshot_id,
                    finalized.correlation_id,
                    finalized.causation_id,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return finalized

    def iter_events(
        self,
        *,
        aggregate_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[JournalEvent, ...]:
        sql = "SELECT * FROM journal_events"
        clauses: list[str] = []
        params: list[Any] = []
        if aggregate_id is not None:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sequence_number ASC"
        rows = self._conn.execute(sql, params).fetchall()
        events: list[JournalEvent] = []
        for row in rows:
            events.append(
                JournalEvent(
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    aggregate_type=row["aggregate_type"],
                    aggregate_id=row["aggregate_id"],
                    sequence_number=row["sequence_number"],
                    occurred_at=datetime.fromisoformat(row["occurred_at"]),
                    recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    schema_version=row["schema_version"],
                    payload_json=row["payload_json"],
                    payload_hash=row["payload_hash"],
                    previous_event_hash=row["previous_event_hash"],
                    event_hash=row["event_hash"],
                    deployment_id=row["deployment_id"],
                    snapshot_id=row["snapshot_id"],
                    correlation_id=row["correlation_id"],
                    causation_id=row["causation_id"],
                )
            )
        return tuple(events)

    def latest_hash(self) -> str | None:
        row = self._conn.execute(
            "SELECT event_hash FROM journal_events ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()
        return None if row is None else str(row["event_hash"])

    def verify_chain(self) -> bool:
        return verify_chain(self.iter_events())
