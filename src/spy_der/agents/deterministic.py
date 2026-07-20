"""DeterministicDecisionAgent — policy-ensemble based, no LLM (spec §37)."""

from __future__ import annotations

from spy_der.contracts.agents import (
    AgentCapabilities,
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    AgentHealth,
    AgentIdentity,
    AgentPositionAction,
    AgentPositionResponse,
    PositionDecisionPacket,
)
from spy_der.contracts.policies import PolicyAction

__all__ = ["DETERMINISTIC_AGENT_VERSION", "DeterministicDecisionAgent"]

DETERMINISTIC_AGENT_VERSION = "deterministic-agent.v2"
PROMPT_VERSION = "deterministic-passthrough.v2"


class DeterministicDecisionAgent:
    """Selects using authoritative policy view already embedded in the packet.

    Prefers ensemble policy view when present; otherwise V3, then V2, then Legacy.
    Never invents candidate IDs outside the packet. Position path follows
    deterministic exit floors, else HOLD.
    """

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="deterministic",
            model_id="policy-ensemble",
            adapter_version=DETERMINISTIC_AGENT_VERSION,
            prompt_version=PROMPT_VERSION,
        )

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_entry_decisions=True,
            supports_position_decisions=True,
            supports_structured_output=True,
            supports_deterministic_seed=True,
        )

    def health(self) -> AgentHealth:
        return AgentHealth(healthy=True, detail="ok")

    def decide_entry(self, packet: AgentDecisionPacket) -> AgentDecisionResponse:
        if packet.hard_vetoes:
            return AgentDecisionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentEntryAction.ABSTAIN,
                reason_codes=("hard_veto",),
                rationale="hard veto present",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )
        by_name = {v.policy_name: v for v in packet.policy_views}
        preferred = None
        for name in ("ensemble", "v3", "v2", "legacy"):
            if name in by_name:
                preferred = by_name[name]
                break
        if preferred is None:
            ranked = sorted(
                [c for c in packet.candidates if not c.hard_vetoed],
                key=lambda c: (
                    -(c.candidate_utility if c.candidate_utility is not None else float("-inf")),
                    c.candidate_id,
                ),
            )
            if not ranked:
                return AgentDecisionResponse(
                    packet_id=packet.packet_id,
                    packet_hash=packet.packet_hash,
                    action=AgentEntryAction.NO_EDGE,
                    reason_codes=("empty_universe",),
                    rationale="no candidates",
                    model_id=self.identity.model_id,
                    prompt_version=self.identity.prompt_version,
                )
            top = ranked[0]
            exit_id = (
                packet.approved_exit_policies[0].exit_policy_id
                if packet.approved_exit_policies
                else None
            )
            return AgentDecisionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentEntryAction.SELECT_CANDIDATE,
                candidate_id=top.candidate_id,
                size_scalar=min(1.0, packet.risk_max_size_scalar),
                exit_policy_id=exit_id,
                confidence=0.5,
                uncertainty=top.uncertainty,
                geometry_hash=top.geometry_hash,
                reason_codes=("utility_fallback",),
                rationale="selected top utility candidate",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )

        if preferred.action is PolicyAction.ABSTAIN:
            action = AgentEntryAction.ABSTAIN
            cid = None
        elif preferred.action is PolicyAction.NO_EDGE:
            action = AgentEntryAction.NO_EDGE
            cid = None
        else:
            action = AgentEntryAction.SELECT_CANDIDATE
            cid = preferred.candidate_id
            if cid is None or packet.candidate(cid) is None:
                return AgentDecisionResponse(
                    packet_id=packet.packet_id,
                    packet_hash=packet.packet_hash,
                    action=AgentEntryAction.ABSTAIN,
                    reason_codes=("policy_candidate_missing",),
                    rationale="policy candidate not in packet",
                    model_id=self.identity.model_id,
                    prompt_version=self.identity.prompt_version,
                )

        size = 0.0
        geom = None
        exit_id = None
        if action is AgentEntryAction.SELECT_CANDIDATE and cid is not None:
            size = min(float(preferred.size_cap or 1.0), packet.risk_max_size_scalar)
            view = packet.candidate(cid)
            geom = view.geometry_hash if view else None
            if packet.approved_exit_policies:
                exit_id = packet.approved_exit_policies[0].exit_policy_id

        return AgentDecisionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=action,
            candidate_id=cid,
            size_scalar=size,
            exit_policy_id=exit_id,
            confidence=float(preferred.confidence),
            uncertainty=float(preferred.uncertainty),
            geometry_hash=geom,
            reason_codes=preferred.reason_codes or (f"policy:{preferred.policy_name}",),
            rationale=f"deterministic from {preferred.policy_name}",
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
        )

    def decide_position(self, packet: PositionDecisionPacket) -> AgentPositionResponse:
        signal = packet.deterministic_exit_signal
        if signal or packet.hard_vetoes:
            return AgentPositionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentPositionAction.CLOSE,
                confidence=1.0,
                uncertainty=0.0,
                reason_codes=("deterministic_exit_floor", signal or "hard_veto"),
                rationale=f"close on {signal or 'hard_veto'}",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )
        # Soft heuristic: take profit / cut losers without inventing prices.
        pnl = packet.position.unrealized_pnl_ratio
        if pnl >= 0.5:
            return AgentPositionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentPositionAction.CLOSE,
                confidence=0.7,
                uncertainty=0.3,
                reason_codes=("deterministic_target",),
                rationale="target reached",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )
        if pnl <= -0.35:
            return AgentPositionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentPositionAction.CLOSE,
                confidence=0.7,
                uncertainty=0.3,
                reason_codes=("deterministic_stop",),
                rationale="stop reached",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )
        return AgentPositionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=AgentPositionAction.HOLD,
            confidence=0.5,
            uncertainty=0.5,
            reason_codes=("deterministic_hold",),
            rationale="no exit floor",
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
        )
