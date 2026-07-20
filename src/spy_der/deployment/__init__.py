"""Deployment and operations: manifests, promotion, drift, rollback, cutover."""

from spy_der.deployment.agent_manifest import AGENT_DEPLOYMENT_SCHEMA, AgentDeploymentManifest
from spy_der.deployment.cutover import (
    AgentAuthorityControl,
    ControlledCutover,
    CutoverApproval,
    CutoverPhase,
    CutoverSnapshot,
    CutoverState,
    LiveExecutionGate,
    RuntimeSystem,
    activate_controlled_cutover,
    default_system_a_rollback_manifest,
    default_system_b_primary_manifest,
)
from spy_der.deployment.dashboard import OpsDashboardView, build_ops_dashboard, dashboard_as_dict
from spy_der.deployment.drift import DriftLevel, DriftReport, evaluate_drift
from spy_der.deployment.manifest import (
    DEPLOYMENT_SCHEMA,
    DeploymentError,
    DeploymentManifest,
    DeploymentMode,
    assert_mode_permission,
)
from spy_der.deployment.notifications import Notification, NotificationBus, NotificationLevel
from spy_der.deployment.promotion import PromotionReviewPacket, promote, validate_promotion_packet
from spy_der.deployment.rollback import DeploymentPointer, freeze_deployment, rollback_deployment
from spy_der.deployment.runbooks import RUNBOOKS, Runbook, get_runbook
from spy_der.training.registry import ModelRegistry, RegistryError

__all__ = [
    "AGENT_DEPLOYMENT_SCHEMA",
    "DEPLOYMENT_SCHEMA",
    "RUNBOOKS",
    "AgentAuthorityControl",
    "AgentDeploymentManifest",
    "ControlledCutover",
    "CutoverApproval",
    "CutoverPhase",
    "CutoverSnapshot",
    "CutoverState",
    "DeploymentError",
    "DeploymentManifest",
    "DeploymentMode",
    "DeploymentPointer",
    "DriftLevel",
    "DriftReport",
    "LiveExecutionGate",
    "ModelRegistry",
    "Notification",
    "NotificationBus",
    "NotificationLevel",
    "OpsDashboardView",
    "PromotionReviewPacket",
    "RegistryError",
    "Runbook",
    "RuntimeSystem",
    "activate_controlled_cutover",
    "assert_mode_permission",
    "build_ops_dashboard",
    "dashboard_as_dict",
    "default_system_a_rollback_manifest",
    "default_system_b_primary_manifest",
    "evaluate_drift",
    "freeze_deployment",
    "get_runbook",
    "promote",
    "rollback_deployment",
    "validate_promotion_packet",
]
