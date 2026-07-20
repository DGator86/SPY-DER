"""Volatility features (master spec §19).

ATM straddle and expected (implied) move from the option chain, migrated in
spirit from System A ``straddle_breakeven`` / ``expected_range`` usage. Prices
that cannot be formed (no two-sided market at the ATM strike) yield ``None``
rather than a fabricated value (spec §7.5).
"""

from __future__ import annotations

from decimal import Decimal

from spy_der.contracts.market import CanonicalMarketSnapshot, OptionQuote, OptionType
from spy_der.contracts.structure import VolatilitySummary

__all__ = ["compute_volatility"]


def _mid(quote: OptionQuote) -> Decimal | None:
    if quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid >= 0:
        return (quote.bid + quote.ask) / 2
    if quote.mark is not None:
        return quote.mark
    if quote.last is not None:
        return quote.last
    return None


def compute_volatility(
    snapshot: CanonicalMarketSnapshot,
    *,
    session_open_price: Decimal | None = None,
) -> VolatilitySummary | None:
    """ATM straddle / expected-move summary; ``None`` if no ATM straddle."""
    spot = snapshot.underlying_price
    if not snapshot.option_chain:
        return None

    strikes = {q.contract.strike for q in snapshot.option_chain}
    atm_strike = min(strikes, key=lambda k: abs(k - spot))

    call_mid: Decimal | None = None
    put_mid: Decimal | None = None
    for quote in snapshot.option_chain:
        if quote.contract.strike != atm_strike:
            continue
        if quote.contract.option_type is OptionType.CALL and call_mid is None:
            call_mid = _mid(quote)
        elif quote.contract.option_type is OptionType.PUT and put_mid is None:
            put_mid = _mid(quote)

    if call_mid is None or put_mid is None:
        return None

    straddle = call_mid + put_mid
    expected_move = straddle
    expected_move_pct = float(straddle / spot) if spot else 0.0

    consumed: float | None = None
    if session_open_price is not None and straddle > 0:
        consumed = float(abs(spot - session_open_price) / straddle)

    return VolatilitySummary(
        atm_strike=atm_strike,
        atm_straddle=straddle,
        expected_move=expected_move,
        expected_move_pct=expected_move_pct,
        expected_move_consumed=consumed,
    )
