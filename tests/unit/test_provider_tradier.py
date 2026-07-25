"""Tradier provider — real-time NBBO with greeks.

Ported from 0DTE `tradier_feed`. Unlike the Massive snapshot plan, Tradier
returns genuine two-sided option quotes, so nothing here needs a day-close
fallback; a row that cannot be priced is dropped instead.

No network: `get_json` is stubbed, so these assert the mapping and the vendor
quirks, not connectivity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from spy_der.contracts.market import OptionType
from spy_der.market_data.providers.factory import (
    AVAILABLE_PROVIDERS,
    PENDING_PROVIDERS,
    build_provider_chain,
)
from spy_der.market_data.providers.tradier import TradierProvider

TS = datetime(2026, 7, 22, 16, 30, tzinfo=UTC)  # 12:30 ET, a live session
SESSION = "2026-07-22"


def _option(
    *,
    option_type: str = "call",
    strike: float = 100.0,
    bid: float = 1.00,
    ask: float = 1.10,
    open_interest: int | None = 250,
    gamma: float | None = 0.05,
    delta: float | None = 0.45,
    expiration: str = SESSION,
    symbol: str | None = None,
    volume: int = 12,
) -> dict[str, Any]:
    greeks: dict[str, Any] = {"theta": -0.4, "vega": 0.1, "mid_iv": 0.22}
    if gamma is not None:
        greeks["gamma"] = gamma
    if delta is not None:
        greeks["delta"] = delta
    row: dict[str, Any] = {
        "option_type": option_type,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "volume": volume,
        "expiration_date": expiration,
        "greeks": greeks,
    }
    if open_interest is not None:
        row["open_interest"] = open_interest
    if symbol is not None:
        row["symbol"] = symbol
    return row


def _chain_payload(options: Any) -> dict[str, Any]:
    return {"options": {"option": options}}


def _quote_payload(last: Any = 100.05, symbol: str = "SPY") -> dict[str, Any]:
    return {"quotes": {"quote": {"symbol": symbol, "last": last}}}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch):
    """Route the two endpoints to canned payloads; record the URLs called."""

    def _install(*, chain: dict[str, Any], quote: dict[str, Any] | None = None) -> list[str]:
        calls: list[str] = []

        def fake(url: str, *, headers: Any = None, config: Any = None) -> dict[str, Any]:
            calls.append(url)
            if "/markets/quotes" in url:
                return quote if quote is not None else _quote_payload()
            if "/markets/options/chains" in url:
                return chain
            raise AssertionError(f"unexpected URL {url}")

        monkeypatch.setattr("spy_der.market_data.providers.tradier.get_json", fake)
        return calls

    return _install


def _provider(**kw: Any) -> TradierProvider:
    kw.setdefault("api_key", "test-token")
    return TradierProvider(**kw)


# --------------------------------------------------------------------------- #
# Credentials                                                                 #
# --------------------------------------------------------------------------- #
def test_provider_is_inert_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_API_KEY", raising=False)
    provider = TradierProvider()
    assert provider.configured is False
    assert provider.fetch(TS) is None


def test_access_token_env_name_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name existing deployments already use."""
    monkeypatch.delenv("TRADIER_API_KEY", raising=False)
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "from-env")
    assert TradierProvider().configured is True


