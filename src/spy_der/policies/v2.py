"""V2 forecast policy adapter (spec §36; System A prediction_policy.py)."""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.policies import (
    PolicyAction,
    PolicyDecisionView,
    PolicyIdentity,
    PolicyInputPacket,
)
from spy_der.policies._packet import (
    candidate_ids,
    candidate_max_loss,
    envelope_max_risk,
    forecast_field,
    hard_veto_codes,
    legacy_permissions_ok,
    legacy_size_cap,
)

__all__ = ["V2_POLICY_VERSION", "V2Policy", "V2PolicyConfig"]

V2_POLICY_VERSION = "v2-prediction-policy.v1"


@dataclass(frozen=True, slots=True)
class V2PolicyConfig:
    min_direction_prob: float = 0.58
    max_uncertainty: float = 0.75
    min_data_quality: float = 0.40


class V2Policy:
    """Maps MarketForecastBundle fields into a candidate selection."""

    def __init__(self, cfg: V2PolicyConfig | None = None) -> None:
        self.cfg = cfg or V2PolicyConfig()

    @property
    def identity(self) -> PolicyIdentity:
        return PolicyIdentity(name="v2", version=V2_POLICY_VERSION)

    def evaluate(self, packet: PolicyInputPacket) -> PolicyDecisionView:
        vetoes = hard_veto_codes(packet)
        size = legacy_size_cap(packet)
        if not packet.required_inputs_present or packet.market_forecast is None:
            return self._view(
                PolicyAction.ABSTAIN,
                size=size,
                vetoes=vetoes,
                reasons=("missing_forecast",),
                uncertainty=1.0,
            )
        if vetoes:
            return self._view(
                PolicyAction.ABSTAIN,
                size=size,
                vetoes=vetoes,
                reasons=("hard_veto",),
                uncertainty=1.0,
            )
        if not legacy_permissions_ok(packet):
            return self._view(
                PolicyAction.NO_EDGE,
                size=size,
                reasons=("permissions_deny",),
                uncertainty=0.5,
            )

        uncertainty = float(forecast_field(packet, "uncertainty") or 0.5)
        data_quality = float(forecast_field(packet, "data_quality") or 0.0)
        if uncertainty >= self.cfg.max_uncertainty:
            return self._view(
                PolicyAction.ABSTAIN,
                size=size,
                reasons=("high_uncertainty",),
                uncertainty=uncertainty,
                confidence=0.0,
            )
        if data_quality < self.cfg.min_data_quality:
            return self._view(
                PolicyAction.ABSTAIN,
                size=size,
                reasons=("low_data_quality",),
                uncertainty=uncertainty,
                confidence=data_quality,
            )

        p_up = forecast_field(packet, "p_up_30m")
        if p_up is None:
            p_up = forecast_field(packet, "prob_up")
        if p_up is None:
            return self._view(
                PolicyAction.ABSTAIN,
                size=size,
                reasons=("missing_direction",),
                uncertainty=uncertainty,
            )
        p_up_f = float(p_up)
        edge = max(p_up_f, 1.0 - p_up_f)
        if edge < self.cfg.min_direction_prob:
            return self._view(
                PolicyAction.NO_EDGE,
                size=size,
                reasons=("no_directional_edge",),
                uncertainty=uncertainty,
                confidence=edge,
            )

        ids = candidate_ids(packet)
        if not ids:
            return self._view(
                PolicyAction.NO_EDGE,
                size=size,
                reasons=("empty_universe",),
                uncertainty=uncertainty,
                confidence=edge,
            )

        # Prefer candidates whose family direction matches forecast bias.
        bullish = p_up_f >= 0.5
        chosen = ids[0]
        universe = packet.candidate_universe
        if universe is not None:
            for cand in getattr(universe, "candidates", ()) or ():
                direction = str(getattr(cand, "direction", "")).lower()
                if bullish and direction in {"bullish", "long_vol"}:
                    chosen = str(cand.candidate_id)
                    break
                if not bullish and direction in {"bearish", "long_vol"}:
                    chosen = str(cand.candidate_id)
                    break

        max_loss = candidate_max_loss(packet, chosen)
        risk_cap = envelope_max_risk(packet)
        if max_loss is not None and risk_cap is not None and max_loss > risk_cap:
            return self._view(
                PolicyAction.NO_EDGE,
                size=size,
                reasons=("risk_envelope",),
                uncertainty=uncertainty,
                confidence=edge,
            )
        return PolicyDecisionView(
            policy_name="v2",
            policy_version=V2_POLICY_VERSION,
            action=PolicyAction.SELECT_CANDIDATE,
            candidate_id=chosen,
            size_cap=size if size > 0 else min(edge, 1.0),
            confidence=min(edge, 1.0),
            uncertainty=min(max(uncertainty, 0.0), 1.0),
            supporting_evidence=("direction_edge",),
            reason_codes=("v2_select",),
        )

    def _view(
        self,
        action: PolicyAction,
        *,
        size: float,
        reasons: tuple[str, ...],
        uncertainty: float,
        confidence: float = 0.0,
        vetoes: tuple[str, ...] = (),
    ) -> PolicyDecisionView:
        return PolicyDecisionView(
            policy_name="v2",
            policy_version=V2_POLICY_VERSION,
            action=action,
            size_cap=size,
            confidence=confidence,
            uncertainty=min(max(uncertainty, 0.0), 1.0),
            hard_vetoes=vetoes,
            reason_codes=reasons,
        )
