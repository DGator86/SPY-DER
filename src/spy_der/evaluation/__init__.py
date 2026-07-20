"""Evaluation metrics, labels, settlement, comparison, and reports."""

from __future__ import annotations

from spy_der.evaluation.comparison import (
    AblationId,
    ComparisonKind,
    ComparisonManifest,
    ComparisonReport,
    VariantResult,
    compare_agents,
    compare_controlled,
    compare_native,
    compare_policies,
    run_ablations,
)
from spy_der.evaluation.labels import (
    HORIZONS,
    SessionLabeler,
    direction_label,
    first_passage,
    range_survival,
)
from spy_der.evaluation.metrics import (
    EVALUATION_SCHEMA,
    EvaluationResult,
    TradeOutcome,
    evaluate_placeholder,
    evaluate_trades,
)
from spy_der.evaluation.reports import SessionReport, render_comparison_report, session_safe_report
from spy_der.evaluation.settlement import SettlementBatch, settle_candidate, settle_session

__all__ = [
    "EVALUATION_SCHEMA",
    "HORIZONS",
    "AblationId",
    "ComparisonKind",
    "ComparisonManifest",
    "ComparisonReport",
    "EvaluationResult",
    "SessionLabeler",
    "SessionReport",
    "SettlementBatch",
    "TradeOutcome",
    "VariantResult",
    "compare_agents",
    "compare_controlled",
    "compare_native",
    "compare_policies",
    "direction_label",
    "evaluate_placeholder",
    "evaluate_trades",
    "first_passage",
    "range_survival",
    "render_comparison_report",
    "run_ablations",
    "session_safe_report",
    "settle_candidate",
    "settle_session",
]
