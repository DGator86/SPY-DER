"""Objective profitability and reliability evidence gates for Alpha V2.

Passing these gates never changes broker authority.  The report distinguishes
minimum paper evidence from a stronger significant-edge claim and fails closed
when required evidence is absent or any critical operational incident occurs.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "EvidenceGate",
    "EvidenceMetrics",
    "EvidenceReport",
    "EvidenceStatus",
    "EvidenceThresholds",
    "evaluate_evidence",
]


class EvidenceStatus(StrEnum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PAPER_GATES_PASSED = "PAPER_GATES_PASSED"
    SIGNIFICANT_EDGE_CANDIDATE = "SIGNIFICANT_EDGE_CANDIDATE"


@dataclass(frozen=True, slots=True)
class EvidenceThresholds:
    minimum_paper_sessions: int = 60
    minimum_matured_forecasts: int = 5000
    minimum_primary_horizon_samples: int = 750
    minimum_paper_trades: int = 60
    minimum_verified_data_fraction: float = 0.99
    minimum_pq_ready_fraction: float = 0.90
    minimum_trained_primary_fraction: float = 0.90
    minimum_direction_accuracy_15m: float = 0.53
    minimum_direction_wilson_lcb_15m: float = 0.50
    maximum_brier_15m: float = 0.245
    minimum_brier_skill_15m: float = 0.01
    minimum_direction_accuracy_30m: float = 0.525
    minimum_direction_wilson_lcb_30m: float = 0.50
    maximum_brier_30m: float = 0.25
    minimum_brier_skill_30m: float = 0.0
    minimum_interval_coverage: float = 0.72
    maximum_interval_coverage: float = 0.88
    minimum_net_pnl: float = 0.0
    minimum_profit_factor: float = 1.15
    minimum_expectancy_lcb: float = 0.0
    maximum_drawdown: float = 600.0
    minimum_doubled_friction_pnl: float = 0.0
    recent_trade_window: int = 20
    minimum_recent_net_pnl: float = 0.0
    minimum_recent_profit_factor: float = 1.0
    minimum_fill_fraction: float = 0.95
    maximum_mean_fill_slippage: float = 12.0
    maximum_realized_loss_to_model_ratio: float = 1.10
    minimum_tested_regimes: int = 3
    minimum_regime_coverage: float = 0.75
    significant_profit_factor: float = 1.30

    def __post_init__(self) -> None:
        bounded = (
            self.minimum_verified_data_fraction,
            self.minimum_pq_ready_fraction,
            self.minimum_trained_primary_fraction,
            self.minimum_direction_accuracy_15m,
            self.minimum_direction_wilson_lcb_15m,
            self.maximum_brier_15m,
            self.minimum_direction_accuracy_30m,
            self.minimum_direction_wilson_lcb_30m,
            self.maximum_brier_30m,
            self.minimum_interval_coverage,
            self.maximum_interval_coverage,
            self.minimum_fill_fraction,
            self.minimum_regime_coverage,
        )
        if any(not 0.0 <= value <= 1.0 for value in bounded):
            raise ValueError("probability thresholds must be within [0, 1]")
        if self.maximum_interval_coverage < self.minimum_interval_coverage:
            raise ValueError("maximum_interval_coverage must be >= minimum_interval_coverage")
        if self.significant_profit_factor < self.minimum_profit_factor:
            raise ValueError("significant_profit_factor must be >= minimum_profit_factor")


@dataclass(frozen=True, slots=True)
class EvidenceMetrics:
    paper_sessions: int = 0
    matured_forecasts: int = 0
    primary_15m_samples: int = 0
    primary_30m_samples: int = 0
    closed_trades: int = 0
    verified_data_fraction: float | None = None
    pq_ready_fraction: float | None = None
    trained_signal_fraction_15m: float | None = None
    trained_signal_fraction_30m: float | None = None
    direction_accuracy_15m: float | None = None
    direction_wilson_lcb_15m: float | None = None
    brier_15m: float | None = None
    brier_skill_15m: float | None = None
    interval_coverage_15m: float | None = None
    direction_accuracy_30m: float | None = None
    direction_wilson_lcb_30m: float | None = None
    brier_30m: float | None = None
    brier_skill_30m: float | None = None
    interval_coverage_30m: float | None = None
    net_pnl: float | None = None
    profit_factor: float | None = None
    expectancy_lcb: float | None = None
    max_drawdown: float | None = None
    doubled_friction_pnl: float | None = None
    recent_trade_samples: int = 0
    recent_net_pnl: float | None = None
    recent_profit_factor: float | None = None
    pnl_without_top10: float | None = None
    ranking_spearman_pnl: float | None = None
    ranking_spearman_win: float | None = None
    fill_fraction: float | None = None
    mean_fill_slippage: float | None = None
    maximum_realized_loss_to_model_ratio: float | None = None
    tested_regimes: int = 0
    regime_coverage: float | None = None
    negative_expectancy_regimes: int = 0
    critical_stage_failures: int = 0
    journal_failures: int = 0
    reconciliation_errors: int = 0
    duplicate_order_breaches: int = 0
    stale_decision_executions: int = 0
    non_sandbox_orders: int = 0
    replay_mismatches: int = 0
    modeled_loss_violations: int = 0


@dataclass(frozen=True, slots=True)
class EvidenceGate:
    name: str
    passed: bool
    actual: Any
    threshold: Any
    tier: str = "base"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    status: EvidenceStatus
    gates: tuple[EvidenceGate, ...]
    metrics: EvidenceMetrics
    thresholds: EvidenceThresholds
    automatic_live_enable: bool = False
    report_version: str = "alpha-v2-evidence.v1"

    @property
    def passed(self) -> bool:
        return self.status is not EvidenceStatus.INSUFFICIENT_EVIDENCE

    @property
    def significant_edge(self) -> bool:
        return self.status is EvidenceStatus.SIGNIFICANT_EDGE_CANDIDATE

    @property
    def failed_gates(self) -> tuple[EvidenceGate, ...]:
        return tuple(gate for gate in self.gates if not gate.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "gates": [gate.to_dict() for gate in self.gates],
            "metrics": asdict(self.metrics),
            "thresholds": asdict(self.thresholds),
            "automatic_live_enable": False,
            "report_version": self.report_version,
        }


def _finite(value: float | int | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _min(name: str, actual: float | int | None, threshold: float | int, *, tier: str = "base") -> EvidenceGate:
    return EvidenceGate(name, _finite(actual) and float(actual) >= float(threshold), actual, threshold, tier)


def _max(name: str, actual: float | int | None, threshold: float | int) -> EvidenceGate:
    return EvidenceGate(name, _finite(actual) and float(actual) <= float(threshold), actual, threshold)


def _band(name: str, actual: float | None, low: float, high: float) -> EvidenceGate:
    return EvidenceGate(name, _finite(actual) and low <= float(actual) <= high, actual, [low, high])


def _zero(name: str, actual: int) -> EvidenceGate:
    return EvidenceGate(name, actual == 0, actual, 0)


def evaluate_evidence(
    metrics: EvidenceMetrics,
    thresholds: EvidenceThresholds | None = None,
) -> EvidenceReport:
    """Evaluate settled paper evidence without changing deployment authority."""
    t = thresholds or EvidenceThresholds()
    base = [
        _min("paper_sessions", metrics.paper_sessions, t.minimum_paper_sessions),
        _min("matured_forecasts", metrics.matured_forecasts, t.minimum_matured_forecasts),
        _min("15m_sample", metrics.primary_15m_samples, t.minimum_primary_horizon_samples),
        _min("30m_sample", metrics.primary_30m_samples, t.minimum_primary_horizon_samples),
        _min("paper_trades", metrics.closed_trades, t.minimum_paper_trades),
        _min("verified_data", metrics.verified_data_fraction, t.minimum_verified_data_fraction),
        _min("pq_ready", metrics.pq_ready_fraction, t.minimum_pq_ready_fraction),
        _min("15m_trained_signal", metrics.trained_signal_fraction_15m, t.minimum_trained_primary_fraction),
        _min("30m_trained_signal", metrics.trained_signal_fraction_30m, t.minimum_trained_primary_fraction),
        _min("15m_direction", metrics.direction_accuracy_15m, t.minimum_direction_accuracy_15m),
        _min("15m_direction_wilson_lcb", metrics.direction_wilson_lcb_15m, t.minimum_direction_wilson_lcb_15m),
        _max("15m_brier", metrics.brier_15m, t.maximum_brier_15m),
        _min("15m_brier_skill", metrics.brier_skill_15m, t.minimum_brier_skill_15m),
        _band("15m_interval_coverage", metrics.interval_coverage_15m, t.minimum_interval_coverage, t.maximum_interval_coverage),
        _min("30m_direction", metrics.direction_accuracy_30m, t.minimum_direction_accuracy_30m),
        _min("30m_direction_wilson_lcb", metrics.direction_wilson_lcb_30m, t.minimum_direction_wilson_lcb_30m),
        _max("30m_brier", metrics.brier_30m, t.maximum_brier_30m),
        _min("30m_brier_skill", metrics.brier_skill_30m, t.minimum_brier_skill_30m),
        _band("30m_interval_coverage", metrics.interval_coverage_30m, t.minimum_interval_coverage, t.maximum_interval_coverage),
        _min("net_pnl", metrics.net_pnl, t.minimum_net_pnl),
        _min("profit_factor", metrics.profit_factor, t.minimum_profit_factor),
        _min("expectancy_lcb", metrics.expectancy_lcb, t.minimum_expectancy_lcb),
        _max("max_drawdown", metrics.max_drawdown, t.maximum_drawdown),
        _min("doubled_friction_pnl", metrics.doubled_friction_pnl, t.minimum_doubled_friction_pnl),
        _min("recent_trade_samples", metrics.recent_trade_samples, t.recent_trade_window),
        _min("recent_net_pnl", metrics.recent_net_pnl, t.minimum_recent_net_pnl),
        _min("recent_profit_factor", metrics.recent_profit_factor, t.minimum_recent_profit_factor),
        _min("fill_fraction", metrics.fill_fraction, t.minimum_fill_fraction),
        _max("mean_fill_slippage", metrics.mean_fill_slippage, t.maximum_mean_fill_slippage),
        _max("realized_loss_to_model_ratio", metrics.maximum_realized_loss_to_model_ratio, t.maximum_realized_loss_to_model_ratio),
        _min("tested_regimes", metrics.tested_regimes, t.minimum_tested_regimes),
        _min("regime_coverage", metrics.regime_coverage, t.minimum_regime_coverage),
        _zero("negative_expectancy_regimes", metrics.negative_expectancy_regimes),
    ]
    incident_fields = (
        "critical_stage_failures",
        "journal_failures",
        "reconciliation_errors",
        "duplicate_order_breaches",
        "stale_decision_executions",
        "non_sandbox_orders",
        "replay_mismatches",
        "modeled_loss_violations",
    )
    base.extend(_zero(name, int(getattr(metrics, name))) for name in incident_fields)

    significant = [
        _min("significant_profit_factor", metrics.profit_factor, t.significant_profit_factor, tier="significant"),
        EvidenceGate("positive_expectancy_lcb", _finite(metrics.expectancy_lcb) and float(metrics.expectancy_lcb) > 0.0, metrics.expectancy_lcb, "> 0", "significant"),
        EvidenceGate("positive_doubled_friction_pnl", _finite(metrics.doubled_friction_pnl) and float(metrics.doubled_friction_pnl) > 0.0, metrics.doubled_friction_pnl, "> 0", "significant"),
        EvidenceGate("pnl_without_top10", _finite(metrics.pnl_without_top10) and float(metrics.pnl_without_top10) > 0.0, metrics.pnl_without_top10, "> 0", "significant", "edge must survive removal of the ten largest winners"),
        _min("ranking_spearman_pnl", metrics.ranking_spearman_pnl, 0.0, tier="significant"),
        _min("ranking_spearman_win", metrics.ranking_spearman_win, 0.0, tier="significant"),
    ]
    base_ok = all(gate.passed for gate in base)
    significant_ok = base_ok and all(gate.passed for gate in significant)
    status = (
        EvidenceStatus.SIGNIFICANT_EDGE_CANDIDATE
        if significant_ok
        else EvidenceStatus.PAPER_GATES_PASSED
        if base_ok
        else EvidenceStatus.INSUFFICIENT_EVIDENCE
    )
    return EvidenceReport(status, tuple([*base, *significant]), metrics, t)
