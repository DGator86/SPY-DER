"""Yahoo Finance backstop: settlement, bars and the CBOE volatility indices.

Ported from System A ``yahoo_feed`` (DGator86/0DTE @ 2186213). It exists because
three of the four data roles the pipeline needs are commodities that should never
take the system offline — the underlying price, 1-minute bars, and the settlement
close — while the hard role, real-time 0DTE option NBBO with greeks, stays with a
brokerage.

**This provider serves no option chain, by design.** ``has_chain`` is ``False``,
so if it ever reached the front of the failover chain the resulting snapshot
would report ``option_chain`` MISSING and fail closed rather than looking healthy
while being untradeable. Its place is the ``settlement_provider`` slot, where
``CompositeFeed`` uses it as settlement's dedicated source.

It also upgrades the volatility surface: Yahoo publishes the real CBOE indices
(``^VIX9D``, ``^VIX``, ``^VIX3M``, ``^VVIX``) with no entitlement, where a
brokerage account frequently has only the 30-day VIX.

No credentials — the chart endpoint is public. A browser ``User-Agent`` is
required or Yahoo answers 403/429; that is a vendor quirk, not an attempt to
disguise the client.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.market import Bar, VolatilityTermStructure
from spy_der.market_data.providers._http import HttpConfig, ProviderHttpError, get_json
from spy_der.market_data.providers.bars import (
    DEFAULT_LOOKBACK_MINUTES,
    bar_from_ohlcv,
    lookback_window,
    normalize_bars,
)
from spy_der.market_data.providers.base import MarketDataProvider, RawTick

__all__ = ["VIX_TICKERS", "YahooProvider"]

ET = ZoneInfo("America/New_York")

_DEFAULT_BASE_URL = "https://query1.finance.yahoo.com"

#: Yahoo serves the CBOE volatility indices under caret tickers.
VIX_TICKERS: dict[str, str] = {
    "vix": "^VIX",
    "vix9d": "^VIX9D",
    "vix3m": "^VIX3M",
    "vvix": "^VVIX",
}

#: Yahoo retains 1-minute history for about 8 days regardless of what is asked.
_INTRADAY_RANGE = "8d"

#: Quality flags this adapter contributes.
FLAG_SPOT_YAHOO_CHART = "spot:yahoo_chart"
FLAG_BARS_UNAVAILABLE = "bars:unavailable"


class YahooProvider(MarketDataProvider):
    """Settlement and volatility-index backstop. Serves no option chain."""

    def __init__(
        self,
        *,
        symbol: str = "SPY",
        base_url: str | None = None,
        http: HttpConfig | None = None,
        name: str = "yahoo",
        lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
        include_bars: bool = False,
        include_volatility_indices: bool = False,
    ) -> None:
        # Both extras default off because the common deployment uses this as the
        # settlement provider, which is polled every tick and needs exactly one
        # request. Turning both on unconditionally would cost six.
        self.symbol = symbol
        self.lookback_minutes = lookback_minutes
        self.include_bars = include_bars
        self.include_volatility_indices = include_volatility_indices
        self._name = name
        self._http = http or HttpConfig()
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")

    @property
    def name(self) -> str:
        return self._name

    @property
    def configured(self) -> bool:
        """Always true: the endpoint is public, so there is nothing to configure."""
        return True

    @staticmethod
    def _headers() -> dict[str, str]:
        # Yahoo rejects requests without a browser User-Agent.
        return {"User-Agent": "Mozilla/5.0 (compatible; spy-der/1.0)"}

    def _chart(self, symbol: str, params: dict[str, str]) -> dict[str, Any]:
        """One chart request, unwrapped to its single result object."""
        query = urllib.parse.urlencode(params)
        url = f"{self._base_url}/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
        payload = get_json(url, headers=self._headers(), config=self._http)
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            raise ProviderHttpError(f"unexpected chart payload for {symbol}")
        if chart.get("error"):
            raise ProviderHttpError(f"chart error for {symbol}")
        results = chart.get("result")
        if not isinstance(results, list) or not results:
            raise ProviderHttpError(f"no chart result for {symbol}")
        first = results[0]
        if not isinstance(first, dict):
            raise ProviderHttpError(f"malformed chart result for {symbol}")
        return first

    # -- MarketDataProvider --------------------------------------------------
    def fetch(self, timestamp: datetime) -> RawTick | None:
        try:
            price = self._spot()
        except ProviderHttpError:
            return None
        if price is None or price <= 0:
            return None

        bars = self._safe_bars(timestamp) if self.include_bars else ()
        flags = [FLAG_SPOT_YAHOO_CHART]
        if self.include_bars and not bars:
            flags.append(FLAG_BARS_UNAVAILABLE)

        return RawTick(
            provider=self.name,
            symbol=self.symbol,
            observed_at=timestamp,
            underlying_price=price,
            bars_1m=bars,
            option_chain=(),
            # No option chain, ever. See the module docstring.
            has_chain=False,
            quality_flags=tuple(flags),
            volatility_term_structure=(
                self.safe_volatility_term_structure()
                if self.include_volatility_indices
                else None
            ),
        )

    # -- roles ---------------------------------------------------------------
    def _spot(self) -> Decimal | None:
        meta = self._chart(self.symbol, {"interval": "1d", "range": "1d"}).get("meta")
        if not isinstance(meta, dict):
            return None
        for key in ("regularMarketPrice", "previousClose"):
            value = _as_float(meta.get(key))
            if value is not None and value > 0:
                return Decimal(f"{value:.4f}")
        return None

    def _safe_bars(self, timestamp: datetime) -> tuple[Bar, ...]:
        try:
            return self._bars(timestamp)
        except ProviderHttpError:
            return ()

    def _bars(self, timestamp: datetime) -> tuple[Bar, ...]:
        """1-minute OHLCV. Yahoo pads empty minutes with nulls; those are dropped."""
        result = self._chart(
            self.symbol,
            {"interval": "1m", "range": _INTRADAY_RANGE, "includePrePost": "false"},
        )
        stamps = result.get("timestamp")
        quote = _first_quote(result)
        if not isinstance(stamps, list) or quote is None:
            return ()

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        start, _ = lookback_window(timestamp, self.lookback_minutes)
        bars: list[Bar] = []
        for i, epoch in enumerate(stamps):
            if not isinstance(epoch, (int, float)):
                continue
            when = datetime.fromtimestamp(float(epoch), tz=UTC)
            if when < start:
                continue
            bar = bar_from_ohlcv(
                when,
                _at(opens, i),
                _at(highs, i),
                _at(lows, i),
                _at(closes, i),
                _at(volumes, i),
            )
            if bar is not None:
                bars.append(bar)
        return normalize_bars(bars)

    def safe_volatility_term_structure(self) -> VolatilityTermStructure | None:
        """The CBOE indices, or ``None`` when even the 30-day VIX is unavailable.

        Each leg is fetched independently and a failed leg is simply absent, so
        an unentitled or delisted index costs one field rather than the whole
        structure. Legs are *not* backfilled from VIX — see
        :class:`~spy_der.contracts.market.VolatilityTermStructure`.
        """
        levels: dict[str, float] = {}
        for key, ticker in VIX_TICKERS.items():
            try:
                meta = self._chart(ticker, {"interval": "1d", "range": "1d"}).get("meta")
            except ProviderHttpError:
                continue
            if not isinstance(meta, dict):
                continue
            value = _as_float(meta.get("regularMarketPrice")) or _as_float(
                meta.get("previousClose")
            )
            if value is not None and value > 0:
                levels[key] = value

        if "vix" not in levels:
            return None
        return VolatilityTermStructure(
            vix=levels["vix"],
            vix9d=levels.get("vix9d"),
            vix3m=levels.get("vix3m"),
            vvix=levels.get("vvix"),
            source=self.name,
        )

    def settlement_price(self, session_date: str) -> Decimal | None:
        """Official close for ``session_date`` (YYYY-MM-DD), or ``None``.

        A month of daily bars is pulled and the matching session selected, so
        this answers for any recent date rather than only the latest one — which
        is what a settlement backfill after an outage needs.
        """
        try:
            result = self._chart(self.symbol, {"interval": "1d", "range": "1mo"})
        except ProviderHttpError:
            return None
        stamps = result.get("timestamp")
        quote = _first_quote(result)
        if not isinstance(stamps, list) or quote is None:
            return None
        closes = quote.get("close") or []
        for i, epoch in enumerate(stamps):
            if not isinstance(epoch, (int, float)):
                continue
            close = _as_float(_at(closes, i))
            if close is None or close <= 0:
                continue
            when = datetime.fromtimestamp(float(epoch), tz=UTC).astimezone(ET)
            if when.date().isoformat() == session_date:
                return Decimal(f"{close:.4f}")
        return None


def _first_quote(result: dict[str, Any]) -> dict[str, Any] | None:
    """Yahoo nests OHLCV under ``indicators.quote[0]``."""
    indicators = result.get("indicators")
    if not isinstance(indicators, dict):
        return None
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes:
        return None
    first = quotes[0]
    return first if isinstance(first, dict) else None


def _at(values: Any, index: int) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed
