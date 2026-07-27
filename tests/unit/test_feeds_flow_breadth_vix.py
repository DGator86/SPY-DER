"""Flow, breadth, VIX term structure and the Yahoo settlement backstop.

The rule these all share, and the reason they are tested together: an *absent*
reading must never arrive downstream as a zero. Zero put volume is a maximally
call-skewed tape; a zero RSP/SPY divergence asserts perfectly neutral breadth;
a VIX curve backfilled from the 30-day leg looks flat rather than unknown. Each
of those is a confident statement about the market derived from a vendor gap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from spy_der.contracts.market import (
    BreadthState,
    OptionContract,
    OptionQuote,
    OptionType,
    VolatilityTermStructure,
)
from spy_der.features.flow import compute_flow
from spy_der.market_data.assembler import CanonicalSnapshotAssembler
from spy_der.market_data.providers._http import ProviderHttpError
from spy_der.market_data.providers.factory import (
    AVAILABLE_PROVIDERS,
    CHAINLESS_PROVIDERS,
    PENDING_PROVIDERS,
    build_provider_chain,
    build_settlement_provider,
)
from spy_der.market_data.providers.tradier import (
    FLAG_BREADTH_UNAVAILABLE,
    FLAG_VIX_UNAVAILABLE,
    SECTOR_ETFS,
    TOP10_WEIGHTS,
    TradierProvider,
)
from spy_der.market_data.providers.yahoo import YahooProvider

TS = datetime(2026, 7, 22, 16, 30, tzinfo=UTC)
SESSION = date(2026, 7, 22)


# --------------------------------------------------------------------------- #
# Flow                                                                        #
# --------------------------------------------------------------------------- #
def _quote(
    side: OptionType, strike: str, *, volume: int | None, oi: int | None = 100
) -> OptionQuote:
    return OptionQuote(
        contract=OptionContract(
            contract_id=f"SPY-{strike}-{side.value}",
            underlying_symbol="SPY",
            expiration=SESSION,
            option_type=side,
            strike=Decimal(strike),
        ),
        received_at=TS,
        source="test",
        volume=volume,
        open_interest=oi,
    )


def _snapshot(chain: tuple[OptionQuote, ...]) -> Any:
    return CanonicalSnapshotAssembler().assemble(
        timestamp=TS,
        underlying_symbol="SPY",
        underlying_price=Decimal("500"),
        option_chain=chain,
    )


def test_put_call_ratio_and_participation_are_computed() -> None:
    chain = (
        _quote(OptionType.CALL, "500", volume=100, oi=1000),
        _quote(OptionType.PUT, "500", volume=150, oi=1000),
    )
    flow = compute_flow(_snapshot(chain))
    assert flow.call_volume == 100
    assert flow.put_volume == 150
    assert flow.total_volume == 250
    assert flow.pcr_volume == pytest.approx(1.5)
    assert flow.volume_oi_ratio == pytest.approx(250 / 2000)


def test_absent_volume_reads_as_unknown_not_as_zero_flow() -> None:
    """The whole point: a vendor that omits volume must not imply a dead tape."""
    chain = (
        _quote(OptionType.CALL, "500", volume=None),
        _quote(OptionType.PUT, "500", volume=None),
    )
    flow = compute_flow(_snapshot(chain))
    assert flow.pcr_volume is None
    assert flow.volume_oi_ratio is None
    assert flow.is_observed is False


def test_zero_call_volume_is_unknown_rather_than_infinite_skew() -> None:
    chain = (
        _quote(OptionType.CALL, "500", volume=0),
        _quote(OptionType.PUT, "500", volume=90),
    )
    assert compute_flow(_snapshot(chain)).pcr_volume is None


def test_participation_needs_open_interest() -> None:
    chain = (_quote(OptionType.CALL, "500", volume=10, oi=None),)
    flow = compute_flow(_snapshot(chain))
    assert flow.total_open_interest == 0
    assert flow.volume_oi_ratio is None


def test_an_empty_chain_reports_nothing_observed() -> None:
    flow = compute_flow(_snapshot(()))
    assert flow.total_volume == 0
    assert flow.is_observed is False


# --------------------------------------------------------------------------- #
# VIX term structure                                                          #
# --------------------------------------------------------------------------- #
def _tradier(**kw: Any) -> TradierProvider:
    kw.setdefault("api_key", "test-token")
    return TradierProvider(**kw)


def _index_quotes(**levels: float) -> dict[str, Any]:
    return {
        "quotes": {
            "quote": [{"symbol": k.upper(), "last": v} for k, v in levels.items()]
        }
    }


def test_vix_term_structure_is_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _index_quotes(vix=15.0, vix9d=13.5, vix3m=17.0, vvix=92.0)
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json",
        lambda url, **kw: payload,
    )
    term = _tradier()._vix()
    assert term is not None
    assert (term.vix, term.vix9d, term.vix3m, term.vvix) == (15.0, 13.5, 17.0, 92.0)
    assert term.source == "tradier"


def test_missing_legs_stay_none_rather_than_being_backfilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown curve must stay distinguishable from a flat one."""
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json",
        lambda url, **kw: _index_quotes(vix=15.0),
    )
    term = _tradier()._vix()
    assert term is not None
    assert term.vix == 15.0
    assert term.vix9d is None and term.vix3m is None
    assert term.contango is None
    assert term.near_term_stress is None


