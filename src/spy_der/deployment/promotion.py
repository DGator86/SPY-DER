"""Human-gated promotion reviews (no automatic promotion)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from spy_der.contracts.common import content_hash
from spy_der.deployment.manifest import (
    DeploymentError,
    DeploymentManifest,
    DeploymentMode,
    assert_mode_permission,
)

__all__ = [
    "PromotionReviewPacket",
    "promote",
    "validate_promotion_packet",
]


@dataclass(frozen=True, slots=True)
class PromotionReviewPacket:
    review_id: str
    model_group_id: str
    model_ids: tuple[str, ...]
    artifact_hashes: tuple[tuple[str, str], ...]
    dataset_hashes: tuple[tuple[str, str], ...]
    rollback_target: DeploymentManifest
    fold_definitions: tuple[tuple[str, str], ...] = ()
    headline_metrics: tuple[tuple[str, str], ...] = ()
    drift_status: tuple[tuple[str, str], ...] = ()
    known_weaknesses: tuple[str, ...] = ()
    git_commit: str = ""
    reviewer: str | None = None
    approval_status: str = "pending"
    approval_note: str = ""
    review_timestamp: str | None = None

    def packet_hash(self) -> str:
        return content_hash(
            {
                "review_id": self.review_id,
                "model_group_id": self.model_group_id,
                "model_ids": list(self.model_ids),
                "artifact_hashes": list(self.artifact_hashes),
                "dataset_hashes": list(self.dataset_hashes),
                "rollback": self.rollback_target.deployment_id,
                "folds": list(self.fold_definitions),
                "git_commit": self.git_commit,
            }
        )


def validate_promotion_packet(packet: PromotionReviewPacket) -> None:
    if not packet.review_id:
        raise DeploymentError("promotion requires review_id")
    if not packet.model_ids:
        raise DeploymentError("promotion requires model_ids")
    if not packet.artifact_hashes:
        raise DeploymentError("promotion requires artifact_hashes")
    if not packet.dataset_hashes:
        raise DeploymentError("promotion requires dataset_hashes")
    if not packet.rollback_target.deployment_id:
        raise DeploymentError("promotion requires rollback_target")
    if not packet.fold_definitions:
        raise DeploymentError("promotion requires fold_definitions")


def promote(
    *,
    packet: PromotionReviewPacket,
    target_mode: DeploymentMode,
    reviewer: str,
    approval_note: str,
    current_status: str,
    set_status: Callable[[str, str, str], None] | None = None,
) -> DeploymentManifest:
    """Explicit human promotion. Does not retrain or auto-select models."""
    validate_promotion_packet(packet)
    if not reviewer:
        raise DeploymentError("promotion requires reviewer identity")
    if not approval_note:
        raise DeploymentError("promotion requires approval_note")
    if packet.rollback_target.frozen:
        raise DeploymentError("cannot promote while rollback target is frozen")

    if target_mode is DeploymentMode.CHAMPION:
        if current_status == "shadow":
            raise DeploymentError(
                "shadow artifact cannot become champion directly; "
                "promote to candidate first"
            )
        if current_status not in {"candidate", "pending_review", "champion"}:
            raise DeploymentError(
                f"cannot promote status {current_status!r} to champion"
            )
        assert_mode_permission("candidate", "candidate")

    if set_status is not None:
        note = f"review={packet.review_id}; {approval_note}"
        for model_id in packet.model_ids:
            set_status(model_id, target_mode.value, note)

    ts = datetime.now(tz=UTC).isoformat()
    return DeploymentManifest(
        mode=target_mode,
        config_version=packet.rollback_target.config_version,
        model_versions=tuple(
            (mid, dict(packet.artifact_hashes).get(mid, ""))
            for mid in packet.model_ids
        ),
        feature_version=packet.rollback_target.feature_version,
        label_version=packet.rollback_target.label_version,
        risk_version=packet.rollback_target.risk_version,
        policy_version=packet.rollback_target.policy_version,
        execution_version=packet.rollback_target.execution_version,
        fallback_policy=packet.rollback_target.fallback_policy,
        account_id=packet.rollback_target.account_id,
        git_commit=packet.git_commit or packet.rollback_target.git_commit,
        previous_deployment_id=packet.rollback_target.deployment_id,
        notes=(f"promoted_by={reviewer}", f"review={packet.review_id}", f"at={ts}"),
    )
