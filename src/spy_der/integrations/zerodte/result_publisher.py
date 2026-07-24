"""Publish SPY-DER dashboard packets for the 0DTE adapter to read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spy_der.contracts.integration import (
    DASHBOARD_SCHEMA,
    DashboardDojoStatus,
    DashboardPacket,
    dashboard_packet_from_dict,
)
from spy_der.dojo.config import DEFAULT_LIVE_STATE, DEFAULT_REPORTS_DIR
from spy_der.util.files import atomic_write_json

__all__ = [
    "enrich_with_dojo_status",
    "publish_dashboard_packet",
    "read_dashboard_packet",
]


def enrich_with_dojo_status(
    packet: DashboardPacket,
    *,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> DashboardPacket:
    # Lazy import avoids dojo ↔ integrations circular initialization.
    from spy_der.dojo.reports import read_latest_dojo_report

    latest = read_latest_dojo_report(reports_dir)
    if latest is None:
        return packet
    flags = latest.get("flags") or []
    weak = sum(
        1
        for flag in flags
        if isinstance(flag, dict) and str(flag.get("flag", "")).startswith("weak_archetype")
    )
    status = "WARN" if weak else "PASS"
    if latest.get("metrics", {}).get("phases", {}).get("recorded", {}).get("status") == (
        "insufficient_data"
    ) and weak == 0:
        status = "INFO"
    generated = latest.get("generated_at")
    from datetime import datetime

    latest_run = None
    if isinstance(generated, str) and generated:
        latest_run = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    dojo = DashboardDojoStatus(
        latest_status=status,
        latest_run=latest_run,
        latest_report_path=str(Path(reports_dir) / "latest.json"),
        weak_archetypes=weak,
        summary=str(latest.get("summary") or ""),
    )
    return DashboardPacket(
        schema_version=packet.schema_version,
        generated_at=packet.generated_at,
        mode=packet.mode,
        action=packet.action,
        candidate_id=packet.candidate_id,
        confidence=packet.confidence,
        uncertainty=packet.uncertainty,
        trader_model=packet.trader_model,
        reviewer_model=packet.reviewer_model,
        reason_codes=packet.reason_codes,
        rationale=packet.rationale,
        size_scalar=packet.size_scalar,
        structure=packet.structure,
        direction=packet.direction,
        provider=packet.provider,
        available=packet.available,
        dojo=dojo,
        snapshot_id=packet.snapshot_id,
        symbol=packet.symbol,
    )


def publish_dashboard_packet(
    packet: DashboardPacket,
    *,
    path: str | Path = DEFAULT_LIVE_STATE,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
    enrich_dojo: bool = True,
) -> Path:
    body = enrich_with_dojo_status(packet, reports_dir=reports_dir) if enrich_dojo else packet
    if body.schema_version != DASHBOARD_SCHEMA:
        raise ValueError(f"refusing to publish non-dashboard schema {body.schema_version}")
    return atomic_write_json(path, body.to_dict())


def read_dashboard_packet(path: str | Path = DEFAULT_LIVE_STATE) -> DashboardPacket | None:
    target = Path(path)
    if not target.is_file():
        return None
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return None
    return dashboard_packet_from_dict(data)


def publish_raw(path: str | Path, payload: dict[str, Any]) -> Path:
    """Escape hatch for non-contract operator dumps (tests / debug)."""
    return atomic_write_json(path, payload)
