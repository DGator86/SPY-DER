"""Massive (Polygon-compatible) options-snapshot provider.

Ported from 0DTE ``massive_feed`` (DGator86/0DTE @ 6393cd1). SPY-DER owns the
provider integration directly; nothing here depends on 0DTE.

Endpoint: ``GET /v3/snapshot/options/{underlyingAsset}`` — Massive mirrors the
Polygon path.

Field mappings confirmed against the live feed, and the reason several of them
have fallbacks:

===========================================  ==========================================
``details.contract_type`` / ``strike_price``  present
``details.expiration_date``                   present — used to filter to today's expiry
``greeks.gamma`` / ``greeks.delta``           present
``open_interest``                             present
``last_quote.bid`` / ``ask``                   **absent** -> falls back to ``day.close``
``underlying_asset.price``                     **absent** -> recovered via put-call parity
===========================================  ==========================================

Two behaviors are load-bearing and preserved deliberately:

1. **A day-close fallback quote is tagged, not silently used as an NBBO.** Rows
   priced off ``day.close`` carry a ``day_close_fallback`` quality flag and set
   bid == ask. Downstream, the execution guard's fill-probability floor and the
   candidate factory's spread-based quote quality both see a degenerate spread,
   so a chain with no live quotes cannot be traded on as though it had them.
2. **Missing spot yields no snapshot rather than a guessed one.** See
   :mod:`spy_der.market_data.providers.spot`.

Credentials come from the environment only (``MASSIVE_API_KEY``). Never hardcode
a key here or anywhere else.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.market import OptionContract, OptionQuote, OptionType
from spy_der.market_data.providers._http import HttpConfig, ProviderHttpError, get_json
from spy_der.market_data.providers.base import MarketDataProvider, RawTick
from spy_der.market_data.providers.spot import ChainQuoteView, estimate_spot_from_chain

__all__ = ["MassiveProvider"]

ET = ZoneInfo("America/New_York")

_DEFAULT_BASE_URL = "https://api.massive.com"
#: Safety cap on pagination — a runaway ``next_url`` chain must not hang a tick.
_MAX_PAGES = 25
_PAGE_LIMIT = 250

#: Quality flags surfaced on each quote so downstream layers can see provenance.
FLAG_LIVE_QUOTE = "live_quote"
FLAG_DAY_CLOSE_FALLBACK = "day_close_fallback"


class MassiveProvider(MarketDataProvider):
    """Fetch a 0DTE options chain from Massive as a :class:`RawTick`.

    ``fetch`` returns ``None`` on any vendor failure or unusable payload, which
    is how ``CompositeFeed`` knows to fail over rather than aborting the tick.
    """

    def __init__(
        self,
        *,
        symbol: str = "SPY",
        api_key: str | None = None,
        base_url: str | None = None,
        auth_mode: str | None = None,
        zero_dte_only: bool = True,
        http: HttpConfig | None = None,
        name: str = "massive",
    ) -> None:
        self.symbol = symbol
        self.zero_dte_only = zero_dte_only
        self._name = name
        self._http = http or HttpConfig()
        resolved_base = base_url or os.environ.get("MASSIVE_BASE_URL") or _DEFAULT_BASE_URL
        self._base_url = resolved_base.rstrip("/")
        # Massive authenticates by Bearer header or ?apiKey= query param. Default
        # Bearer; set MASSIVE_AUTH=query if the host returns 401 on Bearer.
        self._auth_mode = (auth_mode or os.environ.get("MASSIVE_AUTH") or "bearer").lower()
        self._api_key = api_key if api_key is not None else os.environ.get("MASSIVE_API_KEY")

    @property
    def name(self) -> str:
        return self._name

    @property
    def configured(self) -> bool:
        """False when no credential is available — the adapter is inert."""
        return bool(self._api_key)

    # -- request building ----------------------------------------------------
    def _first_url(self, session: date) -> str:
        url = f"{self._base_url}/v3/snapshot/options/{self.symbol}?limit={_PAGE_LIMIT}"
        if self.zero_dte_only:
            # Filter server-side: the snapshot is otherwise the entire
            # multi-expiry chain (thousands of contracts across many pages) of
            # which all but one expiry is discarded. The client-side check in
            # `_rows` stays as a backstop in case the parameter is ignored.
            url += f"&expiration_date={session.isoformat()}"
        return url

    def _authorize(self, url: str) -> tuple[str, dict[str, str]]:
        key = self._api_key or ""
        if self._auth_mode == "query":
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}apiKey={key}", {}
        return url, {"Authorization": f"Bearer {key}"}

    # -- MarketDataProvider --------------------------------------------------
    def fetch(self, timestamp: datetime) -> RawTick | None:
        if not self.configured:
            return None
        session = timestamp.astimezone(ET).date()
        try:
            quotes, vendor_spot = self._collect(session, timestamp)
        except ProviderHttpError:
            # Vendor failure is a failover signal, never an exception upward.
            return None

        if not quotes:
            return None
        spot = vendor_spot if vendor_spot is not None else self._recover_spot(quotes)
        if spot is None or spot <= 0:
            # No trustworthy underlying price: report unavailable rather than
            # letting a fabricated spot reshape every moneyness band downstream.
            return None

        return RawTick(
            provider=self.name,
            symbol=self.symbol,
            observed_at=timestamp,
            underlying_price=spot,
            bars_1m=(),
            option_chain=tuple(quotes),
            has_chain=True,
        )

    def _collect(
        self, session: date, timestamp: datetime
    ) -> tuple[list[OptionQuote], Decimal | None]:
        """Walk the paginated snapshot, mapping contracts to canonical quotes."""
        url: str | None = self._first_url(session)
        quotes: list[OptionQuote] = []
        vendor_spot: Decimal | None = None
        pages = 0

        while url and pages < _MAX_PAGES:
            request_url, headers = self._authorize(url)
            payload = get_json(request_url, headers=headers, config=self._http)
            results = payload.get("results")
            if not isinstance(results, list):
                break

            for contract in results:
                if not isinstance(contract, dict):
                    continue
                # underlying_asset.price is authoritative when present; it is
                # confirmed absent on the live snapshot, so in practice spot is
                # recovered post-loop via ATM put-call parity.
                price = (contract.get("underlying_asset") or {}).get("price")
                if price:
                    vendor_spot = Decimal(str(price))
                quote = self._quote_from_contract(contract, session, timestamp)
                if quote is not None:
                    quotes.append(quote)

            next_url = payload.get("next_url")
            url = str(next_url) if next_url else None
            pages += 1

        return quotes, vendor_spot

    def _quote_from_contract(
        self, contract: dict[str, Any], session: date, timestamp: datetime
    ) -> OptionQuote | None:
        """Map one snapshot contract; ``None`` when the data is incomplete."""
        details = contract.get("details") or {}
        greeks = contract.get("greeks") or {}

        side = details.get("contract_type")  # "call" | "put"
        strike = details.get("strike_price")
        gamma = greeks.get("gamma")
        delta = greeks.get("delta")
        open_interest = contract.get("open_interest")
        if side is None or strike is None or gamma is None or delta is None:
            return None
        if open_interest is None:
            return None

        expiration_raw = details.get("expiration_date")
        expiration = _parse_date(expiration_raw) or session
        if self.zero_dte_only and expiration != session:
            return None

        option_type = OptionType.CALL if str(side).lower() == "call" else OptionType.PUT
        priced = self._price(contract)
        if priced is None:
            return None
        bid, ask, flag = priced

        contract_id = str(details.get("ticker") or "").strip() or _synthetic_contract_id(
            self.symbol, expiration, option_type, float(strike)
        )
        return OptionQuote(
            contract=OptionContract(
                contract_id=contract_id,
                underlying_symbol=self.symbol,
                expiration=expiration,
                option_type=option_type,
                strike=Decimal(str(strike)),
            ),
            received_at=timestamp,
            source=self.name,
            bid=bid,
            ask=ask,
            mark=(bid + ask) / Decimal("2"),
            volume=_as_int((contract.get("day") or {}).get("volume")),
            open_interest=_as_int(open_interest),
            implied_volatility=_as_float(greeks.get("implied_volatility"))
            or _as_float(contract.get("implied_volatility")),
            delta=_as_float(delta),
            gamma=_as_float(gamma),
            theta=_as_float(greeks.get("theta")),
            vega=_as_float(greeks.get("vega")),
            observed_at=timestamp,
            quality_flags=(flag,),
        )

    @staticmethod
    def _price(contract: dict[str, Any]) -> tuple[Decimal, Decimal, str] | None:
        """``(bid, ask, quality_flag)``, or ``None`` when unpriceable.

        A real two-sided quote wins. Otherwise ``day.close`` stands in with
        bid == ask and a ``day_close_fallback`` flag — a degenerate spread that
        downstream quote-quality and fill-probability checks will penalize, which
        is the point: a stale close is not an NBBO and must not be traded as one.
        """
        quote = contract.get("last_quote") or {}
        bid = _as_float(quote.get("bid"))
        ask = _as_float(quote.get("ask"))
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return Decimal(f"{bid:.4f}"), Decimal(f"{ask:.4f}"), FLAG_LIVE_QUOTE

        close = _as_float((contract.get("day") or {}).get("close"))
        if close is None or close <= 0:
            return None
        priced = Decimal(f"{close:.4f}")
        return priced, priced, FLAG_DAY_CLOSE_FALLBACK

    @staticmethod
    def _recover_spot(quotes: list[OptionQuote]) -> Decimal | None:
        views = [
            ChainQuoteView(
                is_call=q.contract.option_type is OptionType.CALL,
                strike=float(q.contract.strike),
                bid=float(q.bid or 0),
                ask=float(q.ask or 0),
                delta=q.delta,
            )
            for q in quotes
            if q.bid is not None and q.ask is not None
        ]
        return estimate_spot_from_chain(views)

    def live_quote_fraction(self, quotes: list[OptionQuote]) -> float:
        """Share of ``quotes`` carrying a real two-sided market.

        Exposed so the runtime can flag a chain that is entirely day-close
        fallback — not tradeable, and usually a real-time entitlement problem
        rather than a code fault.
        """
        if not quotes:
            return 0.0
        live = sum(1 for q in quotes if FLAG_LIVE_QUOTE in q.quality_flags)
        return live / len(quotes)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/inf would propagate into contracts that require finite greeks.
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _synthetic_contract_id(
    symbol: str, expiration: date, option_type: OptionType, strike: float
) -> str:
    """OCC-style id for a vendor row that omits ``details.ticker``."""
    side = "C" if option_type is OptionType.CALL else "P"
    return f"{symbol}{expiration:%y%m%d}{side}{round(strike * 1000):08d}"
