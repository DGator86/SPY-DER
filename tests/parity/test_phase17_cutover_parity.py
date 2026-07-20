"""Parity golden for Phase 17 controlled cutover snapshot."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from spy_der.contracts import to_canonical_json
from spy_der.deployment import CutoverApproval, activate_controlled_cutover

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase17" / "cutover_snapshot.json"


def _artifact() -> dict[str, object]:
    ctl = activate_controlled_cutover(
        approval=CutoverApproval(
            approved_by="repository-owner",
            approved_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
            approval_note="Phase 17 activate.",
            phase="phase-17",
        ),
        agent_enabled=True,
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
    )
    # Drop volatile state_hash nesting extras; freeze structural fields.
    d = ctl.snapshot().as_dict()
    return {
        "phase": d["phase"],
        "primary": d["primary"],
        "rollback": d["rollback"],
        "primary_deployment_id": d["primary_deployment_id"],
        "rollback_deployment_id": d["rollback_deployment_id"],
        "primary_mode": d["primary_mode"],
        "agent_authority_enabled": d["agent_authority_enabled"],
        "agent_provider": d["agent_provider"],
        "live_execution_enabled": d["live_execution_enabled"],
        "live_execution_reason": d["live_execution_reason"],
        "approved_by": d["approved_by"],
        "system_b_is_primary": d["system_b_is_primary"],
        "system_a_is_rollback": d["system_a_is_rollback"],
        "agent_independently_controlled": d["agent_independently_controlled"],
        "live_execution_disabled": d["live_execution_disabled"],
    }


def test_phase17_cutover_parity() -> None:
    _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(to_canonical_json(_artifact()))
    if not _EXPECTED.exists():
        _EXPECTED.write_text(to_canonical_json(artifact) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert artifact == expected
