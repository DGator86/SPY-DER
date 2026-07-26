"""Shadow account — model quality vs execution quality.

The property that matters most is reconciliation: every decomposition must sum
back to the gap it decomposes. Most of these tests assert that alongside the
behaviour they are actually checking, because a waterfall that silently drops a
few dollars would still look plausible in every individual assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spy_der.evaluation.attribution import (
    ActualTrade,
    AttributionComponent,
    AttributionConfig,
    BehaviorFlag,
    PlannedTrade,
    assert_reconciles,
    attribute_session,
    attribute_trade,
)

DECIDED = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
PLANNED_EXIT = datetime(2026, 7, 24, 19, 55, tzinfo=UTC)


def _planned(**overrides: object) -> PlannedTrade:
    base: dict[str, object] = {
        "candidate_id": "cand-a",
        "contracts": 10,
        # Credit spread sold for $0.50, planned to expire worthless.
        "entry_price": Decimal("-0.50"),
        "exit_price": Decimal("0"),
        "snapshot_id": "snap-1",
        "session_date": "2026-07-24",
        "decided_at": DECIDED,
        "planned_exit_at": PLANNED_EXIT,
    }
    base.update(overrides)
    return PlannedTrade(**base)  # type: ignore[arg-type]


def _actual(**overrides: object) -> ActualTrade:
    base: dict[str, object] = {
        "candidate_id": "cand-a",
        "contracts": 10,
        "entry_price": Decimal("-0.50"),
        "exit_price": Decimal("0"),
        "entry_at": DECIDED + timedelta(seconds=10),
        "exit_at": PLANNED_EXIT,
    }
    base.update(overrides)
    return ActualTrade(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Price convention                                                            #
# --------------------------------------------------------------------------- #
def test_credit_structure_expiring_worthless_earns_the_credit() -> None:
    result = attribute_trade(_planned(), _actual())
    assert result.model_pnl == Decimal("500.0000")
    assert result.actual_pnl == Decimal("500.0000")


def test_debit_structure_uses_the_same_formula() -> None:
    planned = _planned(entry_price=Decimal("1.00"), exit_price=Decimal("1.50"))
    actual = _actual(entry_price=Decimal("1.00"), exit_price=Decimal("1.50"))
    result = attribute_trade(planned, actual)
    assert result.model_pnl == Decimal("500.0000")


# --------------------------------------------------------------------------- #
# Waterfall                                                                   #
# --------------------------------------------------------------------------- #
def test_perfect_execution_leaves_every_component_at_zero() -> None:
    result = attribute_trade(_planned(), _actual())
    assert result.gap == Decimal("0")
    assert all(v == 0 for v in result.components.values())
    assert result.flags == ()
    assert_reconciles(result)


def test_bad_entry_fill_is_charged_to_entry_not_to_the_model() -> None:
    # Sold for $0.40 instead of the modelled $0.50 — $0.10 * 10 * 100 = $100.
    result = attribute_trade(_planned(), _actual(entry_price=Decimal("-0.40")))
    assert result.model_pnl == Decimal("500.0000")
    assert result.actual_pnl == Decimal("400.0000")
    assert result.components[AttributionComponent.ENTRY.value] == Decimal("-100.0000")
    assert result.components[AttributionComponent.SELECTION.value] == Decimal("0")
    assert_reconciles(result)


def test_early_exit_is_charged_to_exit_and_flagged() -> None:
    # Bought the short spread back at $0.20 instead of letting it expire. A
    # short position's value is negative, so closing at 0.20 is exit_price=-0.20
    # — it kept $300 of the $500 and gave up the last $200.
    result = attribute_trade(
        _planned(),
        _actual(
            exit_price=Decimal("-0.20"),
            exit_at=PLANNED_EXIT - timedelta(minutes=30),
        ),
    )
    assert result.components[AttributionComponent.EXIT.value] == Decimal("-200.0000")
    assert BehaviorFlag.PREMATURE_EXIT.value in result.flags
    assert_reconciles(result)


def test_undersizing_is_charged_to_sizing() -> None:
    result = attribute_trade(_planned(), _actual(contracts=4))
    assert result.components[AttributionComponent.SIZING.value] == Decimal("-300.0000")
    assert BehaviorFlag.UNDERSIZED.value in result.flags
    assert_reconciles(result)


def test_oversizing_flags_without_being_a_loss() -> None:
    result = attribute_trade(_planned(), _actual(contracts=15))
    assert result.components[AttributionComponent.SIZING.value] == Decimal("250.0000")
    assert BehaviorFlag.OVERSIZED.value in result.flags


def test_size_drift_inside_tolerance_is_not_flagged() -> None:
    result = attribute_trade(_planned(), _actual(contracts=11))
    assert BehaviorFlag.OVERSIZED.value not in result.flags


def test_substitution_with_modelled_fills_separates_selection_from_execution() -> None:
    # A worse structure was taken (model would have made $300 on it, not $500),
    # and it was then filled $0.05 worse than modelled.
    result = attribute_trade(
        _planned(),
        _actual(
            candidate_id="cand-b",
            entry_price=Decimal("-0.25"),
            modeled_entry_price=Decimal("-0.30"),
            modeled_exit_price=Decimal("0"),
        ),
    )
    assert result.components[AttributionComponent.SELECTION.value] == Decimal("-200.0000")
    assert result.components[AttributionComponent.ENTRY.value] == Decimal("-50.0000")
    assert result.notes  # records the substitution
    assert_reconciles(result)


def test_substitution_without_modelled_fills_says_so_instead_of_guessing() -> None:
    result = attribute_trade(
        _planned(), _actual(candidate_id="cand-b", entry_price=Decimal("-0.25"))
    )
    assert result.components[AttributionComponent.SELECTION.value] == Decimal("-250.0000")
    assert result.components[AttributionComponent.ENTRY.value] == Decimal("0")
    assert any("absorbs" in note for note in result.notes)
    assert_reconciles(result)


# --------------------------------------------------------------------------- #
# Participation                                                               #
# --------------------------------------------------------------------------- #
def test_skipping_a_winner_is_a_negative_participation_contribution() -> None:
    result = attribute_trade(_planned(), None)
    assert result.model_pnl == Decimal("500.0000")
    assert result.actual_pnl == Decimal("0")
    assert result.components[AttributionComponent.PARTICIPATION.value] == Decimal(
        "-500.0000"
    )
    assert BehaviorFlag.MISSED_SIGNAL.value in result.flags
    assert_reconciles(result)


def test_skipping_a_loser_is_a_positive_participation_contribution() -> None:
    # The signed decomposition must not score every deviation as damage.
    # Short spread sold at 0.50, marked at 1.00 against: exit_price=-1.00.
    planned = _planned(entry_price=Decimal("-0.50"), exit_price=Decimal("-1.00"))
    result = attribute_trade(planned, None)
    assert result.model_pnl == Decimal("-500.0000")
    assert result.components[AttributionComponent.PARTICIPATION.value] == Decimal(
        "500.0000"
    )
    assert_reconciles(result)


def test_unapproved_trade_is_all_participation() -> None:
    result = attribute_trade(None, _actual(approved=False))
    assert result.model_pnl == Decimal("0")
    assert result.components[AttributionComponent.PARTICIPATION.value] == Decimal(
        "500.0000"
    )
    assert BehaviorFlag.UNAPPROVED_TRADE.value in result.flags
    assert_reconciles(result)


def test_both_sides_absent_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="planned or an actual"):
        attribute_trade(None, None)


# --------------------------------------------------------------------------- #
# Latency                                                                     #
# --------------------------------------------------------------------------- #
def test_late_entry_is_flagged() -> None:
    result = attribute_trade(
        _planned(), _actual(entry_at=DECIDED + timedelta(minutes=5))
    )
    assert BehaviorFlag.LATE_ENTRY.value in result.flags


def test_prompt_entry_is_not_flagged() -> None:
    result = attribute_trade(_planned(), _actual())
    assert BehaviorFlag.LATE_ENTRY.value not in result.flags


def test_holding_past_the_plan_is_flagged() -> None:
    result = attribute_trade(
        _planned(), _actual(exit_at=PLANNED_EXIT + timedelta(minutes=5))
    )
    assert BehaviorFlag.HELD_PAST_PLAN.value in result.flags


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #
def test_assert_reconciles_rejects_a_tampered_decomposition() -> None:
    result = attribute_trade(_planned(), _actual(entry_price=Decimal("-0.40")))
    broken = type(result)(
        session_date=result.session_date,
        snapshot_id=result.snapshot_id,
        planned_candidate_id=result.planned_candidate_id,
        actual_candidate_id=result.actual_candidate_id,
        model_pnl=result.model_pnl,
        actual_pnl=result.actual_pnl,
        gap=result.gap,
        components={**result.components, AttributionComponent.ENTRY.value: Decimal("0")},
        flags=result.flags,
    )
    with pytest.raises(ValueError, match="does not reconcile"):
        assert_reconciles(broken)


# --------------------------------------------------------------------------- #
# Session roll-up                                                             #
# --------------------------------------------------------------------------- #
def test_session_separates_a_good_model_from_bad_execution() -> None:
    report = attribute_session(
        [
            (_planned(), _actual(entry_price=Decimal("-0.40"))),
            (
                _planned(snapshot_id="snap-2"),
                _actual(exit_price=Decimal("-0.10")),
            ),
        ]
    )
    assert report.model_pnl == Decimal("1000.0000")
    assert report.actual_pnl == Decimal("800.0000")
    assert report.gap == Decimal("-200.0000")
    assert report.verdict == "execution_drag"
    assert report.model_win_rate == 1.0
    assert sum(report.components.values()) == report.gap


def test_session_identifies_a_weak_model_executed_faithfully() -> None:
    losing = _planned(entry_price=Decimal("-0.50"), exit_price=Decimal("-1.00"))
    report = attribute_session(
        [(losing, _actual(entry_price=Decimal("-0.50"), exit_price=Decimal("-1.00")))]
    )
    assert report.gap == Decimal("0")
    assert report.verdict == "model_weak"


def test_healthy_session_is_named_healthy() -> None:
    report = attribute_session([(_planned(), _actual())])
    assert report.verdict == "healthy"


def test_empty_session_reports_no_data() -> None:
    report = attribute_session([])
    assert report.verdict == "no_data"
    assert report.n_planned == 0


def test_overtrading_counts_unapproved_entries() -> None:
    cfg = AttributionConfig(max_unapproved_per_session=1)
    report = attribute_session(
        [
            (_planned(), _actual()),
            (None, _actual(candidate_id="cand-x", approved=False)),
            (None, _actual(candidate_id="cand-y", approved=False)),
        ],
        config=cfg,
    )
    assert report.n_unapproved == 2
    assert report.flag_counts[BehaviorFlag.OVERTRADING.value] == 1


def test_revenge_trade_needs_an_unapproved_entry_after_a_loss() -> None:
    loss_exit = DECIDED + timedelta(minutes=10)
    report = attribute_session(
        [
            (
                _planned(),
                _actual(
                    entry_price=Decimal("-0.50"),
                    exit_price=Decimal("-1.00"),
                    exit_at=loss_exit,
                ),
            ),
            (
                None,
                _actual(
                    candidate_id="cand-z",
                    approved=False,
                    entry_at=loss_exit + timedelta(minutes=2),
                ),
            ),
        ]
    )
    assert BehaviorFlag.REVENGE_TRADE.value in report.flag_counts


def test_an_approved_trade_after_a_loss_is_not_revenge() -> None:
    loss_exit = DECIDED + timedelta(minutes=10)
    report = attribute_session(
        [
            (
                _planned(),
                _actual(
                    entry_price=Decimal("-0.50"),
                    exit_price=Decimal("-1.00"),
                    exit_at=loss_exit,
                ),
            ),
            (
                _planned(snapshot_id="snap-2", candidate_id="cand-b"),
                _actual(
                    candidate_id="cand-b",
                    entry_at=loss_exit + timedelta(minutes=2),
                ),
            ),
        ]
    )
    assert BehaviorFlag.REVENGE_TRADE.value not in report.flag_counts


def test_report_serializes_to_json_safe_types() -> None:
    report = attribute_session([(_planned(), _actual(entry_price=Decimal("-0.40")))])
    body = report.to_dict()
    assert body["schema_version"] == "attribution.v1"
    assert isinstance(body["model_pnl"], str)
    trades = body["trades"]
    assert isinstance(trades, list)
    assert isinstance(trades[0]["components"]["entry"], str)
