"""Phase 16 — dual-runtime parity, perf samples, rollback rehearsal."""

from __future__ import annotations

import pytest

from spy_der.deployment import DeploymentManifest, DeploymentMode, DeploymentPointer
from spy_der.replay.deterministic import ReplayInputManifest
from spy_der.runtime import (
    ParityInputs,
    assert_identical_inputs,
    run_dual_runtime_parity,
)


def _manifest(**overrides: str) -> ReplayInputManifest:
    base = dict(
        timestamp="2026-01-05T16:30:00+00:00",
        market_snapshot_hash="snap",
        underlying_bars_hash="bars",
        option_chain_hash="chain",
        candidate_universe_hash="cands",
        fees_hash="fees",
        slippage_hash="slip",
        fill_assumptions_hash="fill",
        account_size_hash="acct",
        risk_ceilings_hash="risk",
        exit_policy_hash="exit",
        settlement_hash="settle",
    )
    base.update(overrides)
    return ReplayInputManifest(**base)


def test_identical_inputs_required() -> None:
    a = _manifest()
    b = _manifest(market_snapshot_hash="DIFFERENT")
    inputs = ParityInputs(
        live_shadow_manifest=a,
        replay_manifest=b,
        snapshot_ids=("s1",),
        feature_bundle_hashes=("f1",),
        candidate_universe_hashes=("c1",),
    )
    with pytest.raises(ValueError):
        assert_identical_inputs(inputs)


def test_dual_runtime_parity_match_and_perf() -> None:
    m = _manifest()
    inputs = ParityInputs(
        live_shadow_manifest=m,
        replay_manifest=m,
        snapshot_ids=("snap-1", "snap-2"),
        feature_bundle_hashes=("feat-1", "feat-2"),
        candidate_universe_hashes=("uni-1", "uni-2"),
        outcome_hashes=("out-1", "out-2"),
    )
    pointer = DeploymentPointer(
        current=DeploymentManifest(
            mode=DeploymentMode.SHADOW,
            config_version="cfg",
            model_versions=(("m", "h"),),
            git_commit="parity16",
        )
    )
    report = run_dual_runtime_parity(
        inputs=inputs,
        live_shadow_hashes={
            "snapshot": "sha256:s",
            "feature": "sha256:f",
            "candidate": "sha256:c",
            "outcome": "sha256:o",
        },
        replay_hashes={
            "snapshot": "sha256:s",
            "feature": "sha256:f",
            "candidate": "sha256:c",
            "outcome": "sha256:o",
        },
        live_shadow_decisions=(("snap-1", "SELECT:c1"), ("snap-2", "ABSTAIN")),
        replay_decisions=(("snap-1", "SELECT:c1"), ("snap-2", "ABSTAIN")),
        pointer=pointer,
        workload=lambda: sum(range(1000)),
    )
    assert report.all_matched
    assert report.rollback_rehearsal_ok
    assert report.performance[0].latency_ms >= 0
    assert report.performance[0].peak_memory_kib >= 0


def test_decision_difference_detected() -> None:
    m = _manifest()
    inputs = ParityInputs(
        live_shadow_manifest=m,
        replay_manifest=m,
        snapshot_ids=("snap-1",),
        feature_bundle_hashes=("f",),
        candidate_universe_hashes=("c",),
    )
    report = run_dual_runtime_parity(
        inputs=inputs,
        live_shadow_hashes={"snapshot": "x"},
        replay_hashes={"snapshot": "x"},
        live_shadow_decisions=(("snap-1", "SELECT:a"),),
        replay_decisions=(("snap-1", "SELECT:b"),),
    )
    assert not report.all_matched
    assert report.decision_diffs[0].differs
