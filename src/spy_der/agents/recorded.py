"""RecordedDecisionAgent — replay responses by packet_hash."""

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

__all__ = ["RECORDED_AGENT_VERSION", "RecordedDecisionAgent"]

RECORDED_AGENT_VERSION = "recorded-agent.v2"


class RecordedDecisionAgent:
    def __init__(
        self,
        recordings: dict[str, AgentDecisionResponse] | None = None,
        position_recordings: dict[str, AgentPositionResponse] | None = None,
    ) -> None:
        self._recordings = dict(recordings or {})
        self._position_recordings = dict(position_recordings or {})

    def record(self, packet_hash: str, response: AgentDecisionResponse) -> None:
        self._recordings[packet_hash] = response

    def record_position(self, packet_hash: str, response: AgentPositionResponse) -> None:
        self._position_recordings[packet_hash] = response

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="recorded",
            model_id="replay",
            adapter_version=RECORDED_AGENT_VERSION,
            prompt_version="recorded.v2",
        )

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_entry_decisions=True,
            supports_position_decisions=True,
            supports_deterministic_seed=True,
        )

    def health(self) -> AgentHealth:
        return AgentHealth(
            healthy=True,
            detail=(
                f"entry={len(self._recordings)} "
                f"position={len(self._position_recordings)}"
            ),
        )

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

    def decide_position(self, packet: PositionDecisionPacket) -> AgentPositionResponse:
        stored = self._position_recordings.get(packet.packet_hash)
        if stored is None:
            return AgentPositionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentPositionAction.HOLD,
                reason_codes=("recording_missing",),
                rationale="no position recording for packet_hash",
                model_id=self.identity.model_id,
                prompt_version=self.identity.prompt_version,
            )
        return AgentPositionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=stored.action,
            reduce_fraction=stored.reduce_fraction,
            confidence=stored.confidence,
            uncertainty=stored.uncertainty,
            reason_codes=stored.reason_codes or ("recorded",),
            rationale=stored.rationale or "recorded replay",
            model_id=self.identity.model_id,
            prompt_version=self.identity.prompt_version,
        )