def test_no_vix_at_all_means_no_term_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json",
        lambda url, **kw: _index_quotes(vix9d=13.0),
    )
    assert _tradier()._vix() is None


def test_term_structure_shape_helpers() -> None:
    term = VolatilityTermStructure(vix=20.0, vix9d=18.0, vix3m=22.0)
    assert term.contango == pytest.approx(0.1)
    assert term.near_term_stress == pytest.approx(-0.1)


# --------------------------------------------------------------------------- #
# Breadth                                                                     #
# --------------------------------------------------------------------------- #
def _breadth_quotes(
    *, spy: float, rsp: float, sectors: list[float], top10: float
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        {"symbol": "SPY", "change_percentage": spy * 100},
        {"symbol": "RSP", "change_percentage": rsp * 100},
    ]
    rows += [
        {"symbol": s, "change_percentage": m * 100}
        for s, m in zip(SECTOR_ETFS, sectors, strict=False)
    ]
    rows += [
        {"symbol": s, "change_percentage": top10 * 100} for s in TOP10_WEIGHTS
    ]
    return {"quotes": {"quote": rows}}


def test_breadth_components_are_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _breadth_quotes(
        spy=0.010, rsp=0.004, sectors=[0.01] * 6 + [-0.01] * 5, top10=0.02
    )
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json", lambda url, **kw: payload
    )
    breadth = _tradier()._breadth()
    assert breadth is not None
    assert breadth.rsp_spy_div == pytest.approx(-0.006)  # equal-weight lagging
    assert breadth.sector_align == pytest.approx(6 / 11)
    assert breadth.top10_pressure == pytest.approx(0.02)
    assert breadth.sectors_observed == 11


def test_breadth_falls_back_to_last_over_prevclose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "quotes": {
            "quote": [
                {"symbol": "SPY", "last": 101.0, "prevclose": 100.0},
                {"symbol": "RSP", "last": 100.5, "prevclose": 100.0},
            ]
        }
    }
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json", lambda url, **kw: payload
    )
    breadth = _tradier()._breadth()
    assert breadth is not None
    assert breadth.rsp_spy_div == pytest.approx(-0.005)


def test_a_partial_breadth_response_reports_what_it_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "quotes": {"quote": [{"symbol": "XLK", "change_percentage": 1.0}]}
    }
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json", lambda url, **kw: payload
    )
    breadth = _tradier()._breadth()
    assert breadth is not None
    assert breadth.rsp_spy_div is None
    assert breadth.sector_align == pytest.approx(1.0)


