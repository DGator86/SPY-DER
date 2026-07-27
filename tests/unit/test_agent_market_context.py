"""The market and forecast context the entry agent decides against.

Before this, the packet carried candidate summaries and nothing else: a list of
geometries with a utility score, no dealer positioning, no volatility surface,
no trend structure, no walls. An agent given only that can re-rank what it was
handed but cannot disagree with the ranking on evidence — which is the only
thing it adds over the deterministic ordering it already received.

The invariant these pin: absence is the signal. An unknown field is omitted from
the prompt rather than sent as null or zero, because the system prompt tells the
model that a missing key means "not observed".
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from spy_der.agents.market_context import (
    CORE_TECHNICALS,
    build_forecast_context,
    build_market_context,
)
from spy_der.agents.packet import build_agent_decision_packet
from spy_der.agents.prompts import ENTRY_PROMPT_VERSION, build_entry_prompt
from spy_der.contracts.candidates import (
    Candidate,
    CandidateLeg,
    CandidateUniverse,
    DebitCredit,
)
from spy_der.contracts.forecasts import MarketForecastBundle
from spy_der.contracts.market import (
    Bar,
    BreadthState,
    CanonicalMarketSnapshot,
    OptionContract,
    OptionQuote,
    OptionType,
    VolatilityTermStructure,
)
from spy_der.features.pipeline import SnapshotFeaturePipeline
from spy_der.features.resample import ET
from spy_der.market_data.assembler import CanonicalSnapshotAssembler

OPEN = datetime(2026, 1, 5, 9, 30, tzinfo=ET)
NOW = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
CREATED = datetime(2026, 1, 5, 19, 30, tzinfo=UTC)
EXPIRY = date(2026, 1, 5)


def _bars(n: int = 300) -> tuple[Bar, ...]:
    out: list[Bar] = []
    for i in range(n):
        close = Decimal(str(500.0 + i * 0.02))
        out.append(
            Bar(
                timestamp=OPEN + timedelta(minutes=i),
                open=close,
                high=close + Decimal("0.25"),
                low=close - Decimal("0.25"),
                close=close,
                volume=1000 + i,
            )
        )
    return tuple(out)


def _chain() -> tuple[OptionQuote, ...]:
    quotes: list[OptionQuote] = []
    for strike in range(490, 511, 2):
        for side in (OptionType.CALL, OptionType.PUT):
            intrinsic = max(500 - strike, 0) if side is OptionType.CALL else max(strike - 500, 0)
            mid = Decimal(str(intrinsic + 1.0))
            quotes.append(
                OptionQuote(
                    contract=OptionContract(
                        contract_id=f"SPY-{strike}-{side.value}",
                        underlying_symbol="SPY",
                        expiration=EXPIRY,
                        option_type=side,
                        strike=Decimal(str(strike)),
                    ),
                    received_at=NOW,
                    source="test",
                    bid=mid - Decimal("0.05"),
                    ask=mid + Decimal("0.05"),
                    volume=100 if side is OptionType.CALL else 150,
                    open_interest=1000,
                    gamma=0.02,
                    delta=0.5 if side is OptionType.CALL else -0.5,
                )
            )
    return tuple(quotes)


def _snapshot(**kw: Any) -> CanonicalMarketSnapshot:
    params: dict[str, Any] = {
        "timestamp": NOW,
        "underlying_symbol": "SPY",
        "underlying_price": Decimal("500"),
        "underlying_bid": Decimal("499.98"),
        "underlying_ask": Decimal("500.02"),
        "bars_1m": _bars(),
        "option_chain": _chain(),
        "volatility_term_structure": VolatilityTermStructure(
            vix=15.0, vix9d=13.0, vix3m=17.0, vvix=90.0
        ),
        "breadth": BreadthState(rsp_spy_div=-0.004, sector_align=0.6, top10_pressure=0.01),
    }
    params.update(kw)
    return CanonicalSnapshotAssembler().assemble(**params)


def _universe(snapshot: CanonicalMarketSnapshot) -> CandidateUniverse:
    candidate = Candidate(
        candidate_id="c1",
        snapshot_id=snapshot.snapshot_id,
        family="long_call",
        direction="bullish",
        expiration=EXPIRY,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("500"),
                quantity=1,
                expiration=EXPIRY,
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=Decimal("400"),
        maximum_loss=Decimal("100"),
        breakevens=(Decimal("501"),),
        capital_required=Decimal("100"),
        terminal_payoff_hash="payoff-c1",
        geometry_hash="geom-c1",
    )
    return CandidateUniverse(snapshot_id=snapshot.snapshot_id, candidates=(candidate,))


def _packet(snapshot: CanonicalMarketSnapshot, **kw: Any) -> Any:
    return build_agent_decision_packet(
        snapshot=snapshot,
        universe=_universe(snapshot),
        created_at=CREATED,
        **kw,
    )


def _prompt_body(packet: Any) -> dict[str, Any]:
    return json.loads(build_entry_prompt(packet)["user"])


# --------------------------------------------------------------------------- #
# Building the context                                                        #
# --------------------------------------------------------------------------- #
def test_the_context_carries_the_measured_market_state() -> None:
    snapshot = _snapshot()
    features = SnapshotFeaturePipeline().build(snapshot)
    context = build_market_context(snapshot, features=features)

    assert context.underlying_price == Decimal("500")
    assert context.underlying_bid == Decimal("499.98")
    assert context.net_gex_bn is not None
    assert context.gamma_sign in (-1, 0, 1)
    assert context.call_wall is not None and context.put_wall is not None
    assert context.atm_straddle is not None
    assert context.expected_move is not None
    assert context.vix == 15.0
    assert context.vix_contango is not None
    assert context.pcr_volume == pytest.approx(1.5)
    assert context.sector_align == 0.6
    assert context.technicals


def test_wall_and_flip_distances_are_relative_to_spot() -> None:
    """A 2-point cushion means different things at 400 and at 700."""
    context = build_market_context(
        _snapshot(), features=SnapshotFeaturePipeline().build(_snapshot())
    )
    assert context.flip_cushion is not None and abs(context.flip_cushion) < 1.0
    assert context.call_wall_distance is not None
    assert context.put_wall_distance is not None


def test_technicals_are_the_core_subset_by_default() -> None:
    """The full matrix is a couple hundred numbers on a throttled per-tick call."""
    snapshot = _snapshot()
    context = build_market_context(
        snapshot, features=SnapshotFeaturePipeline().build(snapshot)
    )
    names = {key.split(".", 1)[1] for key, _ in context.technicals}
    assert names <= CORE_TECHNICALS
    assert "rsi" in names and "adx" in names


def test_the_full_matrix_can_be_requested() -> None:
    snapshot = _snapshot()
    features = SnapshotFeaturePipeline().build(snapshot)
    full = build_market_context(snapshot, features=features, technical_fields=None)
    core = build_market_context(snapshot, features=features)
    assert len(full.technicals) > len(core.technicals)


def test_technicals_exclude_the_non_timeframe_families() -> None:
    """`gex.net_bn` is snapshot state, not a per-timeframe indicator."""
    snapshot = _snapshot()
    context = build_market_context(
        snapshot, features=SnapshotFeaturePipeline().build(snapshot), technical_fields=None
    )
    prefixes = {key.split(".", 1)[0] for key, _ in context.technicals}
    assert not prefixes & {"gex", "vol", "rnd", "flow", "breadth", "vix", "session"}


def test_a_context_without_features_still_carries_the_underlying() -> None:
    """Strictly better than the nothing the packet carried before."""
    context = build_market_context(_snapshot())
    assert context.underlying_price == Decimal("500")
    assert context.minutes_to_close is not None
    assert context.net_gex_bn is None
    assert context.is_populated


def test_unknown_state_is_none_not_zero() -> None:
    bare = _snapshot(
        bars_1m=(), option_chain=(), volatility_term_structure=None, breadth=None
    )
    context = build_market_context(
        bare, features=SnapshotFeaturePipeline().build(bare)
    )
    assert context.net_gex_bn is None
    assert context.vix is None
    assert context.sector_align is None
    assert context.technicals == ()


def test_data_quality_flags_travel_with_the_context() -> None:
    """The agent is told how spot was obtained and which feeds were degraded."""
    context = build_market_context(_snapshot())
    assert isinstance(context.data_quality_flags, tuple)


# --------------------------------------------------------------------------- #
# Forecast context                                                            #
# --------------------------------------------------------------------------- #
def test_forecast_context_flattens_the_bundle() -> None:
    bundle = MarketForecastBundle(
        snapshot_id="snap-1",
        ts=NOW.isoformat(),
        session_date="2026-01-05",
        forecast_id="fc-1",
        model_group_id="mg-1",
        p_up_30m=0.58,
        expected_return_30m=0.0012,
        return_q10_30m=-0.003,
        return_q90_30m=0.005,
    )
    context = build_forecast_context(bundle)
    assert context is not None
    horizons = dict(context.horizons)
    assert horizons["p_up_30m"] == 0.58
    assert horizons["return_q90_30m"] == 0.005
    assert "p_up_5m" not in horizons  # unset stays absent
    assert context.forecast_id == "fc-1"


def test_no_forecast_is_none_not_an_empty_context() -> None:
    """The agent must tell 'no forecast' from 'a forecast with nothing in it'."""
    assert build_forecast_context(None) is None


def test_an_all_empty_forecast_is_also_none() -> None:
    empty = MarketForecastBundle(
        snapshot_id="snap-1", ts=NOW.isoformat(), session_date="2026-01-05"
    )
    assert build_forecast_context(empty) is None


# --------------------------------------------------------------------------- #
# Reaching the prompt                                                         #
# --------------------------------------------------------------------------- #
def test_the_prompt_carries_the_market_context() -> None:
    """The actual gap: the agent could not see the market it was trading."""
    snapshot = _snapshot()
    packet = _packet(snapshot, features=SnapshotFeaturePipeline().build(snapshot))
    body = _prompt_body(packet)

    context = body["market_context"]
    assert context["underlying_price"] == "500"
    assert "net_gex_bn" in context
    assert "call_wall" in context and "put_wall" in context
    assert "expected_move" in context
    assert "vix" in context
    assert "pcr_volume" in context
    assert "sector_align" in context
    assert context["technicals"]


def test_the_prompt_carries_the_forecast_when_one_ran() -> None:
    snapshot = _snapshot()
    forecast = MarketForecastBundle(
        snapshot_id=snapshot.snapshot_id,
        ts=NOW.isoformat(),
        session_date="2026-01-05",
        forecast_id="fc-1",
        p_up_30m=0.61,
    )
    packet = _packet(snapshot, forecast=forecast)
    body = _prompt_body(packet)
    assert body["forecast_context"]["horizons"]["p_up_30m"] == 0.61


def test_the_prompt_omits_the_forecast_when_none_ran() -> None:
    """Absent must not read as neutral — the forecast stage is fail-closed."""
    body = _prompt_body(_packet(_snapshot()))
    assert "forecast_context" not in body


def test_unknown_market_fields_are_omitted_rather_than_nulled() -> None:
    """A null is a value to reason about; absence is the documented signal."""
    bare = _snapshot(
        bars_1m=(), option_chain=(), volatility_term_structure=None, breadth=None
    )
    body = _prompt_body(_packet(bare))
    context = body["market_context"]
    assert "net_gex_bn" not in context
    assert "vix" not in context
    assert "technicals" not in context
    assert not any(v is None for v in context.values())


def test_the_prompt_explains_how_to_read_the_context() -> None:
    snapshot = _snapshot()
    system = build_entry_prompt(
        _packet(snapshot, features=SnapshotFeaturePipeline().build(snapshot))
    )["system"]
    assert "market_context" in system
    assert "forecast_context" in system
    assert "OMITTED, never zero" in system


def test_candidate_views_now_carry_their_geometry() -> None:
    """Judging fit against the walls needs the strikes, not just a utility score."""
    snapshot = _snapshot()
    candidate = _prompt_body(_packet(snapshot))["candidates"][0]
    assert candidate["legs"]
    assert candidate["legs"][0]["strike"] == "500"
    assert "expiration" in candidate


def test_the_prompt_version_records_the_change() -> None:
    """A prompt whose content changed must not be attributed to the old version."""
    assert ENTRY_PROMPT_VERSION == "spy-der-entry-prompt.v3"


# --------------------------------------------------------------------------- #
# Packet identity and safety                                                  #
# --------------------------------------------------------------------------- #
def test_the_context_participates_in_the_packet_hash() -> None:
    """Same candidates, different market state, is a different decision."""
    snapshot = _snapshot()
    other = _snapshot(underlying_price=Decimal("505"))
    a = _packet(snapshot, features=SnapshotFeaturePipeline().build(snapshot))
    b = _packet(other, features=SnapshotFeaturePipeline().build(other))
    assert a.packet_hash != b.packet_hash


def test_packet_identity_is_still_reproducible() -> None:
    snapshot = _snapshot()
    features = SnapshotFeaturePipeline().build(snapshot)
    assert _packet(snapshot, features=features).packet_hash == _packet(
        snapshot, features=features
    ).packet_hash


def test_the_feature_bundle_is_referenced_as_evidence() -> None:
    snapshot = _snapshot()
    features = SnapshotFeaturePipeline().build(snapshot)
    packet = _packet(snapshot, features=features)
    assert f"features:{features.bundle_id}" in packet.evidence_ids


def test_the_prompt_still_contains_no_secrets() -> None:
    snapshot = _snapshot()
    prompt = build_entry_prompt(
        _packet(snapshot, features=SnapshotFeaturePipeline().build(snapshot))
    )
    lowered = prompt["combined"].lower()
    for forbidden in ("api_key", "authorization", "bearer ", "access_token"):
        assert forbidden not in lowered
