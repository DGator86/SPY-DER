"""Policy adapter contracts (master spec §36)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from spy_der.contracts.common import SCHEMA_VERSION, require_probability

__all__ = [
    "PolicyAction",
    "PolicyDecisionView",
    "PolicyDisagreement",
    "PolicyIdentity",
    "PolicyInputPacket",
    "PolicyMode",
]


class PolicyAction(StrEnum):
    SELECT_CANDIDATE = "SELECT_CANDIDATE"
    NO_EDGE = "NO_EDGE"
    ABSTAIN = "ABSTAIN"


class PolicyMode(StrEnum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    CHAMPION = "champion"


@dataclass(frozen=True, slots=True)
class PolicyIdentity:
    name: str
    version: str
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PolicyInputPacket:
    """Read-only inputs shared by every policy (spec §36)."""

    snapshot_id: str
    schema_version: str = SCHEMA_VERSION
    # Opaque typed objects are attached by callers; policies must not mutate them.
    legacy_view: object | None = None
    market_forecast: object | None = None
    candidate_universe: object | None = None
    economics: tuple[object, ...] = ()
    value_forecasts: tuple[object, ...] = ()
    ranking: object | None = None
    risk_envelope: object | None = None
    meta_decision: object | None = None
    required_inputs_present: bool = True


@dataclass(frozen=True, slots=True)
class PolicyDecisionView:
    policy_name: str
    policy_version: str
    action: PolicyAction
    candidate_id: str | None = None
    size_cap: float = 0.0
    confidence: float = 0.0
    uncertainty: float = 1.0
    supporting_evidence: tuple[str, ...] = ()
    contradictory_evidence: tuple[str, ...] = ()
    hard_vetoes: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_probability(self.size_cap, "size_cap")
        require_probability(self.confidence, "confidence")
        require_probability(self.uncertainty, "uncertainty")
        if self.action is PolicyAction.SELECT_CANDIDATE and not self.candidate_id:
            raise ValueError("SELECT_CANDIDATE requires candidate_id")


@dataclass(frozen=True, slots=True)
class PolicyDisagreement:
    """Cross-policy disagreement summary (deterministic)."""

    disagree: bool
    action_conflict: bool
    candidate_conflict: bool
    agreeing_policies: tuple[str, ...] = ()
    disagreeing_policies: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    diagnostics: tuple[tuple[str, str], ...] = field(default_factory=tuple)
