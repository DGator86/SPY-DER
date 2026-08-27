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
from spy_der.deployment.evidence import EvidenceMetrics, evaluate_evidence
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


def _significant_evidence():
    return evaluate_evidence(
        EvidenceMetrics(
            paper_sessions=60,
            matured_forecasts=5000,
            primary_15m_samples=750,
            primary_30m_samples=750,
            closed_trades=60,
            verified_data_fraction=0.99,
            pq_ready_fraction=0.90,
            trained_signal_fraction_15m=0.90,
            trained_signal_fraction_30m=0.90,
            direction_accuracy_15m=0.54,
            direction_wilson_lcb_15m=0.50,
            brier_15m=0.24,
            brier_skill_15m=0.02,
            interval_coverage_15m=0.80,
            direction_accuracy_30m=0.53,
            direction_wilson_lcb_30m=0.50,
            brier_30m=0.24,
            brier_skill_30m=0.01,
            interval_coverage_30m=0.80,
            net_pnl=1000.0,
            profit_factor=1.40,
            expectancy_lcb=1.0,
            max_drawdown=500.0,
            doubled_friction_pnl=200.0,
            recent_trade_samples=20,
            recent_net_pnl=100.0,
            recent_profit_factor=1.10,
            pnl_without_top10=50.0,
            ranking_spearman_pnl=0.10,
            ranking_spearman_win=0.05,
            fill_fraction=0.96,
            mean_fill_slippage=10.0,
            maximum_realized_loss_to_model_ratio=1.05,
            tested_regimes=3,
            regime_coverage=0.80,
        )
    )


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
        evidence_report=_significant_evidence(),
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
    assert "evidence=SIGNIFICANT_EDGE_CANDIDATE" in promoted.notes

    restored = rollback_deployment(pointer, reason="expectancy_drop")
    assert restored.deployment_id == current.deployment_id
    assert pointer.current.mode is DeploymentMode.CANDIDATE


def test_champion_promotion_fails_without_significant_evidence() -> None:
    current = _manifest(mode=DeploymentMode.CANDIDATE)
    packet = PromotionReviewPacket(
        review_id="rev-no-evidence",
        model_group_id="group-1",
        model_ids=("dir_v2",),
        artifact_hashes=(("dir_v2", "sha256:abc"),),
        dataset_hashes=(("train", "sha256:ds"),),
        rollback_target=current,
        fold_definitions=(("fold0", "x"),),
    )
    with pytest.raises(DeploymentError, match="evidence_report"):
        promote(
            packet=packet,
            target_mode=DeploymentMode.CHAMPION,
            reviewer="alice",
            approval_note="no evidence",
            current_status="candidate",
        )


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
        evidence_report=_significant_evidence(),
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
    view = build_ops_dashboard(manifest=frozen, drift=drift, notifications=bus.history())
    assert view.frozen
    assert view.drift_level == "rollback"
    assert get_runbook("freeze").steps
    with pytest.raises(DeploymentError, match="frozen"):
        pointer.activate(_manifest(mode=DeploymentMode.CHAMPION, git_commit="other"))


def test_model_registry_still_importable(tmp_path) -> None:
    registry = ModelRegistry(directory=str(tmp_path / "models"))
    assert registry is not None
