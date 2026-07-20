"""Executable economics contracts (master spec §33, §34)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from spy_der.contracts.common import require_non_negative, require_probability

__all__ = [
    "ECONOMICS_VERSION",
    "FILL_RECORD_VERSION",
    "CandidateEconomics",
    "FillRecord",
]

ECONOMICS_VERSION = "economics.v1"
FILL_RECORD_VERSION = "v3.0.0"


@dataclass(frozen=True, slots=True)
class CandidateEconomics:
    """Common economics view for all policies/agents (spec §33). Midpoint is diagnostic."""

    candidate_id: str
    economics_version: str = ECONOMICS_VERSION
    mid_price: Decimal | None = None
    natural_price: Decimal | None = None
    expected_fill_price: Decimal | None = None
    conservative_fill_price: Decimal | None = None
    fill_probability: float = 0.0
    expected_fill_fraction: float = 0.0
    fees: Decimal = Decimal("0")
    entry_slippage: Decimal = Decimal("0")
    exit_slippage: Decimal = Decimal("0")
    stop_slippage: Decimal = Decimal("0")
    liquidity_score: float = 0.0
    quote_quality: tuple[str, ...] = ()
    maximum_loss: Decimal = Decimal("0")
    maximum_profit: Decimal | None = None
    return_on_defined_risk: float | None = None
    expected_value: Decimal | None = None
    cvar: Decimal | None = None
    expected_shortfall: Decimal | None = None
    touch_probability: float | None = None
    wall_distance: float | None = None
    data_quality_penalty: float = 0.0
    fallback_level: str = "deterministic_prior"
    diagnostics: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        require_probability(self.fill_probability, "fill_probability")
        require_probability(self.expected_fill_fraction, "expected_fill_fraction")
        require_probability(self.liquidity_score, "liquidity_score")
        require_non_negative(float(self.fees), "fees")
        require_non_negative(float(self.entry_slippage), "entry_slippage")
        require_non_negative(float(self.exit_slippage), "exit_slippage")
        require_non_negative(float(self.stop_slippage), "stop_slippage")
        require_non_negative(float(self.maximum_loss), "maximum_loss")
        require_non_negative(self.data_quality_penalty, "data_quality_penalty")
        if self.touch_probability is not None:
            require_probability(self.touch_probability, "touch_probability")
        # Monotonicity: mid >= expected >= conservative (credit convention).
        if (
            self.mid_price is not None
            and self.expected_fill_price is not None
            and self.expected_fill_price > self.mid_price
        ):
            raise ValueError("expected_fill_price cannot be better than mid_price")
        if (
            self.expected_fill_price is not None
            and self.conservative_fill_price is not None
            and self.conservative_fill_price > self.expected_fill_price
        ):
            raise ValueError("conservative_fill_price cannot be better than expected")


@dataclass(frozen=True, slots=True)
class FillRecord:
    """One attempted order for empirical fill models (spec §34)."""

    fill_record_id: str
    snapshot_id: str
    candidate_id: str
    session_date: str
    decision_ts: str
    submitted_ts: str
    resolved_ts: str | None
    symbol: str
    family: str
    side: str
    n_legs: int
    limit_credit: Decimal
    mid_credit_at_submit: Decimal
    natural_credit_at_submit: Decimal
    relative_spread: float
    absolute_spread: float
    option_price_scale: float
    quote_age_seconds: float
    minutes_to_close: float
    realized_volatility: float | None = None
    implied_remaining_move: float | None = None
    dominant_regime: str | None = None
    data_quality: float | None = None
    replacement_count: int = 0
    replacement_prices: tuple[Decimal, ...] = ()
    filled: bool = False
    partial_fill: bool = False
    filled_quantity: int = 0
    requested_quantity: int = 0
    seconds_to_first_fill: float | None = None
    seconds_to_complete_fill: float | None = None
    fill_credit: Decimal | None = None
    fill_fraction: float | None = None
    fill_fraction_raw: float | None = None
    fill_fraction_clipped: float | None = None
    cancelled: bool = False
    expired_unfilled: bool = False
    rejected: bool = False
    source: str = "paper"
    mode: str = "shadow"
    version: str = FILL_RECORD_VERSION
    diagnostics: tuple[tuple[str, str], ...] = field(default_factory=tuple)
