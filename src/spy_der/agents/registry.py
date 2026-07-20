"""Agent registry / factory (spec §37)."""

from __future__ import annotations

from collections.abc import Callable

from spy_der.agents.protocols import DecisionAgent

__all__ = ["AgentRegistry"]

AgentFactory = Callable[[], DecisionAgent]


class AgentRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AgentFactory] = {}

    def register(self, provider: str, factory: AgentFactory) -> None:
        key = provider.strip().lower()
        if not key:
            raise ValueError("provider name required")
        self._factories[key] = factory

    def create(self, provider: str) -> DecisionAgent:
        key = provider.strip().lower()
        if key not in self._factories:
            raise KeyError(f"unknown agent provider: {provider}")
        return self._factories[key]()

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
