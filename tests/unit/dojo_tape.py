"""Shared recorded-tape builder for Dojo tests.

Both the promotion-trial and archetype-training suites need a
``FileMarketExperienceProvider`` over synthetic sessions, differing only in what
each tick pays and how uncertain it was. Keeping one builder here means the two
suites cannot drift apart on packet shape, and neither has to import the other's
private helpers.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider

__all__ = ["DEFAULT_SESSIONS", "seed_tape"]

DEFAULT_SESSIONS: tuple[str, ...] = (
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
)


def _candidate(cid: str = "c1") -> MarketCandidateView:
    return MarketCandidateView(
        candidate_id=cid,
        family="long_call",
        direction="bullish",
        maximum_loss=Decimal("1"),
        capital_required=Decimal("1"),
        geometry_hash=f"sha256:{cid}",
        expiration=date(2026, 7, 22),
        utility=1.0,
    )


def seed_tape(
    root: Path,
    *,
    pnl_for_tick: Callable[[int], float],
    uncertainty_for_tick: Callable[[int], float],
    sessions: Sequence[str] = DEFAULT_SESSIONS,
    ticks: int = 40,
) -> FileMarketExperienceProvider:
    """Write a recorded tape whose per-tick P&L and uncertainty you choose."""
    (root / "snapshots").mkdir(parents=True)
    (root / "outcomes").mkdir(parents=True)
    for session in sessions:
        session_date = date.fromisoformat(session)
        for tick in range(ticks):
            pnl = pnl_for_tick(tick)
            snap_id = f"snap-{session}-{tick}"
            packet = MarketPacket(
                schema_version=MARKET_PACKET_SCHEMA,
                snapshot_id=snap_id,
                session_date=session_date,
                symbol="SPY",
                underlying_price=Decimal("600"),
                data_quality=1.0,
                forecast_uncertainty=uncertainty_for_tick(tick),
                candidates=(_candidate(),),
                forecast={
                    "labels": {
                        "realized_pnl": pnl,
                        "true_direction": "bullish",
                        "realized_pnl_by_candidate": {"c1": pnl},
                    }
                },
                generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
            )
            (root / "snapshots" / f"{snap_id}.json").write_text(
                json.dumps(packet.to_dict()), encoding="utf-8"
            )
            outcome = OutcomePacket(
                schema_version=OUTCOME_PACKET_SCHEMA,
                snapshot_id=snap_id,
                session_date=session_date,
                symbol="SPY",
                candidate_id="c1",
                action="TRADE",
                realized_pnl=Decimal(str(pnl)),
                settled=True,
                labels={
                    "true_direction": "bullish",
                    "realized_pnl_by_candidate": {"c1": pnl},
                },
                settled_at=datetime(2026, 7, 22, 20, 0, tzinfo=UTC),
            )
            (root / "outcomes" / f"{snap_id}.json").write_text(
                json.dumps(outcome.to_dict()), encoding="utf-8"
            )
    (root / "sessions.json").write_text(json.dumps(list(sessions)), encoding="utf-8")
    return FileMarketExperienceProvider(root)
