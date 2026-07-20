"""Phase 17 - controlled cutover tests (spec §63)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from spy_der.agents import MockDecisionAgent, build_agent_decision_packet
from spy_der.contracts import (
    AgentEntryAction,
    Candidate,
    CandidateLeg,
    CandidateUniverse,
    CanonicalMarketSnapshot,
    DebitCredit,
    OptionType,
    PolicyAction,
    PolicyDecisionView,
    SessionStatus,
)
from spy_der.deployment import (
    AgentDeploymentManifest,
    CutoverApproval,
    CutoverPhase,
    DeploymentError,
    DeploymentMode,
    LiveExecutionGate,
    RuntimeSystem,
    activate_controlled_cutover,
    get_runbook,
)
from spy_der.runtime import PrimaryResearchRuntime


def _approval() -> CutoverApproval:
    return CutoverApproval(
        approved_by="repository-owner",
        approved_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        approval_note="Phase 17 activate.",
        phase="phase-17",
    )


def test_activate_makes_system_b_primary_with_system_a_rollback() -> None:
    ctl = activate_controlled_cutover(approval=_approval())
    snap = ctl.snapshot()
    assert snap.system_b_is_primary
    assert snap.system_a_is_rollback
    assert snap.live_execution_disabled
    assert snap.agent_independently_controlled
    assert ctl.state.phase is CutoverPhase.ACTIVE
    assert ctl.state.primary is RuntimeSystem.SYSTEM_B
    assert ctl.state.rollback is RuntimeSystem.SYSTEM_A
    assert ctl.state.primary_manifest.mode is DeploymentMode.SHADOW
    assert ctl.state.rollback_manifest.account_id.startswith("system_a_")
    assert ctl.pointer.current.deployment_id == "system-b-primary-shadow"
    assert len(ctl.pointer.history) == 1
    assert ctl.pointer.history[0].deployment_id == "system-a-rollback"


def test_live_execution_cannot_be_enabled() -> None:
    with pytest.raises(DeploymentError, match="cannot be enabled"):
        LiveExecutionGate(enabled=True)
    gate = LiveExecutionGate()
    with pytest.raises(DeploymentError, match="forbidden"):
        gate.attempt_enable()
    ctl = activate_controlled_cutover(approval=_approval())
    ctl.assert_live_execution_disabled()
    assert ctl.state.live_execution.enabled is False


def test_agent_authority_independent_of_cutover() -> None:
    ctl = activate_controlled_cutover(approval=_approval(), agent_enabled=True)
    assert ctl.state.agent_authority.enabled
    ctl.set_agent_authority(enabled=False)
    assert ctl.state.phase is CutoverPhase.ACTIVE
    assert ctl.state.primary is RuntimeSystem.SYSTEM_B
    assert ctl.state.agent_authority.enabled is False
    ctl.set_agent_authority(enabled=True)
    assert ctl.state.agent_authority.enabled
    assert ctl.state.agent_authority.permits("SELECT_CANDIDATE")
    assert not ctl.state.agent_authority.permits("ADD")  # not permitted


def test_rollback_to_system_a() -> None:
    ctl = activate_controlled_cutover(approval=_approval())
    ctl.rollback_to_system_a(reason="parity_regression")
    assert ctl.state.phase is CutoverPhase.ROLLED_BACK
    assert ctl.state.primary is RuntimeSystem.SYSTEM_A
    assert ctl.state.live_execution.enabled is False
    snap = ctl.snapshot()
    assert not snap.system_b_is_primary


def test_requires_owner_approval_fields() -> None:
    with pytest.raises(DeploymentError, match="approved_by"):
        CutoverApproval(
            approved_by=" ",
            approved_at=datetime(2026, 7, 20, tzinfo=UTC),
            approval_note="ok",
        )
    with pytest.raises(DeploymentError, match="phase-17"):
        activate_controlled_cutover(
            approval=CutoverApproval(
                approved_by="owner",
                approved_at=datetime(2026, 7, 20, tzinfo=UTC),
                approval_note="ok",
                phase="phase-16",
            )
        )


def test_primary_runtime_respects_agent_gate() -> None:
    ctl = activate_controlled_cutover(approval=_approval(), agent_enabled=False)
    agent = MockDecisionAgent(action=AgentEntryAction.SELECT_CANDIDATE, candidate_id="c1")
    runtime = PrimaryResearchRuntime.from_cutover(ctl, agent)
    result = runtime.tick(packet=_entry_packet(), now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC))
    assert not result.accepted
    assert result.reason == "agent_authority_disabled"

    ctl.set_agent_authority(enabled=True)
    result2 = runtime.tick(packet=_entry_packet(), now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC))
    assert result2.accepted
    assert result2.loop_result is not None
    assert runtime.live_state()["live_execution_enabled"] is False
    assert runtime.live_state()["cutover"]["primary"] == "system_b"


def test_cutover_runbook_exists() -> None:
    rb = get_runbook("cutover")
    assert "System B" in " ".join(rb.steps)
    assert "live" in " ".join(rb.steps).lower()


def test_agent_manifest_configuration_hash_stable() -> None:
    m = AgentDeploymentManifest(
        deployment_id="agent-1",
        provider="grok",
        model_id="grok-2",
        adapter_version="v2",
        prompt_version="p1",
    )
    assert m.configuration_hash == m.configuration_hash
    assert m.configuration_hash.startswith("sha256:")


def _entry_packet():
    now = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
    exp = date(2026, 7, 20)
    cand = Candidate(
        candidate_id="c1",
        snapshot_id="snap-17",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("100"),
                quantity=1,
                expiration=exp,
                contract_id="c1",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal("10"),
        breakevens=(),
        capital_required=Decimal("10"),
        terminal_payoff_hash="sha256:pay",
        geometry_hash="sha256:c1",
    )
    universe = CandidateUniverse(
        universe_id="u17", snapshot_id="snap-17", candidates=(cand,)
    )
    snap = CanonicalMarketSnapshot(
        snapshot_id="snap-17",
        content_hash="sha256:17",
        timestamp=now,
        session_date=date(2026, 7, 20),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        minutes_to_close=90,
    )
    return build_agent_decision_packet(
        snapshot=snap,
        universe=universe,
        created_at=now,
        policy_views=(
            PolicyDecisionView(
                policy_name="ensemble",
                policy_version="v1",
                action=PolicyAction.SELECT_CANDIDATE,
                candidate_id="c1",
                size_cap=1.0,
                confidence=0.7,
                uncertainty=0.3,
            ),
        ),
        risk_max_size_scalar=1.0,
    )
