"""Parity golden for Phase 13 journal settlement + hash chain tip."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    DebitCredit,
    OptionType,
    to_canonical_json,
)
from spy_der.evaluation.settlement import settle_session
from spy_der.journal import InMemoryJournalStore, project_events

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase13" / "journal_settlement.json"


def _candidate(cid: str) -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-phase13",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("500"),
                quantity=1,
                expiration=exp,
                contract_id="SPY250105C00500000",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal("3"),
        breakevens=(),
        capital_required=Decimal("3"),
        terminal_payoff_hash="sha256:pay13",
        geometry_hash=f"sha256:{cid}",
    )


def _artifact() -> dict[str, object]:
    now = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)
    store = InMemoryJournalStore()
    traded = _candidate("cand-traded")
    blocked = _candidate("cand-blocked")
    batch = settle_session(
        session_date="2026-01-05",
        settlement_price=Decimal("505"),
        traded=[(traded, Decimal("-3"), 1, "pos-13")],
        blocked=[(blocked, Decimal("-3"), "no_edge", "v3")],
        journal=store,
        now=now,
        deployment_id="phase13-parity",
    )
    proj = project_events(store.iter_events())
    return {
        "tip_hash": store.latest_hash(),
        "chain_valid": store.verify_chain(),
        "event_types": [e.event_type for e in store.iter_events()],
        "outcome_pnl": str(batch.outcomes[0].realized_pnl),
        "counterfactual_pnl": str(batch.counterfactuals[0].realized_pnl),
        "session": {
            "outcomes": proj.sessions["2026-01-05"].outcomes,
            "counterfactuals": proj.sessions["2026-01-05"].counterfactuals,
            "realized_pnl_total": str(proj.sessions["2026-01-05"].realized_pnl_total),
        },
    }


def test_phase13_journal_parity() -> None:
    _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(to_canonical_json(_artifact()))
    if not _EXPECTED.exists():
        _EXPECTED.write_text(to_canonical_json(artifact) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert artifact == expected
