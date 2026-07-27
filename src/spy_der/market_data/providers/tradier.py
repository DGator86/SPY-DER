"""Tradier brokerage options provider.

Ported from 0DTE ``tradier_feed``. SPY-DER owns the integration directly;
nothing here depends on 0DTE.

Why this adapter matters relative to :mod:`massive`: a Tradier brokerage
account returns **real-time option NBBO** with greeks and open interest, where
the Massive snapshot plan routinely has no ``last_quote`` and falls back to
``day.close``. A chain priced off yesterday's close is not tradeable data, and
the Massive adapter deliberately flags it as such. Tradier quotes arrive as
genuine two-sided markets, so they carry ``live_quote``.

Endpoints (all documented, JSON):

===============================================  =================================
``GET /v1/markets/quotes?symbols=SPY``            real-time underlying + NBBO
``GET /v1/markets/options/chains?symbol=&...``    chain with ``greeks=true``
``GET /v1/markets/timesales?symbol=&interval=``   1-minute OHLCV bars
===============================================  =================================

Three vendor quirks are handled here rather than downstream:

1. **Single-element collections are returned bare.** Tradier emits
   ``{"option": {...}}`` for a one-contract chain and ``{"option": [...]}``
   otherwise. Anything iterating the result without normalizing walks the keys
   of a dict and silently produces nothing.
2. **Greeks come from ORATS and can lag the quote by minutes.** Bid/ask are
   real-time regardless, so a row missing greeks is dropped rather than being
   priced with stale ones — the same fail-closed rule the rest of the layer uses.
3. **``timesales`` timestamps are epoch seconds in exchange-local terms.** The
   sibling ``time`` field is a *naive* ISO string in ET; parsing that instead of
   the epoch field silently produces UTC-labelled ET bars, which shifts every
   session boundary by four or five hours. Only ``timestamp`` is read.

Bars are fetched but never allowed to fail the tick: an account without market
history entitlement still returns a tradeable chain, so a bars failure degrades
the snapshot (the ``bars`` feed component reports MISSING) instead of dropping
it. The chain and the underlying quote are what make a tick usable.

Credentials come from the environment only. ``TRADIER_ACCESS_TOKEN`` is read
first because that is the name the token already has in existing deployments;
``TRADIER_API_KEY`` is accepted as an alias so the shipped env template and the
older name both work. Never hardcode a token here or anywhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.market import Bar, OptionContract, OptionQuote, OptionType
from spy_der.market_data.providers._http import HttpConfig, ProviderHttpError, get_json
from spy_der.market_data.providers.bars import (
    DEFAULT_LOOKBACK_MINUTES,
    bar_from_ohlcv,
    lookback_window,
    normalize_bars,
)
from spy_der.market_data.providers.base import MarketDataProvider, RawTick
from spy_der.market_data.providers.spot import ChainQuoteView, estimate_spot_from_chain

__all__ = ["TradierProvider"]

ET = ZoneInfo("America/New_York")

_DEFAULT_BASE_URL = "https://api.tradier.com"

#: Quality flag: Tradier quotes are a real two-sided market.
FLAG_LIVE_QUOTE = "live_quote"

#: Snapshot-level provenance for how the underlying price was obtained.
FLAG_SPOT_VENDOR_QUOTE = "spot:tradier_quote"
FLAG_SPOT_PARITY = "spot:put_call_parity"
#: Bars were requested and the vendor did not supply a usable series.
FLAG_BARS_UNAVAILABLE = "bars:unavailable"


@dataclass(frozen=True, slots=True)
class _UnderlyingQuote:
    """What ``/markets/quotes`` yields for the underlying, each part optional."""

    price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None


def _as_list(value: Any) -> list[Any]:
    """Normalize Tradier's bare-object-for-one-result shape into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _synthetic_contract_id(
    symbol: str, expiration: date, option_type: OptionType, strike: float
) -> str:
    right = "C" if option_type is OptionType.CALL else "P"
    return f"{symbol}{expiration:%y%m%d}{right}{round(strike * 1000):08d}"


