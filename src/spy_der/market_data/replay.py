"""Deterministic replay with corruption detection (master spec §15, §63 Phase 2).

Reads recordings produced by :mod:`spy_der.market_data.recording` and replays
them with no network, in time/sequence order, failing closed on corruption:
malformed records, content-hash mismatches, sequence gaps, and schema mismatches
(spec §15). Replay is independent of wall-clock speed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from spy_der.contracts.common import ContractError, ErrorCode, content_hash

__all__ = ["CorruptRecordingError", "ReplayFeed"]

_REQUIRED_KEYS = ("seq", "snapshot_id", "schema_version", "record_hash", "snapshot")


class CorruptRecordingError(ContractError):
    """A recording failed an integrity check (spec §15 fail-closed)."""


def _verify(records: list[dict[str, Any]], expected_schema: str | None) -> None:
    previous_seq: int | None = None
    for index, record in enumerate(records):
        for key in _REQUIRED_KEYS:
            if key not in record:
                raise CorruptRecordingError(
                    ErrorCode.MALFORMED_RECORD,
                    f"record {index} is missing '{key}'",
                )
        recomputed = content_hash(record["snapshot"])
        if recomputed != record["record_hash"]:
            raise CorruptRecordingError(
                ErrorCode.RECORD_HASH_MISMATCH,
                f"record {index} (seq={record['seq']}) hash mismatch",
            )
        seq = record["seq"]
        if previous_seq is not None and seq != previous_seq + 1:
            raise CorruptRecordingError(
                ErrorCode.SEQUENCE_GAP,
                f"record {index}: seq {seq} does not follow {previous_seq}",
            )
        if expected_schema is not None and record["schema_version"] != expected_schema:
            raise CorruptRecordingError(
                ErrorCode.SCHEMA_MISMATCH,
                f"record {index}: schema {record['schema_version']!r} != {expected_schema!r}",
            )
        previous_seq = seq


class ReplayFeed:
    """Replay a verified recording deterministically."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        *,
        expected_schema: str | None = None,
    ) -> None:
        _verify(records, expected_schema)
        self._records = records

    @classmethod
    def from_jsonl(cls, text: str, *, expected_schema: str | None = None) -> ReplayFeed:
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise CorruptRecordingError(
                    ErrorCode.MALFORMED_RECORD,
                    f"line {line_no} is not valid JSON",
                ) from exc
        return cls(records, expected_schema=expected_schema)

    @classmethod
    def from_file(cls, path: str | Path, *, expected_schema: str | None = None) -> ReplayFeed:
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_jsonl(text, expected_schema=expected_schema)

    def __len__(self) -> int:
        return len(self._records)

    def snapshot_ids(self) -> tuple[str, ...]:
        return tuple(record["snapshot_id"] for record in self._records)

    def replay(self) -> Iterator[dict[str, Any]]:
        """Yield each recorded canonical snapshot in sequence order."""
        for record in self._records:
            yield record["snapshot"]