def test_a_breadth_response_with_nothing_usable_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "spy_der.market_data.providers.tradier.get_json", lambda url, **kw: {"quotes": {}}
    )
    assert _tradier()._breadth() is None


def test_breadth_state_reports_whether_it_was_observed() -> None:
    assert BreadthState().is_observed is False
    assert BreadthState(sector_align=0.5).is_observed is True


def test_vix_and_breadth_failures_degrade_the_tick_but_keep_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chain is tradeable without breadth; losing the tick over it would not be."""

    def fake(url: str, **_kw: Any) -> dict[str, Any]:
        if "symbols=VIX" in url or "symbols=SPY,RSP" in url:
            raise ProviderHttpError("HTTP 403", status=403)
        if "/markets/quotes" in url:
            return {"quotes": {"quote": {"symbol": "SPY", "last": 100.05}}}
        if "/markets/timesales" in url:
            return {"series": {"data": []}}
        return {
            "options": {
                "option": [
                    {
                        "option_type": "call",
                        "strike": 100.0,
                        "bid": 1.0,
                        "ask": 1.1,
                        "open_interest": 10,
                        "expiration_date": "2026-07-22",
                        "greeks": {"gamma": 0.05, "delta": 0.45},
                    }
                ]
            }
        }

    monkeypatch.setattr("spy_der.market_data.providers.tradier.get_json", fake)
    tick = _tradier().fetch(TS)
    assert tick is not None
    assert tick.option_chain
    assert tick.volatility_term_structure is None
    assert tick.breadth is None
    assert FLAG_VIX_UNAVAILABLE in tick.quality_flags
    assert FLAG_BREADTH_UNAVAILABLE in tick.quality_flags


# --------------------------------------------------------------------------- #
# Yahoo settlement backstop                                                   #
# --------------------------------------------------------------------------- #
def _chart_payload(
    *, meta: dict[str, Any] | None = None, stamps: list[int] | None = None,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if meta is not None:
        result["meta"] = meta
    if stamps is not None:
        result["timestamp"] = stamps
    if quote is not None:
        result["indicators"] = {"quote": [quote]}
    return {"chart": {"result": [result], "error": None}}


def test_settlement_selects_the_requested_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answering for any recent date is what a post-outage backfill needs."""
    days = [
        int(datetime(2026, 7, 20, 20, 0, tzinfo=UTC).timestamp()),
        int(datetime(2026, 7, 21, 20, 0, tzinfo=UTC).timestamp()),
        int(datetime(2026, 7, 22, 20, 0, tzinfo=UTC).timestamp()),
    ]
    payload = _chart_payload(stamps=days, quote={"close": [500.0, 501.0, 502.0]})
    monkeypatch.setattr(
        "spy_der.market_data.providers.yahoo.get_json", lambda url, **kw: payload
    )
    assert YahooProvider().settlement_price("2026-07-21") == Decimal("501.0000")


def test_settlement_for_an_unknown_session_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _chart_payload(
        stamps=[int(datetime(2026, 7, 20, 20, 0, tzinfo=UTC).timestamp())],
        quote={"close": [500.0]},
    )
    monkeypatch.setattr(
        "spy_der.market_data.providers.yahoo.get_json", lambda url, **kw: payload
    )
    assert YahooProvider().settlement_price("2026-01-01") is None


def test_settlement_survives_a_vendor_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **_kw: Any) -> dict[str, Any]:
        raise ProviderHttpError("HTTP 429", status=429)

    monkeypatch.setattr("spy_der.market_data.providers.yahoo.get_json", boom)
    assert YahooProvider().settlement_price("2026-07-21") is None


