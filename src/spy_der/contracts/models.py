from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

SCHEMA_VERSION = "1.0.0"


def _require_tz_aware(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
        msg = "timestamp must be timezone-aware"
        raise ValueError(msg)


def _require_probability(value: float, name: str) -> None:
    if not 0.0 <= value <= 1.0:
        msg = f"{name} must be between 0 and 1"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class FeatureBundle:
    schema_version: str = SCHEMA_VERSION
    bundle_id: str = ""
    snapshot_id: str = ""
    features: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class StructuralState:
    schema_version: str = SCHEMA_VERSION
    state_id: str = ""
    regime: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyPermissions:
    schema_version: str = SCHEMA_VERSION
    options_allowed: bool = False
    new_positions_allowed: bool = False


@dataclass(frozen=True, slots=True)
class HardVeto:
    schema_version: str = SCHEMA_VERSION
    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LegacyDecisionView:
    schema_version: str = SCHEMA_VERSION
    structural_state: StructuralState = field(default_factory=StructuralState)
    permissions: StrategyPermissions = field(default_factory=StrategyPermissions)
    hard_vetoes: tuple[HardVeto, ...] = ()


# MarketForecastBundle lives in spy_der.contracts.forecasts (Phase 5).
# Candidate / CandidateUniverse live in spy_der.contracts.candidates (Phase 7).


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """Legacy lightweight leg reference retained for journal/execution stubs."""

    contract: str
    quantity: int
    side: str
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CandidateForecast:
    schema_version: str = SCHEMA_VERSION
    candidate_id: str = ""
    probability_positive_utility: float = 0.0

    def __post_init__(self) -> None:
        _require_probability(self.probability_positive_utility, "probability_positive_utility")


@dataclass(frozen=True, slots=True)
class CandidateRanking:
    schema_version: str = SCHEMA_VERSION
    ordered_candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class V3DecisionView:
    schema_version: str = SCHEMA_VERSION
    ranking: CandidateRanking = field(default_factory=CandidateRanking)
    forecasts: tuple[CandidateForecast, ...] = ()


class SystemAction(StrEnum):
    ABSTAIN = "ABSTAIN"
    SELECT_CANDIDATE = "SELECT_CANDIDATE"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True, slots=True)
class RiskEnvelope:
    schema_version: str = SCHEMA_VERSION
    max_defined_risk_per_trade: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class SystemDecision:
    schema_version: str = SCHEMA_VERSION
    action: SystemAction = SystemAction.ABSTAIN
    selected_candidate_id: str | None = None
    reason: str = ""
    market_snapshot_id: str = ""
    feature_bundle_id: str = ""
    legacy_state_id: str = ""
    forecast_model_version: str = ""
    candidate_universe_id: str = ""
    veto_codes: tuple[str, ...] = ()
    config_version: str = ""


@dataclass(frozen=True, slots=True)
class RiskDecision:
    schema_version: str = SCHEMA_VERSION
    allowed: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OrderIntent:
    schema_version: str = SCHEMA_VERSION
    intent_id: str = ""
    candidate_id: str = ""
    limit_price: Decimal = Decimal("0")


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    ROUTED = "ROUTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class OrderState:
    schema_version: str = SCHEMA_VERSION
    order_id: str = ""
    status: OrderStatus = OrderStatus.CREATED


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class PositionState:
    schema_version: str = SCHEMA_VERSION
    position_id: str = ""
    status: PositionStatus = PositionStatus.OPEN


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    schema_version: str = SCHEMA_VERSION
    take_profit_ratio: float = 0.0
    stop_loss_ratio: float = 0.0
    max_holding_minutes: int = 0


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    schema_version: str = SCHEMA_VERSION
    record_id: str = ""
    realized_pnl: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class JournalEvent:
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_type: str = ""
    payload_json: str = "{}"

    def __post_init__(self) -> None:
        _require_tz_aware(self.timestamp)


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    schema_version: str = SCHEMA_VERSION
    manifest_id: str = ""
    config_version: str = ""
    model_versions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SystemAdapter:
    schema_version: str = SCHEMA_VERSION
    adapter_name: str = ""
    adapter_version: str = ""
