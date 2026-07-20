"""Deterministic snapshot recording (master spec §15, §63 Phase 2).

Migrated intent from System A ``chain_store.ChainRecorder``: append each
canonical snapshot as a self-describing JSONL record carrying a stable
per-session sequence and a content hash, so replay can detect corruption and
reproduce identity without a network (spec §15). Serialization is deterministic
(canonical JSON), so identical snapshots always yield identical bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spy_der.contracts.common import content_hash, to_canonical_json
from spy_der.contracts.market import CanonicalMarketSnapshot

__all__ = ["SnapshotRecorder", "build_record"]


def build_record(seq: int, snapshot: CanonicalMarketSnapshot) -> dict[str, Any]:
    """Build one integrity-checkable recording record for ``snapshot``."""
    canonical: Any = json.loads(to_canonical_json(snapshot))
    return {
        "seq": seq,
        "snapshot_id": snapshot.snapshot_id,
        "schema_version": snapshot.schema_version,
        "record_hash": content_hash(canonical),
        "snapshot": canonical,
    }


class SnapshotRecorder:
    """Accumulate canonical snapshots into a deterministic JSONL recording."""

    def __init__(self) -> None:
        self._seq = 0
        self._records: list[dict[str, Any]] = []

    def record(self, snapshot: CanonicalMarketSnapshot) -> dict[str, Any]:
        record = build_record(self._seq, snapshot)
        self._records.append(record)
        self._seq += 1
        return record

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._records)

    def to_jsonl(self) -> str:
        lines = [
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            for record in self._records
        ]
        return "".join(f"{line}\n" for line in lines)

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_jsonl(), encoding="utf-8")
