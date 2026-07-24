"""Cross-repository integration contracts (0DTE ↔ SPY-DER).

These packets are the only approved surface between repositories for live
decisions, Dojo experience feeds, and dashboard display. Neither side may
import the other's internal agents, training, or market-simulation modules.

Schemas:
  zerodte.spyder.market.v1     — 0DTE → SPY-DER market/decision input
  zerodte.spyder.outcome.v1    — 0DTE → SPY-DER settlement / outcome
  spyder.dashboard.v1          — SPY-DER → 0DTE dashboard display
  spyder.decision.request.v1   — HTTP decision request wrapper
  spyder.decision.response.v1  — HTTP decision response wrapper
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from spy_der.contracts.common import (
    ErrorCode,
    ValidationError,
    require_finite,
    require_probability,
    require_tz_aware,
)

__all__ = [
    "DASHBOARD_SCHEMA",
    "DECISION_REQUEST_SCHEMA",
    "DECISION_RESPONSE_SCHEMA",
    "MARKET_PACKET_SCHEMA",
    "OUTCOME_PACKET_SCHEMA",
    "DashboardDojoStatus",
    "DashboardPacket",
    "DecisionMode",
    "DecisionRequest",
    "DecisionResponse",
    "MarketCandidateView",
    "MarketPacket",
    "OutcomePacket",
    "dashboard_packet_from_dict",
    "market_packet_from_dict",
    "outcome_packet_from_dict",
]

MARKET_PACKET_SCHEMA = "zerodte.spyder.market.v1"
OUTCOME_PACKET_SCHEMA = "zerodte.spyder.outcome.v1"
DASHBOARD_SCHEMA = "spyder.dashboard.v1"
DECISION_REQUEST_SCHEMA = "spyder.decision.request.v1"
DECISION_RESPONSE_SCHEMA = "spyder.decision.response.v1"


class DecisionMode(StrEnum):
    SHADOW = "shadow"
    PAPER = "paper"
    OBSERVE = "observe"


@dataclass(frozen=True, slots=True)
class MarketCandidateView:
    """Read-only candidate summary produced by 0DTE for SPY-DER decisions."""

    candidate_id: str
    family: str
    direction: str
    maximum_loss: Decimal
    capital_required: Decimal
    geometry_hash: str
    expiration: date
    mid_price: Decimal | None = None
    fill_probability: float = 1.0
    utility: float | None = None
    v3_rank: int | None = None
    hard_vetoed: bool = False

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "candidate_id required")
        require_probability(self.fill_probability, "fill_probability")


@dataclass(frozen=True, slots=True)
class MarketPacket:
    """0DTE → SPY-DER market state for a single decision tick / Dojo snapshot."""

    schema_version: str
    snapshot_id: str
    session_date: date
    symbol: str
    underlying_price: Decimal
    data_quality: float
    forecast_uncertainty: float
    hard_vetoes: tuple[str, ...] = ()
    forecast: dict[str, Any] = field(default_factory=dict)
    candidates: tuple[MarketCandidateView, ...] = ()
    track_record: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime | None = None
    risk_max_size_scalar: float = 1.0

    def __post_init__(self) -> None:
        if self.schema_version != MARKET_PACKET_SCHEMA:
            raise ValidationError(
                ErrorCode.SCHEMA_MISMATCH,
                f"expected {MARKET_PACKET_SCHEMA}, got {self.schema_version!r}",
            )
        if not self.snapshot_id:
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "snapshot_id required")
        if not self.symbol:
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "symbol required")
        require_finite(float(self.underlying_price), "underlying_price")
        require_probability(self.data_quality, "data_quality")
        require_probability(self.forecast_uncertainty, "forecast_uncertainty")
        require_probability(self.risk_max_size_scalar, "risk_max_size_scalar")
        if self.generated_at is not None:
            require_tz_aware(self.generated_at, "generated_at")
        object.__setattr__(self, "hard_vetoes", tuple(self.hard_vetoes))
        object.__setattr__(self, "candidates", tuple(self.candidates))

    def to_dict(self) -> dict[str, Any]:
        return _packet_to_dict(self)


@dataclass(frozen=True, slots=True)
class OutcomePacket:
    """0DTE → SPY-DER settlement / outcome for a prior snapshot or decision."""

    schema_version: str
    snapshot_id: str
    session_date: date
    symbol: str
    candidate_id: str | None
    action: str
    realized_pnl: Decimal | None = None
    settled: bool = False
    labels: dict[str, Any] = field(default_factory=dict)
    settled_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.schema_version != OUTCOME_PACKET_SCHEMA:
            raise ValidationError(
                ErrorCode.SCHEMA_MISMATCH,
                f"expected {OUTCOME_PACKET_SCHEMA}, got {self.schema_version!r}",
            )
        if not self.snapshot_id:
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "snapshot_id required")
        if self.settled_at is not None:
            require_tz_aware(self.settled_at, "settled_at")

    def to_dict(self) -> dict[str, Any]:
        return _packet_to_dict(self)


@dataclass(frozen=True, slots=True)
class DashboardDojoStatus:
    latest_status: str = "UNKNOWN"
    latest_run: datetime | None = None
    latest_report_path: str = ""
    weak_archetypes: int = 0
    summary: str = ""

    def __post_init__(self) -> None:
        if self.latest_run is not None:
            require_tz_aware(self.latest_run, "latest_run")
        if self.weak_archetypes < 0:
            raise ValidationError(
                ErrorCode.NEGATIVE_VALUE, "weak_archetypes must be non-negative"
            )


@dataclass(frozen=True, slots=True)
class DashboardPacket:
    """SPY-DER → 0DTE public state for the Vercel dashboard adapter."""

    schema_version: str
    generated_at: datetime
    mode: DecisionMode | str
    action: str
    candidate_id: str | None
    confidence: float
    uncertainty: float
    trader_model: str | None = None
    reviewer_model: str | None = None
    reason_codes: tuple[str, ...] = ()
    rationale: str = ""
    size_scalar: float = 0.0
    structure: str | None = None
    direction: str | None = None
    provider: str = "spy_der"
    available: bool = True
    dojo: DashboardDojoStatus = field(default_factory=DashboardDojoStatus)
    snapshot_id: str = ""
    symbol: str = "SPY"

    def __post_init__(self) -> None:
        if self.schema_version != DASHBOARD_SCHEMA:
            raise ValidationError(
                ErrorCode.SCHEMA_MISMATCH,
                f"expected {DASHBOARD_SCHEMA}, got {self.schema_version!r}",
            )
        require_tz_aware(self.generated_at, "generated_at")
        require_probability(self.confidence, "confidence")
        require_probability(self.uncertainty, "uncertainty")
        require_probability(self.size_scalar, "size_scalar")
        mode = self.mode.value if isinstance(self.mode, DecisionMode) else str(self.mode)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    def to_dict(self) -> dict[str, Any]:
        return _packet_to_dict(self)


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """HTTP wrapper around a MarketPacket for POST /v1/decision."""

    schema_version: str
    market: MarketPacket
    request_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_REQUEST_SCHEMA:
            raise ValidationError(
                ErrorCode.SCHEMA_MISMATCH,
                f"expected {DECISION_REQUEST_SCHEMA}, got {self.schema_version!r}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "market": self.market.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DecisionResponse:
    """HTTP wrapper around a DashboardPacket for POST /v1/decision."""

    schema_version: str
    decision: DashboardPacket
    request_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_RESPONSE_SCHEMA:
            raise ValidationError(
                ErrorCode.SCHEMA_MISMATCH,
                f"expected {DECISION_RESPONSE_SCHEMA}, got {self.schema_version!r}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "decision": self.decision.to_dict(),
        }


def _packet_to_dict(packet: Any) -> dict[str, Any]:
    raw: dict[str, Any] = asdict(packet)
    normalized = _normalize_dict(raw)
    assert isinstance(normalized, dict)
    return normalized


def _normalize_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_dict(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    return value


def _parse_date(raw: Any, field_name: str) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return date.fromisoformat(raw)
    raise ValidationError(
        ErrorCode.MALFORMED_RECORD, f"{field_name} must be an ISO date"
    )


def _parse_datetime(raw: Any, field_name: str) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return require_tz_aware(raw, field_name)
    if isinstance(raw, str):
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return require_tz_aware(parsed, field_name)
    raise ValidationError(
        ErrorCode.MALFORMED_RECORD, f"{field_name} must be an ISO datetime"
    )


def _parse_decimal(raw: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except Exception as exc:
        raise ValidationError(
            ErrorCode.MALFORMED_RECORD, f"{field_name} is not a decimal"
        ) from exc


def market_packet_from_dict(data: dict[str, Any]) -> MarketPacket:
    if not isinstance(data, dict):
        raise ValidationError(ErrorCode.MALFORMED_RECORD, "market packet must be an object")
    candidates_raw = data.get("candidates") or []
    if not isinstance(candidates_raw, list):
        raise ValidationError(ErrorCode.MALFORMED_RECORD, "candidates must be a list")
    candidates: list[MarketCandidateView] = []
    for item in candidates_raw:
        if not isinstance(item, dict):
            raise ValidationError(ErrorCode.MALFORMED_RECORD, "candidate must be an object")
        exp = _parse_date(item.get("expiration") or data.get("session_date"), "expiration")
        candidates.append(
            MarketCandidateView(
                candidate_id=str(item["candidate_id"]),
                family=str(item.get("family") or "unknown"),
                direction=str(item.get("direction") or "both"),
                maximum_loss=_parse_decimal(item.get("maximum_loss", 1), "maximum_loss"),
                capital_required=_parse_decimal(
                    item.get("capital_required", item.get("maximum_loss", 1)),
                    "capital_required",
                ),
                geometry_hash=str(item.get("geometry_hash") or f"sha256:{item['candidate_id']}"),
                expiration=exp,
                mid_price=(
                    _parse_decimal(item["mid_price"], "mid_price")
                    if item.get("mid_price") is not None
                    else None
                ),
                fill_probability=float(item.get("fill_probability", 1.0)),
                utility=(
                    float(item["utility"]) if item.get("utility") is not None else None
                ),
                v3_rank=(int(item["v3_rank"]) if item.get("v3_rank") is not None else None),
                hard_vetoed=bool(item.get("hard_vetoed", False)),
            )
        )
    return MarketPacket(
        schema_version=str(data.get("schema_version") or MARKET_PACKET_SCHEMA),
        snapshot_id=str(data["snapshot_id"]),
        session_date=_parse_date(data["session_date"], "session_date"),
        symbol=str(data.get("symbol") or "SPY"),
        underlying_price=_parse_decimal(data["underlying_price"], "underlying_price"),
        data_quality=float(data.get("data_quality", 1.0)),
        forecast_uncertainty=float(data.get("forecast_uncertainty", 0.0)),
        hard_vetoes=tuple(str(v) for v in (data.get("hard_vetoes") or [])),
        forecast=dict(data.get("forecast") or {}),
        candidates=tuple(candidates),
        track_record=dict(data.get("track_record") or {}),
        generated_at=_parse_datetime(data.get("generated_at"), "generated_at"),
        risk_max_size_scalar=float(data.get("risk_max_size_scalar", 1.0)),
    )


def outcome_packet_from_dict(data: dict[str, Any]) -> OutcomePacket:
    if not isinstance(data, dict):
        raise ValidationError(ErrorCode.MALFORMED_RECORD, "outcome packet must be an object")
    return OutcomePacket(
        schema_version=str(data.get("schema_version") or OUTCOME_PACKET_SCHEMA),
        snapshot_id=str(data["snapshot_id"]),
        session_date=_parse_date(data["session_date"], "session_date"),
        symbol=str(data.get("symbol") or "SPY"),
        candidate_id=(
            str(data["candidate_id"]) if data.get("candidate_id") is not None else None
        ),
        action=str(data.get("action") or "UNKNOWN"),
        realized_pnl=(
            _parse_decimal(data["realized_pnl"], "realized_pnl")
            if data.get("realized_pnl") is not None
            else None
        ),
        settled=bool(data.get("settled", False)),
        labels=dict(data.get("labels") or {}),
        settled_at=_parse_datetime(data.get("settled_at"), "settled_at"),
    )


def dashboard_packet_from_dict(data: dict[str, Any]) -> DashboardPacket:
    if not isinstance(data, dict):
        raise ValidationError(
            ErrorCode.MALFORMED_RECORD, "dashboard packet must be an object"
        )
    dojo_raw = data.get("dojo") or {}
    if not isinstance(dojo_raw, dict):
        raise ValidationError(ErrorCode.MALFORMED_RECORD, "dojo must be an object")
    dojo = DashboardDojoStatus(
        latest_status=str(dojo_raw.get("latest_status") or "UNKNOWN"),
        latest_run=_parse_datetime(dojo_raw.get("latest_run"), "latest_run"),
        latest_report_path=str(dojo_raw.get("latest_report_path") or ""),
        weak_archetypes=int(dojo_raw.get("weak_archetypes") or 0),
        summary=str(dojo_raw.get("summary") or ""),
    )
    generated = _parse_datetime(data.get("generated_at"), "generated_at")
    if generated is None:
        raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "generated_at required")
    return DashboardPacket(
        schema_version=str(data.get("schema_version") or DASHBOARD_SCHEMA),
        generated_at=generated,
        mode=str(data.get("mode") or DecisionMode.SHADOW.value),
        action=str(data.get("action") or "ABSTAIN"),
        candidate_id=(
            str(data["candidate_id"]) if data.get("candidate_id") is not None else None
        ),
        confidence=float(data.get("confidence", 0.0)),
        uncertainty=float(data.get("uncertainty", 1.0)),
        trader_model=(
            str(data["trader_model"]) if data.get("trader_model") is not None else None
        ),
        reviewer_model=(
            str(data["reviewer_model"]) if data.get("reviewer_model") is not None else None
        ),
        reason_codes=tuple(str(c) for c in (data.get("reason_codes") or [])),
        rationale=str(data.get("rationale") or ""),
        size_scalar=float(data.get("size_scalar", 0.0)),
        structure=(str(data["structure"]) if data.get("structure") is not None else None),
        direction=(str(data["direction"]) if data.get("direction") is not None else None),
        provider=str(data.get("provider") or "spy_der"),
        available=bool(data.get("available", True)),
        dojo=dojo,
        snapshot_id=str(data.get("snapshot_id") or ""),
        symbol=str(data.get("symbol") or "SPY"),
    )
