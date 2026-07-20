"""V3 candidate-value policy adapter (spec §36)."""

from __future__ import annotations

from spy_der.contracts.policies import (
    PolicyAction,
    PolicyDecisionView,
    PolicyIdentity,
    PolicyInputPacket,
)
from spy_der.contracts.value import MetaAction
from spy_der.policies._packet import (
    candidate_max_loss,
    envelope_max_risk,
    hard_veto_codes,
    legacy_permissions_ok,
    legacy_size_cap,
    ranked_candidate_ids,
    top_value_candidate_id,
)

__all__ = ["V3_POLICY_VERSION", "V3Policy"]

V3_POLICY_VERSION = "v3-value-policy.v1"


class V3Policy:
    """Selects from SnapshotRanking / value forecasts / meta-action."""

    @property
    def identity(self) -> PolicyIdentity:
        return PolicyIdentity(name="v3", version=V3_POLICY_VERSION)

    def evaluate(self, packet: PolicyInputPacket) -> PolicyDecisionView:
        vetoes = hard_veto_codes(packet)
        size = legacy_size_cap(packet)
        if not packet.required_inputs_present:
            return self._abstain(size, vetoes, ("missing_inputs",))
        if vetoes:
            return self._abstain(size, vetoes, ("hard_veto",))
        if not legacy_permissions_ok(packet):
            return self._no_edge(size, ("permissions_deny",))

        meta = packet.meta_decision
        if meta is not None:
            action = getattr(meta, "action", None)
            if action is MetaAction.HARD_VETO or action == MetaAction.HARD_VETO.value:
                return self._abstain(size, vetoes, ("meta_hard_veto",))
            if action is MetaAction.ABSTAIN or action == MetaAction.ABSTAIN.value:
                return self._abstain(size, vetoes, ("meta_abstain",))
            if action is MetaAction.NO_EDGE or action == MetaAction.NO_EDGE.value:
                return self._no_edge(size, ("meta_no_edge",))
            selected = getattr(meta, "selected_candidate_id", None)
            if selected:
                return self._select(str(selected), size, ("meta_trade",))

        ordered = ranked_candidate_ids(packet)
        chosen = ordered[0] if ordered else top_value_candidate_id(packet)
        if chosen is None:
            return self._no_edge(size, ("no_ranked_candidate",))

        max_loss = candidate_max_loss(packet, chosen)
        risk_cap = envelope_max_risk(packet)
        if max_loss is not None and risk_cap is not None and max_loss > risk_cap:
            return self._no_edge(size, ("risk_envelope",))
        return self._select(chosen, size, ("v3_rank_select",))

    def _abstain(
        self,
        size: float,
        vetoes: tuple[str, ...],
        reasons: tuple[str, ...],
    ) -> PolicyDecisionView:
        return PolicyDecisionView(
            policy_name="v3",
            policy_version=V3_POLICY_VERSION,
            action=PolicyAction.ABSTAIN,
            size_cap=size,
            confidence=0.0,
            uncertainty=1.0,
            hard_vetoes=vetoes,
            reason_codes=reasons,
        )

    def _no_edge(self, size: float, reasons: tuple[str, ...]) -> PolicyDecisionView:
        return PolicyDecisionView(
            policy_name="v3",
            policy_version=V3_POLICY_VERSION,
            action=PolicyAction.NO_EDGE,
            size_cap=size,
            confidence=0.2,
            uncertainty=0.5,
            reason_codes=reasons,
        )

    def _select(
        self,
        candidate_id: str,
        size: float,
        reasons: tuple[str, ...],
    ) -> PolicyDecisionView:
        return PolicyDecisionView(
            policy_name="v3",
            policy_version=V3_POLICY_VERSION,
            action=PolicyAction.SELECT_CANDIDATE,
            candidate_id=candidate_id,
            size_cap=size if size > 0 else 1.0,
            confidence=0.7,
            uncertainty=0.3,
            supporting_evidence=("v3_ranking",),
            reason_codes=reasons,
        )
