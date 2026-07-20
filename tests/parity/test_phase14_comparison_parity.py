"""Parity golden for Phase 14 controlled comparison report."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from spy_der.contracts import to_canonical_json
from spy_der.evaluation import (
    ComparisonManifest,
    TradeOutcome,
    compare_controlled,
    render_comparison_report,
)
from spy_der.replay.deterministic import ReplayInputManifest

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase14" / "comparison_report.json"


def _artifact() -> dict[str, object]:
    manifest = ComparisonManifest(
        system_a_commit="de4a6e7ced98ff97c778e8b4418c08848d7ce82d",
        system_b_commit="phase14-parity",
        snapshot_ids=("snap-phase14",),
        feature_version="features.v1",
        candidate_version="candidate.v1",
        economics_version="economics.v1",
        fee_version="fees.v1",
        slippage_version="slip.v1",
        fill_model_version="fill.v1",
        risk_configuration="risk.v1",
        exit_registry="exits.v1",
        settlement_source="session_close",
        account_size="10000",
        random_seed="parity-14",
        deployment_ids=("system_b_ensemble", "system_b_v3"),
    )
    rm = ReplayInputManifest(
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
    baseline = (
        TradeOutcome("2026-01-05", Decimal("10"), Decimal("25"), was_traded=True),
        TradeOutcome("2026-01-05", Decimal("-4"), Decimal("25"), was_traded=True),
    )
    candidate = (
        TradeOutcome("2026-01-05", Decimal("12"), Decimal("25"), was_traded=True),
        TradeOutcome("2026-01-05", Decimal("-3"), Decimal("25"), was_traded=True),
    )
    report = compare_controlled(
        manifest=manifest,
        replay_manifest_a=rm,
        replay_manifest_b=rm,
        baseline_outcomes=baseline,
        candidate_outcomes=candidate,
        candidate_id="system_b_ensemble",
    )
    return render_comparison_report(report)


def test_phase14_comparison_parity() -> None:
    _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(to_canonical_json(_artifact()))
    if not _EXPECTED.exists():
        _EXPECTED.write_text(to_canonical_json(artifact) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert artifact == expected
