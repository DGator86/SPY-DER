"""Exit ratios must be signed by entry type, not by raw price movement.

`avg_fill_price` is a positive price magnitude for credit and debit alike — the
direction lives in the order's `side`. So `(mark - entry) / entry` is right for
a debit structure and exactly backwards for a credit one, and SPY-DER trades
credit families (iron fly, iron condor, credit spreads) most of all.

The consequence was not subtle. A winning iron fly — bought back cheaper than
it was sold — produced a negative ratio and tripped `stop`. A losing one
produced a positive ratio and tripped `target`. Both exits lock in a loss, and
nothing raises: the position closes with a plausible-looking reason attached.

`test_a_winning_credit_structure_is_not_stopped_out` is the regression that
matters; it fails against the old arithmetic with `reason == "stop"`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from spy_der.contracts.positions import (
    ApprovedExitPolicyId,
    ExitPolicy,
    PositionState,
    PositionStatus,
    profit_ratio,
)
from spy_der.positions.exits import evaluate_exit

# 11:00 ET — mid-session, so the EOD rule cannot mask the ratio under test.
NOW = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)


def _position(
    entry: str, mark: str, *, credit: bool, peak: str = "0", policy_id: str = ""
) -> PositionState:
    return PositionState(
        position_id="p1",
        account_id="system_b_grok",
        candidate_id="c1",
        status=PositionStatus.OPEN,
        opened_contracts=1,
        open_contracts=1,
        entry_price=Decimal(entry),
        mark_price=Decimal(mark),
        max_loss=Decimal("4"),
        peak_pnl=Decimal(peak),
        opened_at=NOW,
        opened_for_credit=credit,
        exit_policy_id=policy_id or ApprovedExitPolicyId.TARGET_AND_STOP.value,
    )


def _signal(pos: PositionState, policy: ExitPolicy | None = None):
    assert pos.mark_price is not None
    return evaluate_exit(
        pos, mark_price=pos.mark_price, now=NOW, policy=policy or ExitPolicy()
    )


# --------------------------------------------------------------------------- #
# The inversion                                                               #
# --------------------------------------------------------------------------- #
def test_a_winning_credit_structure_is_not_stopped_out() -> None:
    """Sold at 1.00, buyable at 0.50 — half the credit captured. That is the target."""
    signal = _signal(_position("1.00", "0.50", credit=True))
    assert signal.should_exit is True
    assert signal.reason == "target", "a winning credit structure was stopped out"


def test_a_losing_credit_structure_is_not_taken_as_profit() -> None:
    """Sold at 1.00, now costs 1.60 to close — that is a loss, and a stop."""
    signal = _signal(_position("1.00", "1.60", credit=True))
    assert signal.should_exit is True
    assert signal.reason == "stop", "a losing credit structure was taken as profit"


def test_a_credit_structure_drifting_slightly_is_held() -> None:
    assert _signal(_position("1.00", "0.90", credit=True)).should_exit is False


@pytest.mark.parametrize(
    ("mark", "expected"),
    [("1.60", "target"), ("0.50", "stop"), ("1.10", "hold")],
)
def test_debit_structures_are_unchanged(mark: str, expected: str) -> None:
    """The fix must not move the case that was already right."""
    assert _signal(_position("1.00", mark, credit=False)).reason == expected


# --------------------------------------------------------------------------- #
# The shared helper every consumer must come through                          #
# --------------------------------------------------------------------------- #
def test_profit_ratio_is_positive_for_profit_either_way() -> None:
    assert profit_ratio("1.00", "0.50", opened_for_credit=True) == Decimal("0.5")
    assert profit_ratio("1.00", "1.50", opened_for_credit=False) == Decimal("0.5")


def test_profit_ratio_is_negative_for_loss_either_way() -> None:
    assert profit_ratio("1.00", "1.50", opened_for_credit=True) == Decimal("-0.5")
    assert profit_ratio("1.00", "0.50", opened_for_credit=False) == Decimal("-0.5")


def test_a_zero_entry_price_is_refused_rather_than_dividing() -> None:
    with pytest.raises(ValueError, match="non-zero entry price"):
        profit_ratio("0", "1.00", opened_for_credit=True)


def test_take_profit_ratio_means_the_conventional_thing_for_a_credit() -> None:
    """`take_profit_ratio=0.5` should mean buying back for half the credit."""
    policy = ExitPolicy(take_profit_ratio=0.5)
    assert _signal(_position("2.00", "1.01", credit=True), policy).should_exit is False
    assert _signal(_position("2.00", "0.99", credit=True), policy).reason == "target"


# --------------------------------------------------------------------------- #
# Trailing                                                                    #
# --------------------------------------------------------------------------- #
def test_trailing_arms_on_a_credit_structures_real_gain() -> None:
    """Peak is a profit ratio, so it must be measured in the same direction."""
    policy = ExitPolicy(
        policy_id=ApprovedExitPolicyId.TRAILING.value,
        take_profit_ratio=0.9,
        stop_loss_ratio=0.9,
        trailing_arm_ratio=0.25,
        trailing_giveback_ratio=0.15,
    )
    # Sold at 1.00, ran to 0.60 (peak +0.40 armed), given back to 0.80 (+0.20).
    position = _position("1.00", "0.80", credit=True, peak="0.40")
    signal = _signal(position, policy)
    assert signal.should_exit is True
    assert signal.reason == "trail"


def test_the_position_manager_marks_a_credit_structure_in_the_right_direction() -> None:
    """`mark()` feeds `peak_pnl`, so an inverted ratio there breaks trailing too."""
    from spy_der.contracts.execution import OrderState, OrderStatus
    from spy_der.positions.manager import PositionManager

    manager = PositionManager()
    order = OrderState(
        order_id="o1",
        intent_id="i1",
        account_id="system_b_grok",
        candidate_id="c1",
        status=OrderStatus.FILLED,
        requested_contracts=1,
        filled_contracts=1,
        avg_fill_price=Decimal("1.00"),
    )
    opened = manager.on_order_state(
        order, max_loss=Decimal("4"), opened_for_credit=True, now=NOW
    )
    assert opened is not None
    assert opened.opened_for_credit is True

    # Buying it back cheaper is a gain.
    marked = manager.mark(opened.position_id, Decimal("0.60"), now=NOW)
    assert marked.peak_pnl == Decimal("0.4")
    assert marked.unrealized_pnl > 0
