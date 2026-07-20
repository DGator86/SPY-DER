"""MockDecisionAgent with configured deterministic responses."""

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

__all__ = ["MOCK_AGENT_VERSION", "MockDecisionAgent"]

MOCK_AGENT_VERSION = "mock-agent.v2"


class MockDecisionAgent:
    def __init__(
        self,
        *,
        action: AgentEntryAction = AgentEntryAction.NO_EDGE,
        candidate_id: str | None = None,
        size_scalar: float = 0.0,
        exit_policy_id: str | None = None,
        position_action: AgentPositionAction = AgentPositionAction.HOLD,
        reduce_fraction: float = 0.0,
        rationale: str = "mock",
    ) -> None:
        self._action = action
        self._candidate_id = candidate_id
        self._size_scalar = size_scalar
        self._exit_policy_id = exit_policy_id
        self._position_action = position_action
        self._reduce_fraction = reduce_fraction
        self._rationale = rationale

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="mock",
            model_id="mock",
            adapter_version=MOCK_AGENT_VERSION,
            prompt_version="mock.v2",
        )

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_entry_decisions=True,
            supports_position_decisions=True,
            supports_structured_output=True,
        )

    def health(self) -> AgentHealth:
        return AgentHealth(healthy=True, detail="mock")

    def decide_entry(self, packet: AgentDecisionPacket) -> AgentDecisionResponse:
        cid = self._candidate_id
        geom = None
        size = self._size_scalar
        exit_id = self._exit_policy_id
        if self._action is AgentEntryAction.SELECT_CANDIDATE:
            if cid is None and packet.candidates:
                cid = packet.candidates[0].candidate_id
            view = packet.candidate(cid) if cid else None
            geom = view.geometry_hash if view else None
            size = min(size or packet.risk_max_size_scalar, packet.risk_max_size_scalar)
            if exit_id is None and packet.approved_exit_policies:
                exit_id = packet.approved_exit_policies[0].exit_policy_id
        else:
            cid = None
            size = 0.0
            exit_id = None
        return AgentDecisionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=self._action,
            candidate_id=cid,
            size_scalar=size,
            exit_policy_id=exit_id,
            confidence=0.5,
            uncertainty=0.5,
            geometry_hash=geom,
            reason_codes=("mock",),
            rationale=self._rationale,
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
        )

    def decide_position(self, packet: PositionDecisionPacket) -> AgentPositionResponse:
        frac = (
            self._reduce_fraction
            if self._position_action is AgentPositionAction.REDUCE
            else 0.0
        )
        return AgentPositionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=self._position_action,
            reduce_fraction=frac,
            confidence=0.5,
            uncertainty=0.5,
            reason_codes=("mock",),
            rationale=self._rationale,
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
        )
