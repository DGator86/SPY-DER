"""Fail-closed runtime wrapper for any DecisionAgent."""

from __future__ import annotations

from datetime import datetime

from spy_der.agents.protocols import DecisionAgent
from spy_der.agents.validation import (
    ValidationError,
    abstain_response,
    hold_response,
    validate_agent_response,
    validate_position_response,
)
from spy_der.contracts.agents import (
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentPositionAction,
    AgentPositionResponse,
    PositionDecisionPacket,
)

__all__ = ["FailClosedAgentRuntime"]


class FailClosedAgentRuntime:
    """Validate agent output; convert every failure to ABSTAIN / HOLD."""

    def __init__(self, agent: DecisionAgent) -> None:
        self.agent = agent

    def decide_entry(
        self,
        packet: AgentDecisionPacket,
        *,
        now: datetime | None = None,
    ) -> AgentDecisionResponse:
        identity = self.agent.identity
        try:
            response = self.agent.decide_entry(packet)
            response = _stamp_entry_identity(response, identity.model_id, identity.prompt_version)
            return validate_agent_response(packet, response, now=now)
        except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
            return abstain_response(
                packet,
                reason=f"agent_failure:{type(exc).__name__}:{exc}",
                model_id=identity.model_id,
                prompt_version=identity.prompt_version,
            )

    def decide_position(
        self,
        packet: PositionDecisionPacket,
        *,
        now: datetime | None = None,
    ) -> AgentPositionResponse:
        identity = self.agent.identity
        try:
            if not self.agent.capabilities.supports_position_decisions:
                if _hard_exit_floor(packet):
                    return AgentPositionResponse(
                        packet_id=packet.packet_id,
                        packet_hash=packet.packet_hash,
                        action=AgentPositionAction.CLOSE,
                        confidence=1.0,
                        uncertainty=0.0,
                        reason_codes=("hard_exit_floor",),
                        rationale="position_decisions_unsupported_forced_close",
                        model_id=identity.model_id,
                        prompt_version=identity.prompt_version,
                    )
                return hold_response(
                    packet,
                    reason="position_decisions_unsupported",
                    model_id=identity.model_id,
                    prompt_version=identity.prompt_version,
                )
            response = self.agent.decide_position(packet)
            response = _stamp_position_identity(
                response, identity.model_id, identity.prompt_version
            )
            return validate_position_response(packet, response, now=now)
        except (ValidationError, ValueError, TypeError, RuntimeError, AttributeError) as exc:
            # Fail closed: HOLD by default, but hard exit floors must CLOSE.
            if _hard_exit_floor(packet):
                return AgentPositionResponse(
                    packet_id=packet.packet_id,
                    packet_hash=packet.packet_hash,
                    action=AgentPositionAction.CLOSE,
                    confidence=1.0,
                    uncertainty=0.0,
                    reason_codes=("hard_exit_floor",),
                    rationale=f"agent_failure_forced_close:{type(exc).__name__}:{exc}",
                    model_id=identity.model_id,
                    prompt_version=identity.prompt_version,
                )
            return hold_response(
                packet,
                reason=f"agent_failure:{type(exc).__name__}:{exc}",
                model_id=identity.model_id,
                prompt_version=identity.prompt_version,
            )


def _hard_exit_floor(packet: PositionDecisionPacket) -> bool:
    if packet.hard_vetoes:
        return True
    return packet.deterministic_exit_signal in {
        "emergency_exit",
        "stop",
        "eod",
        "expiration_settlement",
        "structural_ras_exit",
    }


def _stamp_entry_identity(
    response: AgentDecisionResponse,
    model_id: str,
    prompt_version: str,
) -> AgentDecisionResponse:
    if response.model_id and response.prompt_version:
        return response
    return AgentDecisionResponse(
        packet_id=response.packet_id,
        packet_hash=response.packet_hash,
        action=response.action,
        candidate_id=response.candidate_id,
        size_scalar=response.size_scalar,
        exit_policy_id=response.exit_policy_id,
        confidence=response.confidence,
        uncertainty=response.uncertainty,
        supporting_evidence_ids=response.supporting_evidence_ids,
        contradictory_evidence_ids=response.contradictory_evidence_ids,
        reason_codes=response.reason_codes,
        rationale=response.rationale,
        schema_version=response.schema_version,
        model_id=response.model_id or model_id,
        prompt_version=response.prompt_version or prompt_version,
        geometry_hash=response.geometry_hash,
    )


def _stamp_position_identity(
    response: AgentPositionResponse,
    model_id: str,
    prompt_version: str,
) -> AgentPositionResponse:
    if response.model_id and response.prompt_version:
        return response
    return AgentPositionResponse(
        packet_id=response.packet_id,
        packet_hash=response.packet_hash,
        action=response.action,
        reduce_fraction=response.reduce_fraction,
        confidence=response.confidence,
        uncertainty=response.uncertainty,
        reason_codes=response.reason_codes,
        rationale=response.rationale,
        schema_version=response.schema_version,
        model_id=response.model_id or model_id,
        prompt_version=response.prompt_version or prompt_version,
    )
