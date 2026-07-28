"""Explicit repair for market recordings with sequence gaps (spec §15).

Replay fails closed on ``SEQUENCE_GAP``. That is correct for training and Dojo:
a broken sequence must not silently train. But the gap is often recoverable —
``spy-der-market`` used to restart ``seq`` at 0 after ``Restart=always``, so one
session file holds two contiguous segments (``…, 576, 0, 1, …``). Content hashes
still match; only the envelope sequence is wrong.

This module renumbers ``seq`` to ``0..n-1`` in file order after verifying every
kept record's content hash. It never auto-runs: an operator must invoke
``spy-der-repair-recording``. Hash mismatches and missing keys still refuse —
those are not sequence problems.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spy_der.contracts.common import content_hash
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed

__all__ = [
    "RepairError",
    "RepairReport",
    "inspect_recording",
    "repair_recording",
    "repair_state_root",
]

log = logging.getLogger("spy_der.market_data.repair")

_REQUIRED_KEYS = ("seq", "snapshot_id", "schema_version", "record_hash", "snapshot")


class RepairError(RuntimeError):
    """A recording cannot be repaired safely."""


@dataclass(frozen=True, slots=True)
class RepairReport:
    """Outcome of inspecting or repairing one recording."""

    path: str
    status: str
    records: int = 0
    sequence_breaks: int = 0
    skipped_unparseable: int = 0
    rewritten: bool = False
    backup_path: str | None = None
    detail: str = ""

    def describe(self) -> str:
        bits = [f"{Path(self.path).name}: {self.status}"]
        if self.records:
            bits.append(f"{self.records} record(s)")
        if self.sequence_breaks:
            bits.append(f"{self.sequence_breaks} sequence break(s)")
        if self.skipped_unparseable:
            bits.append(f"dropped {self.skipped_unparseable} unparseable line(s)")
        if self.rewritten:
            bits.append("rewritten")
        if self.backup_path:
            bits.append(f"backup={self.backup_path}")
        if self.detail:
            bits.append(self.detail)
        return "; ".join(bits)


@dataclass
class _LoadedRecording:
    records: list[dict[str, Any]] = field(default_factory=list)
    sequence_breaks: list[str] = field(default_factory=list)
    skipped_unparseable: int = 0
    hard_errors: list[str] = field(default_factory=list)


def _load_for_repair(path: Path) -> _LoadedRecording:
    """Parse a recording, collecting repairable gaps vs hard corruption."""
    loaded = _LoadedRecording()
    text = path.read_text(encoding="utf-8")
    previous_seq: int | None = None
    for line_no, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            # Truncated tails are common after a crash mid-write; drop them the
            # same way resume does rather than refusing the whole session.
            loaded.skipped_unparseable += 1
            continue
        if not isinstance(record, dict):
            loaded.hard_errors.append(f"line {line_no}: record is not an object")
            continue
        missing = [key for key in _REQUIRED_KEYS if key not in record]
        if missing:
            loaded.hard_errors.append(
                f"line {line_no}: missing {', '.join(repr(k) for k in missing)}"
            )
            continue
        try:
            recomputed = content_hash(record["snapshot"])
        except (TypeError, ValueError) as exc:
            loaded.hard_errors.append(f"line {line_no}: snapshot not hashable: {exc}")
            continue
        if recomputed != record["record_hash"]:
            loaded.hard_errors.append(
                f"line {line_no} (seq={record.get('seq')}): record hash mismatch"
            )
            continue
        try:
            seq = int(record["seq"])
        except (TypeError, ValueError):
            loaded.hard_errors.append(f"line {line_no}: seq is not an integer")
            continue
        if previous_seq is not None and seq != previous_seq + 1:
            loaded.sequence_breaks.append(
                f"record {len(loaded.records)}: seq {seq} does not follow {previous_seq}"
            )
        previous_seq = seq
        loaded.records.append(record)
    return loaded


def inspect_recording(path: str | Path) -> RepairReport:
    """Classify a recording without writing."""
    target = Path(path)
    if not target.is_file():
        return RepairReport(path=str(target), status="missing", detail="file not found")
    try:
        loaded = _load_for_repair(target)
    except OSError as exc:
        return RepairReport(path=str(target), status="unreadable", detail=str(exc))

    if loaded.hard_errors:
        return RepairReport(
            path=str(target),
            status="unrepairable",
            records=len(loaded.records),
            sequence_breaks=len(loaded.sequence_breaks),
            skipped_unparseable=loaded.skipped_unparseable,
            detail=loaded.hard_errors[0],
        )
    if not loaded.records:
        return RepairReport(
            path=str(target),
            status="empty",
            skipped_unparseable=loaded.skipped_unparseable,
            detail="no usable records",
        )
    if loaded.sequence_breaks:
        return RepairReport(
            path=str(target),
            status="sequence_gap",
            records=len(loaded.records),
            sequence_breaks=len(loaded.sequence_breaks),
            skipped_unparseable=loaded.skipped_unparseable,
            detail=loaded.sequence_breaks[0],
        )
    # Confirm the same reader training uses accepts the file.
    try:
        ReplayFeed(loaded.records)
    except CorruptRecordingError as exc:
        return RepairReport(
            path=str(target),
            status="unrepairable",
            records=len(loaded.records),
            skipped_unparseable=loaded.skipped_unparseable,
            detail=str(exc),
        )
    return RepairReport(
        path=str(target),
        status="clean",
        records=len(loaded.records),
        skipped_unparseable=loaded.skipped_unparseable,
    )


def _renumber(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seq, record in enumerate(records):
        rewritten = dict(record)
        rewritten["seq"] = seq
        out.append(rewritten)
    return out


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    staging = path.with_suffix(path.suffix + ".partial")
    with staging.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            )
            handle.write("\n")
    staging.replace(path)


def repair_recording(
    path: str | Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> RepairReport:
    """Renumber ``seq`` when that is the only integrity failure.

    Returns a report. Raises :class:`RepairError` only when the caller asked to
    write and the file is not a recoverable sequence gap.
    """
    target = Path(path)
    report = inspect_recording(target)
    if report.status == "clean":
        return report
    if report.status != "sequence_gap":
        if dry_run:
            return report
        raise RepairError(report.describe())

    loaded = _load_for_repair(target)
    rewritten = _renumber(loaded.records)
    # Prove the rewrite would pass the same fail-closed reader before touching disk.
    try:
        ReplayFeed(rewritten)
    except CorruptRecordingError as exc:
        raise RepairError(
            f"{target.name}: renumbered recording still fails integrity: {exc}"
        ) from exc

    if dry_run:
        return RepairReport(
            path=str(target),
            status="sequence_gap",
            records=len(rewritten),
            sequence_breaks=len(loaded.sequence_breaks),
            skipped_unparseable=loaded.skipped_unparseable,
            detail=f"dry-run; would renumber ({loaded.sequence_breaks[0]})",
        )

    backup_path: str | None = None
    if backup:
        bak = target.with_suffix(target.suffix + ".bak")
        # Never clobber an earlier backup of a different generation.
        if bak.exists():
            n = 1
            while True:
                candidate = target.with_suffix(f"{target.suffix}.bak.{n}")
                if not candidate.exists():
                    bak = candidate
                    break
                n += 1
        bak.write_bytes(target.read_bytes())
        backup_path = str(bak)

    _write_jsonl(target, rewritten)
    log.info(
        "repaired %s: renumbered %d record(s) across %d sequence break(s)",
        target.name,
        len(rewritten),
        len(loaded.sequence_breaks),
    )
    return RepairReport(
        path=str(target),
        status="repaired",
        records=len(rewritten),
        sequence_breaks=len(loaded.sequence_breaks),
        skipped_unparseable=loaded.skipped_unparseable,
        rewritten=True,
        backup_path=backup_path,
        detail=loaded.sequence_breaks[0],
    )


def repair_state_root(
    state_root: str | Path,
    *,
    sessions: list[str] | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> list[RepairReport]:
    """Inspect/repair ``market/*.jsonl`` under ``state_root``."""
    market = Path(state_root) / "market"
    if not market.is_dir():
        raise RepairError(f"market directory {market} does not exist")

    wanted = set(sessions) if sessions else None
    reports: list[RepairReport] = []
    for path in sorted(market.glob("*.jsonl")):
        if wanted is not None and path.stem not in wanted:
            continue
        report = inspect_recording(path)
        if report.status == "sequence_gap":
            report = repair_recording(path, dry_run=dry_run, backup=backup)
        reports.append(report)
    return reports
