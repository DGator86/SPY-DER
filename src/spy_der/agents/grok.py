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

__all__ = [
    "DEFAULT_TRADER_MODEL_ID",
    "GROK_ADAPTER_VERSION",
    "GrokConfig",
    "GrokDecisionAgent",
    "GrokTransport",
]

GROK_ADAPTER_VERSION = "grok-adapter.v2"

# Transport: (url, headers, body) -> response text
GrokTransport = Callable[[str, dict[str, str], dict[str, Any]], str]


# Default hot-path trader: non-reasoning keeps 60s ticks cheap.
DEFAULT_TRADER_MODEL_ID = "grok-4.20-0309-non-reasoning"


@dataclass(frozen=True, slots=True)
class GrokConfig:
    api_base: str = "https://api.x.ai/v1/chat/completions"
    model_id: str = DEFAULT_TRADER_MODEL_ID
    api_key_env: str = "XAI_API_KEY"
    # Env overrides so ops can bump the model / endpoint without a code change.
    model_id_env: str = "XAI_MODEL"
    api_base_env: str = "XAI_API_BASE"
    # Cost controls: only sent for reasoning models. Empty = omit the field
    # (correct for non-reasoning trader). Reviewer sets low via its own env.
    reasoning_effort_env: str = "XAI_REASONING_EFFORT"
    reasoning_effort: str = ""
    max_completion_tokens_env: str = "XAI_MAX_COMPLETION_TOKENS"
    max_completion_tokens: int = 512
    timeout_seconds: float = 30.0
    # When True, attach HttpGrokTransport if env key is present and no transport given.
    auto_http: bool = True


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _should_send_reasoning_effort(model_id: str, effort: str) -> bool:
    """Omit reasoning_effort for non-reasoning models (they may reject it)."""
    if not effort or effort in {"none", "off", "disable", "disabled"}:
        return False
    mid = model_id.lower()
    if "non-reasoning" in mid:
        return False
    return (
        "grok-4.5" in mid
        or mid.endswith("-reasoning")
        or "multi-agent" in mid
        or "grok-4.3" in mid
    )


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
        # Env overrides win over the config default so operators can bump the
        # model id / endpoint via /etc/zerodte/zerodte.env with no redeploy.
        self._model_id = os.environ.get(self.cfg.model_id_env, "").strip() or self.cfg.model_id
        self._api_base = os.environ.get(self.cfg.api_base_env, "").strip() or self.cfg.api_base
        effort = os.environ.get(self.cfg.reasoning_effort_env, "").strip().lower()
        if effort and effort not in {"low", "medium", "high", "none", "off", "disable"}:
            effort = self.cfg.reasoning_effort
        elif not effort:
            effort = self.cfg.reasoning_effort
        self._reasoning_effort = effort
        self._max_completion_tokens = _env_int(
            self.cfg.max_completion_tokens_env, self.cfg.max_completion_tokens
        )
        if self._transport is None and self.cfg.auto_http and self._resolve_api_key():
            self._transport = make_http_grok_transport(
                timeout_seconds=self.cfg.timeout_seconds
            )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def transport(self) -> GrokTransport | None:
        return self._transport

    @property
    def api_key(self) -> str:
        return self._resolve_api_key()

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            provider="grok",
            model_id=self._model_id,
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

    def call_raw(self, prompt: dict[str, str]) -> str:
        """Low-level chat call used by entry/position/review prompts."""
        return self._call(prompt)

    def _call(self, prompt: dict[str, str]) -> str:
        assert self._transport is not None
        api_key = self._resolve_api_key()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        }
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "temperature": 0.0,
            # Cap completion (+ reasoning) tokens so a single tick cannot run away.
            "max_completion_tokens": self._max_completion_tokens,
        }
        if _should_send_reasoning_effort(self._model_id, self._reasoning_effort):
            body["reasoning_effort"] = self._reasoning_effort
        raw = self._transport(self._api_base, headers, body)
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
