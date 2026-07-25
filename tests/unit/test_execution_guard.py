"""Deterministic execution guard — the AI must not be the last word."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from spy_der.execution.guard import (
    ExecutionMode,
    GuardBlock,
    GuardContext,
    GuardLimits,
    apply_execution_guard,
    guard_shadow_decision,
)


@dataclass(frozen=True)
class _Cand:
    candidate_id: str
    family: str = "iron_condor"
    maximum_loss: Decimal = Decimal("100")
    capital_required: Decimal = Decimal("100")
    fill_probability: float = 0.9
    hard_vetoed: bool = False


def _ctx(**kw: object) -> GuardContext:
    base: dict[str, object] = {
        "eligible": (_Cand("c1"), _Cand("c2", family="bull_put_credit_spread")),
        "mode": ExecutionMode.SHADOW,
        "now": datetime(2026, 7, 24, 14, 0, tzinfo=UTC),
        "requested_contracts": 1,
    }
    base.update(kw)
    return GuardContext(**base)  # type: ignore[arg-type]


def _guard(**kw: object):
    params: dict[str, object] = {
        "action": "TRADE",
        "candidate_id": "c1",
        "size_scalar": 0.5,
        "context": _ctx(),
    }
    params.update(kw)
    return apply_execution_guard(**params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #
def test_eligible_trade_is_approved() -> None:
    out = _guard()
    assert out.approved is True
    assert out.blocks == ()
    assert out.candidate_id == "c1"
    assert out.size_scalar == pytest.approx(0.5)


def test_non_trade_action_is_approved_with_zero_exposure() -> None:
    for action in ("NO_EDGE", "ABSTAIN", "abstain", ""):
        out = _guard(action=action, candidate_id=None, size_scalar=0.0)
        assert out.approved is True
        assert out.size_scalar == 0.0
        assert out.contracts == 0
        assert out.candidate_id is None


def test_abstain_is_never_converted_into_a_trade() -> None:
    out = _guard(action="NO_EDGE", candidate_id="c1", size_scalar=1.0)
    assert out.candidate_id is None
    assert out.size_scalar == 0.0


# --------------------------------------------------------------------------- #
# Candidate provenance — the AI cannot invent a trade                         #
# --------------------------------------------------------------------------- #
def test_candidate_outside_the_eligible_set_is_blocked() -> None:
    out = _guard(candidate_id="invented")
    assert out.blocked
    assert GuardBlock.CANDIDATE_NOT_ELIGIBLE in out.blocks
    assert out.candidate_id is None
    assert out.size_scalar == 0.0


def test_trade_without_a_candidate_is_blocked() -> None:
    out = _guard(candidate_id=None)
    assert GuardBlock.NO_CANDIDATE_SELECTED in out.blocks


def test_hard_vetoed_candidate_cannot_be_selected() -> None:
    ctx = _ctx(eligible=(_Cand("c1", hard_vetoed=True),))
    out = _guard(context=ctx)
    assert GuardBlock.CANDIDATE_HARD_VETOED in out.blocks


def test_snapshot_level_veto_blocks_everything() -> None:
    out = _guard(context=_ctx(snapshot_vetoes=("data_quality", "catalyst_lockout")))
    assert GuardBlock.SNAPSHOT_HARD_VETO in out.blocks
    assert "catalyst_lockout" in out.details["snapshot_vetoes"]


def test_failed_eligibility_layer_blocks_the_trade() -> None:
    out = _guard(context=_ctx(eligibility_passed=False))
    assert GuardBlock.ELIGIBILITY_NOT_PASSED in out.blocks


# --------------------------------------------------------------------------- #
# Economics are re-derived from the candidate, not read off the decision       #
# --------------------------------------------------------------------------- #
def test_maximum_loss_cap_is_enforced_from_the_candidate() -> None:
    ctx = _ctx(eligible=(_Cand("c1", maximum_loss=Decimal("100000")),))
    out = _guard(context=ctx)
    assert GuardBlock.MAX_LOSS_EXCEEDED in out.blocks
    assert out.details["maximum_loss"] == "100000"


def test_capital_cap_is_enforced() -> None:
    ctx = _ctx(eligible=(_Cand("c1", capital_required=Decimal("99999")),))
    assert GuardBlock.CAPITAL_EXCEEDED in _guard(context=ctx).blocks


def test_insufficient_buying_power_blocks() -> None:
    ctx = _ctx(
        eligible=(_Cand("c1", capital_required=Decimal("400")),),
        buying_power=Decimal("100"),
    )
    assert GuardBlock.INSUFFICIENT_BUYING_POWER in _guard(context=ctx).blocks


def test_low_fill_probability_blocks() -> None:
    ctx = _ctx(eligible=(_Cand("c1", fill_probability=0.01),))
    out = _guard(context=ctx)
    assert GuardBlock.FILL_PROBABILITY_TOO_LOW in out.blocks
    assert out.details["fill_probability"] == "0.0100"


# --------------------------------------------------------------------------- #
# Position limits                                                             #
# --------------------------------------------------------------------------- #
def test_open_position_limit_blocks() -> None:
    out = _guard(context=_ctx(open_positions=3))
    assert GuardBlock.POSITION_LIMIT in out.blocks


def test_family_concentration_blocks() -> None:
    out = _guard(context=_ctx(open_families={"iron_condor": 1}))
    assert GuardBlock.FAMILY_CONCENTRATION in out.blocks
    assert out.details["family"] == "iron_condor"


def test_a_different_family_is_unaffected_by_concentration() -> None:
    out = _guard(candidate_id="c2", context=_ctx(open_families={"iron_condor": 1}))
    assert out.approved is True


# --------------------------------------------------------------------------- #
# Size can only shrink                                                        #
# --------------------------------------------------------------------------- #
def test_oversized_scalar_is_clamped_not_rejected() -> None:
    out = _guard(size_scalar=5.0)
    assert out.approved is True
    assert out.size_scalar == pytest.approx(1.0)
    assert any("size_scalar" in c for c in out.clamps)


def test_guard_never_enlarges_a_conservative_request() -> None:
    out = _guard(size_scalar=0.05)
    assert out.size_scalar == pytest.approx(0.05)
    assert out.clamps == ()


def test_zero_or_negative_size_blocks() -> None:
    assert GuardBlock.SIZE_NOT_POSITIVE in _guard(size_scalar=0.0).blocks
    assert GuardBlock.SIZE_NOT_POSITIVE in _guard(size_scalar=-1.0).blocks


def test_contract_count_is_clamped_to_the_limit() -> None:
    out = _guard(context=_ctx(requested_contracts=500))
    assert out.approved is True
    assert out.contracts == GuardLimits().max_contracts
    assert any("contracts" in c for c in out.clamps)


def test_zero_contracts_blocks() -> None:
    assert GuardBlock.CONTRACTS_EXCEEDED in _guard(context=_ctx(requested_contracts=0)).blocks


# --------------------------------------------------------------------------- #
# Session window and timestamps                                               #
# --------------------------------------------------------------------------- #
def test_trade_before_the_window_opens_is_blocked() -> None:
    out = _guard(context=_ctx(now=datetime(2026, 7, 24, 9, 31, tzinfo=UTC)))
    assert GuardBlock.OUTSIDE_SESSION_WINDOW in out.blocks


def test_trade_after_the_window_closes_is_blocked() -> None:
    out = _guard(context=_ctx(now=datetime(2026, 7, 24, 15, 59, tzinfo=UTC)))
    assert GuardBlock.OUTSIDE_SESSION_WINDOW in out.blocks


def test_missing_timestamp_fails_closed() -> None:
    assert GuardBlock.MISSING_TIMESTAMP in _guard(context=_ctx(now=None)).blocks


def test_naive_timestamp_fails_closed() -> None:
    """A naive timestamp cannot be compared to a session window safely."""
    out = _guard(context=_ctx(now=datetime(2026, 7, 24, 14, 0)))
    assert GuardBlock.NAIVE_TIMESTAMP in out.blocks


def test_custom_session_window_is_respected() -> None:
    limits = GuardLimits(session_open=time(13, 0), session_close=time(23, 0))
    out = _guard(context=_ctx(now=datetime(2026, 7, 24, 22, 0, tzinfo=UTC)), limits=limits)
    assert out.approved is True


# --------------------------------------------------------------------------- #
# Liquidity                                                                   #
# --------------------------------------------------------------------------- #
def test_stale_quotes_block() -> None:
    out = _guard(context=_ctx(quote_age_seconds=60.0))
    assert GuardBlock.QUOTE_STALE in out.blocks
    assert out.details["quote_age_seconds"] == "60.00"


def test_fresh_quotes_pass() -> None:
    assert _guard(context=_ctx(quote_age_seconds=1.0)).approved is True


# --------------------------------------------------------------------------- #
# Live-trading permission fails closed                                        #
# --------------------------------------------------------------------------- #
def test_live_mode_is_blocked_by_default() -> None:
    out = _guard(context=_ctx(mode=ExecutionMode.LIVE))
    assert GuardBlock.LIVE_NOT_PERMITTED in out.blocks


def test_live_mode_requires_explicit_permission() -> None:
    out = _guard(context=_ctx(mode=ExecutionMode.LIVE), limits=GuardLimits(allow_live=True))
    assert out.approved is True
    assert out.mode is ExecutionMode.LIVE


def test_paper_and_shadow_never_need_live_permission() -> None:
    for mode in (ExecutionMode.SHADOW, ExecutionMode.PAPER):
        assert _guard(context=_ctx(mode=mode)).approved is True


def test_killswitch_blocks_every_mode() -> None:
    for mode in ExecutionMode:
        out = _guard(context=_ctx(mode=mode, trading_enabled=False))
        assert GuardBlock.TRADING_DISABLED in out.blocks


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #
def test_multiple_blocks_are_all_reported_once() -> None:
    ctx = _ctx(
        eligible=(_Cand("c1", maximum_loss=Decimal("9999"), fill_probability=0.0),),
        open_positions=99,
        now=None,
        trading_enabled=False,
    )
    out = _guard(context=ctx)
    assert len(out.blocks) == len(set(out.blocks))
    assert GuardBlock.TRADING_DISABLED in out.blocks
    assert GuardBlock.MAX_LOSS_EXCEEDED in out.blocks
    assert GuardBlock.POSITION_LIMIT in out.blocks
    assert GuardBlock.MISSING_TIMESTAMP in out.blocks


def test_decision_serializes_for_the_journal() -> None:
    payload = _guard().to_dict()
    assert payload["approved"] is True
    assert payload["mode"] == "shadow"
    assert payload["blocks"] == []


def test_blocked_decision_carries_no_exposure() -> None:
    out = _guard(candidate_id="nope")
    assert out.blocked
    assert out.candidate_id is None
    assert out.size_scalar == 0.0
    assert out.contracts == 0


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #
def test_shadow_decision_adapter_reads_only_the_three_fields() -> None:
    @dataclass(frozen=True)
    class _Decision:
        action: str = "TRADE"
        candidate_id: str = "c1"
        size_scalar: float = 0.4
        # A decision claiming its own limits must have no effect.
        max_loss_per_trade: Decimal = Decimal("1000000")

    out = guard_shadow_decision(_Decision(), _ctx())
    assert out.approved is True
    assert out.size_scalar == pytest.approx(0.4)

    over = guard_shadow_decision(
        _Decision(candidate_id="invented"),
        _ctx(),
    )
    assert over.blocked


# --------------------------------------------------------------------------- #
# Limit validation                                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_loss_per_trade": Decimal("-1")}, "max_loss_per_trade"),
        ({"max_size_scalar": 1.5}, "max_size_scalar"),
        ({"max_contracts": -1}, "max_contracts"),
        ({"session_open": time(16, 0), "session_close": time(9, 0)}, "session_open"),
    ],
)
def test_guard_limits_validate(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        GuardLimits(**kwargs)  # type: ignore[arg-type]
