"""What a position would actually have realized, not what it was worth at expiry.

:func:`~spy_der.evaluation.settlement.settled_candidate_pnl` answers "what did
this structure pay if held to settlement". SPY-DER never holds to settlement —
its live exits are ``trail``, ``target`` and ``eod``. Training the
candidate-value model on terminal payoff therefore fitted it to a quantity the
system never realizes, and the error is not symmetric: a credit structure
usually pins near max profit at expiry while the managed version gets stopped
out on the excursion along the way. Expiry-value training teaches the model to
love exactly the family that keeps getting stopped.

So this walks the session's bar path from the observation tick, marks the
structure at each bar, and applies the *production* exit policy through
:func:`~spy_der.positions.exits.evaluate_exit`. The result is the P&L at the
exit that would actually have fired — or the terminal payoff when none did,
which is the old answer as a special case rather than a separate code path.

Two honest costs, stated here rather than discovered later:

* **The mark is modelled, not observed.** Implied volatility is frozen at its
  entry value for the whole path. Real IV moves, and a vol spike that would
  blow through a stop is invisible to this. That makes the target approximate
  where the expiry payoff was exact — but exact arithmetic on a quantity the
  system never realizes is the worse of the two errors.
* **The target is policy-dependent.** Change the exit ratios and every fitted
  model is stale, because the label means "value under policy P". Callers must
  record the policy alongside the model; :mod:`spy_der.training.candidate_value`
  puts it in ``label_version`` so a stale model cannot load silently.

Sign conventions are the part that bites. ``entry_credit`` is cash collected
(positive for a credit structure, negative for a debit), and it equals the
negated net option value at entry, so:

    pnl(t) = entry_credit + V(t)     where V(t) = Σ qtyᵢ · valueᵢ(t)

At expiry ``valueᵢ`` is intrinsic and this reduces exactly to
:func:`~spy_der.candidates.payoff.terminal_payoff`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from spy_der.contracts.candidates import Candidate
from spy_der.contracts.market import Bar, OptionQuote, OptionType
from spy_der.contracts.positions import (
    ExitPolicy,
    PositionState,
    PositionStatus,
    profit_ratio,
)
from spy_der.evaluation.settlement import settled_candidate_pnl
from spy_der.features.resample import ET
from spy_der.positions.exits import evaluate_exit
from spy_der.synthetic.pricing import black_call_forward, black_put_forward

__all__ = [
    "DEFAULT_FALLBACK_IV",
    "SESSION_CLOSE",
    "ManagedOutcome",
    "mark_structure",
    "simulate_managed_exit",
]

#: Exchange close. Time to expiry is measured to this instant for a 0DTE
#: structure, which is what makes theta bleed through the session.
SESSION_CLOSE = time(16, 0)

#: Used only when a leg's quote carried no implied volatility. A missing IV is
#: not zero — zero vol would price every option at intrinsic and make the
#: simulated path jump discontinuously at the first bar.
DEFAULT_FALLBACK_IV = 0.20

_YEAR_MINUTES = 365.0 * 24.0 * 60.0


def _leg_ivs(candidate: Candidate, chain: Sequence[OptionQuote]) -> dict[str, float]:
    """Entry implied volatility per leg contract, with a stated fallback."""
    by_id = {q.contract.contract_id: q for q in chain}
    out: dict[str, float] = {}
    for leg in candidate.legs:
        quote = by_id.get(leg.contract_id)
        iv = quote.implied_volatility if quote is not None else None
        out[leg.contract_id] = (
            float(iv) if iv is not None and iv > 0 else DEFAULT_FALLBACK_IV
        )
    return out


def _minutes_to_expiry(now: datetime, expiration: date) -> float:
    close = datetime.combine(expiration, SESSION_CLOSE, tzinfo=ET)
    return max((close - now.astimezone(ET)).total_seconds() / 60.0, 0.0)


def mark_structure(
    candidate: Candidate,
    ivs: dict[str, float],
    spot: Decimal,
    minutes_left: float,
) -> Decimal:
    """Net option value ``V(t)`` of the structure — positive is net long premium.

    Forward-Black with no discounting and forward == spot: for a same-day
    expiry the carry term is far below the tick size, and pretending otherwise
    would imply a precision this mark does not have.
    """
    years = max(minutes_left, 0.0) / _YEAR_MINUTES
    total = Decimal("0")
    forward = float(spot)
    for leg in candidate.legs:
        sigma = ivs.get(leg.contract_id, DEFAULT_FALLBACK_IV)
        total_vol = sigma * math.sqrt(years) if years > 0.0 else 0.0
        strike = float(leg.strike)
        if leg.option_type is OptionType.CALL:
            value = black_call_forward(forward, strike, total_vol)
        else:
            value = black_put_forward(forward, strike, total_vol)
        total += Decimal(leg.quantity) * Decimal(str(round(value, 6)))
    return total


@dataclass(frozen=True, slots=True)
class ManagedOutcome:
    """P&L the position would have realized, and why it closed."""

    realized_pnl: Decimal
    exit_reason: str
    exit_at: datetime | None
    #: True when no exit fired and the structure ran to settlement, in which
    #: case `realized_pnl` is the terminal payoff.
    held_to_expiry: bool

    @property
    def was_managed(self) -> bool:
        return not self.held_to_expiry


def simulate_managed_exit(
    candidate: Candidate,
    *,
    chain: Sequence[OptionQuote],
    bars: Sequence[Bar],
    observed_at: datetime,
    session: date,
    settlement: Decimal,
    policy: ExitPolicy | None = None,
) -> ManagedOutcome | None:
    """Walk forward from ``observed_at`` and exit where the policy says to.

    Returns ``None`` when the structure does not expire on ``session`` — a
    later expiry still holds time value at the close and cannot be settled
    here — or when no bar follows the observation.
    """
    if candidate.expiration != session:
        return None
    forward = [b for b in bars if b.timestamp > observed_at]
    if not forward:
        return None

    exit_policy = policy or ExitPolicy()
    ivs = _leg_ivs(candidate, chain)
    # The entry is *observed* cash from real quote mids, not a modelled mark.
    # Only the forward path is modelled, so a pricing error cannot shift what
    # the position is measured against.
    entry_credit = candidate.entry_credit
    entry_price = abs(entry_credit)
    if entry_price == 0:
        # A structure with no net premium has no ratio to measure against.
        return None
    opened_for_credit = entry_credit > 0

    peak = Decimal("0")
    for bar in forward:
        minutes_left = _minutes_to_expiry(bar.timestamp, session)
        value = mark_structure(candidate, ivs, Decimal(str(bar.close)), minutes_left)
        mark_price = abs(value)
        ratio = profit_ratio(
            entry_price, mark_price, opened_for_credit=opened_for_credit
        )
        peak = max(peak, ratio)
        position = PositionState(
            position_id="sim",
            candidate_id=candidate.candidate_id,
            status=PositionStatus.OPEN,
            opened_contracts=1,
            open_contracts=1,
            entry_price=entry_price,
            mark_price=mark_price,
            max_loss=candidate.maximum_loss,
            peak_pnl=peak,
            opened_at=observed_at,
            exit_policy_id=exit_policy.policy_id,
            opened_for_credit=opened_for_credit,
        )
        signal = evaluate_exit(
            position,
            mark_price=mark_price,
            now=bar.timestamp,
            policy=exit_policy,
            expired=minutes_left <= 0.0,
        )
        if signal.should_exit:
            # Closed at the mark: cash collected at entry plus what the
            # structure is worth when it is bought back. `eod` is a *managed*
            # exit at 15:55, not settlement — the position closes early and
            # whatever the structure does in the last five minutes is not ours.
            return ManagedOutcome(
                realized_pnl=entry_credit + value,
                exit_reason=signal.reason,
                exit_at=bar.timestamp,
                held_to_expiry=signal.reason == "expiration_settlement",
            )

    # No exit fired before the tape ran out: the structure settles, which is
    # the terminal payoff — the old target, as one branch rather than the rule.
    settled = settled_candidate_pnl(candidate, session, settlement)
    if settled is None:
        return None
    return ManagedOutcome(
        realized_pnl=settled,
        exit_reason="expiration_settlement",
        exit_at=forward[-1].timestamp,
        held_to_expiry=True,
    )