def test_yahoo_serves_the_real_cboe_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    levels = {"^VIX": 15.0, "^VIX9D": 13.0, "^VIX3M": 17.0, "^VVIX": 90.0}

    def fake(url: str, **_kw: Any) -> dict[str, Any]:
        # Match the full path segment: "%5EVIX" is a prefix of "%5EVIX9D".
        for ticker, value in levels.items():
            if f"/{urllib_quote(ticker)}?" in url:
                return _chart_payload(meta={"regularMarketPrice": value})
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("spy_der.market_data.providers.yahoo.get_json", fake)
    term = YahooProvider().safe_volatility_term_structure()
    assert term is not None
    assert (term.vix, term.vix9d, term.vix3m, term.vvix) == (15.0, 13.0, 17.0, 90.0)
    assert term.source == "yahoo"


def test_one_unavailable_index_costs_one_field_not_the_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(url: str, **_kw: Any) -> dict[str, Any]:
        if f"/{urllib_quote('^VVIX')}?" in url:
            raise ProviderHttpError("HTTP 404", status=404)
        return _chart_payload(meta={"regularMarketPrice": 15.0})

    monkeypatch.setattr("spy_der.market_data.providers.yahoo.get_json", fake)
    term = YahooProvider().safe_volatility_term_structure()
    assert term is not None
    assert term.vix == 15.0
    assert term.vvix is None


def test_yahoo_never_claims_an_option_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """It backstops the easy roles; pretending otherwise would look healthy."""
    monkeypatch.setattr(
        "spy_der.market_data.providers.yahoo.get_json",
        lambda url, **kw: _chart_payload(meta={"regularMarketPrice": 500.0}),
    )
    tick = YahooProvider().fetch(TS)
    assert tick is not None
    assert tick.has_chain is False
    assert tick.option_chain == ()
    assert tick.underlying_price == Decimal("500.0000")


def test_yahoo_bars_drop_the_null_padded_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = int(TS.timestamp()) - 180
    payload = _chart_payload(
        meta={"regularMarketPrice": 500.0},
        stamps=[base, base + 60, base + 120],
        quote={
            "open": [500.0, None, 501.0],
            "high": [500.5, None, 501.5],
            "low": [499.5, None, 500.5],
            "close": [500.2, None, 501.2],
            "volume": [1000, None, 1200],
        },
    )
    monkeypatch.setattr(
        "spy_der.market_data.providers.yahoo.get_json", lambda url, **kw: payload
    )
    tick = YahooProvider(include_bars=True).fetch(TS)
    assert tick is not None
    assert len(tick.bars_1m) == 2


# --------------------------------------------------------------------------- #
# Factory registration                                                        #
# --------------------------------------------------------------------------- #
def test_yahoo_is_available_and_no_longer_pending() -> None:
    assert "yahoo" in AVAILABLE_PROVIDERS
    assert "yahoo" not in PENDING_PROVIDERS


def test_a_chainless_provider_is_kept_out_of_the_failover_chain() -> None:
    """In the primary chain it would produce snapshots that fail closed every tick."""
    assert "yahoo" in CHAINLESS_PROVIDERS
    chain = build_provider_chain(["yahoo"])
    assert chain.is_empty
    assert "settlement" in chain.describe()


def test_yahoo_is_accepted_as_the_settlement_provider() -> None:
    provider = build_settlement_provider("yahoo")
    assert provider is not None
    assert provider.name == "yahoo"


def test_no_settlement_provider_configured_is_none() -> None:
    assert build_settlement_provider(None) is None
    assert build_settlement_provider("") is None


def urllib_quote(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value)


def test_the_settlement_role_costs_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is polled every tick, so bars and the four index legs stay opt-in."""
    calls: list[str] = []

    def fake(url: str, **_kw: Any) -> dict[str, Any]:
        calls.append(url)
        return _chart_payload(meta={"regularMarketPrice": 500.0})

    monkeypatch.setattr("spy_der.market_data.providers.yahoo.get_json", fake)
    YahooProvider().fetch(TS)
    assert len(calls) == 1


def test_the_settlement_factory_enables_the_volatility_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yahoo's real CBOE indices are the point of polling it at all."""
    provider = build_settlement_provider("yahoo")
    assert isinstance(provider, YahooProvider)
    assert provider.include_volatility_indices is True
    assert provider.include_bars is False


