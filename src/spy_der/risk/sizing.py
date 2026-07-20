"""Position sizing helpers (System A scale_risk + contract calc)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from math import floor

__all__ = [
    "KELLY_FRACTION",
    "MIN_TRADES_TO_SCALE",
    "RISK_CEILING",
    "RISK_FLOOR",
    "SizeResult",
    "contracts_for_risk",
    "scale_risk",
    "size_scalar_for_contracts",
]

RISK_CEILING = 0.10
RISK_FLOOR = 0.02
KELLY_FRACTION = 0.5
MIN_TRADES_TO_SCALE = 30


@dataclass(frozen=True, slots=True)
class SizeResult:
    contracts: int
    risk_dollars: Decimal
    size_scalar: float
    reason: str = ""


def scale_risk(
    n_trades: int,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    *,
    risk_floor: float = RISK_FLOOR,
    risk_ceiling: float = RISK_CEILING,
    kelly_fraction: float = KELLY_FRACTION,
    min_trades: int = MIN_TRADES_TO_SCALE,
) -> tuple[float, str]:
    """Half-Kelly earned-risk scaling (System A spy0dte.scale_risk)."""
    if n_trades < min_trades:
        return risk_floor, f"only {n_trades} logged trades - floor until {min_trades}"
    if avg_loss <= 0 or win_rate <= 0:
        return risk_floor, "no measurable edge yet - floor"
    ratio = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / ratio
    if kelly <= 0:
        return risk_floor, f"Kelly {kelly:.2f} <= 0 - edge not positive, floor"
    frac = max(risk_floor, min(risk_ceiling, kelly * kelly_fraction))
    note = (
        f"W={win_rate:.0%}, R={ratio:.2f} -> Kelly {kelly:.2f}, "
        f"half {kelly * kelly_fraction:.2f} -> risk {frac:.0%}"
    )
    if kelly * kelly_fraction > risk_ceiling:
        note += f" (capped at {risk_ceiling:.0%})"
    return frac, note


def contracts_for_risk(
    *,
    equity: Decimal,
    risk_frac: float,
    max_loss_per_contract: Decimal,
    max_contracts: int = 0,
    max_risk_dollars: Decimal | None = None,
    size_scalar: float = 1.0,
) -> SizeResult:
    """Floor contracts from equity * risk_frac / max_loss, clamped by ceilings."""
    if max_loss_per_contract <= 0:
        return SizeResult(0, Decimal("0"), 0.0, reason="non_positive_max_loss")
    if equity <= 0 or risk_frac <= 0 or size_scalar <= 0:
        return SizeResult(0, Decimal("0"), 0.0, reason="zero_budget")

    budget = Decimal(str(equity)) * Decimal(str(risk_frac)) * Decimal(str(size_scalar))
    if max_risk_dollars is not None and max_risk_dollars > 0:
        budget = min(budget, Decimal(str(max_risk_dollars)))

    raw = floor(float(budget / Decimal(str(max_loss_per_contract))))
    contracts = max(0, int(raw))
    if max_contracts > 0:
        contracts = min(contracts, max_contracts)
    if contracts < 1:
        return SizeResult(0, Decimal("0"), 0.0, reason="insufficient_budget")

    risk_dollars = (Decimal(str(max_loss_per_contract)) * Decimal(contracts)).quantize(
        Decimal("0.0001"), rounding=ROUND_DOWN
    )
    return SizeResult(
        contracts=contracts,
        risk_dollars=risk_dollars,
        size_scalar=float(size_scalar),
        reason="sized",
    )


def size_scalar_for_contracts(
    *,
    requested_contracts: int,
    max_contracts: int,
) -> float:
    if requested_contracts <= 0 or max_contracts <= 0:
        return 0.0
    return min(1.0, requested_contracts / max_contracts)
