"""Phase 17 - Controlled cutover (spec §63).

Owner-approved cutover:
* System B is the primary research/shadow runtime.
* System A is retained as the rollback target.
* Agent authority is independently controlled.
* Live execution remains disabled (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from spy_der.agents.grok import GROK_ADAPTER_VERSION, GrokConfig
from spy_der.agents.prompts import ENTRY_PROMPT_VERSION
from spy_der.contracts.common import content_hash
from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.deployment.agent_manifest import AgentDeploymentManifest
from spy_der.deployment.manifest import (
    DeploymentError,
    DeploymentManifest,
    DeploymentMode,
)
from spy_der.deployment.notifications import NotificationBus, NotificationLevel
from spy_der.deployment.rollback import DeploymentPointer
from spy_der.journal.store import InMemoryJournalStore

__all__ = [
    "AgentAuthorityControl",
    "ControlledCutover",
    "CutoverApproval",
    "CutoverPhase",
    "CutoverSnapshot",
    "CutoverState",
    "LiveExecutionGate",
    "RuntimeSystem",
    "activate_controlled_cutover",
    "default_system_a_rollback_manifest",
    "default_system_b_primary_manifest",
]


class RuntimeSystem(StrEnum):
    SYSTEM_A = "system_a"
    SYSTEM_B = "system_b"


class CutoverPhase(StrEnum):
    PRE_CUTOVER = "pre_cutover"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class CutoverApproval:
    """Explicit repository-owner approval required by spec §63 Phase 17."""

    approved_by: str
    approved_at: datetime
    approval_note: str
    phase: str = "phase-17"

    def __post_init__(self) -> None:
        if not self.approved_by.strip():
            raise DeploymentError("cutover requires approved_by")
        if not self.approval_note.strip():
            raise DeploymentError("cutover requires approval_note")
        if self.approved_at.tzinfo is None:
            raise DeploymentError("approved_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LiveExecutionGate:
    """Live broker / live trading authority gate.

    Phase 17 keeps this permanently disabled. Enabling is a hard error.
    """

    enabled: bool = False
    reason: str = "phase17_live_execution_disabled"

    def __post_init__(self) -> None:
        if self.enabled:
            raise DeploymentError(
                "live execution cannot be enabled in Phase 17 controlled cutover"
            )

    def assert_disabled(self) -> None:
        if self.enabled:
            raise DeploymentError("live execution unexpectedly enabled")

    def attempt_enable(self, *_args: object, **_kwargs: object) -> None:
        raise DeploymentError(
            "live execution enablement is forbidden; Phase 17 keeps live disabled"
        )


@dataclass(frozen=True, slots=True)
class AgentAuthorityControl:
    """Independently toggled AI authority (orthogonal to primary runtime)."""

    enabled: bool
    manifest: AgentDeploymentManifest

    def permits(self, action: str) -> bool:
        if not self.enabled:
            return False
        return action in self.manifest.permitted_actions


@dataclass(frozen=True, slots=True)
class CutoverState:
    phase: CutoverPhase
    primary: RuntimeSystem
    rollback: RuntimeSystem
    primary_manifest: DeploymentManifest
    rollback_manifest: DeploymentManifest
    agent_authority: AgentAuthorityControl
    live_execution: LiveExecutionGate
    approval: CutoverApproval | None
    activated_at: datetime | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.live_execution.assert_disabled()
        if self.phase is CutoverPhase.ACTIVE:
            if self.primary is not RuntimeSystem.SYSTEM_B:
                raise DeploymentError("active cutover primary must be System B")
            if self.rollback is not RuntimeSystem.SYSTEM_A:
                raise DeploymentError("active cutover rollback must be System A")
            if self.approval is None:
                raise DeploymentError("active cutover requires owner approval")

    @property
    def state_hash(self) -> str:
        return content_hash(
            {
                "phase": self.phase.value,
                "primary": self.primary.value,
                "rollback": self.rollback.value,
                "primary_deployment": self.primary_manifest.deployment_id,
                "rollback_deployment": self.rollback_manifest.deployment_id,
                "agent_enabled": self.agent_authority.enabled,
                "agent_hash": self.agent_authority.manifest.configuration_hash,
                "live_enabled": self.live_execution.enabled,
                "approval": (
                    None
                    if self.approval is None
                    else {
                        "by": self.approval.approved_by,
                        "at": self.approval.approved_at.isoformat(),
                        "note": self.approval.approval_note,
                        "phase": self.approval.phase,
                    }
                ),
                "notes": list(self.notes),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "primary": self.primary.value,
            "rollback": self.rollback.value,
            "primary_deployment_id": self.primary_manifest.deployment_id,
            "rollback_deployment_id": self.rollback_manifest.deployment_id,
            "primary_mode": self.primary_manifest.mode.value,
            "agent_authority_enabled": self.agent_authority.enabled,
            "agent_provider": self.agent_authority.manifest.provider,
            "agent_model_id": self.agent_authority.manifest.model_id,
            "live_execution_enabled": self.live_execution.enabled,
            "live_execution_reason": self.live_execution.reason,
            "approved_by": (
                self.approval.approved_by if self.approval is not None else None
            ),
            "approval_note": (
                self.approval.approval_note if self.approval is not None else None
            ),
            "activated_at": (
                self.activated_at.isoformat() if self.activated_at is not None else None
            ),
            "state_hash": self.state_hash,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class CutoverSnapshot:
    """Immutable view for dashboards / parity baselines."""

    state: CutoverState
    system_b_is_primary: bool
    system_a_is_rollback: bool
    agent_independently_controlled: bool
    live_execution_disabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.state.as_dict(),
            "system_b_is_primary": self.system_b_is_primary,
            "system_a_is_rollback": self.system_a_is_rollback,
            "agent_independently_controlled": self.agent_independently_controlled,
            "live_execution_disabled": self.live_execution_disabled,
        }


def default_system_b_primary_manifest(*, git_commit: str = "") -> DeploymentManifest:
    return DeploymentManifest(
        deployment_id="system-b-primary-shadow",
        mode=DeploymentMode.SHADOW,
        config_version="cutover-17",
        model_versions=(("system_b", "primary"),),
        feature_version="features.v1",
        label_version="labels.v1",
        risk_version="risk.v1",
        policy_version="policy.v1",
        execution_version="execution.paper.v1",
        fallback_policy="abstain",
        account_id="system_b_ensemble",
        git_commit=git_commit,
        notes=("phase17_primary", "research_shadow"),
    )


def default_system_a_rollback_manifest(*, git_commit: str = "") -> DeploymentManifest:
    return DeploymentManifest(
        deployment_id="system-a-rollback",
        mode=DeploymentMode.SHADOW,
        config_version="system-a-retained",
        model_versions=(("system_a", "rollback"),),
        feature_version="features.v1",
        label_version="labels.v1",
        risk_version="risk.v1",
        policy_version="policy.v1",
        execution_version="execution.paper.v1",
        fallback_policy="legacy",
        account_id="system_a_legacy",
        git_commit=git_commit,
        notes=("phase17_rollback_target", "system_a_retained"),
    )


@dataclass
class ControlledCutover:
    """Mutable cutover controller with atomic pointer + independent agent gate."""

    state: CutoverState
    pointer: DeploymentPointer
    journal: InMemoryJournalStore = field(default_factory=InMemoryJournalStore)
    notifications: NotificationBus = field(default_factory=NotificationBus)

    def snapshot(self) -> CutoverSnapshot:
        st = self.state
        return CutoverSnapshot(
            state=st,
            system_b_is_primary=st.primary is RuntimeSystem.SYSTEM_B
            and st.phase is CutoverPhase.ACTIVE,
            system_a_is_rollback=st.rollback is RuntimeSystem.SYSTEM_A,
            agent_independently_controlled=True,
            live_execution_disabled=not st.live_execution.enabled,
        )

    def set_agent_authority(
        self,
        *,
        enabled: bool,
        manifest: AgentDeploymentManifest | None = None,
        now: datetime | None = None,
    ) -> CutoverState:
        """Toggle AI authority without changing primary/rollback cutover."""
        now = now or datetime.now(tz=UTC)
        agent_manifest = manifest or replace(
            self.state.agent_authority.manifest, enabled=enabled
        )
        if agent_manifest.enabled != enabled:
            agent_manifest = replace(agent_manifest, enabled=enabled)
        control = AgentAuthorityControl(enabled=enabled, manifest=agent_manifest)
        self.state = replace(
            self.state,
            agent_authority=control,
            notes=(*self.state.notes, f"agent_authority:{'on' if enabled else 'off'}"),
        )
        self.journal.append(
            JournalEvent(
                event_type=JournalEventType.DEPLOYMENT_CHANGED,
                aggregate_type=AggregateType.DEPLOYMENT,
                aggregate_id=self.state.primary_manifest.deployment_id,
                occurred_at=now,
                payload={
                    "kind": "agent_authority",
                    "enabled": enabled,
                    "provider": agent_manifest.provider,
                    "configuration_hash": agent_manifest.configuration_hash,
                },
                deployment_id=self.state.primary_manifest.deployment_id,
            )
        )
        self.notifications.publish(
            level=NotificationLevel.INFO,
            topic="cutover.agent_authority",
            message=f"agent_authority={'enabled' if enabled else 'disabled'}",
        )
        return self.state

    def rollback_to_system_a(
        self, *, reason: str, now: datetime | None = None
    ) -> CutoverState:
        now = now or datetime.now(tz=UTC)
        if not reason.strip():
            raise DeploymentError("rollback requires reason")
        # Restore System A as primary research/shadow; keep System B in history.
        restored = self.pointer.current
        if self.pointer.history:
            restored = self.pointer.history[-1]
        # Prefer explicit System A rollback manifest.
        target = self.state.rollback_manifest
        self.pointer.activate(
            replace(
                target,
                notes=(*target.notes, f"cutover_rollback:{reason}"),
                previous_deployment_id=self.state.primary_manifest.deployment_id,
            )
        )
        self.state = CutoverState(
            phase=CutoverPhase.ROLLED_BACK,
            primary=RuntimeSystem.SYSTEM_A,
            rollback=RuntimeSystem.SYSTEM_B,
            primary_manifest=self.pointer.current,
            rollback_manifest=self.state.primary_manifest,
            agent_authority=self.state.agent_authority,
            live_execution=LiveExecutionGate(),
            approval=self.state.approval,
            activated_at=now,
            notes=(*self.state.notes, f"rolled_back:{reason}", f"was:{restored.deployment_id}"),
        )
        self.journal.append(
            JournalEvent(
                event_type=JournalEventType.DEPLOYMENT_ROLLED_BACK,
                aggregate_type=AggregateType.DEPLOYMENT,
                aggregate_id=self.state.primary_manifest.deployment_id,
                occurred_at=now,
                payload={
                    "kind": "cutover_rollback",
                    "reason": reason,
                    "primary": self.state.primary.value,
                    "rollback": self.state.rollback.value,
                },
                deployment_id=self.state.primary_manifest.deployment_id,
            )
        )
        self.notifications.publish(
            level=NotificationLevel.CRITICAL,
            topic="cutover.rollback",
            message=f"rolled_back_to_system_a:{reason}",
        )
        return self.state

    def assert_live_execution_disabled(self) -> None:
        self.state.live_execution.assert_disabled()


def activate_controlled_cutover(
    *,
    approval: CutoverApproval,
    system_b_manifest: DeploymentManifest | None = None,
    system_a_rollback_manifest: DeploymentManifest | None = None,
    agent_manifest: AgentDeploymentManifest | None = None,
    agent_enabled: bool = True,
    journal: InMemoryJournalStore | None = None,
    notifications: NotificationBus | None = None,
    now: datetime | None = None,
) -> ControlledCutover:
    """Activate Phase 17 cutover after explicit owner approval."""
    now = now or datetime.now(tz=UTC)
    if approval.phase != "phase-17":
        raise DeploymentError("approval.phase must be 'phase-17'")

    primary = system_b_manifest or default_system_b_primary_manifest()
    if primary.mode not in {DeploymentMode.RESEARCH, DeploymentMode.SHADOW}:
        raise DeploymentError(
            "System B primary must be research or shadow (live still disabled)"
        )
    if primary.account_id.startswith("system_a_"):
        raise DeploymentError("System B primary cannot use a System A account_id")

    rollback = system_a_rollback_manifest or default_system_a_rollback_manifest()
    if not rollback.account_id.startswith("system_a_"):
        raise DeploymentError("System A rollback must use a system_a_* account_id")

    agent = agent_manifest or AgentDeploymentManifest(
        deployment_id="agent-grok-cutover-17",
        provider="grok",
        # Derive from the Grok adapter's source of truth so the manifest never
        # drifts behind a model/version bump.
        model_id=GrokConfig().model_id,
        adapter_version=GROK_ADAPTER_VERSION,
        prompt_version=ENTRY_PROMPT_VERSION,
        mode="shadow",
        enabled=agent_enabled,
        approved_by=approval.approved_by,
        approved_at=approval.approved_at,
    )

    # Start with System A as prior pointer, then activate System B.
    pointer = DeploymentPointer(current=rollback)
    pointer.activate(primary)

    state = CutoverState(
        phase=CutoverPhase.ACTIVE,
        primary=RuntimeSystem.SYSTEM_B,
        rollback=RuntimeSystem.SYSTEM_A,
        primary_manifest=primary,
        rollback_manifest=rollback,
        agent_authority=AgentAuthorityControl(enabled=agent_enabled, manifest=agent),
        live_execution=LiveExecutionGate(),
        approval=approval,
        activated_at=now,
        notes=("phase17_activated",),
    )

    ctl = ControlledCutover(
        state=state,
        pointer=pointer,
        journal=journal if journal is not None else InMemoryJournalStore(),
        notifications=notifications if notifications is not None else NotificationBus(),
    )
    ctl.journal.append(
        JournalEvent(
            event_type=JournalEventType.DEPLOYMENT_CHANGED,
            aggregate_type=AggregateType.DEPLOYMENT,
            aggregate_id=primary.deployment_id,
            occurred_at=now,
            payload={
                "kind": "controlled_cutover_activate",
                "primary": RuntimeSystem.SYSTEM_B.value,
                "rollback": RuntimeSystem.SYSTEM_A.value,
                "approved_by": approval.approved_by,
                "approval_note": approval.approval_note,
                "agent_enabled": agent_enabled,
                "live_execution_enabled": False,
                "state_hash": state.state_hash,
            },
            deployment_id=primary.deployment_id,
        )
    )
    ctl.notifications.publish(
        level=NotificationLevel.INFO,
        topic="cutover.activate",
        message="system_b_primary_shadow; system_a_rollback; live_disabled",
    )
    return ctl
