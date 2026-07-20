"""Legacy structural-decision contracts (master spec §23).

The Legacy layer owns current market-structure interpretation: preferred
direction, permitted/prohibited option families, structural confidence, a size
cap, hard vetoes, supporting/contradictory evidence, a regime label, and reason
codes. It does not own forecasting, final geometry, risk approval, or execution
(spec §23). Operational restrictions (immutable) are kept separate from
empirical structural hypotheses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from spy_der.contracts.common import SCHEMA_VERSION, require_probability

__all__ = [
    "DirectionPreference",
    "EvidenceRef",
    "HardVeto",
    "LegacyDecisionView",
    "VetoCategory",
    "VetoCode",
]


class DirectionPreference(StrEnum):
    CALL_BIASED = "CALL_BIASED"
    PUT_BIASED = "PUT_BIASED"
    NEUTRAL = "NEUTRAL"
    NONE = "NONE"


class VetoCategory(StrEnum):
    """Operational restrictions are immutable; structural are hypotheses (§23)."""

    OPERATIONAL = "OPERATIONAL"
    STRUCTURAL = "STRUCTURAL"


class VetoCode(StrEnum):
    # Operational (immutable) — spec §23 operational-restriction list.
    STALE_DATA = "STALE_DATA"
    MISSING_CHAIN = "MISSING_CHAIN"
    INVALID_SURFACE = "INVALID_SURFACE"
    CATALYST_LOCKOUT = "CATALYST_LOCKOUT"
    SESSION_CLOSED = "SESSION_CLOSED"
    ENTRY_CUTOFF = "ENTRY_CUTOFF"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    SYSTEM_UNAVAILABLE = "SYSTEM_UNAVAILABLE"
    # Structural (empirical hypotheses).
    SHORT_GAMMA_REGIME = "SHORT_GAMMA_REGIME"


@dataclass(frozen=True, slots=True)
class HardVeto:
    code: VetoCode
    category: VetoCategory
    reason: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LegacyDecisionView:
    """Legacy structural interpretation for one snapshot (spec §23)."""

    view_id: str
    snapshot_id: str
    structural_state_id: str
    legacy_version: str = "1.0.0"
    schema_version: str = SCHEMA_VERSION
    preferred_direction: DirectionPreference = DirectionPreference.NONE
    permitted_families: tuple[str, ...] = ()
    prohibited_families: tuple[str, ...] = ()
    structural_confidence: float = 0.0
    size_cap: float = 0.0
    hard_vetoes: tuple[HardVeto, ...] = ()
    supporting_evidence: tuple[EvidenceRef, ...] = ()
    contradictory_evidence: tuple[EvidenceRef, ...] = ()
    regime_label: str | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        require_probability(self.structural_confidence, "structural_confidence")
        require_probability(self.size_cap, "size_cap")

    @property
    def is_tradeable(self) -> bool:
        """No hard veto blocks entry (spec §23: vetoes are non-bypassable)."""
        return not self.hard_vetoes and bool(self.permitted_families)

    @property
    def operational_vetoes(self) -> tuple[HardVeto, ...]:
        return tuple(v for v in self.hard_vetoes if v.category is VetoCategory.OPERATIONAL)
