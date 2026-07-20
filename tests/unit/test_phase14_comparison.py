"""Phase 14 — evaluation metrics, native/controlled/policy/agent/ablation comparison."""

from __future__ import annotations

from decimal import Decimal

import pytest

from spy_der.evaluation import (
    AblationId,
    ComparisonManifest,
    TradeOutcome,
    compare_agents,
    compare_controlled,
    compare_native,
    compare_policies,
    evaluate_trades,
    render_comparison_report,
    run_ablations,
    session_safe_report,
)
from spy_der.replay.deterministic import ReplayInputManifest


def _manifest() -> ComparisonManifest:
    return ComparisonManifest(
        system_a_commit="de4a6e7",
        system_b_commit="phase14",
        snapshot_ids=("snap-1",),
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
        random_seed="seed-14",
        deployment_ids=("system_b_ensemble",),
    )


def _replay() -> ReplayInputManifest:
    return ReplayInputManifest(
        timestamp="2026-01-05T16:00:00+00:00",
        market_snapshot_hash="a",
        underlying_bars_hash="b",
        option_chain_hash="c",
        candidate_universe_hash="d",
        fees_hash="e",
        slippage_hash="f",
        fill_assumptions_hash="g",
        account_size_hash="h",
        risk_ceilings_hash="i",
        exit_policy_hash="j",
        settlement_hash="k",
    )


def _trades(pnls: list[str], *, session: str = "2026-01-05") -> list[TradeOutcome]:
    return [
        TradeOutcome(
            session_date=session,
            realized_pnl=Decimal(p),
            max_loss=Decimal("25"),
            was_traded=True,
        )
        for p in pnls
    ]


def test_evaluate_trades_basic_metrics() -> None:
    metrics = evaluate_trades(_trades(["10", "-5", "20"]))
    assert metrics.trade_count == 3
    assert metrics.net_pnl == Decimal("25")
    assert metrics.win_rate == pytest.approx(2 / 3)
    assert metrics.expectancy == pytest.approx(25 / 3)
    assert metrics.maximum_drawdown >= 0


def test_native_and_controlled_comparison() -> None:
    m = _manifest()
    native = compare_native(
        manifest=m,
        system_a=_trades(["5"]),
        system_b=_trades(["8"]),
    )
    assert native.candidates[0].metrics.net_pnl == Decimal("8")
    assert native.delta_net_pnl["system_b_native"] == "3"

    rm = _replay()
    controlled = compare_controlled(
        manifest=m,
        replay_manifest_a=rm,
        replay_manifest_b=rm,
        baseline_outcomes=_trades(["5"]),
        candidate_outcomes=_trades(["4"]),
    )
    assert controlled.kind.value == "controlled"
    assert controlled.delta_net_pnl["system_b_controlled"] == "-1"


def test_controlled_manifest_mismatch_fails_closed() -> None:
    m = _manifest()
    a = _replay()
    b = ReplayInputManifest(
        timestamp=a.timestamp,
        market_snapshot_hash="DIFFERENT",
        underlying_bars_hash=a.underlying_bars_hash,
        option_chain_hash=a.option_chain_hash,
        candidate_universe_hash=a.candidate_universe_hash,
        fees_hash=a.fees_hash,
        slippage_hash=a.slippage_hash,
        fill_assumptions_hash=a.fill_assumptions_hash,
        account_size_hash=a.account_size_hash,
        risk_ceilings_hash=a.risk_ceilings_hash,
        exit_policy_hash=a.exit_policy_hash,
        settlement_hash=a.settlement_hash,
    )
    with pytest.raises(ValueError):
        compare_controlled(
            manifest=m,
            replay_manifest_a=a,
            replay_manifest_b=b,
            baseline_outcomes=_trades(["1"]),
            candidate_outcomes=_trades(["1"]),
        )


def test_policy_agent_and_ablations() -> None:
    m = _manifest()
    policies = compare_policies(
        manifest=m,
        policy_outcomes={
            "legacy": _trades(["1"]),
            "v2": _trades(["3"]),
            "v3": _trades(["2"]),
        },
        baseline_policy="legacy",
    )
    assert {c.variant_id for c in policies.candidates} == {"v2", "v3"}

    agents = compare_agents(
        manifest=m,
        agent_outcomes={
            "deterministic": _trades(["2"]),
            "grok": _trades(["1"]),
        },
        baseline_agent="deterministic",
    )
    assert agents.candidates[0].variant_id == "grok"

    abl = run_ablations(
        manifest=m,
        full_system=_trades(["10"]),
        ablations={
            AblationId.WITHOUT_V2: _trades(["8"]),
            AblationId.WITHOUT_V3: _trades(["7"]),
        },
    )
    assert len(abl.candidates) == 2
    rendered = render_comparison_report(abl)
    assert rendered["kind"] == "ablation"
    assert rendered["manifest_hash"].startswith("sha256:")


def test_session_safe_report() -> None:
    metrics = evaluate_trades(_trades(["4"], session="2026-01-06"))
    report = session_safe_report(session_date="2026-01-06", metrics=metrics)
    assert report.session_date == "2026-01-06"
    with pytest.raises(ValueError):
        session_safe_report(session_date="", metrics=metrics)
