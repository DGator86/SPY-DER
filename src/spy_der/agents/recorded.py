"""RecordedDecisionAgent — replay responses by packet_hash."""

from __future__ import annotations

from spy_der.contracts.agents import (
    AgentCapabilities,
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    AgentHealth,
    AgentIdentity,
)

__all__ = ["RECORDED_AGENT_VERSION", "RecordedDecisionAgent"]

RECORDED_AGENT_VERSION = "recorded-agent.v1"


class RecordedDecisionAgent:
    def __init__(self, recordings: dict[str, AgentDecisionResponse] | None = None) -> None:
        self._recordings = dict(recordings or {})

    def record(self, packet_hash: str, response: AgentDecisionResponse) -> None:
        self._recordings[packet_hash] = response

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="recorded",
            model_id="replay",
            adapter_version=RECORDED_AGENT_VERSION,
            prompt_version="recorded.v1",
        )

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supports_deterministic_seed=True)

    def health(self) -> AgentHealth:
        return AgentHealth(healthy=True, detail=f"recordings={len(self._recordings)}")

    def decide_entry(self, packet: AgentDecisionPacket) -> AgentDecisionResponse:
        stored = self._recordings.get(packet.packet_hash)
        if stored is None:
            return AgentDecisionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentEntryAction.ABSTAIN,
                reason_codes=("recording_missing",),
                rationale="no recording for packet_hash",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )
        # Rebind packet identity fields in case recording was templated.
        return AgentDecisionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=stored.action,
            candidate_id=stored.candidate_id,
            size_scalar=stored.size_scalar,
            exit_policy_id=stored.exit_policy_id,
            confidence=stored.confidence,
            uncertainty=stored.uncertainty,
            supporting_evidence_ids=stored.supporting_evidence_ids,
            contradictory_evidence_ids=stored.contradictory_evidence_ids,
            reason_codes=stored.reason_codes or ("recorded",),
            rationale=stored.rationale or "recorded replay",
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
            geometry_hash=stored.geometry_hash,
        )
