"""Build the configured provider chain by name.

``config.yaml``'s ``market_data.providers`` list is an ordered failover chain;
this turns those names into provider instances. Two properties matter:

* **Order is preserved.** ``CompositeFeed`` tries members in sequence, so the
  list is a priority, not a set.
* **Unconfigured providers are skipped, not fatal.** A provider whose credential
  is absent reports ``configured == False`` and is dropped with a note. That way
  a host with only one vendor's key still runs, instead of failing to start —
  and :func:`build_provider_chain` reports which names were skipped so the
  omission is visible rather than silent.

Adapters not yet ported raise :class:`UnknownProviderError` rather than being
silently ignored, so a typo or a premature config change fails loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spy_der.market_data.providers._http import HttpConfig
from spy_der.market_data.providers.base import MarketDataProvider
from spy_der.market_data.providers.massive import MassiveProvider
from spy_der.market_data.providers.tradier import TradierProvider
from spy_der.market_data.providers.yahoo import YahooProvider

__all__ = [
    "AVAILABLE_PROVIDERS",
    "CHAINLESS_PROVIDERS",
    "PENDING_PROVIDERS",
    "ProviderChain",
    "UnknownProviderError",
    "build_provider_chain",
    "build_settlement_provider",
]

#: Adapters that exist today.
AVAILABLE_PROVIDERS: frozenset[str] = frozenset({"massive", "tradier", "yahoo"})

#: Named in docs/config but not yet ported. Listed explicitly so requesting one
#: produces a clear message instead of "unknown provider".
PENDING_PROVIDERS: frozenset[str] = frozenset({"tastytrade"})

#: Adapters that serve no option chain. They are legitimate settlement sources
#: but cannot drive a tick on their own, so putting one in the primary failover
#: chain would produce snapshots that fail closed on every tick.
CHAINLESS_PROVIDERS: frozenset[str] = frozenset({"yahoo"})


class UnknownProviderError(ValueError):
    """A configured provider name has no adapter."""


@dataclass(frozen=True, slots=True)
class ProviderChain:
    """The resolved chain plus what was left out and why."""

    providers: tuple[MarketDataProvider, ...] = ()
    skipped: tuple[tuple[str, str], ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not self.providers

    def describe(self) -> str:
        active = ", ".join(p.name for p in self.providers) or "none"
        text = f"active: {active}"
        if self.skipped:
            text += "; skipped: " + ", ".join(f"{n} ({why})" for n, why in self.skipped)
        return text


def _build_one(
    name: str, *, symbol: str, http: HttpConfig | None
) -> MarketDataProvider:
    if name == "massive":
        return MassiveProvider(symbol=symbol, http=http)
    if name == "tradier":
        return TradierProvider(symbol=symbol, http=http)
    if name == "yahoo":
        return YahooProvider(symbol=symbol, http=http)
    if name in PENDING_PROVIDERS:
        msg = (
            f"provider {name!r} is not ported yet; "
            "see migrations/inventory/zerodte_disposition.json"
        )
        raise UnknownProviderError(msg)
    msg = f"unknown provider {name!r}; available: {sorted(AVAILABLE_PROVIDERS)}"
    raise UnknownProviderError(msg)


def build_provider_chain(
    names: list[str],
    *,
    symbol: str = "SPY",
    http: HttpConfig | None = None,
) -> ProviderChain:
    """Resolve ``names`` into an ordered chain, skipping unconfigured adapters.

    A chainless adapter named here is skipped rather than accepted: it cannot
    serve an option chain, so every snapshot it produced would fail closed. It
    belongs in the settlement slot instead (:func:`build_settlement_provider`),
    and saying so beats letting the operator discover it from a day of unusable
    snapshots.
    """
    providers: list[MarketDataProvider] = []
    skipped: list[tuple[str, str]] = []
    for name in names:
        key = name.strip().lower()
        provider = _build_one(key, symbol=symbol, http=http)
        if key in CHAINLESS_PROVIDERS:
            skipped.append((name, "serves no option chain; use it as the settlement provider"))
            continue
        if not getattr(provider, "configured", True):
            skipped.append((name, "no credential"))
            continue
        providers.append(provider)
    return ProviderChain(providers=tuple(providers), skipped=tuple(skipped))


def build_settlement_provider(
    name: str | None,
    *,
    symbol: str = "SPY",
    http: HttpConfig | None = None,
) -> MarketDataProvider | None:
    """Resolve the dedicated settlement source; ``None`` when unset or unconfigured.

    Unlike the failover chain, a chainless adapter is exactly what belongs here.
    """
    if not name:
        return None
    key = name.strip().lower()
    if key == "yahoo":
        # Yahoo publishes the real CBOE indices with no entitlement, where a
        # brokerage account frequently has only the 30-day VIX — so the
        # settlement poll doubles as the volatility-surface backstop. Bars stay
        # off: the primary provider already supplies them every tick.
        provider: MarketDataProvider = YahooProvider(
            symbol=symbol,
            http=http,
            include_bars=False,
            include_volatility_indices=True,
        )
    else:
        provider = _build_one(key, symbol=symbol, http=http)
    if not getattr(provider, "configured", True):
        return None
    return provider
