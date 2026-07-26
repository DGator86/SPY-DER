"""Session-safe comparison reports (spec §56 / §61)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.serialization import to_canonical_json
from spy_der.evaluation.attribution import ShadowAccountReport
from spy_der.evaluation.comparison import ComparisonReport
from spy_der.evaluation.metrics import EvaluationResult
from spy_der.util.files import atomic_write_json

__all__ = [
    "SessionReport",
    "persist_attribution_report",
    "read_latest_attribution_report",
    "render_comparison_report",
    "session_safe_report",
]

ET = ZoneInfo("America/New_York")


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
    parsed: dict[str, Any] = json.loads(to_canonical_json(payload))
    return parsed


def persist_attribution_report(
    reports_dir: str | Path,
    *,
    report: ShadowAccountReport,
    now: datetime | None = None,
) -> dict[str, str]:
    """Write a stamped shadow-account report plus `latest.json`.

    Same layout and naming as `dojo.reports.persist_dojo_report`, so the
    dashboard API's report-index helper reads it without a second code path and
    the stamped names keep sorting chronologically.
    """
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp_dt = now or datetime.now(ET)
    payload = report.to_dict()
    payload["generated_at"] = stamp_dt.isoformat()
    payload["report_date"] = report.sessions[-1] if report.sessions else ""
    payload["summary"] = (
        f"{report.verdict}: model {report.model_pnl} vs actual "
        f"{report.actual_pnl} (gap {report.gap})"
    )
    stamped = root / f"attribution_{stamp_dt.strftime('%Y%m%d_%H%M%S')}.json"
    atomic_write_json(stamped, payload)
    atomic_write_json(root / "latest.json", payload)
    return {"json_path": str(stamped), "latest_path": str(root / "latest.json")}


def read_latest_attribution_report(reports_dir: str | Path) -> dict[str, Any] | None:
    latest = Path(reports_dir) / "latest.json"
    if not latest.is_file():
        return None
    with open(latest, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else None
