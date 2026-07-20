"""Legacy structural analyzer (master spec §23).

Interprets the Phase 3 structural state into a :class:`LegacyDecisionView`:
gamma regime, preferred direction, permitted/prohibited option families,
structural confidence, a size cap, hard vetoes, evidence, and reason codes.

Gamma regime drives family permission, mirroring System A gate logic
(``gate_scorer.py`` / ``decision_matrix.py``, 0DTE @ de4a6e7): long gamma is a
pinning / premium-selling regime; short gamma is a trending / directional-debit
regime and vetoes premium selling structurally. The analyzer explains and
constrains only — it never sizes final risk or builds geometry (spec §23).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spy_der.contracts.common import deterministic_id
from spy_der.contracts.legacy import (
    DirectionPreference,
    EvidenceRef,
    HardVeto,
    LegacyDecisionView,
    VetoCategory,
    VetoCode,
)
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.structure import StructuralState
from spy_der.legacy.permissions import (
    LegacyPermissionConfig,
    evaluate_operational_vetoes,
)

__all__ = ["LegacyAnalyzer", "LegacyConfig"]

# Approved option families (spec §31), grouped by structural regime.
_PREMIUM_FAMILIES = (
    "bull_put_credit_spread",
    "bear_call_credit_spread",
    "iron_condor",
    "iron_butterfly",
)
_DEBIT_FAMILIES = (
    "call_debit_spread",
    "put_debit_spread",
    "long_call",
    "long_put",
)


@dataclass(frozen=True, slots=True)
class LegacyConfig:
    permissions: LegacyPermissionConfig = field(default_factory=LegacyPermissionConfig)
    # |net GEX| ($bn) below which the regime is treated as a flip transition.
    flip_transition_band_bn: float = 0.05


class LegacyAnalyzer:
    """Produce a :class:`LegacyDecisionView` from a snapshot + structural state."""

    def __init__(self, config: LegacyConfig | None = None) -> None:
        self.config = config or LegacyConfig()

    def analyze(
        self,
        snapshot: CanonicalMarketSnapshot,
        structural_state: StructuralState,
    ) -> LegacyDecisionView:
        vetoes = list(evaluate_operational_vetoes(snapshot, self.config.permissions))
        supporting: list[EvidenceRef] = []
        contradictory: list[EvidenceRef] = []
        reason_codes: list[str] = []

        direction = DirectionPreference.NONE
        permitted: tuple[str, ...] = ()
        prohibited: tuple[str, ...] = ()
        confidence = 0.0
        regime_label: str | None = None

        gex = structural_state.gex_oi
        if gex is not None:
            regime_label, direction, permitted, prohibited, structural_vetoes = self._regime(
                gex.gamma_sign, gex.net_gex_bn, snapshot, structural_state
            )
            vetoes.extend(structural_vetoes)
            confidence = self._confidence(gex.net_ratio, gex.gex_concentration)
            reason_codes.append(f"regime:{regime_label}")
            supporting.append(
                EvidenceRef("net_gex_bn", f"{gex.net_gex_bn:.4f}")
            )
            supporting.append(EvidenceRef("gamma_flip", str(gex.gamma_flip)))
            if gex.gamma_sign < 0:
                contradictory.append(EvidenceRef("short_gamma", "premium selling vetoed"))
        else:
            reason_codes.append("no_structural_gex")

        size_cap = 0.0 if vetoes else min(confidence, 1.0)
        if vetoes:
            reason_codes.append(f"vetoed:{len(vetoes)}")

        view_id = deterministic_id(
            "legacy",
            structural_state.structural_state_id,
            "1.0.0",
        )
        return LegacyDecisionView(
            view_id=view_id,
            snapshot_id=snapshot.snapshot_id,
            structural_state_id=structural_state.structural_state_id,
            preferred_direction=direction,
            permitted_families=permitted,
            prohibited_families=prohibited,
            structural_confidence=confidence,
            size_cap=size_cap,
            hard_vetoes=tuple(vetoes),
            supporting_evidence=tuple(supporting),
            contradictory_evidence=tuple(contradictory),
            regime_label=regime_label,
            reason_codes=tuple(reason_codes),
        )

    def _regime(
        self,
        gamma_sign: int,
        net_gex_bn: float,
        snapshot: CanonicalMarketSnapshot,
        structural_state: StructuralState,
    ) -> tuple[str, DirectionPreference, tuple[str, ...], tuple[str, ...], list[HardVeto]]:
        if abs(net_gex_bn) < self.config.flip_transition_band_bn:
            # Flip transition: only defined-risk debits, no directional bias.
            return "flip_transition", DirectionPreference.NEUTRAL, _DEBIT_FAMILIES, (), []
        if gamma_sign > 0:
            # Long gamma: pinning / premium-selling regime.
            return "long_gamma_pin", DirectionPreference.NEUTRAL, _PREMIUM_FAMILIES, (), []
        # Short gamma: trending regime; premium selling is structurally vetoed.
        direction = self._trend_direction(snapshot, structural_state)
        veto = [
            HardVeto(
                code=VetoCode.SHORT_GAMMA_REGIME,
                category=VetoCategory.STRUCTURAL,
                reason="short gamma vetoes premium selling",
            )
        ]
        return "short_gamma_trend", direction, _DEBIT_FAMILIES, _PREMIUM_FAMILIES, veto

    @staticmethod
    def _trend_direction(
        snapshot: CanonicalMarketSnapshot,
        structural_state: StructuralState,
    ) -> DirectionPreference:
        gex = structural_state.gex_oi
        if gex is None:
            return DirectionPreference.NEUTRAL
        if snapshot.underlying_price > gex.gamma_flip:
            return DirectionPreference.CALL_BIASED
        if snapshot.underlying_price < gex.gamma_flip:
            return DirectionPreference.PUT_BIASED
        return DirectionPreference.NEUTRAL

    @staticmethod
    def _confidence(net_ratio: float, concentration: float) -> float:
        raw = 0.5 * abs(net_ratio) + 0.5 * concentration
        return max(0.0, min(1.0, raw))
