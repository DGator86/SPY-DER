"""Approved exit-policy evaluation (spec §52)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from spy_der.contracts.positions import ApprovedExitPolicyId, ExitPolicy, PositionState

__all__ = ["ExitSignal", "evaluate_exit"]

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class ExitSignal:
    should_exit: bool
    reason: str = ""
    policy_id: str = ""


def evaluate_exit(
    position: PositionState,
    *,
    mark_price: Decimal,
    now: datetime,
    policy: ExitPolicy | None = None,
    ras_exit: bool = False,
    emergency: bool = False,
    expired: bool = False,
) -> ExitSignal:
    policy = policy or ExitPolicy(policy_id=position.exit_policy_id)
    if position.open_contracts <= 0:
        return ExitSignal(False, reason="flat")

    if emergency or policy.policy_id == ApprovedExitPolicyId.EMERGENCY_EXIT.value:
        if emergency:
            return ExitSignal(True, reason="emergency_exit", policy_id=policy.policy_id)

    if expired or policy.policy_id == ApprovedExitPolicyId.EXPIRATION_SETTLEMENT.value:
        if expired:
            return ExitSignal(True, reason="expiration_settlement", policy_id=policy.policy_id)

    if ras_exit and policy.policy_id in {
        ApprovedExitPolicyId.STRUCTURAL_RAS_EXIT.value,
        ApprovedExitPolicyId.TARGET_AND_STOP.value,
        ApprovedExitPolicyId.TRAILING.value,
    }:
        return ExitSignal(True, reason="structural_ras_exit", policy_id=policy.policy_id)

    entry = position.entry_price
    if entry is None or entry == 0:
        return ExitSignal(False, reason="no_entry")

    # Long premium PnL proxy: (mark - entry) / entry
    pnl_ratio = float((Decimal(str(mark_price)) - Decimal(str(entry))) / Decimal(str(entry)))

    pid = policy.policy_id
    if pid in {
        ApprovedExitPolicyId.FIXED_TARGET.value,
        ApprovedExitPolicyId.TARGET_AND_STOP.value,
        ApprovedExitPolicyId.TRAILING.value,
    }:
        if policy.take_profit_ratio > 0 and pnl_ratio >= policy.take_profit_ratio:
            return ExitSignal(True, reason="target", policy_id=pid)

    if pid in {
        ApprovedExitPolicyId.FIXED_STOP.value,
        ApprovedExitPolicyId.TARGET_AND_STOP.value,
        ApprovedExitPolicyId.TRAILING.value,
    }:
        if policy.stop_loss_ratio > 0 and pnl_ratio <= -policy.stop_loss_ratio:
            return ExitSignal(True, reason="stop", policy_id=pid)

    if pid == ApprovedExitPolicyId.TRAILING.value:
        peak = float(position.peak_pnl)
        if peak >= policy.trailing_arm_ratio:
            giveback = peak - pnl_ratio
            if giveback >= policy.trailing_giveback_ratio:
                return ExitSignal(True, reason="trail", policy_id=pid)

    if policy.max_holding_minutes > 0 and position.opened_at is not None:
        held = (now - position.opened_at).total_seconds() / 60.0
        if held >= policy.max_holding_minutes:
            return ExitSignal(True, reason="time_exit", policy_id=pid)

    if policy.eod_close or pid == ApprovedExitPolicyId.EOD_EXIT.value:
        local = now.astimezone(ET).timetz().replace(tzinfo=None)
        if local >= time(15, 55):
            return ExitSignal(True, reason="eod", policy_id=pid)

    return ExitSignal(False, reason="hold", policy_id=pid)
