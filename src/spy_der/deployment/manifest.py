"""Deployment manifests and mode permissions (spec §63 Phase 15)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spy_der.contracts.common import content_hash

__all__ = [
    "DEPLOYMENT_SCHEMA",
    "DeploymentError",
    "DeploymentManifest",
    "DeploymentMode",
    "assert_mode_permission",
]

DEPLOYMENT_SCHEMA = "deployment.manifest.v1"


class DeploymentError(RuntimeError):
    """Fail-closed deployment / promotion / rollback error."""


class DeploymentMode(StrEnum):
    RESEARCH = "research"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    CANDIDATE = "candidate"
    CHAMPION = "champion"
    FROZEN = "frozen"


_MODE_PERMISSIONS: dict[str, frozenset[str]] = {
    "research": frozenset({"research"}),
    "shadow": frozenset({"research", "shadow"}),
    "advisory": frozenset({"research", "shadow", "advisory"}),
    "candidate": frozenset({"research", "shadow", "advisory", "candidate"}),
    "pending_review": frozenset({"research", "shadow", "advisory", "candidate"}),
    "champion": frozenset(
        {"research", "shadow", "advisory", "candidate", "champion"}
    ),
    "rejected": frozenset({"research"}),
    "archived": frozenset({"research"}),
    "frozen": frozenset({"research", "shadow"}),
}


def assert_mode_permission(artifact_status: str, mode: str) -> None:
    allowed = _MODE_PERMISSIONS.get(artifact_status, frozenset())
    if mode not in allowed:
        raise DeploymentError(
            f"artifact status {artifact_status!r} cannot serve mode {mode!r}"
        )


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    """Pinned deployment configuration (human-promoted pointer)."""

    schema_version: str = DEPLOYMENT_SCHEMA
    deployment_id: str = ""
    mode: DeploymentMode = DeploymentMode.SHADOW
    config_version: str = ""
    model_versions: tuple[tuple[str, str], ...] = ()
    feature_version: str = ""
    label_version: str = ""
    risk_version: str = ""
    policy_version: str = ""
    execution_version: str = ""
    fallback_policy: str = "abstain"
    account_id: str = "system_b_ensemble"
    git_commit: str = ""
    frozen: bool = False
    notes: tuple[str, ...] = ()
    previous_deployment_id: str | None = None

    def __post_init__(self) -> None:
        if not self.deployment_id:
            object.__setattr__(
                self,
                "deployment_id",
                content_hash(
                    {
                        "mode": self.mode.value,
                        "config": self.config_version,
                        "models": list(self.model_versions),
                        "commit": self.git_commit,
                    }
                ),
            )
        if self.fallback_policy not in {"abstain", "legacy", "no_trade"}:
            raise DeploymentError(
                f"invalid fallback_policy: {self.fallback_policy}"
            )

    @property
    def configuration_hash(self) -> str:
        return content_hash(
            {
                "deployment_id": self.deployment_id,
                "mode": self.mode.value,
                "config_version": self.config_version,
                "model_versions": list(self.model_versions),
                "feature_version": self.feature_version,
                "label_version": self.label_version,
                "risk_version": self.risk_version,
                "policy_version": self.policy_version,
                "execution_version": self.execution_version,
                "fallback_policy": self.fallback_policy,
                "account_id": self.account_id,
                "git_commit": self.git_commit,
                "frozen": self.frozen,
            }
        )
