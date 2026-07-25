"""Stage artifacts on disk, in the envelope the market recorder already uses.

`deploy/config.yaml.example` declares `forecasts/`, `candidates/`, `decisions/`,
`positions/` and `settlements/` under the state root. Nothing wrote them, so the
question when implementing `spy-der engine` and `spy-der settlement` was what an
on-disk stage record should look like.

It should look like the one that already exists. `spy_der.market_data.recording`
writes JSONL records carrying `seq`, an identity, a schema version and a
`record_hash` over the payload, and `spy_der.market_data.replay.ReplayFeed`
verifies exactly that shape — content hashes, sequence continuity, schema
uniformity — and fails closed on corruption. Reusing it means stage artifacts
are integrity-checked by code that is already tested, and `spy-der validate`'s
`recorded_session_replay` gate can be pointed at them unchanged.

The journal is a different thing and keeps its own store: `SqliteJournalStore`
is append-only and hash-chained, and it is the system of record for *what
happened*. These files are the bulk artifacts a stage produced.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from spy_der.contracts.common import content_hash, to_canonical_json

__all__ = ["StageArtifactStore", "build_artifact_record"]


def build_artifact_record(
    seq: int,
    *,
    artifact_id: str,
    schema_version: str,
    payload: Any,
) -> dict[str, Any]:
    """One integrity-checkable stage record, shaped like a market recording."""
    canonical: Any = json.loads(to_canonical_json(payload))
    return {
        "seq": seq,
        "snapshot_id": artifact_id,
        "schema_version": schema_version,
        "record_hash": content_hash(canonical),
        "snapshot": canonical,
    }


class StageArtifactStore:
    """Append stage artifacts to `<state_root>/<stage>/<session>.jsonl`.

    Sequence numbers restart per session file, matching the market recorder, so
    `ReplayFeed` can verify a session in isolation.
    """

    def __init__(self, state_root: str | Path, stage: str) -> None:
        self.directory = Path(state_root) / stage
        self.stage = stage
        self._seq: dict[str, int] = {}

    def path_for(self, session: date | str) -> Path:
        session_str = session.isoformat() if isinstance(session, date) else str(session)
        return self.directory / f"{session_str}.jsonl"

    def _next_seq(self, path: Path) -> int:
        """Continue the file's existing sequence so restarts do not create gaps."""
        key = str(path)
        if key not in self._seq:
            existing = -1
            if path.is_file():
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            existing = int(json.loads(stripped)["seq"])
                        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                            # A damaged tail must not silently restart the
                            # sequence at 0 and mask the gap from ReplayFeed.
                            continue
            self._seq[key] = existing + 1
        seq = self._seq[key]
        self._seq[key] = seq + 1
        return seq

    def append(
        self,
        session: date | str,
        *,
        artifact_id: str,
        schema_version: str,
        payload: Any,
    ) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(session)
        record = build_artifact_record(
            self._next_seq(path),
            artifact_id=artifact_id,
            schema_version=schema_version,
            payload=payload,
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        return record

    def read(self, session: date | str) -> Iterator[dict[str, Any]]:
        """Yield verified payloads for a session, or nothing when absent."""
        path = self.path_for(session)
        if not path.is_file():
            return
        # Imported here so the artifact layer does not pull market_data in at
        # module import time.
        from spy_der.market_data.replay import ReplayFeed

        yield from ReplayFeed.from_file(path).replay()

    def sessions(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.jsonl"))
