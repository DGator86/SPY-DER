"""Legacy policy adapter (spec §36; System A legacy_matrix / gate path)."""

from __future__ import annotations

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
    hard_veto_codes,
    legacy_permissions_ok,
    legacy_size_cap,
)

__all__ = ["LEGACY_POLICY_VERSION", "LegacyPolicy"]

LEGACY_POLICY_VERSION = "legacy-policy.v1"


class LegacyPolicy:
    """Selects first universe candidate allowed by Legacy permissions/vetoes."""

    @property
    def identity(self) -> PolicyIdentity:
        return PolicyIdentity(name="legacy", version=LEGACY_POLICY_VERSION)

    def evaluate(self, packet: PolicyInputPacket) -> PolicyDecisionView:
        vetoes = hard_veto_codes(packet)
        size = legacy_size_cap(packet)
        if not packet.required_inputs_present:
            return PolicyDecisionView(
                policy_name="legacy",
                policy_version=LEGACY_POLICY_VERSION,
                action=PolicyAction.ABSTAIN,
                size_cap=size,
                confidence=0.0,
                uncertainty=1.0,
                hard_vetoes=vetoes,
                reason_codes=("missing_inputs",),
            )
        if vetoes:
            return PolicyDecisionView(
                policy_name="legacy",
                policy_version=LEGACY_POLICY_VERSION,
                action=PolicyAction.ABSTAIN,
                size_cap=size,
                confidence=0.0,
                uncertainty=1.0,
                hard_vetoes=vetoes,
                reason_codes=("hard_veto",),
            )
        if not legacy_permissions_ok(packet):
            return PolicyDecisionView(
                policy_name="legacy",
                policy_version=LEGACY_POLICY_VERSION,
                action=PolicyAction.NO_EDGE,
                size_cap=size,
                confidence=0.0,
                uncertainty=0.5,
                reason_codes=("permissions_deny",),
            )
        ids = candidate_ids(packet)
        if not ids:
            return PolicyDecisionView(
                policy_name="legacy",
                policy_version=LEGACY_POLICY_VERSION,
                action=PolicyAction.NO_EDGE,
                size_cap=size,
                confidence=0.2,
                uncertainty=0.5,
                reason_codes=("empty_universe",),
            )
        chosen = ids[0]
        max_loss = candidate_max_loss(packet, chosen)
        risk_cap = envelope_max_risk(packet)
        if max_loss is not None and risk_cap is not None and max_loss > risk_cap:
            return PolicyDecisionView(
                policy_name="legacy",
                policy_version=LEGACY_POLICY_VERSION,
                action=PolicyAction.NO_EDGE,
                size_cap=size,
                confidence=0.2,
                uncertainty=0.4,
                reason_codes=("risk_envelope",),
            )
        conf = float(getattr(packet.legacy_view, "structural_confidence", 0.5) or 0.5)
        return PolicyDecisionView(
            policy_name="legacy",
            policy_version=LEGACY_POLICY_VERSION,
            action=PolicyAction.SELECT_CANDIDATE,
            candidate_id=chosen,
            size_cap=size if size > 0 else 1.0,
            confidence=min(max(conf, 0.0), 1.0),
            uncertainty=max(0.0, 1.0 - conf),
            supporting_evidence=("legacy_universe_order",),
            reason_codes=("legacy_select",),
        )
