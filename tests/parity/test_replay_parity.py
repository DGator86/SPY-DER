"""Phase 2 deterministic replay fixture (master spec §15, §63 Phase 2).

A frozen 3-tick recording (`baseline/fixtures/phase2/recording.jsonl`) must
replay bit-for-bit, verify its integrity, and reproduce a frozen sequence of
snapshot ids on every host and run — with no network. This locks the
record/replay contract so later phases cannot silently drift it (spec §15).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spy_der.contracts.common import ErrorCode
from spy_der.market_data import CorruptRecordingError, ReplayFeed

_ROOT = Path(__file__).resolve().parents[2]
_RECORDING = _ROOT / "baseline" / "fixtures" / "phase2" / "recording.jsonl"

_EXPECTED_IDS = (
    "snap-da77579da329",
    "snap-7106e3939af5",
    "snap-179ccd03c783",
)


def test_recording_replays_and_verifies() -> None:
    feed = ReplayFeed.from_file(_RECORDING, expected_schema="1.0.0")
    assert len(feed) == 3
    assert feed.snapshot_ids() == _EXPECTED_IDS


def test_replay_output_is_frozen() -> None:
    feed = ReplayFeed.from_file(_RECORDING)
    snapshots = list(feed.replay())
    prices = [s["underlying_price"] for s in snapshots]
    assert prices == ["500.10", "500.25", "500.05"]


def test_tampered_recording_fails_closed() -> None:
    original = _RECORDING.read_text()
    tampered = original.replace("500.10", "501.10", 1)
    assert tampered != original
    with pytest.raises(CorruptRecordingError) as exc:
        ReplayFeed.from_jsonl(tampered)
    assert exc.value.code is ErrorCode.RECORD_HASH_MISMATCH
