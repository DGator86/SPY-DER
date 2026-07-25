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

__all__ = [
    "AVAILABLE_PROVIDERS",
    "PENDING_PROVIDERS",
    "ProviderChain",
    "UnknownProviderError",
    "build_provider_chain",
]

#: Adapters that exist today.
AVAILABLE_PROVIDERS: frozenset[str] = frozenset({"massive", "tradier"})

#: Named in docs/config but not yet ported. Listed explicitly so requesting one
#: produces a clear message instead of "unknown provider".
PENDING_PROVIDERS: frozenset[str] = frozenset({"tastytrade", "yahoo"})


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
    """Resolve ``names`` into an ordered chain, skipping unconfigured adapters."""
    providers: list[MarketDataProvider] = []
    skipped: list[tuple[str, str]] = []
    for name in names:
        provider = _build_one(name.strip().lower(), symbol=symbol, http=http)
        configured = getattr(provider, "configured", True)
        if not configured:
            skipped.append((name, "no credential"))
            continue
        providers.append(provider)
    return ProviderChain(providers=tuple(providers), skipped=tuple(skipped))
