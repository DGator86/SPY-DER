"""Risk contracts (master spec §50; System A zerodte/contracts/risk.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = [
    "RISK_DECISION_SCHEMA",
    "RISK_SCHEMA",
    "LockoutState",
    "OperationalState",
    "PortfolioState",
    "RiskCheck",
    "RiskDecision",
    "RiskEnvelope",
    "RiskLimits",
    "RiskVeto",
]

RISK_SCHEMA = "risk.envelope.v1"
RISK_DECISION_SCHEMA = "risk.decision.v1"


@dataclass(frozen=True, slots=True)
class RiskVeto:
    code: str
    reason: str = ""
    severity: str = "hard"

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("veto code is required")
        if self.severity not in {"hard", "soft"}:
            raise ValueError("severity must be 'hard' or 'soft'")


@dataclass(frozen=True, slots=True)
class RiskCheck:
    name: str
    passed: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("check name is required")


@dataclass(frozen=True, slots=True)
class LockoutState:
    """Session / emergency lockout flags (spec §50, Phase 11)."""

    session_warmup: bool = False
    entry_cutoff: bool = False
    emergency_lockout: bool = False
    catalyst_lockout: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def locked(self) -> bool:
        return bool(
            self.session_warmup
            or self.entry_cutoff
            or self.emergency_lockout
            or self.catalyst_lockout
            or self.reasons
        )


@dataclass(frozen=True, slots=True)
class OperationalState:
    """Deterministic operational gates before order intent."""

    market_open: bool = False
    session_warmup: bool = False
    entry_locked: bool = False
    data_valid: bool = False
    broker_available: bool = False
    deployment_permission: bool = True
    journal_available: bool = True
    hard_vetoes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def entries_allowed(self) -> bool:
        return (
            self.market_open
            and not self.session_warmup
            and not self.entry_locked
            and self.data_valid
            and self.broker_available
            and self.deployment_permission
            and self.journal_available
            and not self.hard_vetoes
        )


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Account/portfolio snapshot used by envelope + firewall."""

    account_id: str
    equity: Decimal
    cash: Decimal
    open_positions: int = 0
    daily_realized_pnl: Decimal = Decimal("0")
    daily_unrealized_pnl: Decimal = Decimal("0")
    open_risk_dollars: Decimal = Decimal("0")
    portfolio_gamma: Decimal = Decimal("0")
    portfolio_delta: Decimal = Decimal("0")
    family_counts: tuple[tuple[str, int], ...] = ()
    expiration_counts: tuple[tuple[str, int], ...] = ()
    open_geometry_hashes: tuple[str, ...] = ()
    open_candidate_ids: tuple[str, ...] = ()
    state_id: str = ""

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id is required")
        if self.equity < 0:
            raise ValueError("equity cannot be negative")
        if self.cash < 0:
            raise ValueError("cash cannot be negative")
        if self.open_positions < 0:
            raise ValueError("open_positions cannot be negative")
        if self.open_risk_dollars < 0:
            raise ValueError("open_risk_dollars cannot be negative")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Static risk ceilings used to build an envelope."""

    max_risk_dollars: Decimal = Decimal("0")
    max_contracts: int = 0
    max_positions: int = 0
    max_daily_loss: Decimal = Decimal("0")
    delta_limit: Decimal | None = None
    gamma_limit: Decimal | None = None
    max_family_positions: int = 0
    max_expiration_positions: int = 0
    risk_per_trade_frac: float = 0.02
    decision_ttl_seconds: int = 60

    def __post_init__(self) -> None:
        if self.max_risk_dollars < 0:
            raise ValueError("max_risk_dollars cannot be negative")
        if self.max_contracts < 0:
            raise ValueError("max_contracts cannot be negative")
        if self.max_positions < 0:
            raise ValueError("max_positions cannot be negative")
        if self.max_daily_loss < 0:
            raise ValueError("max_daily_loss cannot be negative")
        if not 0.0 <= self.risk_per_trade_frac <= 1.0:
            raise ValueError("risk_per_trade_frac must be within [0, 1]")
        if self.decision_ttl_seconds <= 0:
            raise ValueError("decision_ttl_seconds must be positive")


@dataclass(frozen=True, slots=True)
class RiskEnvelope:
    """Pre-trade risk envelope (spec §50 + System A envelope fields)."""

    schema_version: str = RISK_SCHEMA
    account_id: str = ""
    approved: bool = True
    max_risk_dollars: Decimal = Decimal("0")
    max_contracts: int = 0
    max_positions: int = 0
    max_daily_loss: Decimal = Decimal("0")
    max_size_scalar: float = 1.0
    remaining_daily_loss_budget: Decimal = Decimal("0")
    remaining_position_slots: int = 0
    delta_limit: Decimal | None = None
    gamma_limit: Decimal | None = None
    max_family_positions: int = 0
    max_expiration_positions: int = 0
    lockout_active: bool = False
    deployment_permission: bool = True
    decision_ttl_seconds: int = 60
    hard_vetoes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    # Phase-0/policy compatibility field name (alias of max_risk_dollars).
    max_defined_risk_per_trade: Decimal | None = None

    def __post_init__(self) -> None:
        risk = self.max_risk_dollars
        if self.max_defined_risk_per_trade is not None:
            risk = self.max_defined_risk_per_trade
            object.__setattr__(self, "max_risk_dollars", risk)
        if risk < 0:
            raise ValueError("max_risk_dollars cannot be negative")
        if not 0.0 <= self.max_size_scalar <= 1.0:
            raise ValueError("max_size_scalar must be within [0, 1]")
        if self.remaining_position_slots < 0:
            raise ValueError("remaining_position_slots cannot be negative")
        if self.hard_vetoes and self.approved:
            raise ValueError("risk envelope cannot be approved with hard vetoes")
        if not self.approved and self.max_size_scalar != 0.0:
            raise ValueError("rejected risk envelope must use max_size_scalar=0")
        # Keep both names synchronized for getattr-based policy helpers.
        object.__setattr__(self, "max_defined_risk_per_trade", risk)

    @classmethod
    def rejected(cls, *reasons: str, account_id: str = "") -> RiskEnvelope:
        return cls(
            account_id=account_id,
            approved=False,
            max_risk_dollars=Decimal("0"),
            max_size_scalar=0.0,
            remaining_daily_loss_budget=Decimal("0"),
            remaining_position_slots=0,
            lockout_active=True,
            deployment_permission=False,
            hard_vetoes=tuple(reasons) or ("risk_rejected",),
        )


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Firewall output immediately before order intent (spec §50)."""

    schema_version: str = RISK_DECISION_SCHEMA
    approved: bool = False
    approved_contracts: int = 0
    approved_risk_dollars: Decimal = Decimal("0")
    vetoes: tuple[RiskVeto, ...] = ()
    checks: tuple[RiskCheck, ...] = ()
    candidate_hash: str = ""
    market_snapshot_id: str = ""
    account_state_id: str = ""
    expires_at: datetime | None = None
    # Phase-0 compatibility (preferred path uses approved/vetoes).
    allowed: bool | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.allowed is not None and self.allowed != self.approved:
            object.__setattr__(self, "approved", bool(self.allowed))
        if self.approved and self.approved_contracts < 0:
            raise ValueError("approved_contracts cannot be negative")
        if self.approved_risk_dollars < 0:
            raise ValueError("approved_risk_dollars cannot be negative")
        if self.approved and any(v.severity == "hard" for v in self.vetoes):
            raise ValueError("approved decision cannot carry hard vetoes")
        # Populate legacy fields for older callers/tests.
        object.__setattr__(self, "allowed", self.approved)
        if not self.reason:
            if self.vetoes:
                object.__setattr__(
                    self,
                    "reason",
                    ",".join(v.code for v in self.vetoes),
                )
            elif self.approved:
                object.__setattr__(self, "reason", "within deterministic risk envelope")
            else:
                object.__setattr__(self, "reason", "risk_rejected")
