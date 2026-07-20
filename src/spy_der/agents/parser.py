"""Strict structured-response parser (spec §44/§45). Fail closed on ambiguity."""

from __future__ import annotations

import json
from typing import Any

from spy_der.contracts.agents import (
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    AgentPositionAction,
    AgentPositionResponse,
    PositionDecisionPacket,
)

__all__ = [
    "ParseError",
    "extract_json_object",
    "parse_agent_json",
    "parse_position_json",
]


class ParseError(ValueError):
    """Model output could not be parsed into an agent response."""


_FORBIDDEN = {
    "legs",
    "strikes",
    "order",
    "orders",
    "api_key",
    "credential",
    "broker",
}


def parse_agent_json(
    raw: str,
    packet: AgentDecisionPacket,
    *,
    model_id: str = "",
    prompt_version: str = "",
) -> AgentDecisionResponse:
    data = _load_object(raw)

    try:
        action = AgentEntryAction(str(data.get("action", "")).upper())
    except ValueError as exc:
        raise ParseError("invalid action") from exc

    candidate_id = data.get("candidate_id")
    if candidate_id is not None:
        candidate_id = str(candidate_id)

    exit_policy_id = data.get("exit_policy_id")
    if exit_policy_id is not None:
        exit_policy_id = str(exit_policy_id)

    try:
        size_scalar = float(data.get("size_scalar", 0.0))
        confidence = float(data.get("confidence", 0.0))
        uncertainty = float(data.get("uncertainty", 1.0))
    except (TypeError, ValueError) as exc:
        raise ParseError("numeric fields invalid") from exc

    reasons = _reasons(data.get("reason_codes"))

    geom = None
    if action is AgentEntryAction.SELECT_CANDIDATE and candidate_id:
        view = packet.candidate(candidate_id)
        if view is not None:
            geom = view.geometry_hash

    try:
        return AgentDecisionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=action,
            candidate_id=candidate_id if action is AgentEntryAction.SELECT_CANDIDATE else None,
            size_scalar=size_scalar if action is AgentEntryAction.SELECT_CANDIDATE else 0.0,
            exit_policy_id=exit_policy_id if action is AgentEntryAction.SELECT_CANDIDATE else None,
            confidence=confidence,
            uncertainty=uncertainty,
            reason_codes=reasons,
            rationale=str(data.get("rationale", "")),
            model_id=model_id,
            prompt_version=prompt_version,
            geometry_hash=geom,
        )
    except ValueError as exc:
        raise ParseError(str(exc)) from exc


def parse_position_json(
    raw: str,
    packet: PositionDecisionPacket,
    *,
    model_id: str = "",
    prompt_version: str = "",
) -> AgentPositionResponse:
    data = _load_object(raw)
    try:
        action = AgentPositionAction(str(data.get("action", "")).upper())
    except ValueError as exc:
        raise ParseError("invalid position action") from exc

    try:
        reduce_fraction = float(data.get("reduce_fraction", 0.0))
        confidence = float(data.get("confidence", 0.0))
        uncertainty = float(data.get("uncertainty", 1.0))
    except (TypeError, ValueError) as exc:
        raise ParseError("numeric fields invalid") from exc

    if action is not AgentPositionAction.REDUCE:
        reduce_fraction = 0.0

    try:
        return AgentPositionResponse(
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            action=action,
            reduce_fraction=reduce_fraction,
            confidence=confidence,
            uncertainty=uncertainty,
            reason_codes=_reasons(data.get("reason_codes")),
            rationale=str(data.get("rationale", "")),
            model_id=model_id,
            prompt_version=prompt_version,
        )
    except ValueError as exc:
        raise ParseError(str(exc)) from exc


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model blob."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ParseError("no json object found")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid json object: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("extracted value is not an object")
    return data


def _load_object(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = extract_json_object(raw)
    if not isinstance(data, dict):
        raise ParseError("response must be a JSON object")
    for key in data:
        if str(key).lower() in _FORBIDDEN:
            raise ParseError(f"forbidden response field: {key}")
    return data


def _reasons(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if not isinstance(raw, (list, tuple)):
        raise ParseError("reason_codes must be a list")
    return tuple(str(r) for r in raw)
