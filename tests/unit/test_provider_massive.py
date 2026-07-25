"""Massive provider — faithful port of the 0DTE adapter's hard-won behavior.

The fixture mirrors the real snapshot shape *including its absences*:
``last_quote`` and ``underlying_asset.price`` are missing on the live feed, which
is exactly why the day-close fallback and the put-call-parity spot recovery
exist. Tests that fed the adapter a tidy payload would pass while the live
behavior stayed broken.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from spy_der.contracts.market import OptionType
from spy_der.market_data.providers import _http
from spy_der.market_data.providers._http import (
    HttpConfig,
    ProviderHttpError,
    get_json,
    redact,
)
from spy_der.market_data.providers.massive import (
    FLAG_DAY_CLOSE_FALLBACK,
    FLAG_LIVE_QUOTE,
    MassiveProvider,
)
from spy_der.market_data.providers.spot import ChainQuoteView, estimate_spot_from_chain

TS = datetime(2026, 7, 24, 17, 30, tzinfo=UTC)  # 13:30 ET
SESSION = "2026-07-24"


def _contract(
    strike: float,
    side: str,
    *,
    bid: float | None = None,
    ask: float | None = None,
    close: float | None = None,
    expiration: str = SESSION,
    oi: int = 1000,
    include_underlying_price: bool = False,
) -> dict[str, Any]:
    """One snapshot row. Omitting bid/ask reproduces the live feed's absence."""
    row: dict[str, Any] = {
        "details": {
            "contract_type": side,
            "strike_price": strike,
            "expiration_date": expiration,
            "ticker": (
                f"O:SPY{expiration.replace('-', '')[2:]}"
                f"{side[0].upper()}{int(strike * 1000):08d}"
            ),
        },
        "greeks": {"gamma": 0.02, "delta": 0.5 if side == "call" else -0.5, "vega": 0.1},
        "open_interest": oi,
        "day": {"volume": 250},
    }
    if bid is not None and ask is not None:
        row["last_quote"] = {"bid": bid, "ask": ask}
    if close is not None:
        row["day"]["close"] = close
    if include_underlying_price:
        row["underlying_asset"] = {"price": 640.12}
    return row


def _live_chain() -> list[dict[str, Any]]:
    """Spot 600: calls/puts priced so parity recovers it."""
    rows: list[dict[str, Any]] = []
    spot = 600.0
    for strike in (596.0, 598.0, 600.0, 602.0, 604.0):
        intrinsic_call = max(spot - strike, 0.0)
        intrinsic_put = max(strike - spot, 0.0)
        call_mid = intrinsic_call + 1.0
        put_mid = intrinsic_put + 1.0
        rows.append(_contract(strike, "call", bid=call_mid - 0.05, ask=call_mid + 0.05))
        rows.append(_contract(strike, "put", bid=put_mid - 0.05, ask=put_mid + 0.05))
    return rows


