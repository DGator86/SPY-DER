"""Phase-1 ownership-boundary integration contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from spy_der.contracts import (
    DASHBOARD_SCHEMA,
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    DashboardPacket,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
    ValidationError,
    dashboard_packet_from_dict,
    market_packet_from_dict,
    outcome_packet_from_dict,
    to_canonical_json,
)


def _candidate() -> MarketCandidateView:
    return MarketCandidateView(
        candidate_id="candidate-123",
        family="bull_put_credit",
        direction="bullish",
        maximum_loss=Decimal("1.50"),
        capital_required=Decimal("1.50"),
        geometry_hash="sha256:abc",
        expiration=date(2026, 7, 24),
        utility=0.12,
        v3_rank=1,
    )


def test_market_packet_round_trip() -> None:
    packet = MarketPacket(
        schema_version=MARKET_PACKET_SCHEMA,
        snapshot_id="20260724T153100Z",
        session_date=date(2026, 7, 24),
        symbol="SPY",
        underlying_price=Decimal("637.42"),
        data_quality=0.97,
        forecast_uncertainty=0.24,
        hard_vetoes=(),
        forecast={"p_up_30m": 0.55},
        candidates=(_candidate(),),
        track_record={"n_trades": 3, "win_rate": 0.67},
        generated_at=datetime(2026, 7, 24, 15, 31, 0, tzinfo=UTC),
    )
    restored = market_packet_from_dict(packet.to_dict())
    assert restored.snapshot_id == packet.snapshot_id
    assert restored.candidates[0].candidate_id == "candidate-123"
    assert to_canonical_json(restored.to_dict()) == to_canonical_json(packet.to_dict())


def test_market_packet_rejects_wrong_schema() -> None:
    with pytest.raises(ValidationError):
        MarketPacket(
            schema_version="wrong",
            snapshot_id="s",
            session_date=date(2026, 7, 24),
            symbol="SPY",
            underlying_price=Decimal("1"),
            data_quality=1.0,
            forecast_uncertainty=0.0,
        )


def test_dashboard_packet_round_trip() -> None:
    packet = DashboardPacket(
        schema_version=DASHBOARD_SCHEMA,
        generated_at=datetime(2026, 7, 24, 15, 31, 4, tzinfo=UTC),
        mode="shadow",
        action="TRADE",
        candidate_id="candidate-123",
        confidence=0.81,
        uncertainty=0.19,
        trader_model="grok-test",
        reviewer_model=None,
        reason_codes=("edge",),
    )
    restored = dashboard_packet_from_dict(packet.to_dict())
    assert restored.action == "TRADE"
    assert restored.dojo.latest_status == "UNKNOWN"
    assert restored.confidence == 0.81


def test_outcome_packet_schema() -> None:
    packet = OutcomePacket(
        schema_version=OUTCOME_PACKET_SCHEMA,
        snapshot_id="snap-1",
        session_date=date(2026, 7, 24),
        symbol="SPY",
        candidate_id="candidate-123",
        action="TRADE",
        realized_pnl=Decimal("0.25"),
        settled=True,
        settled_at=datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
    )
    restored = outcome_packet_from_dict(packet.to_dict())
    assert restored.realized_pnl == Decimal("0.25")
    assert restored.settled is True
