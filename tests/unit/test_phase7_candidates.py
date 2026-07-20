"""Phase 7 candidate factory unit tests (spec §7.1, §31, §32)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from spy_der.candidates import (
    APPROVED_FAMILIES,
    REJECTED_FAMILIES,
    FactoryConfig,
    apply_deterministic_dominance,
    generate_candidate_universe,
    is_approved_family,
    prove_bounded_loss,
    terminal_payoff,
)
from spy_der.candidates.registry import canonical_family_name
from spy_der.contracts import (
    Candidate,
    CandidateFamily,
    CandidateLeg,
    CanonicalMarketSnapshot,
    DebitCredit,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
    geometry_hash,
    make_candidate_id,
)


def _quote(
    *,
    strike: str,
    option_type: OptionType,
    bid: str,
    ask: str,
    expiration: date = date(2026, 1, 5),
) -> OptionQuote:
    right = "C" if option_type is OptionType.CALL else "P"
    sid = f"SPY{expiration:%y%m%d}{right}{int(Decimal(strike) * 1000):08d}"
    return OptionQuote(
        contract=OptionContract(
            contract_id=sid,
            underlying_symbol="SPY",
            expiration=expiration,
            option_type=option_type,
            strike=Decimal(strike),
        ),
        received_at=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        source="test",
        bid=Decimal(bid),
        ask=Decimal(ask),
        mark=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
    )


def _snapshot(strikes: list[str] | None = None) -> CanonicalMarketSnapshot:
    ks = strikes or [str(x) for x in range(95, 106)]
    chain: list[OptionQuote] = []
    for k in ks:
        # Rough smile: OTM cheaper.
        dist = abs(Decimal(k) - Decimal("100"))
        mid = max(Decimal("0.10"), Decimal("5") - dist * Decimal("0.4"))
        half = Decimal("0.05")
        bid, ask = str(mid - half), str(mid + half)
        chain.append(_quote(strike=k, option_type=OptionType.CALL, bid=bid, ask=ask))
        chain.append(_quote(strike=k, option_type=OptionType.PUT, bid=bid, ask=ask))
    return CanonicalMarketSnapshot(
        snapshot_id="snap-phase7",
        content_hash="sha256:test",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        option_chain=tuple(chain),
        minutes_to_close=120,
    )


def test_approved_registry_excludes_naked_and_csp() -> None:
    assert "long_call" in APPROVED_FAMILIES
    assert "cash_secured_put" in REJECTED_FAMILIES
    assert not is_approved_family("naked_defended_call")
    assert canonical_family_name("put_credit") == CandidateFamily.BULL_PUT_CREDIT_SPREAD.value


def test_max_loss_proof_rejects_naked_short_call() -> None:
    legs = (
        CandidateLeg(
            option_type=OptionType.CALL,
            strike=Decimal("100"),
            quantity=-1,
            expiration=date(2026, 1, 5),
            contract_id="short",
        ),
    )
    proof = prove_bounded_loss(legs, entry_credit=Decimal("1.50"))
    assert proof.unbounded
    assert "unbounded_short_call_tail" in proof.reasons


def test_credit_spread_max_loss_is_width_minus_credit() -> None:
    exp = date(2026, 1, 5)
    legs = (
        CandidateLeg(
            option_type=OptionType.PUT,
            strike=Decimal("99"),
            quantity=-1,
            expiration=exp,
            contract_id="short",
        ),
        CandidateLeg(
            option_type=OptionType.PUT,
            strike=Decimal("97"),
            quantity=1,
            expiration=exp,
            contract_id="long",
        ),
    )
    credit = Decimal("0.80")
    proof = prove_bounded_loss(legs, entry_credit=credit)
    assert not proof.unbounded
    # Width 2, credit 0.80 → max loss 1.20
    assert proof.maximum_loss == Decimal("1.20")
    pnl_low = terminal_payoff(legs, entry_credit=credit, spot=Decimal("0"))
    assert pnl_low == Decimal("-1.20")


def test_stable_ids_are_deterministic() -> None:
    legs = (
        CandidateLeg(
            option_type=OptionType.CALL,
            strike=Decimal("100"),
            quantity=1,
            expiration=date(2026, 1, 5),
            contract_id="c1",
        ),
    )
    exp = legs[0].expiration
    g1 = geometry_hash(family="long_call", expiration=exp, legs=legs)
    g2 = geometry_hash(family="long_call", expiration=exp, legs=tuple(reversed(legs)))
    assert g1 == g2
    id1 = make_candidate_id(snapshot_id="snap-a", factory_version="v", geometry=g1)
    id2 = make_candidate_id(snapshot_id="snap-a", factory_version="v", geometry=g1)
    assert id1 == id2
    assert id1.startswith("cand-")


def test_factory_generates_bounded_approved_candidates() -> None:
    universe = generate_candidate_universe(
        _snapshot(),
        cfg=FactoryConfig(
            families=frozenset(
                {
                    CandidateFamily.BULL_PUT_CREDIT_SPREAD.value,
                    CandidateFamily.CALL_DEBIT_SPREAD.value,
                    CandidateFamily.LONG_CALL.value,
                    CandidateFamily.IRON_CONDOR.value,
                }
            ),
            spread_widths=(Decimal("1"), Decimal("2")),
        ),
    )
    assert universe.universe_id
    assert universe.candidates
    families = {c.family for c in universe.candidates}
    assert families <= APPROVED_FAMILIES
    for cand in universe.candidates:
        assert cand.maximum_loss >= 0
        assert cand.geometry_hash.startswith("sha256:")
        assert cand.candidate_id.startswith("cand-")
        assert cand.max_loss == cand.maximum_loss


def test_dominance_removes_duplicate_geometry() -> None:
    exp = date(2026, 1, 5)
    legs = (
        CandidateLeg(
            option_type=OptionType.CALL,
            strike=Decimal("100"),
            quantity=1,
            expiration=exp,
            contract_id="c1",
        ),
    )
    ghash = geometry_hash(family="long_call", expiration=exp, legs=legs)

    def _cand(cid: str, quality: str) -> Candidate:
        return Candidate(
            candidate_id=cid,
            snapshot_id="snap",
            family="long_call",
            direction="bullish",
            expiration=exp,
            legs=legs,
            entry_type=DebitCredit.DEBIT,
            maximum_profit=None,
            maximum_loss=Decimal("2"),
            breakevens=(),
            capital_required=Decimal("2"),
            terminal_payoff_hash="sha256:pay",
            geometry_hash=ghash,
            entry_credit=Decimal("-2"),
            quote_quality=Decimal(quality),
        )

    kept = apply_deterministic_dominance([_cand("cand-a", "0.5"), _cand("cand-b", "0.9")])
    assert len(kept) == 1
    assert kept[0].candidate_id == "cand-b"


def test_factory_is_deterministic() -> None:
    snap = _snapshot()
    u1 = generate_candidate_universe(snap)
    u2 = generate_candidate_universe(snap)
    assert [c.candidate_id for c in u1.candidates] == [c.candidate_id for c in u2.candidates]
    assert u1.universe_id == u2.universe_id


def test_stock_dependent_candidate_rejected() -> None:
    exp = date(2026, 1, 5)
    with pytest.raises(ValueError, match="stock-dependent"):
        Candidate(
            candidate_id="c1",
            snapshot_id="s",
            family="long_call",
            direction="bullish",
            expiration=exp,
            legs=(
                CandidateLeg(
                    option_type=OptionType.CALL,
                    strike=Decimal("100"),
                    quantity=1,
                    expiration=exp,
                ),
            ),
            entry_type=DebitCredit.DEBIT,
            maximum_profit=None,
            maximum_loss=Decimal("1"),
            breakevens=(),
            capital_required=Decimal("1"),
            terminal_payoff_hash="sha256:x",
            geometry_hash="sha256:y",
            requires_stock_ownership=True,
        )
