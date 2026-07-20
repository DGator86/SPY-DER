"""Provider-neutral DecisionAgent protocol (spec §37)."""

from __future__ import annotations

from typing import Protocol

from spy_der.contracts.agents import (
    AgentCapabilities,
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentHealth,
    AgentIdentity,
)

__all__ = ["DecisionAgent"]


class DecisionAgent(Protocol):
    @property
    def identity(self) -> AgentIdentity: ...

    @property
    def capabilities(self) -> AgentCapabilities: ...

    def decide_entry(self, packet: AgentDecisionPacket) -> AgentDecisionResponse: ...

    def health(self) -> AgentHealth: ...
