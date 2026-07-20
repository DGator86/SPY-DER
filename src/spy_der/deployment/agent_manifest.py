"""Agent deployment manifest (spec §58) — independently controlled authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from spy_der.contracts.common import content_hash, require_tz_aware

__all__ = ["AGENT_DEPLOYMENT_SCHEMA", "AgentDeploymentManifest"]

AGENT_DEPLOYMENT_SCHEMA = "agent.deployment.v1"

_DEFAULT_PERMITTED = (
    "SELECT_CANDIDATE",
    "NO_EDGE",
    "ABSTAIN",
    "HOLD",
    "REDUCE",
    "CLOSE",
)


@dataclass(frozen=True, slots=True)
class AgentDeploymentManifest:
    """Independent agent authority control — orthogonal to runtime cutover."""

    deployment_id: str
    provider: str
    model_id: str
    adapter_version: str
    prompt_version: str
    mode: str = "shadow"
    response_schema_version: str = "agent.decision.v1"
    capability_version: str = "1.0.0"
    timeout_seconds: float = 30.0
    maximum_retries: int = 0
    maximum_packet_age_seconds: float = 30.0
    maximum_calls_per_session: int = 10_000
    maximum_cost_per_session: Decimal | None = None
    allowed_account_ids: tuple[str, ...] = ("system_b_grok",)
    permitted_actions: tuple[str, ...] = _DEFAULT_PERMITTED
    rollback_deployment_id: str | None = None
    enabled: bool = True
    approved_by: str | None = None
    approved_at: datetime | None = None
    schema_version: str = AGENT_DEPLOYMENT_SCHEMA

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id is required")
        if not self.provider:
            raise ValueError("provider is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.maximum_retries < 0:
            raise ValueError("maximum_retries cannot be negative")
        if self.approved_at is not None:
            require_tz_aware(self.approved_at, "approved_at")
        for acct in self.allowed_account_ids:
            if not acct.startswith("system_b_") and not acct.startswith("system_a_"):
                raise ValueError(f"unknown account scope: {acct}")

    @property
    def configuration_hash(self) -> str:
        return content_hash(
            {
                "deployment_id": self.deployment_id,
                "provider": self.provider,
                "model_id": self.model_id,
                "adapter_version": self.adapter_version,
                "prompt_version": self.prompt_version,
                "response_schema_version": self.response_schema_version,
                "capability_version": self.capability_version,
                "mode": self.mode,
                "timeout_seconds": self.timeout_seconds,
                "maximum_retries": self.maximum_retries,
                "maximum_packet_age_seconds": self.maximum_packet_age_seconds,
                "maximum_calls_per_session": self.maximum_calls_per_session,
                "maximum_cost_per_session": (
                    str(self.maximum_cost_per_session)
                    if self.maximum_cost_per_session is not None
                    else None
                ),
                "allowed_account_ids": list(self.allowed_account_ids),
                "permitted_actions": list(self.permitted_actions),
                "rollback_deployment_id": self.rollback_deployment_id,
                "enabled": self.enabled,
            }
        )
