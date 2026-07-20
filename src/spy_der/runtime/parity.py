"""Dual-runtime parity between live-shadow and replay (spec §63 Phase 16)."""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from spy_der.contracts.common import content_hash
from spy_der.contracts.serialization import to_canonical_json
from spy_der.deployment.rollback import DeploymentPointer, rollback_deployment
from spy_der.replay.deterministic import ReplayInputManifest, ensure_matching_manifests

__all__ = [
    "DecisionDiff",
    "DualRuntimeParityReport",
    "ParityBucket",
    "ParityInputs",
    "PerformanceSample",
    "assert_identical_inputs",
    "compare_parity_buckets",
    "measure_runtime",
    "rehearse_rollback",
    "run_dual_runtime_parity",
]


@dataclass(frozen=True, slots=True)
class ParityInputs:
    """Shared identity for live-shadow and replay runs."""

    live_shadow_manifest: ReplayInputManifest
    replay_manifest: ReplayInputManifest
    snapshot_ids: tuple[str, ...]
    feature_bundle_hashes: tuple[str, ...]
    candidate_universe_hashes: tuple[str, ...]
    outcome_hashes: tuple[str, ...] = ()
    seed: str = "parity-16"

    @property
    def inputs_hash(self) -> str:
        return content_hash(
            {
                "live": to_canonical_json(self.live_shadow_manifest),
                "replay": to_canonical_json(self.replay_manifest),
                "snapshots": list(self.snapshot_ids),
                "features": list(self.feature_bundle_hashes),
                "candidates": list(self.candidate_universe_hashes),
                "outcomes": list(self.outcome_hashes),
                "seed": self.seed,
            }
        )


@dataclass(frozen=True, slots=True)
class ParityBucket:
    name: str
    live_shadow_hash: str
    replay_hash: str

    @property
    def matched(self) -> bool:
        return self.live_shadow_hash == self.replay_hash


@dataclass(frozen=True, slots=True)
class DecisionDiff:
    snapshot_id: str
    live_shadow_decision: str
    replay_decision: str

    @property
    def differs(self) -> bool:
        return self.live_shadow_decision != self.replay_decision


@dataclass(frozen=True, slots=True)
class PerformanceSample:
    label: str
    latency_ms: float
    peak_memory_kib: float


@dataclass(frozen=True, slots=True)
class DualRuntimeParityReport:
    inputs_hash: str
    buckets: tuple[ParityBucket, ...]
    decision_diffs: tuple[DecisionDiff, ...]
    performance: tuple[PerformanceSample, ...]
    rollback_rehearsal_ok: bool
    notes: tuple[str, ...] = ()

    @property
    def all_matched(self) -> bool:
        return all(b.matched for b in self.buckets) and not any(
            d.differs for d in self.decision_diffs
        )


def assert_identical_inputs(inputs: ParityInputs) -> None:
    """Fail closed unless live-shadow and replay manifests match exactly."""
    ensure_matching_manifests(inputs.live_shadow_manifest, inputs.replay_manifest)


def compare_parity_buckets(
    *,
    live_shadow: Mapping[str, str],
    replay: Mapping[str, str],
) -> tuple[ParityBucket, ...]:
    names = sorted(set(live_shadow) | set(replay))
    buckets: list[ParityBucket] = []
    for name in names:
        buckets.append(
            ParityBucket(
                name=name,
                live_shadow_hash=str(live_shadow.get(name, "")),
                replay_hash=str(replay.get(name, "")),
            )
        )
    return tuple(buckets)


def measure_runtime(label: str, fn: Callable[[], object]) -> tuple[object, PerformanceSample]:
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sample = PerformanceSample(
        label=label,
        latency_ms=elapsed_ms,
        peak_memory_kib=peak / 1024.0,
    )
    return result, sample


def rehearse_rollback(pointer: DeploymentPointer, *, reason: str = "parity_rehearsal") -> bool:
    """Exercise rollback without leaving the pointer unrestored when possible."""
    if not pointer.history:
        # Activate a temporary twin then roll back to prove the path.
        from dataclasses import replace

        twin = replace(
            pointer.current,
            deployment_id="",  # force new id
            notes=(*pointer.current.notes, "parity_twin"),
            git_commit=(pointer.current.git_commit or "parity") + "-twin",
        )
        before = pointer.current.deployment_id
        pointer.activate(twin)
        restored = rollback_deployment(pointer, reason=reason)
        return restored.deployment_id == before
    before = pointer.history[-1].deployment_id
    restored = rollback_deployment(pointer, reason=reason)
    return restored.deployment_id == before


def run_dual_runtime_parity(
    *,
    inputs: ParityInputs,
    live_shadow_hashes: Mapping[str, str],
    replay_hashes: Mapping[str, str],
    live_shadow_decisions: Sequence[tuple[str, str]] = (),
    replay_decisions: Sequence[tuple[str, str]] = (),
    pointer: DeploymentPointer | None = None,
    workload: Callable[[], object] | None = None,
) -> DualRuntimeParityReport:
    """Compare live-shadow vs replay parity buckets and optional perf/rollback."""
    assert_identical_inputs(inputs)
    buckets = compare_parity_buckets(live_shadow=live_shadow_hashes, replay=replay_hashes)

    live_map = dict(live_shadow_decisions)
    replay_map = dict(replay_decisions)
    diffs = tuple(
        DecisionDiff(
            snapshot_id=sid,
            live_shadow_decision=live_map.get(sid, ""),
            replay_decision=replay_map.get(sid, ""),
        )
        for sid in sorted(set(live_map) | set(replay_map))
    )

    perf: list[PerformanceSample] = []
    if workload is not None:
        _result, sample = measure_runtime("dual_runtime_workload", workload)
        perf.append(sample)

    rollback_ok = True
    notes: list[str] = []
    if pointer is not None:
        rollback_ok = rehearse_rollback(pointer)
        notes.append("rollback_rehearsal_executed")
    else:
        notes.append("rollback_rehearsal_skipped")

    return DualRuntimeParityReport(
        inputs_hash=inputs.inputs_hash,
        buckets=buckets,
        decision_diffs=diffs,
        performance=tuple(perf),
        rollback_rehearsal_ok=rollback_ok,
        notes=tuple(notes),
    )
