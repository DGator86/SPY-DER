from __future__ import annotations

from dataclasses import replace

from spy_der.deployment.evidence import EvidenceMetrics, EvidenceStatus, evaluate_evidence


def _passing_metrics() -> EvidenceMetrics:
    return EvidenceMetrics(
        paper_sessions=65,
        matured_forecasts=6000,
        primary_15m_samples=800,
        primary_30m_samples=800,
        closed_trades=75,
        verified_data_fraction=0.995,
        pq_ready_fraction=0.95,
        trained_signal_fraction_15m=0.96,
        trained_signal_fraction_30m=0.95,
        direction_accuracy_15m=0.55,
        direction_wilson_lcb_15m=0.51,
        brier_15m=0.235,
        brier_skill_15m=0.03,
        interval_coverage_15m=0.80,
        direction_accuracy_30m=0.545,
        direction_wilson_lcb_30m=0.505,
        brier_30m=0.24,
        brier_skill_30m=0.02,
        interval_coverage_30m=0.79,
        net_pnl=2400.0,
        profit_factor=1.45,
        expectancy_lcb=7.5,
        max_drawdown=420.0,
        doubled_friction_pnl=1200.0,
        recent_trade_samples=20,
        recent_net_pnl=350.0,
        recent_profit_factor=1.25,
        pnl_without_top10=500.0,
        ranking_spearman_pnl=0.18,
        ranking_spearman_win=0.12,
        fill_fraction=0.97,
        mean_fill_slippage=8.0,
        maximum_realized_loss_to_model_ratio=1.02,
        tested_regimes=4,
        regime_coverage=0.82,
        negative_expectancy_regimes=0,
    )


def test_strong_clean_evidence_reaches_significant_edge_candidate() -> None:
    report = evaluate_evidence(_passing_metrics())
    assert report.status is EvidenceStatus.SIGNIFICANT_EDGE_CANDIDATE
    assert report.significant_edge is True
    assert report.automatic_live_enable is False
    assert not report.failed_gates


def test_paper_floor_can_pass_without_significant_edge_claim() -> None:
    report = evaluate_evidence(
        replace(
            _passing_metrics(),
            profit_factor=1.20,
            pnl_without_top10=0.0,
            ranking_spearman_pnl=-0.01,
        )
    )
    assert report.status is EvidenceStatus.PAPER_GATES_PASSED
    failed = {gate.name for gate in report.failed_gates if gate.tier == "significant"}
    assert {"significant_profit_factor", "pnl_without_top10", "ranking_spearman_pnl"} <= failed


def test_one_operational_incident_blocks_every_success_tier() -> None:
    report = evaluate_evidence(replace(_passing_metrics(), reconciliation_errors=1))
    assert report.status is EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert "reconciliation_errors" in {gate.name for gate in report.failed_gates}


def test_missing_metric_fails_closed() -> None:
    report = evaluate_evidence(replace(_passing_metrics(), brier_15m=None))
    assert report.status is EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert next(g for g in report.gates if g.name == "15m_brier").passed is False


def test_profit_concentration_blocks_significant_claim() -> None:
    report = evaluate_evidence(replace(_passing_metrics(), pnl_without_top10=-1.0))
    assert report.status is EvidenceStatus.PAPER_GATES_PASSED
    assert next(g for g in report.gates if g.name == "pnl_without_top10").passed is False


def test_inverted_ranking_blocks_significant_claim() -> None:
    report = evaluate_evidence(
        replace(_passing_metrics(), ranking_spearman_pnl=-0.10, ranking_spearman_win=-0.20)
    )
    assert report.status is EvidenceStatus.PAPER_GATES_PASSED
    failed = {gate.name for gate in report.failed_gates}
    assert {"ranking_spearman_pnl", "ranking_spearman_win"} <= failed
