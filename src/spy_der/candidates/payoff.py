"""Deterministic terminal payoff and maximum-loss proof (spec §7.1, §31)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from math import isfinite

from spy_der.contracts.candidates import CandidateLeg, terminal_payoff_hash
from spy_der.contracts.market import OptionType

__all__ = [
    "PayoffProof",
    "entry_credit_from_mids",
    "intrinsic",
    "prove_bounded_loss",
    "terminal_payoff",
]


def intrinsic(option_type: OptionType, strike: Decimal, spot: Decimal) -> Decimal:
    if option_type is OptionType.CALL:
        return max(spot - strike, Decimal("0"))
    return max(strike - spot, Decimal("0"))


def terminal_payoff(
    legs: tuple[CandidateLeg, ...],
    *,
    entry_credit: Decimal,
    spot: Decimal,
) -> Decimal:
    """P&L per share at settlement: credit + signed intrinsic."""
    total = entry_credit
    for leg in legs:
        total += Decimal(leg.quantity) * intrinsic(leg.option_type, leg.strike, spot)
    return total


def entry_credit_from_mids(
    legs: tuple[CandidateLeg, ...],
    mids: dict[str, Decimal],
) -> Decimal | None:
    """Cash collected at entry (short adds, long subtracts). None if any mid missing."""
    total = Decimal("0")
    for leg in legs:
        mid = mids.get(leg.contract_id)
        if mid is None:
            return None
        if mid < 0 or not isfinite(float(mid)):
            return None
        total += -Decimal(leg.quantity) * mid
    return total


@dataclass(frozen=True, slots=True)
class PayoffProof:
    maximum_loss: Decimal
    maximum_profit: Decimal | None
    breakevens: tuple[Decimal, ...]
    capital_required: Decimal
    evaluation_spots: tuple[Decimal, ...]
    payoffs: tuple[Decimal, ...]
    payoff_hash: str
    unbounded: bool
    reasons: tuple[str, ...]


def _net_call_quantity(legs: tuple[CandidateLeg, ...]) -> int:
    return sum(leg.quantity for leg in legs if leg.option_type is OptionType.CALL)


def _evaluation_spots(legs: tuple[CandidateLeg, ...]) -> list[Decimal]:
    strikes = sorted({leg.strike for leg in legs})
    spots: list[Decimal] = [Decimal("0")]
    spots.extend(strikes)
    # Far upside probe used only when net call qty >= 0 (bounded-loss side).
    if strikes:
        span = max(strikes[-1] - strikes[0], Decimal("1"))
        spots.append(strikes[-1] + span * Decimal("10"))
    else:
        spots.append(Decimal("1000"))
    # Midpoints between adjacent strikes catch kink accuracy.
    for left, right in pairwise(strikes):
        spots.append((left + right) / Decimal("2"))
    return sorted(set(spots))


def _breakevens(
    spots: list[Decimal],
    payoffs: list[Decimal],
) -> tuple[Decimal, ...]:
    zeros: list[Decimal] = []
    for spot, pnl in zip(spots, payoffs, strict=True):
        if pnl == 0:
            zeros.append(spot)
    for i in range(len(spots) - 1):
        p0, p1 = payoffs[i], payoffs[i + 1]
        if p0 == 0 or p1 == 0:
            continue
        if (p0 > 0 and p1 < 0) or (p0 < 0 and p1 > 0):
            s0, s1 = spots[i], spots[i + 1]
            # Linear interpolate zero crossing.
            frac = p0 / (p0 - p1)
            zeros.append(s0 + (s1 - s0) * frac)
    # Deduplicate with stable string keys.
    uniq: dict[str, Decimal] = {str(z): z for z in sorted(zeros)}
    return tuple(uniq[k] for k in sorted(uniq))


def prove_bounded_loss(
    legs: tuple[CandidateLeg, ...],
    *,
    entry_credit: Decimal,
) -> PayoffProof:
    """Prove finite maximum loss via piecewise-linear breakpoints and tails.

    Rejects unbounded upside short-call exposure (net call quantity < 0).
    Put downside is always finite at spot=0 for option-only structures.
    """
    reasons: list[str] = []
    net_calls = _net_call_quantity(legs)
    if net_calls < 0:
        return PayoffProof(
            maximum_loss=Decimal("0"),
            maximum_profit=None,
            breakevens=(),
            capital_required=Decimal("0"),
            evaluation_spots=(),
            payoffs=(),
            payoff_hash="",
            unbounded=True,
            reasons=("unbounded_short_call_tail",),
        )

    spots = _evaluation_spots(legs)
    payoffs = [terminal_payoff(legs, entry_credit=entry_credit, spot=s) for s in spots]
    min_pnl = min(payoffs)
    max_pnl = max(payoffs)
    max_loss = -min_pnl if min_pnl < 0 else Decimal("0")
    if max_loss <= 0 and entry_credit <= 0:
        # Pure debit with non-negative curve everywhere still risks the debit.
        max_loss = -entry_credit if entry_credit < 0 else Decimal("0")
        reasons.append("debit_floor")

    # Unlimited upside profit when net long calls and far-spot payoff is the max
    # and still rising relative to last strike payoff.
    unlimited_profit = net_calls > 0
    maximum_profit: Decimal | None
    if unlimited_profit:
        maximum_profit = None
    else:
        maximum_profit = max_pnl if max_pnl > 0 else Decimal("0")

    if max_loss < 0:
        return PayoffProof(
            maximum_loss=Decimal("0"),
            maximum_profit=None,
            breakevens=(),
            capital_required=Decimal("0"),
            evaluation_spots=tuple(spots),
            payoffs=tuple(payoffs),
            payoff_hash="",
            unbounded=True,
            reasons=("negative_max_loss_invariant",),
        )

    # Capital at risk: max loss for defined-risk; debit paid for long premium.
    capital = max(max_loss, -entry_credit if entry_credit < 0 else Decimal("0"))
    be = _breakevens(spots, payoffs)
    ph = terminal_payoff_hash(
        entry_credit=entry_credit,
        evaluation_spots=tuple(spots),
        payoffs=tuple(payoffs),
    )
    return PayoffProof(
        maximum_loss=max_loss,
        maximum_profit=maximum_profit,
        breakevens=be,
        capital_required=capital,
        evaluation_spots=tuple(spots),
        payoffs=tuple(payoffs),
        payoff_hash=ph,
        unbounded=False,
        reasons=tuple(reasons),
    )
