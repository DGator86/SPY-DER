"""Structural-state service (master spec §22).

Assembles a deterministic :class:`StructuralState` from a canonical snapshot by
running the GEX, volatility, and RND feature computations. Components that
cannot be produced from a single snapshot are recorded in ``missing_fields``
rather than defaulted (spec §7.5, §22). Fields that require history (velocities,
dynamics, regime probabilities) are out of scope for a single-snapshot pass and
listed as missing.
"""

from __future__ import annotations

from decimal import Decimal

from spy_der.contracts.common import deterministic_id
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.structure import StructuralState
from spy_der.features.gex import compute_oi_gex
from spy_der.features.rnd import compute_rnd
from spy_der.features.volatility import compute_volatility

__all__ = ["StructuralStateService"]

# Single-snapshot pass cannot produce history-dependent structure.
_HISTORY_FIELDS = (
    "gamma_flip_velocity",
    "call_wall_velocity",
    "put_wall_velocity",
    "wall_stability",
    "wall_rupture",
    "pin_score",
    "realized_volatility",
    "regime_probabilities",
)


class StructuralStateService:
    """Compute :class:`StructuralState` from a canonical snapshot."""

    def build(
        self,
        snapshot: CanonicalMarketSnapshot,
        *,
        session_open_price: Decimal | None = None,
    ) -> StructuralState:
        gex = compute_oi_gex(snapshot)
        volatility = compute_volatility(snapshot, session_open_price=session_open_price)
        rnd = compute_rnd(snapshot)

        missing: list[str] = list(_HISTORY_FIELDS)
        if gex is None:
            missing.append("gex_oi")
        if volatility is None:
            missing.append("volatility")
        if rnd is None:
            missing.append("rnd")

        state_id = deterministic_id(
            "struct",
            snapshot.snapshot_id,
            "1.0.0",
        )
        return StructuralState(
            structural_state_id=state_id,
            snapshot_id=snapshot.snapshot_id,
            gex_oi=gex,
            volatility=volatility,
            rnd=rnd,
            missing_fields=tuple(missing),
        )
