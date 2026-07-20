"""Multi-agent shadow comparison (observation-only; never execution authority)."""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.agents.protocols import DecisionAgent
from spy_der.agents.runtime import FailClosedAgentRuntime
from spy_der.contracts.agents import AgentDecisionPacket, AgentDecisionResponse, AgentEntryAction

__all__ = ["ShadowComparisonResult", "compare_agents"]


@dataclass(frozen=True, slots=True)
class ShadowComparisonResult:
    packet_id: str
    packet_hash: str
    authoritative_provider: str
    authoritative: AgentDecisionResponse
    shadows: tuple[tuple[str, AgentDecisionResponse], ...]
    action_disagreement: bool
    candidate_disagreement: bool
    diagnostics: tuple[tuple[str, str], ...] = ()


def compare_agents(
    packet: AgentDecisionPacket,
    *,
    authoritative: DecisionAgent,
    shadows: dict[str, DecisionAgent],
) -> ShadowComparisonResult:
    """Run fail-closed agents; comparison is observational only."""
    auth_runtime = FailClosedAgentRuntime(authoritative)
    auth_resp = auth_runtime.decide_entry(packet)
    shadow_rows: list[tuple[str, AgentDecisionResponse]] = []
    for name, agent in sorted(shadows.items()):
        resp = FailClosedAgentRuntime(agent).decide_entry(packet)
        shadow_rows.append((name, resp))

    action_dis = any(r.action != auth_resp.action for _, r in shadow_rows)
    cand_dis = False
    if auth_resp.action is AgentEntryAction.SELECT_CANDIDATE:
        cand_dis = any(
            r.action is AgentEntryAction.SELECT_CANDIDATE
            and r.candidate_id != auth_resp.candidate_id
            for _, r in shadow_rows
        )
    return ShadowComparisonResult(
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        authoritative_provider=authoritative.identity.provider,
        authoritative=auth_resp,
        shadows=tuple(shadow_rows),
        action_disagreement=action_dis,
        candidate_disagreement=cand_dis,
        diagnostics=(
            ("shadow_count", str(len(shadow_rows))),
            ("auth_action", auth_resp.action.value),
        ),
    )
