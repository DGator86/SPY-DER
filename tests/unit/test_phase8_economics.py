"""Phase 8 economics, fill, value, ranking, meta tests (spec §33-§35)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from spy_der.candidate_value import (
    CandidateValueModel,
    MetaThresholdConfig,
    apply_hard_vetoes,
    build_feature_row,
    candidate_utility,
    decide_meta_action,
    rank_snapshot,
    ranking_regret,
)
from spy_der.candidates import FactoryConfig, generate_candidate_universe
from spy_der.contracts import (
    CandidateFamily,
    CandidateValueForecast,
    CanonicalMarketSnapshot,
    FillRecord,
    MetaAction,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
)
from spy_der.economics import (
    EconomicsConfig,
    FillConcessionModel,
    FillProbabilityModel,
    calculate_candidate_economics,
    calculate_universe_economics,
    expected_order_value,
    fill_fraction_for,
)
from spy_der.execution import enrich_fill_fractions, fill_fraction, validate_fill_record


def _quote(strike: str, option_type: OptionType, bid: str, ask: str) -> OptionQuote:
    exp = date(2026, 1, 5)
    right = "C" if option_type is OptionType.CALL else "P"
    sid = f"SPY{exp:%y%m%d}{right}{int(Decimal(strike) * 1000):08d}"
    return OptionQuote(
        contract=OptionContract(
            contract_id=sid,
            underlying_symbol="SPY",
            expiration=exp,
            option_type=option_type,
            strike=Decimal(strike),
        ),
        received_at=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        source="test",
        bid=Decimal(bid),
        ask=Decimal(ask),
        mark=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        age_seconds=1.0,
    )


def _snapshot() -> CanonicalMarketSnapshot:
    chain: list[OptionQuote] = []
    for k in [str(x) for x in range(97, 104)]:
        dist = abs(Decimal(k) - Decimal("100"))
        mid = max(Decimal("0.15"), Decimal("4") - dist * Decimal("0.5"))
        half = Decimal("0.05")
        bid, ask = str(mid - half), str(mid + half)
        chain.append(_quote(k, OptionType.CALL, bid, ask))
        chain.append(_quote(k, OptionType.PUT, bid, ask))
    return CanonicalMarketSnapshot(
        snapshot_id="snap-phase8",
        content_hash="sha256:phase8",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        option_chain=tuple(chain),
        minutes_to_close=90,
    )


def _universe():
    return generate_candidate_universe(
        _snapshot(),
        cfg=FactoryConfig(
            families=frozenset(
                {
                    CandidateFamily.BULL_PUT_CREDIT_SPREAD.value,
                    CandidateFamily.LONG_CALL.value,
                    CandidateFamily.CALL_DEBIT_SPREAD.value,
                }
            ),
            spread_widths=(Decimal("1"), Decimal("2")),
        ),
    )


def _fill_record(**overrides: object) -> FillRecord:
    base = dict(
        fill_record_id="fr1",
        snapshot_id="snap-phase8",
        candidate_id="cand-1",
        session_date="2026-01-05",
        decision_ts="2026-01-05T15:00:00Z",
        submitted_ts="2026-01-05T15:00:01Z",
        resolved_ts="2026-01-05T15:00:10Z",
        symbol="SPY",
        family="bull_put_credit_spread",
        side="credit",
        n_legs=2,
        limit_credit=Decimal("0.40"),
        mid_credit_at_submit=Decimal("0.50"),
        natural_credit_at_submit=Decimal("0.30"),
        relative_spread=0.10,
        absolute_spread=0.20,
        option_price_scale=1.0,
        quote_age_seconds=1.0,
        minutes_to_close=90.0,
        filled=True,
        fill_credit=Decimal("0.40"),
        requested_quantity=1,
        filled_quantity=1,
        seconds_to_first_fill=5.0,
        seconds_to_complete_fill=5.0,
        source="paper",
        mode="shadow",
    )
    base.update(overrides)
    return FillRecord(**base)  # type: ignore[arg-type]


def test_fill_fraction_and_provenance() -> None:
    raw, clipped = fill_fraction(0.50, 0.30, 0.40)
    assert 0.0 <= clipped <= 1.0
    assert raw == pytest.approx(0.5)
    rec = enrich_fill_fractions(_fill_record())
    assert rec.fill_fraction == pytest.approx(0.5)
    with pytest.raises(ValueError, match="broker_actual"):
        validate_fill_record(
            _fill_record(source="broker_actual", diagnostics=(("simulated", "true"),))
        )
    with pytest.raises(ValueError, match="filled=False"):
        validate_fill_record(_fill_record(source="advisory", filled=True))


def test_fill_prior_penalties_only_worsen() -> None:
    base, _ = fill_fraction_for("bull_put_credit_spread", n_legs=2)
    late, diag = fill_fraction_for(
        "bull_put_credit_spread",
        n_legs=2,
        minutes_to_close=30.0,
        quote_age_seconds=10.0,
    )
    assert late >= base
    assert "late_day" in diag["penalties"]  # type: ignore[operator]


def test_economics_monotonicity_and_fees() -> None:
    snap = _snapshot()
    universe = _universe()
    assert universe.candidates
    eco = calculate_candidate_economics(universe.candidates[0], snap)
    assert eco.mid_price is not None
    assert eco.natural_price is not None
    assert eco.expected_fill_price is not None
    assert eco.conservative_fill_price is not None
    assert eco.expected_fill_price <= eco.mid_price
    assert eco.conservative_fill_price <= eco.expected_fill_price
    assert eco.fees >= 0
    assert eco.entry_slippage >= 0
    assert eco.exit_slippage >= 0
    panel = calculate_universe_economics(universe, snap, cfg=EconomicsConfig())
    assert len(panel) == len(universe.candidates)


def test_expected_order_value() -> None:
    assert expected_order_value(0.5, 2.0) == pytest.approx(1.0)


def test_fill_models_fit_predict() -> None:
    records = [
        _fill_record(fill_record_id=f"fr{i}", filled=i % 2 == 0, fill_credit=Decimal("0.40"))
        for i in range(12)
    ]
    # Ensure some unfilled without fill_credit
    records[1] = _fill_record(
        fill_record_id="fr1",
        filled=False,
        fill_credit=None,
        seconds_to_first_fill=None,
        seconds_to_complete_fill=None,
    )
    stage1 = FillProbabilityModel().fit(records)
    pred = stage1.predict(
        {
            "n_legs": 2.0,
            "relative_spread": 0.1,
            "absolute_spread": 0.2,
            "quote_age_seconds": 1.0,
            "minutes_to_close": 90.0,
            "option_price_scale": 1.0,
            "realized_volatility": float("nan"),
            "data_quality": 0.9,
        },
        family="bull_put_credit_spread",
    )
    assert pred.p_fill_15s <= pred.p_fill_30s <= pred.p_fill_60s <= pred.p_fill_before_cancel + 1e-9

    filled = [r for r in records if r.filled]
    stage2 = FillConcessionModel().fit(filled)
    cpred = stage2.predict(
        {
            "n_legs": 2.0,
            "relative_spread": 0.1,
            "absolute_spread": 0.2,
            "quote_age_seconds": 1.0,
            "minutes_to_close": 90.0,
            "option_price_scale": 1.0,
        },
        family="bull_put_credit_spread",
        n_legs=2,
    )
    assert 0.0 <= cpred.expected_fill_fraction <= 1.0
    assert cpred.fill_q10 <= cpred.fill_q50 <= cpred.fill_q90


def test_candidate_value_ranking_meta() -> None:
    snap = _snapshot()
    universe = _universe()
    ecos = {
        c.candidate_id: calculate_candidate_economics(c, snap) for c in universe.candidates
    }
    rows = [build_feature_row(c, ecos[c.candidate_id]) for c in universe.candidates]
    y_pnl = [0.2, -0.1, 0.05, 0.15, -0.05, 0.1, 0.0, 0.08][: len(rows)]
    y_profit = [1 if v > 0 else 0 for v in y_pnl]
    # Pad if needed
    while len(y_pnl) < len(rows):
        y_pnl.append(0.0)
        y_profit.append(0)
    model = CandidateValueModel().fit(rows, y_pnl, y_profit)
    forecasts = {
        c.candidate_id: model.predict_one(
            build_feature_row(c, ecos[c.candidate_id]),
            candidate=c,
            economics=ecos[c.candidate_id],
        )
        for c in universe.candidates
    }
    ranking = rank_snapshot(
        snapshot_id=snap.snapshot_id,
        candidates=universe.candidates,
        forecasts=forecasts,
    )
    assert ranking.top_candidate_id is not None
    assert ranking.ordered_candidate_ids
    util_map = {cid: float(forecasts[cid].utility or 0.0) for cid in forecasts}
    assert ranking_regret(ranking.top_candidate_id, util_map) == pytest.approx(0.0)

    top = forecasts[ranking.top_candidate_id]
    decision = decide_meta_action(
        p_positive_utility=float(top.p_positive_utility or 0.0),
        expected_order_value=float(top.expected_net_pnl or 0.0),
        selected_candidate_id=ranking.top_candidate_id,
        selected_candidate_utility=top.utility,
        composite_uncertainty=0.2,
        ood_score=0.1,
        data_quality=0.9,
        cfg=MetaThresholdConfig(minimum_trade_probability=0.0, minimum_expected_order_value=-1.0),
    )
    vetoed = apply_hard_vetoes(decision, ("HALT",))
    assert vetoed.action is MetaAction.HARD_VETO
    assert vetoed.selected_candidate_id is None


def test_utility_penalizes_shortfall() -> None:
    base = CandidateValueForecast(
        candidate_id="c",
        expected_net_pnl=Decimal("1.0"),
        expected_shortfall=Decimal("0.0"),
        model_uncertainty=0.0,
        forecast_uncertainty=0.0,
        execution_uncertainty=0.0,
        ood_score=0.0,
        capital_required=Decimal("1"),
    )
    risky = CandidateValueForecast(
        candidate_id="c",
        expected_net_pnl=Decimal("1.0"),
        expected_shortfall=Decimal("2.0"),
        model_uncertainty=0.0,
        forecast_uncertainty=0.0,
        execution_uncertainty=0.0,
        ood_score=0.0,
        capital_required=Decimal("1"),
    )
    assert candidate_utility(risky) < candidate_utility(base)