class _FakeTransport:
    """Records requested URLs and serves canned pages."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(
        self, url: str, *, headers: Any = None, config: Any = None
    ) -> dict[str, Any]:
        self.urls.append(url)
        self.headers.append(dict(headers or {}))
        return self.pages[min(len(self.urls) - 1, len(self.pages) - 1)]


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch):
    def _install(pages: list[dict[str, Any]]) -> _FakeTransport:
        fake = _FakeTransport(pages)
        monkeypatch.setattr("spy_der.market_data.providers.massive.get_json", fake)
        return fake

    return _install


def _provider(**kw: Any) -> MassiveProvider:
    params: dict[str, Any] = {"api_key": "test-key", "base_url": "https://api.example.com"}
    params.update(kw)
    return MassiveProvider(**params)


# --------------------------------------------------------------------------- #
# Inert without credentials                                                   #
# --------------------------------------------------------------------------- #
def test_provider_is_inert_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    provider = MassiveProvider()
    assert provider.configured is False
    # None, not an exception — CompositeFeed must be able to fail over.
    assert provider.fetch(TS) is None


def test_provider_reads_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "from-env")
    assert MassiveProvider().configured is True


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #
def test_live_chain_produces_a_raw_tick(transport: Any) -> None:
    transport([{"results": _live_chain()}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.provider == "massive"
    assert tick.symbol == "SPY"
    assert tick.has_chain is True
    assert len(tick.option_chain) == 10
    assert {q.contract.option_type for q in tick.option_chain} == {
        OptionType.CALL,
        OptionType.PUT,
    }


def test_spot_is_recovered_by_put_call_parity(transport: Any) -> None:
    """underlying_asset.price is absent on the live feed."""
    transport([{"results": _live_chain()}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.underlying_price == pytest.approx(Decimal("600.00"), abs=Decimal("0.05"))


def test_vendor_spot_wins_when_present(transport: Any) -> None:
    rows = _live_chain()
    rows[0] = _contract(596.0, "call", bid=4.95, ask=5.05, include_underlying_price=True)
    transport([{"results": rows}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.underlying_price == Decimal("640.12")


def test_live_quotes_are_flagged_live(transport: Any) -> None:
    transport([{"results": _live_chain()}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert all(FLAG_LIVE_QUOTE in q.quality_flags for q in tick.option_chain)


# --------------------------------------------------------------------------- #
# The day-close fallback — tagged, never passed off as an NBBO                 #
# --------------------------------------------------------------------------- #
def test_missing_quote_falls_back_to_day_close(transport: Any) -> None:
    rows = [
        _contract(600.0, "call", close=2.50),
        _contract(600.0, "put", close=2.50),
    ]
    transport([{"results": rows}])
    tick = _provider().fetch(TS)
    assert tick is not None
    for quote in tick.option_chain:
        assert quote.bid == Decimal("2.5000")
        assert quote.ask == Decimal("2.5000")
        assert FLAG_DAY_CLOSE_FALLBACK in quote.quality_flags
        assert FLAG_LIVE_QUOTE not in quote.quality_flags


def test_fallback_quotes_have_a_degenerate_spread(transport: Any) -> None:
    """bid == ask is what makes downstream quote-quality penalize these rows."""
    transport([{"results": [_contract(600.0, "call", close=2.50)]}])
    tick = _provider().fetch(TS)
    assert tick is not None
    quote = tick.option_chain[0]
    assert quote.ask is not None and quote.bid is not None
    assert quote.ask - quote.bid == Decimal("0")


def test_live_quote_fraction_exposes_an_unusable_chain(transport: Any) -> None:
    rows = [_contract(600.0, "call", close=1.0), _contract(600.0, "put", bid=0.9, ask=1.1)]
    transport([{"results": rows}])
    provider = _provider()
    tick = provider.fetch(TS)
    assert tick is not None
    assert provider.live_quote_fraction(list(tick.option_chain)) == pytest.approx(0.5)


def test_zero_and_negative_quotes_are_not_treated_as_live(transport: Any) -> None:
    rows = [_contract(600.0, "call", bid=0.0, ask=0.0, close=1.25)]
    transport([{"results": rows}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert FLAG_DAY_CLOSE_FALLBACK in tick.option_chain[0].quality_flags


def test_unpriceable_contract_is_dropped(transport: Any) -> None:
    rows = [*_live_chain(), _contract(700.0, "call")]  # no quote, no close
    transport([{"results": rows}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert all(float(q.contract.strike) != 700.0 for q in tick.option_chain)


# --------------------------------------------------------------------------- #
# Refusing to invent a spot                                                   #
# --------------------------------------------------------------------------- #
def test_unrecoverable_spot_yields_no_tick(transport: Any) -> None:
    """Calls only — parity is impossible, and delta is absent."""
    rows = [{
        "details": {"contract_type": "call", "strike_price": 600.0, "expiration_date": SESSION},
        "greeks": {"gamma": 0.02, "delta": 0.0},
        "open_interest": 10,
        "day": {"close": 1.0},
    }]
    transport([{"results": rows}])
    assert _provider().fetch(TS) is None


def test_empty_results_yield_no_tick(transport: Any) -> None:
    transport([{"results": []}])
    assert _provider().fetch(TS) is None


def test_malformed_results_yield_no_tick(transport: Any) -> None:
    transport([{"results": "not-a-list"}])
    assert _provider().fetch(TS) is None


# --------------------------------------------------------------------------- #
# Incomplete rows                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("missing", ["contract_type", "strike_price"])
def test_row_missing_required_detail_is_dropped(transport: Any, missing: str) -> None:
    bad = _contract(600.0, "call", bid=1.0, ask=1.1)
    del bad["details"][missing]
    transport([{"results": [*_live_chain(), bad]}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert len(tick.option_chain) == 10


@pytest.mark.parametrize("missing", ["gamma", "delta"])
def test_row_missing_greeks_is_dropped(transport: Any, missing: str) -> None:
    bad = _contract(700.0, "call", bid=1.0, ask=1.1)
    del bad["greeks"][missing]
    transport([{"results": [*_live_chain(), bad]}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert all(float(q.contract.strike) != 700.0 for q in tick.option_chain)


def test_row_missing_open_interest_is_dropped(transport: Any) -> None:
    bad = _contract(700.0, "call", bid=1.0, ask=1.1)
    del bad["open_interest"]
    transport([{"results": [*_live_chain(), bad]}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert all(float(q.contract.strike) != 700.0 for q in tick.option_chain)


def test_non_finite_greeks_are_dropped_not_propagated(transport: Any) -> None:
    """OptionQuote requires finite greeks — a NaN would raise inside the contract."""
    bad = _contract(602.0, "call", bid=1.0, ask=1.1)
    bad["greeks"]["vega"] = float("nan")
    transport([{"results": [bad, _contract(602.0, "put", bid=1.0, ask=1.1)]}])
    tick = _provider().fetch(TS)
    assert tick is not None
    call = next(q for q in tick.option_chain if q.contract.option_type is OptionType.CALL)
    assert call.vega is None


# --------------------------------------------------------------------------- #
# Expiry filtering                                                            #
# --------------------------------------------------------------------------- #
def test_zero_dte_filter_is_requested_server_side(transport: Any) -> None:
    fake = transport([{"results": _live_chain()}])
    _provider().fetch(TS)
    assert f"expiration_date={SESSION}" in fake.urls[0]


def test_other_expiries_are_dropped_client_side(transport: Any) -> None:
    """Backstop for a vendor that ignores the expiration_date parameter."""
    rows = [*_live_chain(), _contract(600.0, "call", bid=5.0, ask=5.1, expiration="2026-08-21")]
    transport([{"results": rows}])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert {q.contract.expiration.isoformat() for q in tick.option_chain} == {SESSION}


def test_all_expiries_kept_when_zero_dte_disabled(transport: Any) -> None:
    rows = [*_live_chain(), _contract(600.0, "call", bid=5.0, ask=5.1, expiration="2026-08-21")]
    transport([{"results": rows}])
    tick = _provider(zero_dte_only=False).fetch(TS)
    assert tick is not None
    assert len(tick.option_chain) == 11


def test_session_is_resolved_in_exchange_time(transport: Any) -> None:
    """01:00 UTC is still the previous ET session; using UTC would slip a day."""
    fake = transport([{"results": _live_chain()}])
    _provider().fetch(datetime(2026, 7, 25, 1, 0, tzinfo=UTC))
    assert "expiration_date=2026-07-24" in fake.urls[0]


# --------------------------------------------------------------------------- #
# Pagination                                                                  #
# --------------------------------------------------------------------------- #
def test_pagination_follows_next_url(transport: Any) -> None:
    half = _live_chain()
    fake = transport([
        {"results": half[:5], "next_url": "https://api.example.com/page2"},
        {"results": half[5:]},
    ])
    tick = _provider().fetch(TS)
    assert tick is not None
    assert len(fake.urls) == 2
    assert "page2" in fake.urls[1]
    assert len(tick.option_chain) == 10


def test_pagination_is_capped(transport: Any) -> None:
    """A runaway next_url chain must not hang the tick."""
    fake = transport([
        {"results": _live_chain(), "next_url": "https://api.example.com/loop"}
    ])
    _provider().fetch(TS)
    assert len(fake.urls) == 25


# --------------------------------------------------------------------------- #
# Auth modes                                                                  #
# --------------------------------------------------------------------------- #
def test_bearer_auth_uses_a_header_and_keeps_the_key_out_of_the_url(
    transport: Any,
) -> None:
    fake = transport([{"results": _live_chain()}])
    _provider().fetch(TS)
    assert fake.headers[0]["Authorization"] == "Bearer test-key"
    assert "test-key" not in fake.urls[0]


def test_query_auth_appends_the_key(transport: Any) -> None:
    fake = transport([{"results": _live_chain()}])
    _provider(auth_mode="query").fetch(TS)
    assert "apiKey=test-key" in fake.urls[0]
    assert "Authorization" not in fake.headers[0]


# --------------------------------------------------------------------------- #
# Vendor failure is failover, not an exception                                #
# --------------------------------------------------------------------------- #
def test_http_error_yields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise ProviderHttpError("HTTP 503 from https://api.example.com", status=503)

    monkeypatch.setattr("spy_der.market_data.providers.massive.get_json", _boom)
    assert _provider().fetch(TS) is None


# --------------------------------------------------------------------------- #
# Spot estimator, directly                                                    #
# --------------------------------------------------------------------------- #
def _view(
    is_call: bool, strike: float, mid: float, delta: float | None = None
) -> ChainQuoteView:
    return ChainQuoteView(
        is_call=is_call, strike=strike, bid=mid - 0.05, ask=mid + 0.05, delta=delta
    )


def test_parity_recovers_spot_between_strikes() -> None:
    spot = 601.4
    views = []
    for strike in (598.0, 600.0, 602.0, 604.0):
        views.append(_view(True, strike, max(spot - strike, 0.0) + 1.0))
        views.append(_view(False, strike, max(strike - spot, 0.0) + 1.0))
    estimated = estimate_spot_from_chain(views)
    assert estimated is not None
    assert float(estimated) == pytest.approx(spot, abs=0.5)


def test_parity_is_robust_to_one_bad_mark() -> None:
    """The median over the ATM window is why a single bad quote cannot move spot."""
    spot = 600.0
    views = []
    for strike in (596.0, 598.0, 600.0, 602.0, 604.0):
        views.append(_view(True, strike, max(spot - strike, 0.0) + 1.0))
        views.append(_view(False, strike, max(strike - spot, 0.0) + 1.0))
    views.append(_view(True, 598.0, 90.0))  # wild, but a wider spread
    estimated = estimate_spot_from_chain(views)
    assert estimated is not None
    assert float(estimated) == pytest.approx(spot, abs=1.0)


def test_delta_fallback_when_no_strike_is_paired() -> None:
    views = [_view(True, 600.0, 1.0, delta=0.48), _view(True, 610.0, 0.2, delta=0.2)]
    assert estimate_spot_from_chain(views) == Decimal("600.00")


def test_no_estimate_from_an_empty_chain() -> None:
    assert estimate_spot_from_chain([]) is None


def test_tighter_duplicate_strike_wins() -> None:
    wide = ChainQuoteView(is_call=True, strike=600.0, bid=0.0, ask=20.0)
    tight = ChainQuoteView(is_call=True, strike=600.0, bid=0.95, ask=1.05)
    put = ChainQuoteView(is_call=False, strike=600.0, bid=0.95, ask=1.05)
    assert estimate_spot_from_chain([wide, tight, put]) == Decimal("600.00")


# --------------------------------------------------------------------------- #
# Transport: retry and redaction                                              #
# --------------------------------------------------------------------------- #
def test_credentials_are_redacted_from_urls() -> None:
    assert "secret" not in redact("https://x.test/v3?apiKey=secret&limit=250")
    assert "limit=250" in redact("https://x.test/v3?apiKey=secret&limit=250")
    for key in ("api_key", "token", "access_token", "password"):
        assert "secret" not in redact(f"https://x.test/v3?{key}=secret")


def test_redaction_survives_an_unparseable_url() -> None:
    assert redact("http://[::1") == "<unparseable-url>"


def test_retryable_status_is_retried_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fail(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise ProviderHttpError("HTTP 503", status=503)

    monkeypatch.setattr(_http, "_open", _fail)
    slept: list[float] = []
    cfg = HttpConfig(attempts=3, backoff_seconds=(0.1, 0.2), sleep=slept.append)
    with pytest.raises(ProviderHttpError):
        get_json("https://x.test", config=cfg)
    assert calls["n"] == 3
    assert slept == [0.1, 0.2]


def test_non_retryable_status_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 is a config error; retrying it just burns the tick."""
    calls = {"n": 0}

    def _fail(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise ProviderHttpError("HTTP 401", status=401)

    monkeypatch.setattr(_http, "_open", _fail)
    with pytest.raises(ProviderHttpError):
        get_json("https://x.test", config=HttpConfig(attempts=3, sleep=lambda _s: None))
    assert calls["n"] == 1


def test_transport_failure_is_retryable() -> None:
    assert ProviderHttpError("timeout").retryable is True
    assert ProviderHttpError("HTTP 429", status=429).retryable is True
    assert ProviderHttpError("HTTP 404", status=404).retryable is False


def test_retry_succeeds_after_a_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _flaky(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderHttpError("HTTP 502", status=502)
        return {"results": []}

    monkeypatch.setattr(_http, "_open", _flaky)
    cfg = HttpConfig(attempts=3, backoff_seconds=(0.0,), sleep=lambda _s: None)
    assert get_json("https://x.test", config=cfg) == {"results": []}


def test_backoff_schedule_extends_past_its_last_entry() -> None:
    cfg = HttpConfig(attempts=6, backoff_seconds=(1.0, 2.0))
    assert [cfg.backoff_for(i) for i in range(4)] == [1.0, 2.0, 2.0, 2.0]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({"attempts": 0}, "attempts"), ({"timeout_seconds": 0}, "timeout_seconds")],
)
def test_http_config_validates(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        HttpConfig(**kwargs)


# --------------------------------------------------------------------------- #
# Composite integration                                                       #
# --------------------------------------------------------------------------- #
def test_provider_drives_the_canonical_pipeline(transport: Any) -> None:
    """The payoff: a vendor tick becomes a canonical snapshot with no extra glue."""
    from spy_der.market_data.composite import CompositeFeed

    transport([{"results": _live_chain()}])
    snapshot = CompositeFeed([_provider()]).snapshot(TS)
    assert snapshot is not None
    assert snapshot.underlying_symbol == "SPY"
    assert snapshot.option_chain
    assert snapshot.chain_coverage.has_calls and snapshot.chain_coverage.has_puts


def test_composite_fails_over_when_massive_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spy_der.market_data.composite import CompositeFeed
    from spy_der.market_data.providers.base import RawTick
    from spy_der.market_data.providers.static import StaticProvider

    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise ProviderHttpError("HTTP 503", status=503)

    monkeypatch.setattr("spy_der.market_data.providers.massive.get_json", _boom)
    backup = StaticProvider(
        "backup",
        [RawTick(provider="backup", symbol="SPY", observed_at=TS,
                 underlying_price=Decimal("599"), has_chain=False)],
    )
    snapshot = CompositeFeed([_provider(), backup]).snapshot(TS)
    assert snapshot is not None
    assert snapshot.underlying_price == Decimal("599")
    assert any(s.fallback_used for s in snapshot.selected_providers)


def test_fixture_matches_the_documented_live_absences() -> None:
    """Guard the fixture itself: if it grows the absent fields, it stops testing."""
    payload = json.loads(json.dumps({"results": _live_chain()}))
    for row in payload["results"]:
        assert "underlying_asset" not in row
    fallback = _contract(600.0, "call", close=1.0)
    assert "last_quote" not in fallback
