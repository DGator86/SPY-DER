"""Parity golden for Phase 12 paper execution + position lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts import (
    ApprovedExitPolicyId,
    ExitPolicy,
    OrderIntent,
    to_canonical_json,
)
from spy_der.execution import PaperExecutionSimulator
from spy_der.positions import PositionManager

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase12" / "execution_position.json"


def _artifact() -> dict[str, object]:
    now = datetime(2026, 1, 5, 16, 30, tzinfo=UTC)
    sim = PaperExecutionSimulator()
    mgr = PositionManager(accounts=sim.accounts)
    order = sim.submit(
        OrderIntent(
            intent_id="parity-intent-1",
            account_id="system_b_ensemble",
            candidate_id="parity-cand-1",
            contracts=2,
            limit_price=Decimal("1.25"),
            created_at=now,
            fill_probability=1.0,
            exit_policy_id=ApprovedExitPolicyId.TARGET_AND_STOP.value,
            fee_per_contract=Decimal("0.65"),
        ),
        now=now,
    )
    pos = mgr.on_order_state(
        order,
        max_loss=Decimal("50"),
        exit_policy_id=ApprovedExitPolicyId.TARGET_AND_STOP.value,
        now=now,
    )
    assert pos is not None
    mgr.mark(pos.position_id, Decimal("2.00"), now=now)
    closed = mgr.close(
        pos.position_id,
        exit_price=Decimal("2.00"),
        reason="target",
        now=now,
    )
    settled = mgr.settle(closed.position_id)
    acct = sim.accounts.get("system_b_ensemble")
    return {
        "order": {
            "order_id": order.order_id,
            "status": order.status.value,
            "filled_contracts": order.filled_contracts,
            "avg_fill_price": str(order.avg_fill_price),
            "fees": str(order.fees),
            "account_id": order.account_id,
        },
        "position": {
            "position_id": settled.position_id,
            "status": settled.status.value,
            "exit_reason": settled.exit_reason,
            "realized_pnl": str(settled.realized_pnl),
            "exit_policy_id": settled.exit_policy_id,
        },
        "account": {
            "account_id": acct.account_id,
            "cash": str(acct.cash),
            "trade_count": acct.trade_count,
            "open_positions": list(acct.open_position_ids),
            "blocked": acct.blocked,
        },
        "exit_policy": ExitPolicy(
            policy_id=ApprovedExitPolicyId.TARGET_AND_STOP.value
        ).policy_id,
    }


def test_phase12_execution_parity() -> None:
    _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(to_canonical_json(_artifact()))
    if not _EXPECTED.exists():
        _EXPECTED.write_text(to_canonical_json(artifact) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert artifact == expected
