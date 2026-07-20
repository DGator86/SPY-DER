"""Parity golden for Phase 16 dual-runtime report identity."""

from __future__ import annotations

import json
from pathlib import Path

from spy_der.contracts import to_canonical_json
from spy_der.deployment import DeploymentManifest, DeploymentMode, DeploymentPointer
from spy_der.replay.deterministic import ReplayInputManifest
from spy_der.runtime import ParityInputs, run_dual_runtime_parity

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase16" / "dual_runtime_parity.json"


def _artifact() -> dict[str, object]:
    m = ReplayInputManifest(
        timestamp="2026-01-05T16:30:00+00:00",
        market_snapshot_hash="snap16",
        underlying_bars_hash="bars16",
        option_chain_hash="chain16",
        candidate_universe_hash="cands16",
        fees_hash="fees16",
        slippage_hash="slip16",
        fill_assumptions_hash="fill16",
        account_size_hash="acct16",
        risk_ceilings_hash="risk16",
        exit_policy_hash="exit16",
        settlement_hash="settle16",
    )
    inputs = ParityInputs(
        live_shadow_manifest=m,
        replay_manifest=m,
        snapshot_ids=("snap-a",),
        feature_bundle_hashes=("feat-a",),
        candidate_universe_hashes=("cand-a",),
        outcome_hashes=("out-a",),
        seed="phase16-parity",
    )
    pointer = DeploymentPointer(
        current=DeploymentManifest(
            deployment_id="deploy-16",
            mode=DeploymentMode.SHADOW,
            config_version="cfg16",
            model_versions=(("m16", "sha256:m16"),),
            git_commit="phase16",
        )
    )
    report = run_dual_runtime_parity(
        inputs=inputs,
        live_shadow_hashes={
            "snapshot": "sha256:snap",
            "feature": "sha256:feat",
            "candidate": "sha256:cand",
            "outcome": "sha256:out",
        },
        replay_hashes={
            "snapshot": "sha256:snap",
            "feature": "sha256:feat",
            "candidate": "sha256:cand",
            "outcome": "sha256:out",
        },
        live_shadow_decisions=(("snap-a", "ABSTAIN"),),
        replay_decisions=(("snap-a", "ABSTAIN"),),
        pointer=pointer,
    )
    return {
        "inputs_hash": report.inputs_hash,
        "all_matched": report.all_matched,
        "bucket_names": [b.name for b in report.buckets],
        "bucket_matched": [b.matched for b in report.buckets],
        "decision_differs": [d.differs for d in report.decision_diffs],
        "rollback_rehearsal_ok": report.rollback_rehearsal_ok,
        "notes": list(report.notes),
    }


def test_phase16_dual_runtime_parity() -> None:
    _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(to_canonical_json(_artifact()))
    if not _EXPECTED.exists():
        _EXPECTED.write_text(to_canonical_json(artifact) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert artifact == expected
