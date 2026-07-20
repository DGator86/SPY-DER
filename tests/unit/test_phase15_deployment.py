"""Phase 15 — deployment manifests, promotion, drift, freeze, rollback, ops."""

from __future__ import annotations

import pytest

from spy_der.deployment import (
    DeploymentManifest,
    DeploymentMode,
    DeploymentPointer,
    DriftLevel,
    NotificationBus,
    NotificationLevel,
    PromotionReviewPacket,
    assert_mode_permission,
    build_ops_dashboard,
    evaluate_drift,
    freeze_deployment,
    get_runbook,
    promote,
    rollback_deployment,
)
from spy_der.deployment.manifest import DeploymentError
from spy_der.training.registry import ModelRegistry


def _manifest(**kwargs: object) -> DeploymentManifest:
    base = dict(
        mode=DeploymentMode.SHADOW,
        config_version="cfg-1",
        model_versions=(("dir_v2", "sha256:abc"),),
        feature_version="features.v1",
        label_version="labels.v1",
        risk_version="risk.v1",
        policy_version="policy.v1",
        execution_version="execution.v1",
        git_commit="deadbeef",
    )
    base.update(kwargs)
    return DeploymentManifest(**base)  # type: ignore[arg-type]


def test_mode_permissions_fail_closed() -> None:
    assert_mode_permission("champion", "champion")
    with pytest.raises(DeploymentError):
        assert_mode_permission("research", "champion")


def test_human_promotion_and_rollback() -> None:
    current = _manifest(mode=DeploymentMode.CANDIDATE)
    pointer = DeploymentPointer(current=current)
    packet = PromotionReviewPacket(
        review_id="rev-1",
        model_group_id="group-1",
        model_ids=("dir_v2",),
        artifact_hashes=(("dir_v2", "sha256:abc"),),
        dataset_hashes=(("train", "sha256:ds"),),
        rollback_target=current,
        fold_definitions=(("fold0", "2026-01-01:2026-01-31"),),
        git_commit="cafebabe",
    )
    statuses: list[tuple[str, str, str]] = []

    def _set(mid: str, status: str, note: str) -> None:
        statuses.append((mid, status, note))

    promoted = promote(
        packet=packet,
        target_mode=DeploymentMode.CHAMPION,
        reviewer="alice",
        approval_note="metrics acceptable",
        current_status="candidate",
        set_status=_set,
    )
    pointer.activate(promoted)
    assert pointer.current.mode is DeploymentMode.CHAMPION
    assert statuses == [("dir_v2", "champion", "review=rev-1; metrics acceptable")]

    restored = rollback_deployment(pointer, reason="expectancy_drop")
    assert restored.deployment_id == current.deployment_id
    assert pointer.current.mode is DeploymentMode.CANDIDATE


def test_shadow_cannot_promote_directly_to_champion() -> None:
    current = _manifest()
    packet = PromotionReviewPacket(
        review_id="rev-2",
        model_group_id="g",
        model_ids=("m1",),
        artifact_hashes=(("m1", "h"),),
        dataset_hashes=(("d", "h"),),
        rollback_target=current,
        fold_definitions=(("f", "x"),),
    )
    with pytest.raises(DeploymentError, match="shadow"):
        promote(
            packet=packet,
            target_mode=DeploymentMode.CHAMPION,
            reviewer="bob",
            approval_note="nope",
            current_status="shadow",
        )


def test_drift_freeze_and_notifications() -> None:
    pointer = DeploymentPointer(current=_manifest())
    drift = evaluate_drift(psi=0.3, brier_skill=-0.2, expectancy_delta=-0.8)
    assert drift.level is DriftLevel.ROLLBACK
    frozen = freeze_deployment(pointer, reason=",".join(drift.reasons))
    assert frozen.frozen
    bus = NotificationBus()
    bus.publish(
        level=NotificationLevel.CRITICAL,
        topic="deployment.freeze",
        message="frozen on drift",
        payload=(("level", drift.level.value),),
    )
    view = build_ops_dashboard(
        manifest=frozen,
        drift=drift,
        notifications=bus.history(),
    )
    assert view.frozen
    assert view.drift_level == "rollback"
    assert get_runbook("freeze").steps
    with pytest.raises(DeploymentError, match="frozen"):
        pointer.activate(_manifest(mode=DeploymentMode.CHAMPION, git_commit="other"))


def test_model_registry_still_importable(tmp_path) -> None:
    registry = ModelRegistry(directory=str(tmp_path / "models"))
    assert registry is not None