def test_api_key_env_name_is_accepted_as_an_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name the shipped env template uses."""
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("TRADIER_API_KEY", "from-env")
    assert TradierProvider().configured is True


def test_access_token_wins_when_both_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "primary")
    monkeypatch.setenv("TRADIER_API_KEY", "alias")
    assert TradierProvider()._api_key == "primary"


# --------------------------------------------------------------------------- #
# Base URL normalization                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "configured",
    ["https://api.tradier.com", "https://api.tradier.com/", "https://api.tradier.com/v1"],
)
def test_base_url_never_doubles_the_version_segment(
    configured: str, transport: Any
) -> None:
    """Deployments configure this with and without `/v1`; both must work."""
    calls = transport(chain=_chain_payload([_option()]))
    _provider(base_url=configured).fetch(TS)
    assert calls, "no request was made"
    for url in calls:
        assert "/v1/v1/" not in url
        assert url.startswith("https://api.tradier.com/v1/")


def test_base_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADIER_BASE_URL", "https://sandbox.tradier.com/v1")
    assert TradierProvider(api_key="k")._base_url == "https://sandbox.tradier.com"


# --------------------------------------------------------------------------- #
# Chain mapping                                                               #
# --------------------------------------------------------------------------- #
def test_live_chain_produces_a_raw_tick(transport: Any) -> None:
    transport(chain=_chain_payload([_option(option_type="call"), _option(option_type="put")]))
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.provider == "tradier"
    assert len(tick.option_chain) == 2
    assert tick.underlying_price == Decimal("100.05")


def test_quotes_are_flagged_live(transport: Any) -> None:
    """Tradier quotes are a real market — no day-close fallback exists here."""
    transport(chain=_chain_payload([_option()]))
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.option_chain[0].quality_flags == ("live_quote",)


def test_greeks_and_open_interest_are_mapped(transport: Any) -> None:
    transport(chain=_chain_payload([_option()]))
    quote = _provider().fetch(TS).option_chain[0]  # type: ignore[union-attr]
    assert quote.gamma == 0.05
    assert quote.delta == 0.45
    assert quote.theta == -0.4
    assert quote.vega == 0.1
    assert quote.implied_volatility == 0.22
    assert quote.open_interest == 250
    assert quote.volume == 12


def test_mark_is_the_midpoint(transport: Any) -> None:
    transport(chain=_chain_payload([_option(bid=1.00, ask=1.20)]))
    quote = _provider().fetch(TS).option_chain[0]  # type: ignore[union-attr]
    assert quote.mark == Decimal("1.10")
    assert isinstance(quote.mark, Decimal)


def test_vendor_contract_symbol_is_preserved(transport: Any) -> None:
    transport(chain=_chain_payload([_option(symbol="SPY260722C00100000")]))
    quote = _provider().fetch(TS).option_chain[0]  # type: ignore[union-attr]
    assert quote.contract.contract_id == "SPY260722C00100000"


def test_missing_symbol_gets_a_synthetic_id(transport: Any) -> None:
    transport(chain=_chain_payload([_option(strike=100.0, option_type="put")]))
    quote = _provider().fetch(TS).option_chain[0]  # type: ignore[union-attr]
    assert quote.contract.contract_id == "SPY260722P00100000"


def test_option_type_is_mapped(transport: Any) -> None:
    transport(chain=_chain_payload([_option(option_type="put")]))
    quote = _provider().fetch(TS).option_chain[0]  # type: ignore[union-attr]
    assert quote.contract.option_type is OptionType.PUT


# --------------------------------------------------------------------------- #
# Vendor quirk: a one-element collection is returned bare                     #
# --------------------------------------------------------------------------- #
def test_single_contract_chain_is_not_a_list(transport: Any) -> None:
    """Tradier returns `{"option": {...}}` for one row, `[...]` otherwise.

    Iterating the dict form walks its keys and silently yields nothing.
    """
    transport(chain=_chain_payload(_option()))
    tick = _provider().fetch(TS)
    assert tick is not None
    assert len(tick.option_chain) == 1


def test_single_quote_response_is_not_a_list(transport: Any) -> None:
    transport(
        chain=_chain_payload([_option()]),
        quote={"quotes": {"quote": {"symbol": "SPY", "last": 101.25}}},
    )
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.underlying_price == Decimal("101.25")


# --------------------------------------------------------------------------- #
# Fail-closed on incomplete rows                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kwargs",
    [
        {"gamma": None},
        {"delta": None},
        {"open_interest": None},
    ],
    ids=["no_gamma", "no_delta", "no_open_interest"],
)
def test_incomplete_row_is_dropped_not_defaulted(transport: Any, kwargs: Any) -> None:
    """A zeroed gamma is not neutral — it reshapes the GEX surface silently."""
    transport(chain=_chain_payload([_option(**kwargs), _option(strike=101.0)]))
    tick = _provider().fetch(TS)
    assert tick is not None
    assert len(tick.option_chain) == 1
    assert tick.option_chain[0].contract.strike == Decimal("101.0")


@pytest.mark.parametrize(
    "bid,ask",
    [(0.0, 1.0), (1.0, 0.0), (1.5, 1.0)],
    ids=["zero_bid", "zero_ask", "crossed"],
)
def test_unusable_quote_is_dropped(transport: Any, bid: float, ask: float) -> None:
    transport(chain=_chain_payload([_option(bid=bid, ask=ask)]))
    assert _provider().fetch(TS) is None


def test_other_expirations_are_filtered_out(transport: Any) -> None:
    transport(
        chain=_chain_payload([_option(), _option(strike=101.0, expiration="2026-08-21")])
    )
    tick = _provider().fetch(TS)
    assert tick is not None
    assert len(tick.option_chain) == 1


def test_empty_chain_yields_no_tick(transport: Any) -> None:
    transport(chain=_chain_payload([]))
    assert _provider().fetch(TS) is None


def test_vendor_failure_is_failover_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """CompositeFeed reads None as 'try the next provider'."""
    from spy_der.market_data.providers._http import ProviderHttpError

    def boom(*a: Any, **k: Any) -> Any:
        raise ProviderHttpError("HTTP 503 from https://api.tradier.com/...", status=503)

    monkeypatch.setattr("spy_der.market_data.providers.tradier.get_json", boom)
    assert _provider().fetch(TS) is None


# --------------------------------------------------------------------------- #
# Spot                                                                        #
# --------------------------------------------------------------------------- #
def test_unusable_vendor_spot_falls_back_to_put_call_parity(transport: Any) -> None:
    chain = [
        _option(option_type="call", strike=100.0, bid=1.00, ask=1.10),
        _option(option_type="put", strike=100.0, bid=0.90, ask=1.00),
    ]
    transport(chain=_chain_payload(chain), quote={"quotes": {"quote": {}}})
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.underlying_price > 0


def test_no_recoverable_spot_yields_no_tick(transport: Any) -> None:
    """A fabricated spot would reshape every moneyness band downstream.

    Puts only: parity needs a paired strike, and the estimator's delta fallback
    considers calls only, so there is genuinely nothing to recover from.
    """
    transport(
        chain=_chain_payload([_option(option_type="put", strike=100.0)]),
        quote={"quotes": {"quote": {}}},
    )
    assert _provider().fetch(TS) is None


def test_zero_vendor_spot_is_not_trusted(transport: Any) -> None:
    """A vendor `last` of 0 must not become the underlying price."""
    transport(chain=_chain_payload([_option()]), quote=_quote_payload(last=0))
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.underlying_price > 0  # recovered from the chain, not the 0


def test_vendor_spot_is_preferred_over_the_chain_estimate(transport: Any) -> None:
    transport(chain=_chain_payload([_option()]), quote=_quote_payload(last=123.45))
    tick = _provider().fetch(TS)
    assert tick is not None
    assert tick.underlying_price == Decimal("123.45")


# --------------------------------------------------------------------------- #
# Factory registration                                                        #
# --------------------------------------------------------------------------- #
def test_tradier_is_available_not_pending() -> None:
    assert "tradier" in AVAILABLE_PROVIDERS
    assert "tradier" not in PENDING_PROVIDERS


def test_chain_builds_tradier_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "token")
    chain = build_provider_chain(["tradier"])
    assert [p.name for p in chain.providers] == ["tradier"]
    assert chain.is_empty is False


def test_chain_skips_tradier_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unconfigured is skipped with a note, never fatal."""
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_API_KEY", raising=False)
    chain = build_provider_chain(["tradier"])
    assert chain.is_empty
    assert chain.skipped == (("tradier", "no credential"),)


def test_failover_order_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "token")
    monkeypatch.setenv("MASSIVE_API_KEY", "key")
    chain = build_provider_chain(["tradier", "massive"])
    assert [p.name for p in chain.providers] == ["tradier", "massive"]
