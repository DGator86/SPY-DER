"""Grok / xAI adapter — default AI decision maker for SPY-DER.

Owns entry + position (exit/manage) decisions. Uses injectable transport;
when no transport is injected and XAI_API_KEY is present, attaches stdlib HTTP.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from spy_der.agents.parser import ParseError, parse_agent_json, parse_position_json
from spy_der.agents.prompts import (
    ENTRY_PROMPT_VERSION,
    POSITION_PROMPT_VERSION,
    build_entry_prompt,
    build_position_prompt,
)
from spy_der.agents.security import redact_secrets
from spy_der.agents.transport import make_http_grok_transport
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

__all__ = ["GROK_ADAPTER_VERSION", "GrokDecisionAgent", "GrokTransport"]

GROK_ADAPTER_VERSION = "grok-adapter.v2"

# Transport: (url, headers, body) -> response text
GrokTransport = Callable[[str, dict[str, str], dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class GrokConfig:
    api_base: str = "https://api.x.ai/v1/chat/completions"
    model_id: str = "grok-2"
    api_key_env: str = "XAI_API_KEY"
    timeout_seconds: float = 30.0
    # When True, attach HttpGrokTransport if env key is present and no transport given.
    auto_http: bool = True


class GrokDecisionAgent:
    """LLM decision maker. Secrets stay in this adapter, never in packets."""

    def __init__(
        self,
        *,
        transport: GrokTransport | None = None,
        cfg: GrokConfig | None = None,
        api_key: str | None = None,
    ) -> None:
        self.cfg = cfg or GrokConfig()
        self._api_key = api_key
        self._transport = transport
        if self._transport is None and self.cfg.auto_http and self._resolve_api_key():
            self._transport = make_http_grok_transport(
                timeout_seconds=self.cfg.timeout_seconds
            )

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="grok",
            model_id=self.cfg.model_id,
            adapter_version=GROK_ADAPTER_VERSION,
            prompt_version=ENTRY_PROMPT_VERSION,
        )

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_entry_decisions=True,
            supports_position_decisions=True,
            supports_structured_output=True,
            supports_usage_reporting=True,
            maximum_context_tokens=128000,
        )

    def health(self) -> AgentHealth:
        key = self._resolve_api_key()
        if self._transport is None and not key:
            return AgentHealth(healthy=False, detail="missing_api_key_and_transport")
        return AgentHealth(healthy=True, detail="ok")

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        return os.environ.get(self.cfg.api_key_env, "")

    def decide_entry(self, packet: AgentDecisionPacket) -> AgentDecisionResponse:
        if self._transport is None:
            return AgentDecisionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentEntryAction.ABSTAIN,
                reason_codes=("grok_transport_missing",),
                rationale="no transport configured",
                model_id=self.identity.model_id,
                prompt_version=ENTRY_PROMPT_VERSION,
            )

        prompt = build_entry_prompt(packet)
        try:
            text = self._call(prompt)
            return parse_agent_json(
                text,
                packet,
                model_id=self.identity.model_id,
                prompt_version=ENTRY_PROMPT_VERSION,
            )
        except (ParseError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            return AgentDecisionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentEntryAction.ABSTAIN,
                reason_codes=("grok_parse_or_transport_failure",),
                rationale=redact_secrets(f"grok_failure:{type(exc).__name__}:{exc}"),
                model_id=self.identity.model_id,
                prompt_version=ENTRY_PROMPT_VERSION,
            )

    def decide_position(self, packet: PositionDecisionPacket) -> AgentPositionResponse:
        if self._transport is None:
            return AgentPositionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentPositionAction.HOLD,
                reason_codes=("grok_transport_missing",),
                rationale="no transport configured",
                model_id=self.identity.model_id,
                prompt_version=POSITION_PROMPT_VERSION,
            )

        prompt = build_position_prompt(packet)
        try:
            text = self._call(prompt)
            return parse_position_json(
                text,
                packet,
                model_id=self.identity.model_id,
                prompt_version=POSITION_PROMPT_VERSION,
            )
        except (ParseError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            return AgentPositionResponse(
                packet_id=packet.packet_id,
                packet_hash=packet.packet_hash,
                action=AgentPositionAction.HOLD,
                reason_codes=("grok_parse_or_transport_failure",),
                rationale=redact_secrets(f"grok_failure:{type(exc).__name__}:{exc}"),
                model_id=self.identity.model_id,
                prompt_version=POSITION_PROMPT_VERSION,
            )

    def _call(self, prompt: dict[str, str]) -> str:
        assert self._transport is not None
        api_key = self._resolve_api_key()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        }
        body = {
            "model": self.cfg.model_id,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "temperature": 0.0,
        }
        raw = self._transport(self.cfg.api_base, headers, body)
        return _extract_content(raw)


def _extract_content(raw: str) -> str:
    """Accept either raw JSON decision text or OpenAI-style chat envelope."""
    raw = raw.strip()
    if raw.startswith("{") and ('"action"' in raw or "'action'" in raw):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return str(msg["content"])
        if "action" in data:
            return json.dumps(data)
    return str(raw)
