"""Tests for explicit SEQUENCE_GAP repair on market recordings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spy_der.contracts.common import content_hash
from spy_der.market_data.repair import (
    RepairError,
    inspect_recording,
    repair_recording,
    repair_state_root,
)
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed
from spy_der.runtime.repair_recording import main as repair_main


def _record(seq: int, price: str = "500.00") -> dict[str, object]:
    snapshot = {
        "snapshot_id": f"snap-{seq}-{price}",
        "schema_version": "1.0.0",
        "underlying_price": price,
    }
    return {
        "seq": seq,
        "snapshot_id": snapshot["snapshot_id"],
        "schema_version": "1.0.0",
        "record_hash": content_hash(snapshot),
        "snapshot": snapshot,
    }


def _write(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def test_inspect_reports_clean_recording(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-27.jsonl"
    _write(path, [_record(0), _record(1), _record(2)])
    report = inspect_recording(path)
    assert report.status == "clean"
    assert report.records == 3


def test_inspect_reports_the_restart_gap(tmp_path: Path) -> None:
    """The live failure: seq restarts at 0 after a mid-session process restart."""
    path = tmp_path / "2026-07-27.jsonl"
    _write(path, [_record(0, "1"), _record(1, "2"), _record(0, "3"), _record(1, "4")])
    report = inspect_recording(path)
    assert report.status == "sequence_gap"
    assert report.sequence_breaks == 1
    assert "seq 0 does not follow 1" in report.detail


def test_repair_renumbers_and_passes_replay(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-27.jsonl"
    # Mirrors production: 577 records of history then a counter restart at 0.
    first = [_record(i, f"a{i}") for i in range(3)]
    second = [_record(i, f"b{i}") for i in range(2)]
    _write(path, first + second)

    with pytest.raises(CorruptRecordingError):
        ReplayFeed.from_file(path)

    report = repair_recording(path)
    assert report.status == "repaired"
    assert report.rewritten is True
    assert report.backup_path is not None
    assert Path(report.backup_path).is_file()

    feed = ReplayFeed.from_file(path)
    assert len(feed) == 5
    seqs = [json.loads(line)["seq"] for line in path.read_text().splitlines()]
    assert seqs == [0, 1, 2, 3, 4]
    # Snapshots preserved in file order — both segments kept.
    snaps = list(feed.replay())
    assert snaps[2]["underlying_price"] == "a2"
    assert snaps[3]["underlying_price"] == "b0"


def test_repair_refuses_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    records = [_record(0), _record(1)]
    records[1] = dict(records[1])
    snapshot = dict(records[1]["snapshot"])  # type: ignore[arg-type]
    snapshot["underlying_price"] = "999"
    records[1]["snapshot"] = snapshot
    _write(path, records)

    report = inspect_recording(path)
    assert report.status == "unrepairable"
    assert "hash mismatch" in report.detail
    with pytest.raises(RepairError):
        repair_recording(path)
    # Original untouched.
    assert "999" in path.read_text()


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-27.jsonl"
    original = [_record(0), _record(0)]
    _write(path, original)
    before = path.read_text()
    report = repair_recording(path, dry_run=True)
    assert report.status == "sequence_gap"
    assert report.rewritten is False
    assert path.read_text() == before
    assert not list(tmp_path.glob("*.bak*"))


def test_repair_drops_truncated_tail_then_renumbers(tmp_path: Path) -> None:
    path = tmp_path / "partial.jsonl"
    _write(path, [_record(0), _record(0)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 1, "snapshot_id": "trunc')
    report = repair_recording(path)
    assert report.status == "repaired"
    assert report.skipped_unparseable == 1
    assert len(ReplayFeed.from_file(path)) == 2


def test_repair_state_root_targets_one_session(tmp_path: Path) -> None:
    market = tmp_path / "market"
    clean = market / "2026-07-26.jsonl"
    broken = market / "2026-07-27.jsonl"
    _write(clean, [_record(0), _record(1)])
    _write(broken, [_record(0), _record(0)])

    reports = repair_state_root(tmp_path, sessions=["2026-07-27"])
    assert len(reports) == 1
    assert reports[0].status == "repaired"
    assert inspect_recording(clean).status == "clean"


def test_cli_repairs_and_exits_zero(tmp_path: Path) -> None:
    market = tmp_path / "market"
    _write(market / "2026-07-27.jsonl", [_record(0), _record(0)])
    assert (
        repair_main(
            ["--state-root", str(tmp_path), "--session", "2026-07-27"]
        )
        == 0
    )
    assert inspect_recording(market / "2026-07-27.jsonl").status == "clean"


def test_cli_dry_run_exits_zero_without_writing(tmp_path: Path) -> None:
    market = tmp_path / "market"
    path = market / "2026-07-27.jsonl"
    _write(path, [_record(0), _record(0)])
    before = path.read_text()
    assert (
        repair_main(
            ["--state-root", str(tmp_path), "--session", "2026-07-27", "--dry-run"]
        )
        == 0
    )
    assert path.read_text() == before


def test_cli_refuses_unrepairable(tmp_path: Path) -> None:
    market = tmp_path / "market"
    records = [_record(0)]
    records[0] = dict(records[0])
    records[0]["record_hash"] = "sha256:dead"
    _write(market / "2026-07-27.jsonl", records)
    assert (
        repair_main(
            ["--state-root", str(tmp_path), "--session", "2026-07-27"]
        )
        == 2
    )
