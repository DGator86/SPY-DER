"""Options-flow features (master spec §21).

Ported from System A ``massive_feed.flow_lite`` (DGator86/0DTE @ 2186213). Both
measures come out of the option chain every provider already fetches, so this
costs no extra vendor call:

* **put/call volume ratio** — directional pressure in today's trading, as
  distinct from the positioning that open interest describes.
* **volume / open-interest participation** — how much of today's activity is new
  versus existing. A ratio well above its usual level is a participation shock:
  the chain is being repositioned rather than merely held.

Both are ``None`` when the provider supplies no volume. That is the load-bearing
part: absent flow must never read as *zero* flow, because zero put volume is a
maximally call-skewed tape, and several vendors leave volume unset outside
regular hours.
"""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.market import CanonicalMarketSnapshot, OptionType

__all__ = ["FlowState", "compute_flow"]


@dataclass(frozen=True, slots=True)
class FlowState:
    """Chain-derived flow for one snapshot."""

    call_volume: int = 0
    put_volume: int = 0
    total_open_interest: int = 0
    pcr_volume: float | None = None
    volume_oi_ratio: float | None = None

    @property
    def total_volume(self) -> int:
        return self.call_volume + self.put_volume

    @property
    def is_observed(self) -> bool:
        return self.pcr_volume is not None or self.volume_oi_ratio is not None


def compute_flow(snapshot: CanonicalMarketSnapshot) -> FlowState:
    """Put/call volume ratio and volume/OI participation for ``snapshot``."""
    call_volume = 0
    put_volume = 0
    open_interest = 0

    for quote in snapshot.option_chain:
        if quote.open_interest is not None and quote.open_interest > 0:
            open_interest += quote.open_interest
        if quote.volume is None or quote.volume < 0:
            continue
        if quote.contract.option_type is OptionType.CALL:
            call_volume += quote.volume
        else:
            put_volume += quote.volume

    total_volume = call_volume + put_volume
    # A zero call volume makes the ratio undefined rather than infinite; report
    # it as unknown instead of a sentinel that would rank as extreme skew.
    pcr = (put_volume / call_volume) if call_volume > 0 else None
    participation = (
        (total_volume / open_interest) if open_interest > 0 and total_volume > 0 else None
    )

    return FlowState(
        call_volume=call_volume,
        put_volume=put_volume,
        total_open_interest=open_interest,
        pcr_volume=pcr,
        volume_oi_ratio=participation,
    )
