"""Minimal ops dashboard projection (read-only status board)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spy_der.deployment.drift import DriftReport
from spy_der.deployment.manifest import DeploymentManifest
from spy_der.deployment.notifications import Notification

__all__ = ["OpsDashboardView", "build_ops_dashboard"]


@dataclass(frozen=True, slots=True)
class OpsDashboardView:
    deployment_id: str
    mode: str
    frozen: bool
    configuration_hash: str
    drift_level: str
    recent_notifications: tuple[str, ...]
    runbooks: tuple[str, ...] = ("freeze", "promote", "rollback")


def build_ops_dashboard(
    *,
    manifest: DeploymentManifest,
    drift: DriftReport | None = None,
    notifications: tuple[Notification, ...] = (),
) -> OpsDashboardView:
    return OpsDashboardView(
        deployment_id=manifest.deployment_id,
        mode=manifest.mode.value,
        frozen=manifest.frozen,
        configuration_hash=manifest.configuration_hash,
        drift_level=drift.level.value if drift is not None else "unknown",
        recent_notifications=tuple(
            f"{n.level.value}:{n.topic}:{n.message}" for n in notifications[-10:]
        ),
    )


def dashboard_as_dict(view: OpsDashboardView) -> dict[str, Any]:
    return {
        "deployment_id": view.deployment_id,
        "mode": view.mode,
        "frozen": view.frozen,
        "configuration_hash": view.configuration_hash,
        "drift_level": view.drift_level,
        "recent_notifications": list(view.recent_notifications),
        "runbooks": list(view.runbooks),
    }
