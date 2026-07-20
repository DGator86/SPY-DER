"""Mid / natural / fill-price interpolation (System A execution_cost.py)."""

from __future__ import annotations

from decimal import Decimal

from spy_der.contracts.candidates import Candidate, CandidateLeg
from spy_der.contracts.market import OptionQuote

__all__ = [
    "fill_price",
    "half_spread_cost",
    "mid_price",
    "natural_price",
    "quote_map",
]


def quote_map(quotes: tuple[OptionQuote, ...] | list[OptionQuote]) -> dict[str, OptionQuote]:
    return {q.contract.contract_id: q for q in quotes}


def _leg_mid(quote: OptionQuote) -> Decimal | None:
    if quote.mark is not None:
        return quote.mark
    if quote.bid is not None and quote.ask is not None:
        return (quote.bid + quote.ask) / Decimal("2")
    return None


def mid_price(
    legs: tuple[CandidateLeg, ...],
    quotes: dict[str, OptionQuote],
) -> Decimal | None:
    """Net credit at mids (short adds, long subtracts)."""
    total = Decimal("0")
    for leg in legs:
        quote = quotes.get(leg.contract_id)
        if quote is None:
            return None
        mid = _leg_mid(quote)
        if mid is None:
            return None
        total += -Decimal(leg.quantity) * mid
    return total


def natural_price(
    legs: tuple[CandidateLeg, ...],
    quotes: dict[str, OptionQuote],
) -> Decimal | None:
    """Adverse natural: buys at ask, sells at bid."""
    total = Decimal("0")
    for leg in legs:
        quote = quotes.get(leg.contract_id)
        if quote is None or quote.bid is None or quote.ask is None:
            return None
        px = quote.ask if leg.quantity > 0 else quote.bid
        total += -Decimal(leg.quantity) * px
    return total


def half_spread_cost(
    legs: tuple[CandidateLeg, ...],
    quotes: dict[str, OptionQuote],
) -> Decimal | None:
    mid = mid_price(legs, quotes)
    nat = natural_price(legs, quotes)
    if mid is None or nat is None:
        return None
    return max(mid - nat, Decimal("0"))


def fill_price(mid: Decimal, natural: Decimal, fill_frac: float) -> Decimal:
    """Interpolate mid → natural by fill_fraction ∈ [0, 1]."""
    f = Decimal(str(min(max(float(fill_frac), 0.0), 1.0)))
    return mid - f * (mid - natural)


def relative_spread(
    candidate: Candidate,
    quotes: dict[str, OptionQuote],
) -> float | None:
    hs = half_spread_cost(candidate.legs, quotes)
    mid = mid_price(candidate.legs, quotes)
    if hs is None or mid is None:
        return None
    scale = abs(mid) if mid != 0 else Decimal("1")
    return float(hs / scale)