def test_the_settlement_source_backstops_the_volatility_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A brokerage without VIX entitlement still gets a term structure."""
    from spy_der.market_data.composite import CompositeFeed
    from spy_der.market_data.providers.base import RawTick
    from spy_der.market_data.providers.static import StaticProvider

    primary = StaticProvider(
        "tradier",
        [RawTick(provider="tradier", symbol="SPY", observed_at=TS,
                 underlying_price=Decimal("500"), volatility_term_structure=None)],
    )
    settle = StaticProvider(
        "yahoo",
        [RawTick(provider="yahoo", symbol="SPY", observed_at=TS,
                 underlying_price=Decimal("500"), has_chain=False,
                 volatility_term_structure=VolatilityTermStructure(
                     vix=15.0, vix9d=13.0, vix3m=17.0, source="yahoo"))],
    )
    snapshot = CompositeFeed([primary], settlement_provider=settle).snapshot(TS)
    assert snapshot is not None
    assert snapshot.volatility_term_structure is not None
    assert snapshot.volatility_term_structure.source == "yahoo"
    assert snapshot.volatility_term_structure.contango is not None


def test_the_primary_term_structure_wins_over_the_backstop() -> None:
    """Filling a gap is not the same as overriding a reading."""
    from spy_der.market_data.composite import CompositeFeed
    from spy_der.market_data.providers.base import RawTick
    from spy_der.market_data.providers.static import StaticProvider

    primary = StaticProvider(
        "tradier",
        [RawTick(provider="tradier", symbol="SPY", observed_at=TS,
                 underlying_price=Decimal("500"),
                 volatility_term_structure=VolatilityTermStructure(
                     vix=16.0, source="tradier"))],
    )
    settle = StaticProvider(
        "yahoo",
        [RawTick(provider="yahoo", symbol="SPY", observed_at=TS,
                 underlying_price=Decimal("500"), has_chain=False,
                 volatility_term_structure=VolatilityTermStructure(
                     vix=15.0, source="yahoo"))],
    )
    snapshot = CompositeFeed([primary], settlement_provider=settle).snapshot(TS)
    assert snapshot is not None
    assert snapshot.volatility_term_structure is not None
    assert snapshot.volatility_term_structure.source == "tradier"


def test_breadth_reaches_the_snapshot_and_is_reported_as_a_component() -> None:
    from spy_der.market_data.composite import CompositeFeed
    from spy_der.market_data.providers.base import RawTick
    from spy_der.market_data.providers.static import StaticProvider

    tick = RawTick(
        provider="tradier", symbol="SPY", observed_at=TS,
        underlying_price=Decimal("500"),
        breadth=BreadthState(rsp_spy_div=-0.004, sector_align=0.6, source="tradier"),
    )
    snapshot = CompositeFeed([StaticProvider("tradier", [tick])]).snapshot(TS)
    assert snapshot is not None
    assert snapshot.breadth is not None
    assert snapshot.breadth.sector_align == 0.6
    statuses = {o.component.value: o.status.value for o in snapshot.feed_observations}
    assert statuses["breadth"] == "LIVE"


def test_absent_breadth_is_reported_missing_but_does_not_fail_the_snapshot() -> None:
    """Breadth is optional: degraded, not unusable."""
    from spy_der.market_data.composite import CompositeFeed
    from spy_der.market_data.providers.base import RawTick
    from spy_der.market_data.providers.static import StaticProvider

    tick = RawTick(
        provider="tradier", symbol="SPY", observed_at=TS,
        underlying_price=Decimal("500"),
    )
    snapshot = CompositeFeed([StaticProvider("tradier", [tick])]).snapshot(TS)
    assert snapshot is not None
    statuses = {o.component.value: o.status.value for o in snapshot.feed_observations}
    assert statuses["breadth"] == "MISSING"
    assert "breadth" not in snapshot.missing_components  # not a required component
