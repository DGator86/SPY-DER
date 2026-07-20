"""AI Decision Authority — entry maker, exit maker, tracker, analyzer.

The AI agent is the decision maker for every major lever. Deterministic risk,
validation, and hard exit floors remain fail-closed guards around the AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from spy_der.agents.protocols import DecisionAgent
from spy_der.agents.runtime import FailClosedAgentRuntime
from spy_der.contracts.agents import (
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    AgentIdentity,
    AgentPositionAction,
    AgentPositionResponse,
    PositionDecisionPacket,
)
from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.journal.store import InMemoryJournalStore

__all__ = [
    "AiAnalysisSnapshot",
    "AiDecisionAuthority",
    "AiTrackerState",
    "EntryDecisionResult",
    "PositionDecisionResult",
]


@dataclass(frozen=True, slots=True)
class EntryDecisionResult:
    response: AgentDecisionResponse
    selected: bool
    tracked_as: str


@dataclass(frozen=True, slots=True)
class PositionDecisionResult:
    response: AgentPositionResponse
    should_close: bool
    should_reduce: bool
    reduce_fraction: float
    tracked_as: str


@dataclass
class AiTrackerState:
    """In-process view of what the AI is watching right now."""

    ticks: int = 0
    last_entry_action: str = ""
    last_entry_candidate_id: str | None = None
    last_position_action: str = ""
    last_position_id: str | None = None
    open_position_ids: list[str] = field(default_factory=list)
    entry_selects: int = 0
    entry_no_edge: int = 0
    entry_abstains: int = 0
    position_holds: int = 0
    position_reduces: int = 0
    position_closes: int = 0
    last_rationale: str = ""
    last_reason_codes: tuple[str, ...] = ()
    agent_provider: str = ""
    agent_model_id: str = ""
    healthy: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticks": self.ticks,
            "last_entry_action": self.last_entry_action,
            "last_entry_candidate_id": self.last_entry_candidate_id,
            "last_position_action": self.last_position_action,
            "last_position_id": self.last_position_id,
            "open_position_ids": list(self.open_position_ids),
            "entry_selects": self.entry_selects,
            "entry_no_edge": self.entry_no_edge,
            "entry_abstains": self.entry_abstains,
            "position_holds": self.position_holds,
            "position_reduces": self.position_reduces,
            "position_closes": self.position_closes,
            "last_rationale": self.last_rationale,
            "last_reason_codes": list(self.last_reason_codes),
            "agent_provider": self.agent_provider,
            "agent_model_id": self.agent_model_id,
            "healthy": self.healthy,
        }


@dataclass(frozen=True, slots=True)
class AiAnalysisSnapshot:
    """Analyzer output summarizing AI-watched levers on a tick."""

    timestamp: datetime
    agent: AgentIdentity
    tracker: dict[str, Any]
    entry_action: str | None
    position_action: str | None
    levers: dict[str, Any]
    summary: str


class AiDecisionAuthority:
    """Single authority surface: entry, exit/manage, track, analyze."""

    def __init__(
        self,
        agent: DecisionAgent,
        *,
        journal: InMemoryJournalStore | None = None,
        deployment_id: str = "system_b_grok",
        account_id: str = "system_b_grok",
    ) -> None:
        self.agent = agent
        self.runtime = FailClosedAgentRuntime(agent)
        self.journal = journal if journal is not None else InMemoryJournalStore()
        self.deployment_id = deployment_id
        self.account_id = account_id
        self.tracker = AiTrackerState(
            agent_provider=agent.identity.provider,
            agent_model_id=agent.identity.model_id,
            healthy=agent.health().healthy,
        )

    def decide_entry(
        self,
        packet: AgentDecisionPacket,
        *,
        now: datetime | None = None,
    ) -> EntryDecisionResult:
        now = now or datetime.now(tz=UTC)
        self._journal_request("entry", packet.packet_id, packet.packet_hash, now)
        response = self.runtime.decide_entry(packet, now=now)
        self._journal_decided(
            "entry",
            response,
            now,
            snapshot_id=packet.snapshot_summary.snapshot_id,
        )

        self.tracker.ticks += 1
        self.tracker.last_entry_action = response.action.value
        self.tracker.last_entry_candidate_id = response.candidate_id
        self.tracker.last_rationale = response.rationale
        self.tracker.last_reason_codes = response.reason_codes
        self.tracker.healthy = self.agent.health().healthy
        if response.action is AgentEntryAction.SELECT_CANDIDATE:
            self.tracker.entry_selects += 1
            tracked = "entry_select"
        elif response.action is AgentEntryAction.NO_EDGE:
            self.tracker.entry_no_edge += 1
            tracked = "entry_no_edge"
        else:
            self.tracker.entry_abstains += 1
            tracked = "entry_abstain"

        return EntryDecisionResult(
            response=response,
            selected=response.action is AgentEntryAction.SELECT_CANDIDATE,
            tracked_as=tracked,
        )

    def decide_position(
        self,
        packet: PositionDecisionPacket,
        *,
        now: datetime | None = None,
    ) -> PositionDecisionResult:
        now = now or datetime.now(tz=UTC)
        # Deterministic hard floors override AI HOLD before/after call.
        forced = _forced_close(packet)
        self._journal_request(
            "position", packet.packet_id, packet.packet_hash, now
        )
        if forced is not None:
            response = forced
        else:
            response = self.runtime.decide_position(packet, now=now)
            # Re-apply floor if validation somehow allowed HOLD under hard signal.
            if _needs_forced_close(packet) and response.action is AgentPositionAction.HOLD:
                response = _forced_close(packet) or response

        self._journal_decided(
            "position",
            response,
            now,
            snapshot_id=packet.snapshot_summary.snapshot_id,
            position_id=packet.position.position_id,
        )

        self.tracker.ticks += 1
        self.tracker.last_position_action = response.action.value
        self.tracker.last_position_id = packet.position.position_id
        self.tracker.last_rationale = response.rationale
        self.tracker.last_reason_codes = response.reason_codes
        self.tracker.healthy = self.agent.health().healthy
        if response.action is AgentPositionAction.CLOSE:
            self.tracker.position_closes += 1
            tracked = "position_close"
        elif response.action is AgentPositionAction.REDUCE:
            self.tracker.position_reduces += 1
            tracked = "position_reduce"
        else:
            self.tracker.position_holds += 1
            tracked = "position_hold"

        return PositionDecisionResult(
            response=response,
            should_close=response.action is AgentPositionAction.CLOSE,
            should_reduce=response.action is AgentPositionAction.REDUCE,
            reduce_fraction=response.reduce_fraction,
            tracked_as=tracked,
        )

    def track_open_positions(self, position_ids: list[str] | tuple[str, ...]) -> None:
        self.tracker.open_position_ids = list(position_ids)

    def analyze(
        self,
        *,
        now: datetime | None = None,
        entry_action: str | None = None,
        position_action: str | None = None,
        levers: dict[str, Any] | None = None,
    ) -> AiAnalysisSnapshot:
        now = now or datetime.now(tz=UTC)
        levers = levers or {
            "entry": self.tracker.last_entry_action or "idle",
            "exit": self.tracker.last_position_action or "idle",
            "open_positions": len(self.tracker.open_position_ids),
            "health": self.tracker.healthy,
            "data_quality_watched": True,
            "risk_cap_watched": True,
            "exit_policy_watched": True,
            "hard_veto_watched": True,
        }
        summary = (
            f"AI[{self.tracker.agent_provider}/{self.tracker.agent_model_id}] "
            f"entry={self.tracker.last_entry_action or '-'} "
            f"exit={self.tracker.last_position_action or '-'} "
            f"open={len(self.tracker.open_position_ids)} "
            f"healthy={self.tracker.healthy}"
        )
        snap = AiAnalysisSnapshot(
            timestamp=now,
            agent=self.agent.identity,
            tracker=self.tracker.as_dict(),
            entry_action=entry_action or self.tracker.last_entry_action or None,
            position_action=position_action or self.tracker.last_position_action or None,
            levers=levers,
            summary=summary,
        )
        self.journal.append(
            JournalEvent(
                event_type=JournalEventType.SYSTEM_DECIDED,
                aggregate_type=AggregateType.SESSION,
                aggregate_id=self.account_id,
                occurred_at=now,
                payload={
                    "kind": "ai_analysis",
                    "summary": snap.summary,
                    "levers": snap.levers,
                    "tracker": snap.tracker,
                },
                deployment_id=self.deployment_id,
            )
        )
        return snap

    def _journal_request(
        self,
        kind: str,
        packet_id: str,
        packet_hash: str,
        now: datetime,
    ) -> None:
        self.journal.append(
            JournalEvent(
                event_type=JournalEventType.AGENT_REQUESTED,
                aggregate_type=AggregateType.SESSION,
                aggregate_id=self.account_id,
                occurred_at=now,
                payload={
                    "kind": kind,
                    "packet_id": packet_id,
                    "packet_hash": packet_hash,
                    "provider": self.agent.identity.provider,
                    "model_id": self.agent.identity.model_id,
                },
                deployment_id=self.deployment_id,
            )
        )

    def _journal_decided(
        self,
        kind: str,
        response: AgentDecisionResponse | AgentPositionResponse,
        now: datetime,
        *,
        snapshot_id: str = "",
        position_id: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "kind": kind,
            "packet_id": response.packet_id,
            "packet_hash": response.packet_hash,
            "action": response.action.value,
            "confidence": response.confidence,
            "uncertainty": response.uncertainty,
            "reason_codes": list(response.reason_codes),
            "rationale": response.rationale,
            "model_id": response.model_id,
            "prompt_version": response.prompt_version,
        }
        if isinstance(response, AgentDecisionResponse):
            payload["candidate_id"] = response.candidate_id
            payload["size_scalar"] = response.size_scalar
            payload["exit_policy_id"] = response.exit_policy_id
        else:
            payload["reduce_fraction"] = response.reduce_fraction
            payload["position_id"] = position_id
        failed = "validation_failure" in response.reason_codes or any(
            c.endswith("_failure") or c.endswith("_missing") for c in response.reason_codes
        )
        etype = JournalEventType.AGENT_FAILED if failed else JournalEventType.AGENT_DECIDED
        self.journal.append(
            JournalEvent(
                event_type=etype,
                aggregate_type=AggregateType.SESSION,
                aggregate_id=self.account_id,
                occurred_at=now,
                payload=payload,
                deployment_id=self.deployment_id,
                snapshot_id=snapshot_id or None,
            )
        )


# Hard floors override the AI. Soft signals (target/trail/time) inform the AI
# but do not strip exit authority from the agent.
_HARD_EXIT_SIGNALS = frozenset(
    {
        "emergency_exit",
        "stop",
        "eod",
        "expiration_settlement",
        "structural_ras_exit",
    }
)


def _needs_forced_close(packet: PositionDecisionPacket) -> bool:
    return bool(packet.hard_vetoes) or packet.deterministic_exit_signal in _HARD_EXIT_SIGNALS


def _forced_close(packet: PositionDecisionPacket) -> AgentPositionResponse | None:
    if not _needs_forced_close(packet):
        return None
    signal = packet.deterministic_exit_signal or "hard_veto"
    return AgentPositionResponse(
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        action=AgentPositionAction.CLOSE,
        confidence=1.0,
        uncertainty=0.0,
        reason_codes=("deterministic_exit_floor", signal),
        rationale=f"forced close: {signal}",
        model_id="authority-floor",
        prompt_version="authority.v1",
    )
