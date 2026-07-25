"""Local HTTP decision service — hard process boundary for 0DTE.

Preferred integration (ownership boundary Phase 2):

    0DTE → POST http://127.0.0.1:8787/v1/decision → SPY-DER

The service accepts a versioned MarketPacket and returns a DashboardPacket.
It does not import 0DTE modules. Live broker routing remains disabled.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from spy_der.contracts.integration import (
    DASHBOARD_SCHEMA,
    DECISION_REQUEST_SCHEMA,
    DECISION_RESPONSE_SCHEMA,
    DashboardDojoStatus,
    DashboardPacket,
    DecisionMode,
    DecisionResponse,
    MarketPacket,
    market_packet_from_dict,
)
from spy_der.decisions.shadow import (
    ShadowCandidateView,
    decide_shadow_tick,
)
from spy_der.dojo.config import DEFAULT_LIVE_STATE, DEFAULT_REPORTS_DIR
from spy_der.integrations.zerodte.result_publisher import (
    enrich_with_dojo_status,
    publish_dashboard_packet,
)

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "handle_decision_request",
    "main",
    "market_packet_to_decision",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def market_packet_to_decision(market: MarketPacket) -> DashboardPacket:
    """Core decision path used by HTTP and filesystem adapters."""
    views = [
        ShadowCandidateView(
            candidate_id=c.candidate_id,
            family=c.family,
            direction=c.direction,
            maximum_loss=c.maximum_loss,
            capital_required=c.capital_required,
            geometry_hash=c.geometry_hash,
            expiration=c.expiration,
            mid_price=c.mid_price,
            fill_probability=c.fill_probability,
            utility=c.utility,
            v3_rank=c.v3_rank,
            hard_vetoed=c.hard_vetoed,
        )
        for c in market.candidates
    ]
    now = market.generated_at or datetime.now(UTC)
    shadow = decide_shadow_tick(
        snapshot_id=market.snapshot_id,
        symbol=market.symbol,
        session_date=market.session_date,
        underlying_price=market.underlying_price,
        candidates=views,
        now=now,
        risk_max_size_scalar=market.risk_max_size_scalar,
        hard_vetoes=market.hard_vetoes,
        data_quality=market.data_quality,
        forecast_uncertainty=market.forecast_uncertainty,
        track_record=market.track_record or None,
    )
    # Map SELECT_CANDIDATE-style bridge actions onto dashboard TRADE.
    action = shadow.action
    if action == "SELECT_CANDIDATE":
        action = "TRADE"
    packet = DashboardPacket(
        schema_version=DASHBOARD_SCHEMA,
        generated_at=datetime.now(UTC),
        mode=DecisionMode.SHADOW,
        action=action,
        candidate_id=shadow.candidate_id,
        confidence=float(shadow.confidence),
        uncertainty=float(shadow.uncertainty),
        trader_model=shadow.trader_model_id or shadow.model_id or None,
        reviewer_model=shadow.reviewer_model_id or None,
        reason_codes=tuple(shadow.reason_codes),
        rationale=shadow.rationale,
        size_scalar=float(shadow.size_scalar),
        structure=shadow.structure,
        direction=shadow.direction,
        provider=shadow.provider,
        available=True,
        dojo=DashboardDojoStatus(),
        snapshot_id=market.snapshot_id,
        symbol=market.symbol,
    )
    return enrich_with_dojo_status(packet, reports_dir=DEFAULT_REPORTS_DIR)


def handle_decision_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Pure request handler — safe to unit-test without binding sockets."""
    schema = payload.get("schema_version")
    if schema != DECISION_REQUEST_SCHEMA:
        # Allow a bare MarketPacket for convenience.
        if payload.get("schema_version") == "zerodte.spyder.market.v1" or (
            "snapshot_id" in payload and "candidates" in payload
        ):
            market = market_packet_from_dict(payload)
            request_id = ""
        else:
            return {
                "error": "schema_mismatch",
                "expected": DECISION_REQUEST_SCHEMA,
                "got": schema,
            }
    else:
        market_raw = payload.get("market")
        if not isinstance(market_raw, dict):
            return {"error": "missing_market"}
        market = market_packet_from_dict(market_raw)
        request_id = str(payload.get("request_id") or "")

    decision = market_packet_to_decision(market)
    # Best-effort publish for the dashboard adapter.
    try:
        publish_dashboard_packet(decision, path=DEFAULT_LIVE_STATE)
    except OSError:
        pass
    response = DecisionResponse(
        schema_version=DECISION_RESPONSE_SCHEMA,
        decision=decision,
        request_id=request_id,
    )
    return response.to_dict()


class _DecisionHandler(BaseHTTPRequestHandler):
    server_version = "SpyDerDecisionService/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep journal noise low; systemd captures stdout separately if needed.
        return

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/health", "/v1/health"}:
            self._send(200, {"status": "ok", "service": "spy-der-decision"})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/v1/decision":
            self._send(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "expected_object"})
            return
        try:
            result = handle_decision_request(payload)
        except Exception as exc:  # fail closed
            self._send(
                500,
                {"error": "decision_failed", "detail": f"{type(exc).__name__}:{exc}"},
            )
            return
        code = 200 if "error" not in result else 400
        self._send(code, result)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), _DecisionHandler)
    print(f"spy-der decision service listening on http://{host}:{port}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SPY-DER local decision HTTP service")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
