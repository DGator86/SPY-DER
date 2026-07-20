"""Session-safe comparison reports (spec §56 / §61)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spy_der.contracts.serialization import to_canonical_json
from spy_der.evaluation.comparison import ComparisonReport
from spy_der.evaluation.metrics import EvaluationResult

__all__ = ["SessionReport", "render_comparison_report", "session_safe_report"]


@dataclass(frozen=True, slots=True)
class SessionReport:
    session_date: str
    metrics: EvaluationResult
    notes: tuple[str, ...] = ()


def session_safe_report(
    *,
    session_date: str,
    metrics: EvaluationResult,
    notes: tuple[str, ...] = (),
) -> SessionReport:
    """Build a report scoped to one session (no cross-session leakage)."""
    if not session_date:
        raise ValueError("session_date is required")
    return SessionReport(session_date=session_date, metrics=metrics, notes=notes)


def render_comparison_report(report: ComparisonReport) -> dict[str, Any]:
    """Canonical JSON-serializable comparison summary."""
    def _metrics(m: EvaluationResult) -> dict[str, Any]:
        return {
            "net_pnl": str(m.net_pnl),
            "expectancy": m.expectancy,
            "win_rate": m.win_rate,
            "profit_factor": m.profit_factor,
            "maximum_drawdown": m.maximum_drawdown,
            "cvar": m.cvar,
            "abstention_rate": m.abstention_rate,
            "trade_count": m.trade_count,
            "session_count": m.session_count,
        }

    payload = {
        "kind": report.kind.value,
        "manifest_hash": report.manifest.manifest_hash,
        "baseline": {
            "variant_id": report.baseline.variant_id,
            "metrics": _metrics(report.baseline.metrics),
        },
        "candidates": [
            {
                "variant_id": c.variant_id,
                "metrics": _metrics(c.metrics),
                "delta_net_pnl": report.delta_net_pnl.get(c.variant_id, "0"),
            }
            for c in report.candidates
        ],
        "notes": list(report.notes),
    }
    # Round-trip through canonical JSON for stable key ordering.
    import json

    parsed: dict[str, Any] = json.loads(to_canonical_json(payload))
    return parsed
