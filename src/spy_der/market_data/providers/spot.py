"""Recover the underlying price from an option chain.

Ported from 0DTE ``massive_feed._estimate_spot`` (DGator86/0DTE @ 6393cd1),
including the bias fix recorded in its docstring.

Some vendors omit the underlying price from the options snapshot — it is
confirmed absent on the live Massive feed — so spot has to be recovered from the
chain itself. Put-call parity at the money does it:

    C - P = S - K * e^(-rT)   ->   S ~= (C_mid - P_mid) + K

For 0DTE the discount factor is ~1 (``K*r*T`` is a few cents), so the strike
where ``|C_mid - P_mid|`` is smallest is the ATM crossing, and parity there
recovers spot to within the bid/ask. The median over the few strikes nearest that
crossing guards against one bad mark.

**This deliberately replaces the deep-ITM-intrinsic estimate 0DTE used earlier,
which was biased high — roughly 1% on the live SPY chain (735 estimated against
a 727-728 ATM).** Preserving that correction is the reason this logic is ported
rather than reinvented; a 1% spot error moves every moneyness band and silently
reshapes the candidate universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["ChainQuoteView", "estimate_spot_from_chain"]

#: Strikes either side of the ATM crossing included in the median.
_PARITY_WINDOW = 5


@dataclass(frozen=True, slots=True)
class ChainQuoteView:
    """The minimum a spot estimate needs from one contract."""

    is_call: bool
    strike: float
    bid: float
    ask: float
    delta: float | None = None

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread_pct(self) -> float:
        """Relative spread; used to prefer the tighter of duplicate strikes."""
        mid = self.mid
        if mid <= 0.0:
            return float("inf")
        return (self.ask - self.bid) / mid


def estimate_spot_from_chain(quotes: list[ChainQuoteView]) -> Decimal | None:
    """Estimate spot via ATM put-call parity; ``None`` when not recoverable.

    Falls back to the strike of the call whose delta is nearest 0.5 (the ATM
    forward) when no strike has both a call and a put. Returning ``None`` rather
    than a guess matters: the caller treats it as "no data" and fails over to
    another provider instead of building candidates around a fabricated spot.
    """
    calls: dict[float, ChainQuoteView] = {}
    puts: dict[float, ChainQuoteView] = {}
    for quote in quotes:
        bucket = calls if quote.is_call else puts
        existing = bucket.get(quote.strike)
        # Prefer the tighter quote when a strike appears more than once.
        if existing is None or quote.spread_pct < existing.spread_pct:
            bucket[quote.strike] = quote

    paired = sorted(set(calls) & set(puts))
    if paired:
        atm = min(paired, key=lambda k: abs(calls[k].mid - puts[k].mid))
        window = sorted(paired, key=lambda k: abs(k - atm))[:_PARITY_WINDOW]
        estimates = sorted((calls[k].mid - puts[k].mid) + k for k in window)
        median = estimates[len(estimates) // 2]
        if median > 0.0:
            return Decimal(f"{median:.2f}")
        return None

    call_deltas = [q for q in quotes if q.is_call and q.delta is not None and q.delta > 0.0]
    if call_deltas:
        nearest = min(call_deltas, key=lambda q: abs((q.delta or 0.0) - 0.5))
        if nearest.strike > 0.0:
            return Decimal(f"{nearest.strike:.2f}")
    return None
