"""HTTP decision boundary tests (handler-level; no sockets)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts.integration import (
    DECISION_REQUEST_SCHEMA,
    DECISION_RESPONSE_SCHEMA,
    MARKET_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
)
from spy_der.decisions.shadow import reset_shadow_tick_cache
from spy_der.integrations.zerodte.client import HttpDecisionClient
from spy_der.runtime.decision_service import handle_decision_request
from spy_der.runtime.heartbeat import read_heartbeats


def setup_function() -> None:
    os.environ["SPY_DER_AI"] = "0"
    reset_shadow_tick_cache()


def _market(*, with_candidate: bool = True) -> MarketPacket:
    candidates = ()
    if with_candidate:
        candidates = (
            MarketCandidateView(
                candidate_id="candidate-123",
                family="bull_put_credit",
                direction="bullish",
                maximum_loss=Decimal("1.5"),
                capital_required=Decimal("1.5"),
                geometry_hash="sha256:c123",
                expiration=date(2026, 7, 24),
                utility=0.2,
                v3_rank=1,
            ),
        )
    return MarketPacket(
        schema_version=MARKET_PACKET_SCHEMA,
        snapshot_id="20260724T153100Z",
        session_date=date(2026, 7, 24),
        symbol="SPY",
        underlying_price=Decimal("637.42"),
        data_quality=0.97,
        forecast_uncertainty=0.24,
        candidates=candidates,
        generated_at=datetime(2026, 7, 24, 15, 31, 0, tzinfo=UTC),
    )


def test_handle_decision_request_returns_dashboard_schema() -> None:
    payload = {
        "schema_version": DECISION_REQUEST_SCHEMA,
        "request_id": "req-1",
        "market": _market().to_dict(),
    }
    result = handle_decision_request(payload)
    assert result["schema_version"] == DECISION_RESPONSE_SCHEMA
    assert result["request_id"] == "req-1"
    decision = result["decision"]
    assert decision["schema_version"] == "spyder.dashboard.v1"
    assert decision["action"] in {"TRADE", "NO_EDGE", "ABSTAIN", "SELECT_CANDIDATE"}
    assert "dojo" in decision


def test_handle_bare_market_packet() -> None:
    result = handle_decision_request(_market(with_candidate=False).to_dict())
    assert result["schema_version"] == DECISION_RESPONSE_SCHEMA
    assert result["decision"]["action"] == "NO_EDGE"


def test_http_client_with_injected_transport() -> None:
    def fake_post(url: str, payload: dict, timeout: float) -> dict:
        assert "v1/decision" in url
        return handle_decision_request(payload)

    class Transport:
        def post_json(self, url: str, payload: dict, timeout: float) -> dict:
            return fake_post(url, payload, timeout)

    client = HttpDecisionClient(transport=Transport())
    decision = client.decide(_market())
    assert decision.schema_version == "spyder.dashboard.v1"
    assert decision.mode == "shadow"


def test_decision_request_publishes_agent_heartbeat(
    tmp_path: Path, monkeypatch
) -> None:
    """A dead agent must show up on /v1/system — so every decision refreshes it."""
    monkeypatch.setattr(
        "spy_der.runtime.decision_service.DEFAULT_STATE_ROOT", str(tmp_path)
    )
    monkeypatch.setattr(
        "spy_der.runtime.decision_service.DEFAULT_LIVE_STATE",
        str(tmp_path / "live_state.json"),
    )
    monkeypatch.setattr(
        "spy_der.runtime.decision_service.DEFAULT_REPORTS_DIR",
        str(tmp_path / "reports" / "dojo"),
    )
    handle_decision_request(_market(with_candidate=False).to_dict())
    entries = {e["service"]: e for e in read_heartbeats(tmp_path)}
    assert "agent" in entries
    assert entries["agent"]["state"] == "ok"
    assert str(entries["agent"].get("detail", "")).startswith("last=")
