from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from spy_der.contracts.legacy import DirectionPreference, VetoCategory, VetoCode
from spy_der.contracts.market import (
    Bar,
    CanonicalMarketSnapshot,
    CatalystState,
    FeedComponent,
    OptionContract,
    OptionQuote,
    OptionType,
)
from spy_der.contracts.structure import GexLevels, StructuralState
from spy_der.legacy import LegacyAnalyzer, evaluate_operational_vetoes
from spy_der.market_data import CanonicalSnapshotAssembler, build_observation

ET = ZoneInfo("America/New_York")
MIDDAY = datetime(2026, 1, 5, 14, 30, tzinfo=ET)


def _chain() -> tuple[OptionQuote, ...]:
    quotes: list[OptionQuote] = []
    for strike in (495, 500, 505, 510, 515):
        for kind in (OptionType.CALL, OptionType.PUT):
            contract = OptionContract(
                contract_id=f"SPY-{strike}-{kind.value}",
                underlying_symbol="SPY",
                expiration=date(2026, 1, 5),
                option_type=kind,
                strike=Decimal(strike),
            )
            quotes.append(OptionQuote(contract=contract, received_at=MIDDAY, source="t"))
    return tuple(quotes)


def _snapshot(
    ts: datetime = MIDDAY,
    *,
    chain: tuple[OptionQuote, ...] | None = None,
    catalyst: bool = False,
) -> CanonicalMarketSnapshot:
    chain = _chain() if chain is None else chain
    obs = tuple(
        build_observation(c, "tradier", ts, 60.0, observed_at=ts)
        for c in (
            FeedComponent.SPOT,
            FeedComponent.BARS,
            FeedComponent.OPTION_CHAIN,
            FeedComponent.SETTLEMENT,
        )
    )
    bar = Bar(ts, Decimal("500"), Decimal("501"), Decimal("499"), Decimal("500"), 1000)
    return CanonicalSnapshotAssembler().assemble(
        timestamp=ts,
        underlying_symbol="SPY",
        underlying_price=Decimal("505"),
        bars_1m=(bar,),
        option_chain=chain,
        feed_observations=obs,
        catalyst_state=CatalystState(lockout_active=catalyst, reason="FOMC" if catalyst else None),
    )


def _state(snapshot: CanonicalMarketSnapshot, net_gex_bn: float) -> StructuralState:
    gex = GexLevels(
        net_gex_bn=net_gex_bn,
        net_ratio=0.6 if net_gex_bn >= 0 else -0.6,
        gamma_flip=Decimal("500"),
        call_wall=Decimal("510"),
        put_wall=Decimal("490"),
        gex_concentration=0.4,
        wall_concentration=0.3,
        n_contracts=10,
        n_strikes=5,
    )
    return StructuralState(
        structural_state_id="struct-test",
        snapshot_id=snapshot.snapshot_id,
        gex_oi=gex,
    )


# ---------------------------------------------------------- permissions ----
def test_no_operational_vetoes_when_healthy() -> None:
    assert evaluate_operational_vetoes(_snapshot()) == ()


def test_catalyst_lockout_veto() -> None:
    vetoes = evaluate_operational_vetoes(_snapshot(catalyst=True))
    codes = {v.code for v in vetoes}
    assert VetoCode.CATALYST_LOCKOUT in codes


def test_entry_cutoff_veto_near_close() -> None:
    late = datetime(2026, 1, 5, 15, 50, tzinfo=ET)  # 10m to 16:00 close
    vetoes = evaluate_operational_vetoes(_snapshot(late))
    assert VetoCode.ENTRY_CUTOFF in {v.code for v in vetoes}


def test_session_closed_veto() -> None:
    closed = datetime(2026, 1, 5, 17, 0, tzinfo=ET)
    vetoes = evaluate_operational_vetoes(_snapshot(closed))
    assert VetoCode.SESSION_CLOSED in {v.code for v in vetoes}


def test_missing_chain_veto() -> None:
    vetoes = evaluate_operational_vetoes(_snapshot(chain=()))
    assert VetoCode.MISSING_CHAIN in {v.code for v in vetoes}


# -------------------------------------------------------------- analyzer ----
def test_long_gamma_is_premium_pin() -> None:
    snap = _snapshot()
    view = LegacyAnalyzer().analyze(snap, _state(snap, net_gex_bn=0.5))
    assert view.regime_label == "long_gamma_pin"
    assert view.preferred_direction is DirectionPreference.NEUTRAL
    assert "iron_condor" in view.permitted_families
    assert view.is_tradeable
    assert 0.0 <= view.structural_confidence <= 1.0


def test_short_gamma_vetoes_premium_and_biases_direction() -> None:
    snap = _snapshot()  # spot 505 > flip 500 -> call biased
    view = LegacyAnalyzer().analyze(snap, _state(snap, net_gex_bn=-0.5))
    assert view.regime_label == "short_gamma_trend"
    assert view.preferred_direction is DirectionPreference.CALL_BIASED
    assert "long_call" in view.permitted_families
    assert "bull_put_credit_spread" in view.prohibited_families
    structural = [v for v in view.hard_vetoes if v.category is VetoCategory.STRUCTURAL]
    assert any(v.code is VetoCode.SHORT_GAMMA_REGIME for v in structural)


def test_flip_transition_neutral_debits_only() -> None:
    snap = _snapshot()
    view = LegacyAnalyzer().analyze(snap, _state(snap, net_gex_bn=0.0))
    assert view.regime_label == "flip_transition"
    assert view.preferred_direction is DirectionPreference.NEUTRAL
    assert set(view.permitted_families) & {"call_debit_spread", "put_debit_spread"}


def test_operational_veto_zeroes_size_cap() -> None:
    snap = _snapshot(catalyst=True)
    view = LegacyAnalyzer().analyze(snap, _state(snap, net_gex_bn=0.5))
    assert not view.is_tradeable
    assert view.size_cap == 0.0
    assert view.operational_vetoes


def test_view_id_deterministic() -> None:
    snap = _snapshot()
    a = LegacyAnalyzer().analyze(snap, _state(snap, 0.5))
    b = LegacyAnalyzer().analyze(snap, _state(snap, 0.5))
    assert a.view_id == b.view_id
