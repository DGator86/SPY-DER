"""Atomic deployment pointer rollback and freeze."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from spy_der.deployment.manifest import (
    DeploymentError,
    DeploymentManifest,
    DeploymentMode,
)

__all__ = ["DeploymentPointer", "freeze_deployment", "rollback_deployment"]


@dataclass
class DeploymentPointer:
    """Mutable current/previous deployment pointer (single source of authority)."""

    current: DeploymentManifest
    history: list[DeploymentManifest] = field(default_factory=list)

    def activate(self, manifest: DeploymentManifest) -> DeploymentManifest:
        if self.current.frozen and manifest.deployment_id != self.current.deployment_id:
            raise DeploymentError("deployment is frozen; unfreeze before activate")
        self.history.append(self.current)
        self.current = manifest
        return self.current


def freeze_deployment(pointer: DeploymentPointer, *, reason: str) -> DeploymentManifest:
    frozen = replace(
        pointer.current,
        deployment_id=pointer.current.deployment_id,
        mode=DeploymentMode.FROZEN,
        frozen=True,
        notes=(*pointer.current.notes, f"freeze:{reason}"),
    )
    pointer.current = frozen
    return frozen


def rollback_deployment(pointer: DeploymentPointer, *, reason: str) -> DeploymentManifest:
    if not pointer.history:
        raise DeploymentError("no previous deployment to roll back to")
    target = pointer.history.pop()
    if target.frozen:
        raise DeploymentError("rollback target is frozen")
    restored = replace(
        target,
        frozen=False,
        mode=target.mode if target.mode is not DeploymentMode.FROZEN else DeploymentMode.SHADOW,
        notes=(*target.notes, f"rollback:{reason}"),
        previous_deployment_id=pointer.current.deployment_id,
    )
    pointer.current = restored
    return restored
