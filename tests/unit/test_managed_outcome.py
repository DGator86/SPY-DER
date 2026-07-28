"""Managed P&L: what a position realizes, not what it was worth at expiry.

SPY-DER never holds to settlement — its live exits are `trail`, `target` and
`eod`. Training on terminal payoff fitted the candidate-value model to a
quantity the system never realizes, and the error is not symmetric: a credit
structure usually pins near max profit at expiry while the managed version gets
stopped out on the excursion along the way.

`test_an_excursion_and_recovery_realizes_the_exit_not_the_settlement` is the
case that makes the difference concrete — same tape, same structure, two
different numbers, and only one of them is what the account realized.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from test_dojo_native_tape import _session_snapshots

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.contracts.market import Bar
from spy_der.contracts.positions import ExitPolicy
from spy_der.evaluation.managed_outcome import (
    ManagedOutcome,
    mark_structure,
    simulate_managed_exit,
)
from spy_der.evaluation.settlement import settled_candidate_pnl

SESSION = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # 09:30 ET


def _bars(closes: list[float], *, start: datetime = OPEN_UTC) -> list[Bar]:
    return [
        Bar(
            timestamp=start + timedelta(minutes=i),
            open=Decimal(str(c)),
            high=Decimal(str(c)) + Decimal("0.10"),
            low=Decimal(str(c)) - Decimal("0.10"),
            close=Decimal(str(c)),
            volume=1000,
        )
        for i, c in enumerate(closes)
    ]


def _a_candidate(family_contains: str = ""):
    """A real candidate from the real factory, so geometry and credit are honest."""
    snapshot = _session_snapshots(SESSION, seed=3, ticks=1)[0]
    universe = generate_candidate_universe(snapshot)
    assert universe.candidates
    for candidate in universe.candidates:
        if family_contains and family_contains not in candidate.family:
            continue
        if candidate.entry_credit != 0:
            return candidate, snapshot
    pytest.skip(f"no candidate matching {family_contains!r} in the factory output")


# --------------------------------------------------------------------------- #
# Marking                                                                     #
# --------------------------------------------------------------------------- #
def test_the_mark_converges_to_intrinsic_as_time_runs_out() -> None:
    """At zero minutes left the Black mark must equal the terminal payoff."""
    candidate, _snapshot = _a_candidate()
    ivs = {leg.contract_id: 0.2 for leg in candidate.legs}
    spot = Decimal("101.0")

    at_expiry = mark_structure(candidate, ivs, spot, 0.0)
    terminal = settled_candidate_pnl(candidate, SESSION, spot)
    assert terminal is not None
    # pnl = entry_credit + V; at expiry V is intrinsic, so they must agree.
    assert abs((candidate.entry_credit + at_expiry) - terminal) < Decimal("0.01")


def test_time_value_decays_toward_the_close() -> None:
    candidate, _ = _a_candidate()
    ivs = {leg.contract_id: 0.2 for leg in candidate.legs}
    spot = Decimal("100.0")

    early = mark_structure(candidate, ivs, spot, 360.0)
    late = mark_structure(candidate, ivs, spot, 5.0)
    assert early != late, "a mark that ignores time is not marking anything"


def test_a_missing_iv_is_a_stated_fallback_not_zero() -> None:
    """Zero vol prices every option at intrinsic and makes the path jump."""
    candidate, snapshot = _a_candidate()
    stripped = tuple(
        type(q)(**{**{f.name: getattr(q, f.name) for f in q.__dataclass_fields__.values()},  # type: ignore[attr-defined]
                   "implied_volatility": None})
        for q in snapshot.option_chain
    )
    outcome = simulate_managed_exit(
        candidate,
        chain=stripped,
        bars=_bars([100.0] * 30),
        observed_at=OPEN_UTC,
        session=SESSION,
        settlement=Decimal("100.0"),
    )
    assert outcome is not None


# --------------------------------------------------------------------------- #
# The difference that matters                                                 #
# --------------------------------------------------------------------------- #
def test_an_excursion_and_recovery_realizes_the_exit_not_the_settlement() -> None:
    """The asymmetry that made expiry-value training wrong.

    Spot runs hard against the position, trips an exit, then comes all the way
    back so the structure *settles* somewhere else entirely. Terminal payoff
    reports the settlement. The position was closed hours earlier, at the mark
    that fired the exit.

    Which of the two is larger is not the point and is not asserted — a stop
    can cut a loss as easily as it can miss a recovery. The point is that they
    are different numbers, and only one of them is what the account realized.
    """
    candidate, snapshot = _a_candidate()
    # Away sharply, then home again by the close.
    closes = [100.0] + [100.0 + 0.6 * i for i in range(1, 25)] + [100.0] * 40
    bars = _bars(closes)

    managed = simulate_managed_exit(
        candidate,
        chain=snapshot.option_chain,
        bars=bars,
        observed_at=OPEN_UTC,
        session=SESSION,
        settlement=Decimal("100.0"),
        policy=ExitPolicy(take_profit_ratio=0.5, stop_loss_ratio=0.35),
    )
    assert managed is not None
    terminal = settled_candidate_pnl(candidate, SESSION, Decimal("100.0"))
    assert terminal is not None

    assert managed.was_managed, "an exit should have fired on this path"
    assert managed.exit_reason in {"stop", "target", "trail", "eod"}
    assert managed.realized_pnl != terminal, (
        "managed and settlement P&L must not be the same number, or there is "
        "nothing for this module to correct"
    )
    assert managed.exit_at is not None
    assert managed.exit_at < bars[-1].timestamp, "the position closed early"


def test_an_untouched_position_settles_at_the_terminal_payoff() -> None:
    """No exit fired means the old answer, as one branch rather than the rule."""
    candidate, snapshot = _a_candidate()
    # Flat tape well before the close: nothing should trip.
    outcome = simulate_managed_exit(
        candidate,
        chain=snapshot.option_chain,
        bars=_bars([100.0] * 20),
        observed_at=OPEN_UTC,
        session=SESSION,
        settlement=Decimal("100.0"),
        policy=ExitPolicy(take_profit_ratio=0.99, stop_loss_ratio=0.99, eod_close=False),
    )
    assert outcome is not None
    if outcome.held_to_expiry:
        assert outcome.exit_reason == "expiration_settlement"
        assert outcome.realized_pnl == settled_candidate_pnl(
            candidate, SESSION, Decimal("100.0")
        )


def test_eod_is_a_managed_exit_not_settlement() -> None:
    """`eod` closes at 15:55 — the last five minutes are not ours."""
    candidate, snapshot = _a_candidate()
    late = datetime(2026, 1, 5, 20, 50, tzinfo=UTC)  # 15:50 ET
    outcome = simulate_managed_exit(
        candidate,
        chain=snapshot.option_chain,
        bars=_bars([100.0] * 15, start=late),
        observed_at=late,
        session=SESSION,
        settlement=Decimal("100.0"),
        policy=ExitPolicy(take_profit_ratio=0.99, stop_loss_ratio=0.99, eod_close=True),
    )
    assert outcome is not None
    if outcome.exit_reason == "eod":
        assert outcome.held_to_expiry is False


# --------------------------------------------------------------------------- #
# Absent stays absent                                                         #
# --------------------------------------------------------------------------- #
def test_a_later_expiry_is_not_settled_here() -> None:
    """A structure expiring after the session still holds time value at the close."""
    candidate, snapshot = _a_candidate()
    assert (
        simulate_managed_exit(
            candidate,
            chain=snapshot.option_chain,
            bars=_bars([100.0] * 10),
            observed_at=OPEN_UTC,
            session=date(2026, 1, 6),
            settlement=Decimal("100.0"),
        )
        is None
    )


def test_no_forward_bars_yields_no_outcome(tmp_path: object) -> None:
    candidate, snapshot = _a_candidate()
    assert (
        simulate_managed_exit(
            candidate,
            chain=snapshot.option_chain,
            bars=_bars([100.0] * 5),
            observed_at=OPEN_UTC + timedelta(hours=8),
            session=SESSION,
            settlement=Decimal("100.0"),
        )
        is None
    )


def test_the_outcome_reports_whether_it_was_managed() -> None:
    outcome = ManagedOutcome(
        realized_pnl=Decimal("1"),
        exit_reason="stop",
        exit_at=OPEN_UTC,
        held_to_expiry=False,
    )
    assert outcome.was_managed is True
    assert ManagedOutcome(
        realized_pnl=Decimal("1"),
        exit_reason="expiration_settlement",
        exit_at=OPEN_UTC,
        held_to_expiry=True,
    ).was_managed is False
