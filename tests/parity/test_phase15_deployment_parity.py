"""Parity golden for Phase 15 deployment ops dashboard view."""

from __future__ import annotations

import json
from pathlib import Path

from spy_der.contracts import to_canonical_json
from spy_der.deployment import (
    DeploymentManifest,
    DeploymentMode,
    NotificationBus,
    NotificationLevel,
    build_ops_dashboard,
    dashboard_as_dict,
    evaluate_drift,
)

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase15" / "ops_dashboard.json"


def _artifact() -> dict[str, object]:
    manifest = DeploymentManifest(
        deployment_id="deploy-phase15-parity",
        mode=DeploymentMode.CANDIDATE,
        config_version="cfg-parity",
        model_versions=(("direction_v2", "sha256:dir"), ("value_v3", "sha256:val")),
        feature_version="features.v1",
        label_version="labels.v1",
        risk_version="risk.v1",
        policy_version="policy.v1",
        execution_version="execution.v1",
        git_commit="phase15parity",
        account_id="system_b_ensemble",
    )
    drift = evaluate_drift(psi=0.05, brier_skill=0.02, expectancy_delta=0.1)
    bus = NotificationBus()
    bus.publish(
        level=NotificationLevel.INFO,
        topic="deployment.promote",
        message="candidate active",
    )
    view = build_ops_dashboard(
        manifest=manifest,
        drift=drift,
        notifications=bus.history(),
    )
    return dashboard_as_dict(view)


def test_phase15_deployment_parity() -> None:
    _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(to_canonical_json(_artifact()))
    # Strip volatile notification timestamps by using message-only board fields.
    if not _EXPECTED.exists():
        _EXPECTED.write_text(to_canonical_json(artifact) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert artifact == expected
