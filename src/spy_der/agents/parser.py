"""Strict structured-response parser (spec §44/§45). Fail closed on ambiguity."""

from __future__ import annotations

import json
from typing import Any

from spy_der.contracts.agents import (
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
)

__all__ = ["ParseError", "parse_agent_json"]


class ParseError(ValueError):
    """Model output could not be parsed into AgentDecisionResponse."""


def parse_agent_json(
    raw: str,
    packet: AgentDecisionPacket,
    *,
    model_id: str = "",
    prompt_version: str = "",
) -> AgentDecisionResponse:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid json: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("response must be a JSON object")

    # Reject smuggled geometry/execution fields.
    forbidden = {
        "legs",
        "strikes",
        "order",
        "orders",
        "api_key",
        "credential",
        "broker",
    }
    for key in data:
        if str(key).lower() in forbidden:
            raise ParseError(f"forbidden response field: {key}")

    try:
        action = AgentEntryAction(str(data.get("action", "")).upper())
    except ValueError as exc:
        raise ParseError("invalid action") from exc

    candidate_id = data.get("candidate_id")
    if candidate_id is not None:
        candidate_id = str(candidate_id)

    try:
        size_scalar = float(data.get("size_scalar", 0.0))
        confidence = float(data.get("confidence", 0.0))
        uncertainty = float(data.get("uncertainty", 1.0))
    except (TypeError, ValueError) as exc:
        raise ParseError("numeric fields invalid") from exc

    reasons = data.get("reason_codes") or ()
    if isinstance(reasons, str):
        reasons = (reasons,)
    if not isinstance(reasons, (list, tuple)):
        raise ParseError("reason_codes must be a list")

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
            confidence=confidence,
            uncertainty=uncertainty,
            reason_codes=tuple(str(r) for r in reasons),
            rationale=str(data.get("rationale", "")),
            model_id=model_id,
            prompt_version=prompt_version,
            geometry_hash=geom,
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
