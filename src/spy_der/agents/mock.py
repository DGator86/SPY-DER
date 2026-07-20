"""MockDecisionAgent with configured deterministic responses."""

from __future__ import annotations

from spy_der.contracts.agents import (
    AgentCapabilities,
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    AgentHealth,
    AgentIdentity,
)

__all__ = ["MOCK_AGENT_VERSION", "MockDecisionAgent"]

MOCK_AGENT_VERSION = "mock-agent.v1"


class MockDecisionAgent:
    def __init__(
        self,
        *,
        action: AgentEntryAction = AgentEntryAction.NO_EDGE,
        candidate_id: str | None = None,
        size_scalar: float = 0.0,
        rationale: str = "mock",
    ) -> None:
        self._action = action
        self._candidate_id = candidate_id
        self._size_scalar = size_scalar
        self._rationale = rationale

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="mock",
            model_id="mock",
            adapter_version=MOCK_AGENT_VERSION,
            prompt_version="mock.v1",
        )

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supports_structured_output=True)

    def health(self) -> AgentHealth:
        return AgentHealth(healthy=True, detail="mock")

    def decide_entry(self, packet: AgentDecisionPacket) -> AgentDecisionResponse:
        cid = self._candidate_id
        geom = None
        size = self._size_scalar
        if self._action is AgentEntryAction.SELECT_CANDIDATE:
            if cid is None and packet.candidates:
                cid = packet.candidates[0].candidate_id
            view = packet.candidate(cid) if cid else None
            geom = view.geometry_hash if view else None
            size = min(size or packet.risk_max_size_scalar, packet.risk_max_size_scalar)
        else:
            cid = None
            size = 0.0
        return AgentDecisionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=self._action,
            candidate_id=cid,
            size_scalar=size,
            confidence=0.5,
            uncertainty=0.5,
            geometry_hash=geom,
            reason_codes=("mock",),
            rationale=self._rationale,
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
        )
