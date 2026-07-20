"""Fail-closed agent response validation (spec §45)."""

from __future__ import annotations

from datetime import datetime

from spy_der.contracts.agents import (
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    AgentPositionAction,
    AgentPositionResponse,
    PositionDecisionPacket,
)

__all__ = [
    "ValidationError",
    "abstain_response",
    "hold_response",
    "validate_agent_response",
    "validate_position_response",
]


class ValidationError(ValueError):
    """Agent response failed deterministic packet validation."""


def abstain_response(
    packet: AgentDecisionPacket,
    *,
    reason: str,
    model_id: str = "",
    prompt_version: str = "",
) -> AgentDecisionResponse:
    return AgentDecisionResponse(
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        action=AgentEntryAction.ABSTAIN,
        confidence=0.0,
        uncertainty=1.0,
        reason_codes=("validation_failure",),
        rationale=reason,
        model_id=model_id,
        prompt_version=prompt_version,
    )


def hold_response(
    packet: PositionDecisionPacket,
    *,
    reason: str,
    model_id: str = "",
    prompt_version: str = "",
) -> AgentPositionResponse:
    return AgentPositionResponse(
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        action=AgentPositionAction.HOLD,
        confidence=0.0,
        uncertainty=1.0,
        reason_codes=("validation_failure",),
        rationale=reason,
        model_id=model_id,
        prompt_version=prompt_version,
    )


def validate_agent_response(
    packet: AgentDecisionPacket,
    response: AgentDecisionResponse,
    *,
    now: datetime | None = None,
) -> AgentDecisionResponse:
    """Validate response; raise ValidationError on any failure (caller abstains)."""
    if response.packet_id != packet.packet_id:
        raise ValidationError("packet_id mismatch")
    if response.packet_hash != packet.packet_hash:
        raise ValidationError("packet_hash mismatch")
    if now is not None:
        if now.tzinfo is None:
            raise ValidationError("now must be timezone-aware")
        if now > packet.expires_at:
            raise ValidationError("packet expired")
        if now < packet.created_at:
            raise ValidationError("response before packet created_at")

    if packet.hard_vetoes and response.action is AgentEntryAction.SELECT_CANDIDATE:
        raise ValidationError("hard veto blocks candidate selection")

    if response.action is AgentEntryAction.SELECT_CANDIDATE:
        assert response.candidate_id is not None
        cand = packet.candidate(response.candidate_id)
        if cand is None:
            raise ValidationError("agent selected a candidate outside the packet whitelist")
        if cand.hard_vetoed:
            raise ValidationError("agent selected a hard-vetoed candidate")
        if response.geometry_hash is not None and response.geometry_hash != cand.geometry_hash:
            raise ValidationError("geometry_hash mismatch")
        if response.size_scalar > packet.risk_max_size_scalar + 1e-12:
            raise ValidationError("agent size exceeds deterministic risk cap")
        if response.exit_policy_id is not None:
            approved = {p.exit_policy_id for p in packet.approved_exit_policies}
            if approved and response.exit_policy_id not in approved:
                raise ValidationError("exit_policy_id not approved")
    else:
        if response.size_scalar != 0.0:
            raise ValidationError("non-select actions must use size_scalar=0")

    allowed_evidence = set(packet.evidence_ids)
    for eid in response.supporting_evidence_ids + response.contradictory_evidence_ids:
        if allowed_evidence and eid not in allowed_evidence:
            raise ValidationError(f"unknown evidence id: {eid}")

    banned = ("legs=", "strike=", "order_intent=", "broker_")
    low = response.rationale.lower()
    if any(token in low for token in banned):
        raise ValidationError("response rationale contains forbidden execution content")

    return response


def validate_position_response(
    packet: PositionDecisionPacket,
    response: AgentPositionResponse,
    *,
    now: datetime | None = None,
) -> AgentPositionResponse:
    if response.packet_id != packet.packet_id:
        raise ValidationError("packet_id mismatch")
    if response.packet_hash != packet.packet_hash:
        raise ValidationError("packet_hash mismatch")
    if now is not None:
        if now.tzinfo is None:
            raise ValidationError("now must be timezone-aware")
        if now > packet.expires_at:
            raise ValidationError("packet expired")
        if now < packet.created_at:
            raise ValidationError("response before packet created_at")

    # Hard vetoes and deterministic emergency/stop floors force CLOSE.
    hard = bool(packet.hard_vetoes) or packet.deterministic_exit_signal in {
        "emergency_exit",
        "stop",
        "eod",
        "expiration_settlement",
        "structural_ras_exit",
    }
    if hard and response.action is AgentPositionAction.HOLD:
        raise ValidationError("hard exit floor blocks HOLD")

    if response.action is AgentPositionAction.REDUCE:
        if response.reduce_fraction <= 0.0 or response.reduce_fraction > 1.0:
            raise ValidationError("invalid reduce_fraction")

    banned = ("legs=", "strike=", "order_intent=", "broker_")
    low = response.rationale.lower()
    if any(token in low for token in banned):
        raise ValidationError("response rationale contains forbidden execution content")

    return response
