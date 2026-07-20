"""Provider-neutral AI agent contracts (master spec §37-§45)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from spy_der.contracts.common import (
    content_hash,
    deterministic_id,
    require_probability,
    require_tz_aware,
)
from spy_der.contracts.policies import PolicyDecisionView, PolicyDisagreement

__all__ = [
    "AGENT_DECISION_SCHEMA",
    "AGENT_PACKET_SCHEMA",
    "AgentCandidateView",
    "AgentCapabilities",
    "AgentDecisionPacket",
    "AgentDecisionResponse",
    "AgentEntryAction",
    "AgentHealth",
    "AgentIdentity",
    "DeploymentContext",
    "ExitPolicySummary",
    "ReadOnlyLegSummary",
    "SnapshotSummary",
    "packet_hash",
]

AGENT_PACKET_SCHEMA = "agent.packet.v1"
AGENT_DECISION_SCHEMA = "agent.decision.v1"


class AgentEntryAction(StrEnum):
    SELECT_CANDIDATE = "SELECT_CANDIDATE"
    NO_EDGE = "NO_EDGE"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    provider: str
    model_id: str
    adapter_version: str
    prompt_version: str
    response_schema_version: str = AGENT_DECISION_SCHEMA
    capability_version: str = "1.0.0"


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    supports_entry_decisions: bool = True
    supports_position_decisions: bool = False
    supports_structured_output: bool = True
    supports_deterministic_seed: bool = False
    supports_response_ids: bool = False
    supports_usage_reporting: bool = False
    maximum_context_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AgentHealth:
    healthy: bool
    detail: str = ""
    checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    snapshot_id: str
    symbol: str
    session_date: date
    underlying_price: Decimal
    minutes_to_close: int | None = None


@dataclass(frozen=True, slots=True)
class ReadOnlyLegSummary:
    option_type: str
    strike: Decimal
    quantity: int
    expiration: date


@dataclass(frozen=True, slots=True)
class ExitPolicySummary:
    exit_policy_id: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class DeploymentContext:
    deployment_id: str = ""
    mode: str = "shadow"
    configuration_hash: str = ""


@dataclass(frozen=True, slots=True)
class AgentCandidateView:
    """Read-only candidate view. Agent selects candidate_id only (spec §42)."""

    candidate_id: str
    family: str
    direction: str
    expiration: date
    legs_summary: tuple[ReadOnlyLegSummary, ...]
    maximum_loss: Decimal
    capital_required: Decimal
    geometry_hash: str
    maximum_profit: Decimal | None = None
    breakevens: tuple[Decimal, ...] = ()
    mid_price: Decimal | None = None
    natural_price: Decimal | None = None
    expected_fill_price: Decimal | None = None
    conservative_fill_price: Decimal | None = None
    fill_probability: float = 0.0
    estimated_fees: Decimal = Decimal("0")
    estimated_slippage: Decimal = Decimal("0")
    executable_expected_pnl: Decimal | None = None
    probability_positive_utility: float | None = None
    expected_shortfall: Decimal | None = None
    candidate_utility: float | None = None
    v3_rank: int | None = None
    expected_regret: float | None = None
    liquidity_status: str = "unknown"
    uncertainty: float = 0.5
    evidence_ids: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    hard_vetoed: bool = False

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        require_probability(self.fill_probability, "fill_probability")
        require_probability(self.uncertainty, "uncertainty")
        if self.probability_positive_utility is not None:
            require_probability(
                self.probability_positive_utility, "probability_positive_utility"
            )


@dataclass(frozen=True, slots=True)
class AgentDecisionPacket:
    """Processed-output packet for AI entry decisions (spec §41).

    Must never contain credentials, tools, broker functions, or candidate
    constructors.
    """

    packet_id: str
    packet_hash: str
    created_at: datetime
    expires_at: datetime
    snapshot_summary: SnapshotSummary
    candidates: tuple[AgentCandidateView, ...]
    risk_max_size_scalar: float
    hard_vetoes: tuple[str, ...] = ()
    policy_views: tuple[PolicyDecisionView, ...] = ()
    policy_disagreement: PolicyDisagreement | None = None
    approved_exit_policies: tuple[ExitPolicySummary, ...] = ()
    deployment_context: DeploymentContext = field(default_factory=DeploymentContext)
    data_quality: float = 1.0
    forecast_uncertainty: float = 0.0
    schema_version: str = AGENT_PACKET_SCHEMA
    # Explicit allowlist of evidence IDs referenced by candidates/policies.
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.packet_id:
            raise ValueError("packet_id is required")
        require_tz_aware(self.created_at, "created_at")
        require_tz_aware(self.expires_at, "expires_at")
        require_probability(self.risk_max_size_scalar, "risk_max_size_scalar")
        require_probability(self.data_quality, "data_quality")
        require_probability(self.forecast_uncertainty, "forecast_uncertainty")
        ids = [c.candidate_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate IDs must be unique within a packet")
        object.__setattr__(self, "candidates", tuple(self.candidates))

    def candidate(self, candidate_id: str) -> AgentCandidateView | None:
        return next((c for c in self.candidates if c.candidate_id == candidate_id), None)


@dataclass(frozen=True, slots=True)
class AgentDecisionResponse:
    packet_id: str
    packet_hash: str
    action: AgentEntryAction
    candidate_id: str | None = None
    size_scalar: float = 0.0
    exit_policy_id: str | None = None
    confidence: float = 0.0
    uncertainty: float = 1.0
    supporting_evidence_ids: tuple[str, ...] = ()
    contradictory_evidence_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    rationale: str = ""
    schema_version: str = AGENT_DECISION_SCHEMA
    model_id: str = ""
    prompt_version: str = ""
    geometry_hash: str | None = None

    def __post_init__(self) -> None:
        require_probability(self.size_scalar, "size_scalar")
        require_probability(self.confidence, "confidence")
        require_probability(self.uncertainty, "uncertainty")
        if self.action is AgentEntryAction.SELECT_CANDIDATE and not self.candidate_id:
            raise ValueError("SELECT_CANDIDATE requires candidate_id")
        if self.action is not AgentEntryAction.SELECT_CANDIDATE and self.candidate_id is not None:
            raise ValueError("candidate_id is only valid for SELECT_CANDIDATE")


def packet_hash(payload: dict[str, object]) -> str:
    """Content hash of the packet body (excludes packet_id/hash themselves)."""
    return content_hash(payload)


def make_packet_id(snapshot_id: str, *parts: object) -> str:
    return deterministic_id("apkt", snapshot_id, *parts)
