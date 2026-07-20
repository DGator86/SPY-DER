"""Canonical candidate contracts (master spec §7.1, §31, §32).

Identity depends only on normalized geometry and versioning — never on policy,
model score, AI, expected value, or account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from spy_der.contracts.common import SCHEMA_VERSION, content_hash, deterministic_id
from spy_der.contracts.market import OptionType

__all__ = [
    "CANDIDATE_SCHEMA",
    "FACTORY_VERSION",
    "Candidate",
    "CandidateDirection",
    "CandidateFamily",
    "CandidateLeg",
    "CandidateUniverse",
    "DebitCredit",
    "geometry_hash",
    "make_candidate_id",
    "normalize_legs",
    "terminal_payoff_hash",
]

CANDIDATE_SCHEMA = "candidate.v1"
FACTORY_VERSION = "candidate-factory.v1"


class CandidateFamily(StrEnum):
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    CALL_DEBIT_SPREAD = "call_debit_spread"
    PUT_DEBIT_SPREAD = "put_debit_spread"
    BULL_PUT_CREDIT_SPREAD = "bull_put_credit_spread"
    BEAR_CALL_CREDIT_SPREAD = "bear_call_credit_spread"
    IRON_CONDOR = "iron_condor"
    IRON_BUTTERFLY = "iron_butterfly"
    BOUNDED_BROKEN_WING_BUTTERFLY = "bounded_broken_wing_butterfly"
    LONG_STRADDLE = "long_straddle"
    LONG_STRANGLE = "long_strangle"
    BOUNDED_BACKSPREAD_CALL = "bounded_backspread_call"
    BOUNDED_BACKSPREAD_PUT = "bounded_backspread_put"


class DebitCredit(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"
    EVEN = "even"


class CandidateDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    LONG_VOL = "long_vol"
    SHORT_VOL = "short_vol"


@dataclass(frozen=True, slots=True, order=True)
class CandidateLeg:
    """Normalized option leg. Quantity >0 is long, <0 is short."""

    option_type: OptionType
    strike: Decimal
    quantity: int
    expiration: date
    contract_id: str = ""
    multiplier: int = 100

    def __post_init__(self) -> None:
        if self.strike <= 0:
            msg = "strike must be positive"
            raise ValueError(msg)
        if self.quantity == 0:
            msg = "quantity cannot be zero"
            raise ValueError(msg)
        if self.multiplier <= 0:
            msg = "multiplier must be positive"
            raise ValueError(msg)

    def to_canonical(self) -> dict[str, object]:
        return {
            "option_type": self.option_type.value,
            "strike": str(self.strike),
            "quantity": self.quantity,
            "expiration": self.expiration.isoformat(),
            "contract_id": self.contract_id,
            "multiplier": self.multiplier,
        }


def normalize_legs(legs: tuple[CandidateLeg, ...] | list[CandidateLeg]) -> tuple[CandidateLeg, ...]:
    """Sort legs into a stable canonical order."""
    return tuple(
        sorted(
            legs,
            key=lambda leg: (
                leg.expiration.isoformat(),
                leg.option_type.value,
                str(leg.strike),
                leg.quantity,
                leg.contract_id,
                leg.multiplier,
            ),
        )
    )


def geometry_hash(
    *,
    family: str,
    expiration: date,
    legs: tuple[CandidateLeg, ...],
    factory_version: str = FACTORY_VERSION,
) -> str:
    """Hash of normalized geometry only (no quotes, scores, or snapshot)."""
    payload = {
        "factory_version": factory_version,
        "family": family,
        "expiration": expiration.isoformat(),
        "legs": [leg.to_canonical() for leg in normalize_legs(legs)],
    }
    return content_hash(payload)


def terminal_payoff_hash(
    *,
    entry_credit: Decimal,
    evaluation_spots: tuple[Decimal, ...],
    payoffs: tuple[Decimal, ...],
) -> str:
    payload = {
        "entry_credit": str(entry_credit),
        "spots": [str(s) for s in evaluation_spots],
        "payoffs": [str(p) for p in payoffs],
    }
    return content_hash(payload)


def make_candidate_id(
    *,
    snapshot_id: str,
    factory_version: str,
    geometry: str,
) -> str:
    """Stable per-snapshot candidate ID from geometry."""
    return deterministic_id("cand", snapshot_id, factory_version, geometry)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Immutable approved-family candidate with proven maximum loss (spec §31)."""

    candidate_id: str
    snapshot_id: str
    family: str
    direction: str
    expiration: date
    legs: tuple[CandidateLeg, ...]
    entry_type: DebitCredit
    maximum_profit: Decimal | None
    maximum_loss: Decimal
    breakevens: tuple[Decimal, ...]
    capital_required: Decimal
    terminal_payoff_hash: str
    geometry_hash: str
    quote_snapshot_refs: tuple[str, ...] = ()
    schema_version: str = CANDIDATE_SCHEMA
    factory_version: str = FACTORY_VERSION
    entry_credit: Decimal = Decimal("0")
    quote_quality: Decimal = Decimal("0")
    requires_stock_ownership: bool = False

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if not self.snapshot_id:
            raise ValueError("candidate_id snapshot_id is required")
        if not self.family:
            raise ValueError("family is required")
        if not self.legs:
            raise ValueError("at least one option leg is required")
        if self.maximum_loss is None:
            raise ValueError("candidate max_loss must be defined")
        if self.maximum_loss < Decimal("0"):
            raise ValueError("candidate max_loss must be non-negative")
        if self.requires_stock_ownership:
            raise ValueError("stock-dependent candidates are not allowed")
        expirations = {leg.expiration for leg in self.legs}
        if len(expirations) != 1:
            raise ValueError("all candidate legs must share one expiration")
        if self.expiration not in expirations:
            raise ValueError("candidate expiration must match legs")
        object.__setattr__(self, "legs", normalize_legs(self.legs))

    @property
    def max_loss(self) -> Decimal:
        """Compatibility alias used by synthesis/risk (Phase 0 stubs)."""
        return self.maximum_loss


@dataclass(frozen=True, slots=True)
class CandidateUniverse:
    schema_version: str = SCHEMA_VERSION
    universe_id: str = ""
    snapshot_id: str = ""
    factory_version: str = FACTORY_VERSION
    candidates: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not self.universe_id and self.snapshot_id:
            uid = deterministic_id(
                "univ",
                self.snapshot_id,
                self.factory_version,
                [c.candidate_id for c in self.candidates],
            )
            object.__setattr__(self, "universe_id", uid)
