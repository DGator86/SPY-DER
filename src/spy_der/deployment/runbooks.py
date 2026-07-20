"""Operational runbooks for freeze, promotion, and rollback."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RUNBOOKS", "Runbook", "get_runbook"]


@dataclass(frozen=True, slots=True)
class Runbook:
    runbook_id: str
    title: str
    steps: tuple[str, ...]


RUNBOOKS: dict[str, Runbook] = {
    "freeze": Runbook(
        runbook_id="freeze",
        title="Freeze deployment on drift",
        steps=(
            "Confirm DriftReport.level is FREEZE or ROLLBACK.",
            "Call freeze_deployment(pointer, reason=...).",
            "Publish CRITICAL notification topic=deployment.freeze.",
            "Block new champion promotions until human review.",
        ),
    ),
    "promote": Runbook(
        runbook_id="promote",
        title="Human-gated model promotion",
        steps=(
            "Validate PromotionReviewPacket.",
            "Ensure reviewer identity and approval_note are present.",
            "Promote shadow -> candidate before champion.",
            "Activate new DeploymentManifest; keep rollback_target.",
            "Publish INFO notification topic=deployment.promote.",
        ),
    ),
    "rollback": Runbook(
        runbook_id="rollback",
        title="Rollback deployment pointer",
        steps=(
            "Confirm previous pointer exists and is not frozen.",
            "Call rollback_deployment(pointer, reason=...).",
            "Verify configuration_hash matches intended rollback target.",
            "Publish CRITICAL notification topic=deployment.rollback.",
        ),
    ),
    "cutover": Runbook(
        runbook_id="cutover",
        title="Phase 17 controlled cutover",
        steps=(
            "Confirm explicit repository-owner approval (phase-17).",
            "Call activate_controlled_cutover(approval=...).",
            "Verify System B is primary research/shadow runtime.",
            "Verify System A is retained as rollback target.",
            "Confirm agent authority is independently controllable.",
            "Confirm live execution gate remains disabled.",
            "On incident: ControlledCutover.rollback_to_system_a(reason=...).",
        ),
    ),
}


def get_runbook(runbook_id: str) -> Runbook:
    if runbook_id not in RUNBOOKS:
        raise KeyError(f"unknown runbook: {runbook_id}")
    return RUNBOOKS[runbook_id]