class TradierProvider(MarketDataProvider):
    """Fetch a 0DTE options chain from Tradier as a :class:`RawTick`.

    ``fetch`` returns ``None`` on any vendor failure or unusable payload, which
    is how ``CompositeFeed`` knows to fail over rather than aborting the tick.
    """

    def __init__(
        self,
        *,
        symbol: str = "SPY",
        api_key: str | None = None,
        base_url: str | None = None,
        zero_dte_only: bool = True,
        http: HttpConfig | None = None,
        name: str = "tradier",
        lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    ) -> None:
        self.symbol = symbol
        self.zero_dte_only = zero_dte_only
        self.lookback_minutes = lookback_minutes
        self._name = name
        self._http = http or HttpConfig()
        self._base_url = self._resolve_base_url(base_url)
        if api_key is not None:
            self._api_key: str | None = api_key
        else:
            # TRADIER_ACCESS_TOKEN is the name the token already carries in
            # existing deployments; TRADIER_API_KEY matches the env template.
            self._api_key = (
                os.environ.get("TRADIER_ACCESS_TOKEN")
                or os.environ.get("TRADIER_API_KEY")
                or None
            )

    @staticmethod
    def _resolve_base_url(base_url: str | None) -> str:
        """Normalize to the host root, with or without a trailing ``/v1``.

        Deployments configure this both ways — the vendor's own docs use
        ``https://api.tradier.com/v1`` — and every request path here already
        starts with ``/v1``, so an unnormalized value yields ``/v1/v1/...``.
        """
        resolved = base_url or os.environ.get("TRADIER_BASE_URL") or _DEFAULT_BASE_URL
        trimmed = resolved.rstrip("/")
        if trimmed.endswith("/v1"):
            trimmed = trimmed[: -len("/v1")]
        return trimmed

    @property
    def name(self) -> str:
        return self._name

    @property
    def configured(self) -> bool:
        """False when no credential is available — the adapter is inert."""
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key or ''}"}

    # -- MarketDataProvider --------------------------------------------------
    def fetch(self, timestamp: datetime) -> RawTick | None:
        if not self.configured:
            return None
        session = timestamp.astimezone(ET).date()
        try:
            quotes = self._chain(session, timestamp)
            underlying = self._underlying()
        except ProviderHttpError:
            # Vendor failure is a failover signal, never an exception upward.
            return None

        if not quotes:
            return None

        flags: list[str] = []
        spot = underlying.price
        if spot is not None:
            flags.append(FLAG_SPOT_VENDOR_QUOTE)
        else:
            spot = self._recover_spot(quotes)
            flags.append(FLAG_SPOT_PARITY)
        if spot is None or spot <= 0:
            # No trustworthy underlying price: report unavailable rather than
            # letting a fabricated spot reshape every moneyness band downstream.
            return None

        # Bars are best-effort: see the module docstring. A history-entitlement
        # failure must not discard a perfectly good chain.
        bars = self._safe_bars(timestamp)
        if not bars:
            flags.append(FLAG_BARS_UNAVAILABLE)

        return RawTick(
            provider=self.name,
            symbol=self.symbol,
            observed_at=timestamp,
            underlying_price=spot,
            underlying_bid=underlying.bid,
            underlying_ask=underlying.ask,
            bars_1m=bars,
            option_chain=tuple(quotes),
            has_chain=True,
            quality_flags=tuple(flags),
        )

    # -- vendor calls --------------------------------------------------------
    def _underlying(self) -> _UnderlyingQuote:
        """Real-time underlying price and NBBO from ``/markets/quotes``.

        Price precedence is ``last -> close -> prevclose``: the live print when
        the market is open, then today's settle, then yesterday's. Bid and ask
        are reported independently — they are frequently absent outside regular
        hours while ``last`` is still valid, and a one-sided or crossed book is
        dropped rather than passed on as a market.
        """
        url = f"{self._base_url}/v1/markets/quotes?symbols={self.symbol}"
        payload = get_json(url, headers=self._headers(), config=self._http)
        quotes = _as_list((payload.get("quotes") or {}).get("quote"))
        for quote in quotes:
            if not isinstance(quote, dict):
                continue
            if str(quote.get("symbol", "")).upper() != self.symbol.upper():
                continue
            price: Decimal | None = None
            for field in ("last", "close", "prevclose"):
                value = _as_decimal(quote.get(field))
                if value is not None and value > 0:
                    price = value
                    break
            bid = _as_decimal(quote.get("bid"))
            ask = _as_decimal(quote.get("ask"))
            if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
                bid = ask = None
            return _UnderlyingQuote(price=price, bid=bid, ask=ask)
        return _UnderlyingQuote()

    def _safe_bars(self, timestamp: datetime) -> tuple[Bar, ...]:
        """``_bars`` with vendor failures downgraded to an empty series."""
        try:
            return self._bars(timestamp)
        except ProviderHttpError:
            return ()

    def _bars(self, timestamp: datetime) -> tuple[Bar, ...]:
        """1-minute OHLCV from ``/markets/timesales``.

        Tradier wants naive ET strings for ``start``/``end``, so the UTC window
        is converted rather than formatted directly.
        """
        start, end = lookback_window(timestamp, self.lookback_minutes)
        url = (
            f"{self._base_url}/v1/markets/timesales"
            f"?symbol={self.symbol}&interval=1min"
            f"&start={start.astimezone(ET):%Y-%m-%d %H:%M}"
            f"&end={end.astimezone(ET):%Y-%m-%d %H:%M}"
        ).replace(" ", "%20")
        payload = get_json(url, headers=self._headers(), config=self._http)
        points = _as_list((payload.get("series") or {}).get("data"))

        bars: list[Bar] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            epoch = _as_int(point.get("timestamp"))
            if epoch is None:
                continue
            bar = bar_from_ohlcv(
                datetime.fromtimestamp(epoch, tz=UTC),
                point.get("open"),
                point.get("high"),
                point.get("low"),
                point.get("close"),
                point.get("volume"),
            )
            if bar is not None:
                bars.append(bar)
        return normalize_bars(bars)

    def _chain(self, session: date, timestamp: datetime) -> list[OptionQuote]:
        url = (
            f"{self._base_url}/v1/markets/options/chains"
            f"?symbol={self.symbol}&expiration={session.isoformat()}&greeks=true"
        )
        payload = get_json(url, headers=self._headers(), config=self._http)
        options = _as_list((payload.get("options") or {}).get("option"))
        quotes: list[OptionQuote] = []
        for option in options:
            if not isinstance(option, dict):
                continue
            quote = self._quote_from_option(option, session, timestamp)
            if quote is not None:
                quotes.append(quote)
        return quotes

    def _quote_from_option(
        self, option: dict[str, Any], session: date, timestamp: datetime
    ) -> OptionQuote | None:
        """Map one chain row; ``None`` when the data is incomplete.

        Mirrors the Massive adapter's completeness rule: a row without greeks or
        open interest is dropped rather than defaulted, because a zeroed gamma
        is not a neutral value downstream — it silently reshapes the GEX surface.
        """
        side = option.get("option_type")
        strike = _as_decimal(option.get("strike"))
        open_interest = _as_int(option.get("open_interest"))
        greeks = option.get("greeks") or {}
        gamma = _as_float(greeks.get("gamma"))
        delta = _as_float(greeks.get("delta"))
        bid = _as_decimal(option.get("bid"))
        ask = _as_decimal(option.get("ask"))

        if side is None or strike is None or open_interest is None:
            return None
        if gamma is None or delta is None:
            return None
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return None

        expiration = _parse_date(option.get("expiration_date")) or session
        if self.zero_dte_only and expiration != session:
            return None

        option_type = OptionType.CALL if str(side).lower() == "call" else OptionType.PUT
        contract_id = str(option.get("symbol") or "").strip() or _synthetic_contract_id(
            self.symbol, expiration, option_type, float(strike)
        )
        return OptionQuote(
            contract=OptionContract(
                contract_id=contract_id,
                underlying_symbol=self.symbol,
                expiration=expiration,
                option_type=option_type,
                strike=strike,
            ),
            received_at=timestamp,
            source=self.name,
            bid=bid,
            ask=ask,
            mark=(bid + ask) / Decimal("2"),
            volume=_as_int(option.get("volume")),
            open_interest=open_interest,
            implied_volatility=_as_float(greeks.get("mid_iv"))
            or _as_float(greeks.get("smv_vol")),
            delta=delta,
            gamma=gamma,
            theta=_as_float(greeks.get("theta")),
            vega=_as_float(greeks.get("vega")),
            observed_at=timestamp,
            quality_flags=(FLAG_LIVE_QUOTE,),
        )

    def _recover_spot(self, quotes: list[OptionQuote]) -> Decimal | None:
        """Backstop when /markets/quotes is unusable: ATM put-call parity."""
        views: list[ChainQuoteView] = []
        for quote in quotes:
            if quote.bid is None or quote.ask is None:
                continue
            views.append(
                ChainQuoteView(
                    is_call=quote.contract.option_type is OptionType.CALL,
                    strike=float(quote.contract.strike),
                    bid=float(quote.bid),
                    ask=float(quote.ask),
                    delta=quote.delta,
                )
            )
        return estimate_spot_from_chain(views)
