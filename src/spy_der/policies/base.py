"""PolicyService protocol (spec §36)."""

from __future__ import annotations

from typing import Protocol

from spy_der.contracts.policies import PolicyDecisionView, PolicyIdentity, PolicyInputPacket

__all__ = ["PolicyService"]


class PolicyService(Protocol):
    @property
    def identity(self) -> PolicyIdentity: ...

    def evaluate(self, packet: PolicyInputPacket) -> PolicyDecisionView: ...
