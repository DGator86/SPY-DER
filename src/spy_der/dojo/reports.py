"""Dojo report persistence under /var/lib/spy-der/reports/dojo."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.util.files import atomic_write_json

__all__ = ["persist_dojo_report", "read_latest_dojo_report"]

ET = ZoneInfo("America/New_York")


def persist_dojo_report(
    reports_dir: str | Path,
    *,
    report_date: str,
    summary: str,
    flags: list[dict[str, Any]],
    metrics: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, str]:
    """Write stamped JSON + `latest.json`. Never touches champion configs."""
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp_dt = now or datetime.now(ET)
    stamp = stamp_dt.strftime("%Y%m%d_%H%M%S")
    payload = {
        "report_date": report_date,
        "summary": summary,
        "flags": flags,
        "metrics": metrics,
        "generated_at": stamp_dt.isoformat(),
    }
    stamped = root / f"dojo_{stamp}.json"
    latest = root / "latest.json"
    with open(stamped, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.write("\n")
    atomic_write_json(latest, payload)
    return {"json_path": str(stamped), "latest_path": str(latest)}


def read_latest_dojo_report(reports_dir: str | Path) -> dict[str, Any] | None:
    latest = Path(reports_dir) / "latest.json"
    if not latest.is_file():
        return None
    with open(latest, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else None


def ensure_parent(path: str | Path) -> None:
    os.makedirs(Path(path).parent, exist_ok=True)
