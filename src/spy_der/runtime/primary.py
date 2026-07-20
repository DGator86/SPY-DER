"""Primary research/shadow runtime under Phase 17 controlled cutover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from spy_der.agents.authority import AiDecisionAuthority
from spy_der.agents.protocols import DecisionAgent
from spy_der.contracts.agents import AgentDecisionPacket
from spy_der.deployment.cutover import (
    ControlledCutover,
    CutoverPhase,
    RuntimeSystem,
)
from spy_der.runtime.ai_loop import AiLoopTickResult, ShadowAiLoop

__all__ = ["PrimaryResearchRuntime", "PrimaryTickResult"]


@dataclass(frozen=True, slots=True)
class PrimaryTickResult:
    accepted: bool
    reason: str
    loop_result: AiLoopTickResult | None
    cutover: dict[str, Any]


@dataclass
class PrimaryResearchRuntime:
    """System B primary shadow runtime gated by ControlledCutover.

    Live execution is never available here. Agent authority can be toggled
    independently of which system is primary.
    """

    cutover: ControlledCutover
    loop: ShadowAiLoop

    @classmethod
    def from_cutover(
        cls,
        cutover: ControlledCutover,
        agent: DecisionAgent,
    ) -> PrimaryResearchRuntime:
        authority = AiDecisionAuthority(
            agent,
            journal=cutover.journal,
            deployment_id=cutover.state.primary_manifest.deployment_id,
            account_id=cutover.state.agent_authority.manifest.allowed_account_ids[0]
            if cutover.state.agent_authority.manifest.allowed_account_ids
            else "system_b_grok",
        )
        loop = ShadowAiLoop(
            authority=authority,
            account_id=authority.account_id,
        )
        return cls(cutover=cutover, loop=loop)

    def tick(
        self,
        *,
        packet: AgentDecisionPacket | None,
        marks: dict[str, Decimal] | None = None,
        now: datetime | None = None,
        allow_entries: bool = True,
        emergency: bool = False,
        ras_exit: bool = False,
        expired: bool = False,
    ) -> PrimaryTickResult:
        self.cutover.assert_live_execution_disabled()
        snap = self.cutover.snapshot()
        if self.cutover.state.phase is not CutoverPhase.ACTIVE:
            return PrimaryTickResult(
                accepted=False,
                reason=f"cutover_not_active:{self.cutover.state.phase.value}",
                loop_result=None,
                cutover=snap.as_dict(),
            )
        if self.cutover.state.primary is not RuntimeSystem.SYSTEM_B:
            return PrimaryTickResult(
                accepted=False,
                reason="primary_is_not_system_b",
                loop_result=None,
                cutover=snap.as_dict(),
            )
        if not self.cutover.state.agent_authority.enabled:
            return PrimaryTickResult(
                accepted=False,
                reason="agent_authority_disabled",
                loop_result=None,
                cutover=snap.as_dict(),
            )

        result = self.loop.tick(
            packet=packet,
            marks=marks,
            now=now,
            allow_entries=allow_entries,
            emergency=emergency,
            ras_exit=ras_exit,
            expired=expired,
        )
        return PrimaryTickResult(
            accepted=True,
            reason="ok",
            loop_result=result,
            cutover=snap.as_dict(),
        )

    def live_state(self) -> dict[str, Any]:
        base = self.loop.live_state()
        snap = self.cutover.snapshot().as_dict()
        return {
            **base,
            "cutover": snap,
            "primary_runtime": "system_b",
            "rollback_runtime": "system_a",
            "live_execution_enabled": False,
        }
