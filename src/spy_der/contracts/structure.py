"""Structural-state contracts (master spec §18, §22).

Immutable, typed structural evidence derived from a canonical snapshot: GEX
levels (net GEX, gamma flip, call/put walls, concentration), volatility summary
(ATM straddle, expected move), and a risk-neutral-density summary. Fields that
require history (velocities, dynamics, regime) are recorded as missing here and
filled by later phases — never silently defaulted (spec §7.5, §22).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from spy_der.contracts.common import SCHEMA_VERSION, require_finite

__all__ = ["GexLevels", "RndSummary", "StructuralState", "VolatilitySummary"]


@dataclass(frozen=True, slots=True)
class GexLevels:
    """OI-based gamma-exposure levels for one snapshot (spec §18)."""

    net_gex_bn: float
    net_ratio: float
    gamma_flip: Decimal
    call_wall: Decimal
    put_wall: Decimal
    gex_concentration: float
    wall_concentration: float
    n_contracts: int
    n_strikes: int

    def __post_init__(self) -> None:
        require_finite(self.net_gex_bn, "GexLevels.net_gex_bn")
        require_finite(self.net_ratio, "GexLevels.net_ratio")
        require_finite(self.gex_concentration, "GexLevels.gex_concentration")
        require_finite(self.wall_concentration, "GexLevels.wall_concentration")

    @property
    def gamma_sign(self) -> int:
        """+1 dealers long gamma (pinning), -1 short gamma (trending), 0 flat."""
        if self.net_gex_bn > 0:
            return 1
        if self.net_gex_bn < 0:
            return -1
        return 0


@dataclass(frozen=True, slots=True)
class VolatilitySummary:
    """ATM straddle / expected-move summary (spec §19)."""

    atm_strike: Decimal
    atm_straddle: Decimal
    expected_move: Decimal
    expected_move_pct: float
    expected_move_consumed: float | None = None

    def __post_init__(self) -> None:
        require_finite(self.expected_move_pct, "VolatilitySummary.expected_move_pct")
        if self.expected_move_consumed is not None:
            require_finite(
                self.expected_move_consumed,
                "VolatilitySummary.expected_move_consumed",
            )


@dataclass(frozen=True, slots=True)
class RndSummary:
    """Risk-neutral terminal density summary (spec §17), bounded first cut."""

    forward: float
    mean: float
    std: float
    skew: float
    prob_below_spot: float
    n_strikes: int
    normalized: bool

    def __post_init__(self) -> None:
        for name in ("forward", "mean", "std", "skew", "prob_below_spot"):
            require_finite(getattr(self, name), f"RndSummary.{name}")


@dataclass(frozen=True, slots=True)
class StructuralState:
    """Canonical structural state for one snapshot (spec §22)."""

    structural_state_id: str
    snapshot_id: str
    structural_state_version: str = "1.0.0"
    schema_version: str = SCHEMA_VERSION
    gex_oi: GexLevels | None = None
    volatility: VolatilitySummary | None = None
    rnd: RndSummary | None = None
    regime_evidence: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
