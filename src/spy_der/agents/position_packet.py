"""Build PositionDecisionPacket from open position + market context."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from spy_der.contracts.agents import (
    DeploymentContext,
    ExitPolicySummary,
    OpenPositionView,
    PositionDecisionPacket,
    SnapshotSummary,
    make_position_packet_id,
    packet_hash,
)
from spy_der.contracts.positions import PositionState
from spy_der.contracts.serialization import to_canonical_json

__all__ = ["build_open_position_view", "build_position_decision_packet"]


def build_open_position_view(
    position: PositionState,
    *,
    mark_price: Decimal,
    now: datetime,
) -> OpenPositionView:
    entry = position.entry_price or Decimal("0")
    mark = Decimal(str(mark_price))
    pnl_ratio = float((mark - entry) / entry) if entry != 0 else 0.0
    held = 0.0
    if position.opened_at is not None:
        held = max(0.0, (now - position.opened_at).total_seconds() / 60.0)
    return OpenPositionView(
        position_id=position.position_id,
        candidate_id=position.candidate_id,
        open_contracts=position.open_contracts,
        entry_price=entry,
        mark_price=mark,
        unrealized_pnl_ratio=pnl_ratio,
        peak_pnl_ratio=float(position.peak_pnl),
        exit_policy_id=position.exit_policy_id,
        holding_minutes=held,
        max_loss=position.max_loss,
        geometry_hash=position.geometry_hash,
    )


def build_position_decision_packet(
    *,
    position: PositionState,
    snapshot: SnapshotSummary,
    mark_price: Decimal,
    now: datetime,
    ttl_seconds: int = 30,
    approved_exit_policies: tuple[ExitPolicySummary, ...] = (),
    hard_vetoes: tuple[str, ...] = (),
    deterministic_exit_signal: str = "",
    data_quality: float = 1.0,
    forecast_uncertainty: float = 0.0,
    deployment_context: DeploymentContext | None = None,
) -> PositionDecisionPacket:
    view = build_open_position_view(position, mark_price=mark_price, now=now)
    body = {
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "symbol": snapshot.symbol,
            "session_date": snapshot.session_date.isoformat(),
            "underlying_price": str(snapshot.underlying_price),
        },
        "position": {
            "position_id": view.position_id,
            "candidate_id": view.candidate_id,
            "open_contracts": view.open_contracts,
            "entry_price": str(view.entry_price),
            "mark_price": str(view.mark_price),
            "unrealized_pnl_ratio": view.unrealized_pnl_ratio,
            "peak_pnl_ratio": view.peak_pnl_ratio,
            "exit_policy_id": view.exit_policy_id,
            "holding_minutes": view.holding_minutes,
        },
        "hard_vetoes": list(hard_vetoes),
        "deterministic_exit_signal": deterministic_exit_signal,
        "data_quality": data_quality,
        "forecast_uncertainty": forecast_uncertainty,
    }
    # Canonical JSON keeps hash stable across dict key order.
    _ = to_canonical_json(body)
    ph = packet_hash(body)
    pid = make_position_packet_id(position.position_id, snapshot.snapshot_id, ph[:12])
    return PositionDecisionPacket(
        packet_id=pid,
        packet_hash=ph,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        snapshot_summary=snapshot,
        position=view,
        approved_exit_policies=approved_exit_policies,
        hard_vetoes=hard_vetoes,
        deterministic_exit_signal=deterministic_exit_signal,
        data_quality=data_quality,
        forecast_uncertainty=forecast_uncertainty,
        deployment_context=deployment_context or DeploymentContext(),
        evidence_ids=(f"pos:{position.position_id}",),
    )
