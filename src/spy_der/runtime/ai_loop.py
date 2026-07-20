"""Shadow AI decision loop — wires AI into entry, exit, track, analyze.

Paper/shadow only. Live broker routing remains Phase-17 gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from spy_der.agents.authority import AiAnalysisSnapshot, AiDecisionAuthority
from spy_der.agents.position_packet import build_position_decision_packet
from spy_der.agents.protocols import DecisionAgent
from spy_der.contracts.agents import (
    AgentDecisionPacket,
    AgentEntryAction,
    DeploymentContext,
    ExitPolicySummary,
    SnapshotSummary,
)
from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.contracts.execution import OrderIntent, OrderStatus
from spy_der.contracts.positions import ApprovedExitPolicyId, ExitPolicy, PositionState
from spy_der.execution.accounts import IsolatedAccountBook
from spy_der.execution.simulator import PaperExecutionSimulator
from spy_der.journal.store import InMemoryJournalStore
from spy_der.positions.manager import PositionManager

__all__ = ["AiLoopTickResult", "ShadowAiLoop", "default_approved_exits"]


def default_approved_exits() -> tuple[ExitPolicySummary, ...]:
    return (
        ExitPolicySummary(ApprovedExitPolicyId.TARGET_AND_STOP.value, "target_and_stop"),
        ExitPolicySummary(ApprovedExitPolicyId.TRAILING.value, "trailing"),
        ExitPolicySummary(ApprovedExitPolicyId.EOD_EXIT.value, "eod"),
        ExitPolicySummary(ApprovedExitPolicyId.TIME_EXIT.value, "time"),
        ExitPolicySummary(ApprovedExitPolicyId.EMERGENCY_EXIT.value, "emergency"),
    )


@dataclass(frozen=True, slots=True)
class AiLoopTickResult:
    entry_action: str | None
    entry_candidate_id: str | None
    position_actions: tuple[tuple[str, str], ...]
    opened_position_ids: tuple[str, ...]
    closed_position_ids: tuple[str, ...]
    analysis: AiAnalysisSnapshot
    tracker: dict[str, Any]


@dataclass
class ShadowAiLoop:
    """AI-owned shadow loop over paper accounts.

    Every major lever is watched by the AI:
    - entry selection
    - exit / manage on open positions
    - continuous tracker state
    - per-tick analyzer snapshot
    """

    authority: AiDecisionAuthority
    accounts: IsolatedAccountBook = field(default_factory=IsolatedAccountBook)
    simulator: PaperExecutionSimulator | None = None
    positions: PositionManager | None = None
    account_id: str = "system_b_grok"
    default_contracts: int = 1
    default_exit_policy: ExitPolicy = field(default_factory=ExitPolicy)
    approved_exits: tuple[ExitPolicySummary, ...] = field(
        default_factory=default_approved_exits
    )

    def __post_init__(self) -> None:
        if self.simulator is None:
            self.simulator = PaperExecutionSimulator(accounts=self.accounts)
        if self.positions is None:
            self.positions = PositionManager(
                accounts=self.accounts, default_policy=self.default_exit_policy
            )

    @classmethod
    def with_agent(
        cls,
        agent: DecisionAgent,
        *,
        journal: InMemoryJournalStore | None = None,
        account_id: str = "system_b_grok",
        deployment_id: str = "system_b_grok",
    ) -> ShadowAiLoop:
        authority = AiDecisionAuthority(
            agent,
            journal=journal,
            deployment_id=deployment_id,
            account_id=account_id,
        )
        return cls(authority=authority, account_id=account_id)

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
    ) -> AiLoopTickResult:
        assert self.simulator is not None
        assert self.positions is not None
        now = now or datetime.now(tz=UTC)
        marks = marks or {}

        entry_action: str | None = None
        entry_candidate: str | None = None
        opened: list[str] = []
        closed: list[str] = []
        position_actions: list[tuple[str, str]] = []

        # 1) Exit / manage open positions first (AI exit maker).
        for pos in self.positions.open_positions(self.account_id):
            mark = marks.get(pos.position_id) or marks.get(pos.candidate_id) or pos.mark_price
            if mark is None:
                continue
            mark_d = Decimal(str(mark))
            det = self.positions.evaluate_exit(
                pos.position_id,
                mark_price=mark_d,
                now=now,
                ras_exit=ras_exit,
                emergency=emergency,
                expired=expired,
            )
            signal = det.reason if det.should_exit else ""
            hard: tuple[str, ...] = ("emergency",) if emergency else ()
            pos_packet = build_position_decision_packet(
                position=self.positions.get(pos.position_id),
                snapshot=packet.snapshot_summary
                if packet is not None
                else _snapshot_from_position(pos, now),
                mark_price=mark_d,
                now=now,
                approved_exit_policies=self.approved_exits,
                hard_vetoes=hard,
                deterministic_exit_signal=signal,
                data_quality=packet.data_quality if packet is not None else 1.0,
                forecast_uncertainty=(
                    packet.forecast_uncertainty if packet is not None else 0.0
                ),
                deployment_context=(
                    packet.deployment_context
                    if packet is not None
                    else DeploymentContext(mode="shadow")
                ),
            )
            result = self.authority.decide_position(pos_packet, now=now)
            position_actions.append((pos.position_id, result.response.action.value))
            if result.should_close:
                closed_pos = self.positions.close(
                    pos.position_id,
                    exit_price=mark_d,
                    reason=result.response.rationale or result.tracked_as,
                    now=now,
                )
                closed.append(closed_pos.position_id)
                self._journal_position(
                    JournalEventType.POSITION_CLOSED, closed_pos, now
                )
            elif result.should_reduce:
                reduced = self.positions.reduce(
                    pos.position_id,
                    fraction=result.reduce_fraction,
                    mark_price=mark_d,
                    reason=result.response.rationale or result.tracked_as,
                    now=now,
                )
                if reduced.open_contracts <= 0:
                    closed.append(reduced.position_id)
                    self._journal_position(
                        JournalEventType.POSITION_CLOSED, reduced, now
                    )
                else:
                    self._journal_position(
                        JournalEventType.POSITION_REDUCED, reduced, now
                    )

        # 2) Entry decision (AI entry maker) when flat / allowed.
        if (
            allow_entries
            and packet is not None
            and not self.positions.open_positions(self.account_id)
        ):
            entry = self.authority.decide_entry(packet, now=now)
            entry_action = entry.response.action.value
            entry_candidate = entry.response.candidate_id
            if entry.selected and entry.response.candidate_id:
                view = packet.candidate(entry.response.candidate_id)
                limit = Decimal("1")
                if view is not None and view.expected_fill_price is not None:
                    limit = Decimal(str(view.expected_fill_price))
                elif view is not None and view.mid_price is not None:
                    limit = Decimal(str(view.mid_price))
                contracts = max(
                    1, round(self.default_contracts * entry.response.size_scalar)
                )
                exit_policy_id = (
                    entry.response.exit_policy_id
                    or self.default_exit_policy.policy_id
                )
                intent = OrderIntent(
                    account_id=self.account_id,
                    candidate_id=entry.response.candidate_id,
                    contracts=contracts,
                    limit_price=limit,
                    created_at=now,
                    exit_policy_id=exit_policy_id,
                    geometry_hash=entry.response.geometry_hash or "",
                    snapshot_id=packet.snapshot_summary.snapshot_id,
                    fill_probability=1.0,
                )
                order = self.simulator.submit(intent, now=now)
                self.authority.journal.append(
                    JournalEvent(
                        event_type=JournalEventType.ORDER_SUBMITTED_SIMULATED,
                        aggregate_type=AggregateType.ORDER,
                        aggregate_id=order.order_id,
                        occurred_at=now,
                        payload={
                            "candidate_id": order.candidate_id,
                            "status": order.status.value,
                            "contracts": order.requested_contracts,
                        },
                        deployment_id=self.authority.deployment_id,
                        snapshot_id=packet.snapshot_summary.snapshot_id,
                    )
                )
                if order.status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}:
                    opened_pos = self.positions.on_order_state(
                        order,
                        max_loss=view.maximum_loss if view else Decimal("0"),
                        exit_policy_id=exit_policy_id,
                        now=now,
                    )
                    if opened_pos is not None:
                        opened.append(opened_pos.position_id)
                        self._journal_position(
                            JournalEventType.POSITION_OPENED, opened_pos, now
                        )
                elif entry.response.action is AgentEntryAction.SELECT_CANDIDATE:
                    # Still selected; fill just did not happen.
                    pass

        open_ids = [p.position_id for p in self.positions.open_positions(self.account_id)]
        self.authority.track_open_positions(open_ids)
        analysis = self.authority.analyze(
            now=now,
            entry_action=entry_action,
            position_action=position_actions[-1][1] if position_actions else None,
            levers={
                "entry": entry_action or "skipped",
                "exit": [a for _, a in position_actions],
                "opened": opened,
                "closed": closed,
                "open_positions": open_ids,
                "allow_entries": allow_entries,
                "emergency": emergency,
                "account_id": self.account_id,
            },
        )
        return AiLoopTickResult(
            entry_action=entry_action,
            entry_candidate_id=entry_candidate,
            position_actions=tuple(position_actions),
            opened_position_ids=tuple(opened),
            closed_position_ids=tuple(closed),
            analysis=analysis,
            tracker=self.authority.tracker.as_dict(),
        )

    def live_state(self) -> dict[str, Any]:
        """Dashboard-shaped parallel-track payload (observation only)."""
        assert self.positions is not None
        opens = [
            {
                "position_id": p.position_id,
                "candidate_id": p.candidate_id,
                "open_contracts": p.open_contracts,
                "entry_price": str(p.entry_price) if p.entry_price is not None else None,
                "mark_price": str(p.mark_price) if p.mark_price is not None else None,
                "exit_policy_id": p.exit_policy_id,
                "unrealized_pnl": str(p.unrealized_pnl),
            }
            for p in self.positions.open_positions(self.account_id)
        ]
        return {
            "track": "system_b_grok",
            "role": "ai_decision_maker",
            "account_id": self.account_id,
            "agent": {
                "provider": self.authority.agent.identity.provider,
                "model_id": self.authority.agent.identity.model_id,
                "adapter_version": self.authority.agent.identity.adapter_version,
                "healthy": self.authority.agent.health().healthy,
            },
            "tracker": self.authority.tracker.as_dict(),
            "open_positions": opens,
            "mode": "shadow",
        }

    def _journal_position(
        self,
        event_type: JournalEventType,
        position: PositionState,
        now: datetime,
    ) -> None:
        self.authority.journal.append(
            JournalEvent(
                event_type=event_type,
                aggregate_type=AggregateType.POSITION,
                aggregate_id=position.position_id,
                occurred_at=now,
                payload={
                    "account_id": position.account_id,
                    "candidate_id": position.candidate_id,
                    "status": position.status.value,
                    "open_contracts": position.open_contracts,
                    "exit_reason": position.exit_reason,
                    "realized_pnl": str(position.realized_pnl),
                },
                deployment_id=self.authority.deployment_id,
            )
        )


def _snapshot_from_position(pos: PositionState, now: datetime) -> SnapshotSummary:
    return SnapshotSummary(
        snapshot_id=f"pos-{pos.position_id[:12]}",
        symbol="SPY",
        session_date=now.date(),
        underlying_price=pos.mark_price or pos.entry_price or Decimal("0"),
    )
