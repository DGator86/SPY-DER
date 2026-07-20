"""Phase 13 — journal hash chain, projections, settlement, reconstruction."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    DebitCredit,
    JournalEvent,
    JournalEventType,
    OptionType,
)
from spy_der.evaluation.settlement import settle_session
from spy_der.journal import (
    InMemoryJournalStore,
    SqliteJournalStore,
    project_events,
    reconstruct_from_events,
    verify_chain,
)
from spy_der.replay.deterministic import deterministic_events, journal_hash


def _candidate(cid: str = "c1") -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-j",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("100"),
                quantity=1,
                expiration=exp,
                contract_id="SPY",
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal("5"),
        breakevens=(),
        capital_required=Decimal("5"),
        terminal_payoff_hash="sha256:pay",
        geometry_hash=f"sha256:{cid}",
    )


def test_append_only_hash_chain() -> None:
    store = InMemoryJournalStore()
    a = store.append(
        JournalEvent(
            event_type=JournalEventType.SYSTEM_DECIDED.value,
            aggregate_id="sess-1",
            payload={"action": "ABSTAIN"},
            deployment_id="d1",
        )
    )
    b = store.append(
        JournalEvent(
            event_type=JournalEventType.RISK_EVALUATED.value,
            aggregate_id="sess-1",
            payload={"approved": False},
            deployment_id="d1",
            causation_id=a.event_id,
        )
    )
    assert a.sequence_number == 1
    assert b.sequence_number == 2
    assert b.previous_event_hash == a.event_hash
    assert store.verify_chain()


def test_tamper_breaks_chain() -> None:
    store = InMemoryJournalStore()
    store.append(JournalEvent(event_type="system_decided", payload={"x": 1}))
    store.append(JournalEvent(event_type="risk_evaluated", payload={"x": 2}))
    events = list(store.iter_events())
    # Corrupt payload_hash without updating event_hash.
    from dataclasses import replace

    events[1] = replace(events[1], payload_hash="sha256:tampered")
    assert not verify_chain(events)


def test_settlement_and_counterfactual() -> None:
    now = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)
    traded = _candidate("traded")
    blocked = _candidate("blocked")
    store = InMemoryJournalStore()
    batch = settle_session(
        session_date="2026-01-05",
        settlement_price=Decimal("105"),
        traded=[(traded, Decimal("-5"), 1, "pos-1")],  # debit entry
        blocked=[(blocked, Decimal("-5"), "risk_veto", "ensemble")],
        journal=store,
        now=now,
    )
    assert len(batch.outcomes) == 1
    assert len(batch.counterfactuals) == 1
    # Long call strike 100, spot 105, debit 5 -> intrinsic 5 + credit -5 = 0
    assert batch.outcomes[0].realized_pnl == Decimal("0.0000")
    assert batch.counterfactuals[0].reason_not_taken == "risk_veto"
    assert len(store.iter_events()) == 2
    assert store.verify_chain()
    proj = project_events(batch.events)
    assert "2026-01-05" in proj.sessions
    assert proj.sessions["2026-01-05"].outcomes == 1
    assert proj.sessions["2026-01-05"].counterfactuals == 1


def test_reconstruction_matches_projections() -> None:
    store = InMemoryJournalStore()
    store.append(
        JournalEvent(
            event_type=JournalEventType.ORDER_FILLED.value,
            aggregate_type="order",
            aggregate_id="o1",
            payload={
                "order_id": "o1",
                "account_id": "system_b_v2",
                "candidate_id": "c1",
                "filled_contracts": 2,
            },
        )
    )
    store.append(
        JournalEvent(
            event_type=JournalEventType.POSITION_OPENED.value,
            aggregate_type="position",
            aggregate_id="p1",
            payload={
                "position_id": "p1",
                "account_id": "system_b_v2",
                "candidate_id": "c1",
                "open_contracts": 2,
            },
        )
    )
    result = reconstruct_from_events(store.iter_events())
    assert result.chain_valid
    assert "o1" in result.projections.orders
    assert result.projections.positions["p1"].open_contracts == 2


def test_sqlite_journal_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    store = SqliteJournalStore(path)
    store.append(JournalEvent(event_type="snapshot_created", payload={"ok": True}))
    store.append(JournalEvent(event_type="features_computed", payload={"n": 3}))
    assert store.verify_chain()
    assert len(store.iter_events()) == 2


def test_replay_helpers_still_work() -> None:
    events = deterministic_events("seed-a")
    assert len(events) == 2
    assert events[0].timestamp is not None
    digest = journal_hash(events)
    assert len(digest) == 64
