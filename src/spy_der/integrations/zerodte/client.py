"""HTTP client for the SPY-DER decision service (used by 0DTE thin adapter).

Default endpoint: ``http://127.0.0.1:8787/v1/decision``.

Network calls are opt-in. Unit tests inject a transport; production uses
stdlib ``urllib``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from spy_der.contracts.integration import (
    DECISION_REQUEST_SCHEMA,
    DECISION_RESPONSE_SCHEMA,
    DashboardPacket,
    DecisionRequest,
    DecisionResponse,
    MarketPacket,
    dashboard_packet_from_dict,
)

__all__ = [
    "DEFAULT_DECISION_URL",
    "DecisionTransport",
    "HttpDecisionClient",
    "UrllibDecisionTransport",
]

DEFAULT_DECISION_URL = "http://127.0.0.1:8787/v1/decision"


class DecisionTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class UrllibDecisionTransport:
    def post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"decision service unreachable: {exc}") from exc
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("decision service returned non-object JSON")
        return data


class HttpDecisionClient:
    """0DTE-facing client: MarketPacket in → DashboardPacket out."""

    def __init__(
        self,
        *,
        url: str = DEFAULT_DECISION_URL,
        timeout: float = 8.0,
        transport: DecisionTransport | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.transport = transport or UrllibDecisionTransport()

    def decide(self, market: MarketPacket, *, request_id: str = "") -> DashboardPacket:
        request = DecisionRequest(
            schema_version=DECISION_REQUEST_SCHEMA,
            market=market,
            request_id=request_id,
        )
        raw = self.transport.post_json(self.url, request.to_dict(), self.timeout)
        if raw.get("schema_version") != DECISION_RESPONSE_SCHEMA:
            raise RuntimeError(
                f"unexpected response schema {raw.get('schema_version')!r}"
            )
        decision_raw = raw.get("decision")
        if not isinstance(decision_raw, dict):
            raise RuntimeError("decision response missing decision object")
        return dashboard_packet_from_dict(decision_raw)

    def decide_raw(self, market: MarketPacket, *, request_id: str = "") -> DecisionResponse:
        packet = self.decide(market, request_id=request_id)
        return DecisionResponse(
            schema_version=DECISION_RESPONSE_SCHEMA,
            decision=packet,
            request_id=request_id,
        )
