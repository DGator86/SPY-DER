"""Phase 1 snapshot parity fixtures (master spec §63 Phase 1, §65).

These are *determinism / golden-file* parity fixtures: a frozen System A
canonical-snapshot input (`baseline/fixtures/phase1/`) is run through the
System A adapter + assembler and must reproduce a frozen canonical output
(`baseline/expected_outputs/phase1/`), bit-for-bit, on every host and run.

Full field-level behavioral parity against a *running* System A is deferred
until recorded ticks are captured (spec §63 Phase 2 recording/replay); this
suite locks the ingestion contract so later phases cannot silently drift it.
"""

from __future__ import annotations

import json
from pathlib import Path

from spy_der.contracts import to_canonical_json
from spy_der.market_data import SystemASnapshotAdapter

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "baseline" / "fixtures" / "phase1" / "system_a_snapshot.json"
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase1" / "canonical_snapshot.json"

# Frozen identity of the golden snapshot. If ingestion changes intentionally,
# regenerate the expected output and update these deliberately (spec §65).
_EXPECTED_SNAPSHOT_ID = "snap-d6063a90d084"
_EXPECTED_CONTENT_HASH = "sha256:4645b126fd6f2f718bab98197f1766aa4302b7f8f04b1375d3196e54c69d8c43"


def _adapt() -> object:
    legacy = json.loads(_FIXTURE.read_text())
    return SystemASnapshotAdapter().adapt(legacy)


def test_adapter_reproduces_golden_output() -> None:
    snap = _adapt()
    produced = json.loads(to_canonical_json(snap))
    expected = json.loads(_EXPECTED.read_text())
    assert produced == expected


def test_snapshot_identity_is_frozen() -> None:
    snap = _adapt()
    assert snap.snapshot_id == _EXPECTED_SNAPSHOT_ID  # type: ignore[attr-defined]
    assert snap.content_hash == _EXPECTED_CONTENT_HASH  # type: ignore[attr-defined]


def test_adapter_is_deterministic_across_runs() -> None:
    first = to_canonical_json(_adapt())
    second = to_canonical_json(_adapt())
    assert first == second
