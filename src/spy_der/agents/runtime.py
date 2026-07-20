"""Fail-closed runtime wrapper for any DecisionAgent."""

from __future__ import annotations

from datetime import datetime

from spy_der.agents.protocols import DecisionAgent
from spy_der.agents.validation import ValidationError, abstain_response, validate_agent_response
from spy_der.contracts.agents import AgentDecisionPacket, AgentDecisionResponse

__all__ = ["FailClosedAgentRuntime"]


class FailClosedAgentRuntime:
    """Validate agent output; convert every failure to ABSTAIN."""

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
            # Stamp provider metadata when missing.
            if not response.model_id or not response.prompt_version:
                response = AgentDecisionResponse(
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
                    model_id=response.model_id or identity.model_id,
                    prompt_version=response.prompt_version or identity.prompt_version,
                    geometry_hash=response.geometry_hash,
                )
            return validate_agent_response(packet, response, now=now)
        except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
            return abstain_response(
                packet,
                reason=f"agent_failure:{type(exc).__name__}:{exc}",
                model_id=identity.model_id,
                prompt_version=identity.prompt_version,
            )
