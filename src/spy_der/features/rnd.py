"""Risk-neutral density summary (master spec §17), bounded first cut.

Breeden-Litzenberger: the risk-neutral terminal density is proportional to the
second derivative of the call price with respect to strike,
``f(K) = e^{rT} d^2C/dK^2``. For 0DTE the discount factor is ~1, so this module
recovers a normalized terminal density from the call mid-price curve via finite
differences on a uniform strike grid, clips it non-negative (no-arbitrage), and
reports its forward/mean/std/skew and P(S_T < spot).

This is the deterministic, dependency-light core of System A ``rnd_extractor.py``
(spec §17). The richer System A pipeline — total-variance fitting, smooth call
reconstruction, and arbitrage repair — is layered on in a later pass; this cut
never fabricates a density (returns ``None`` when the chain cannot support one).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from spy_der.contracts.market import CanonicalMarketSnapshot, OptionQuote, OptionType
from spy_der.contracts.structure import RndSummary

__all__ = ["compute_rnd"]

_MIN_STRIKES = 5
_GRID_POINTS = 201

# numpy renamed trapz -> trapezoid in 2.0; support both.
_trapz: Any = getattr(np, "trapezoid", getattr(np, "trapz", None))


def _mid(quote: OptionQuote) -> float | None:
    if quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid >= 0:
        return float((quote.bid + quote.ask) / 2)
    if quote.mark is not None:
        return float(quote.mark)
    if quote.last is not None:
        return float(quote.last)
    return None


def _call_curve(snapshot: CanonicalMarketSnapshot) -> tuple[list[float], list[float]]:
    by_strike: dict[float, float] = {}
    for quote in snapshot.option_chain:
        if quote.contract.option_type is not OptionType.CALL:
            continue
        mid = _mid(quote)
        if mid is None:
            continue
        by_strike[float(quote.contract.strike)] = mid
    strikes = sorted(by_strike)
    return strikes, [by_strike[k] for k in strikes]


def compute_rnd(snapshot: CanonicalMarketSnapshot) -> RndSummary | None:
    """Recover a bounded risk-neutral density summary; ``None`` if unsupported."""
    strikes, calls = _call_curve(snapshot)
    if len(strikes) < _MIN_STRIKES:
        return None

    ks = np.asarray(strikes, dtype=float)
    cs = np.asarray(calls, dtype=float)
    grid = np.linspace(ks[0], ks[-1], _GRID_POINTS)
    curve = np.interp(grid, ks, cs)

    density = np.gradient(np.gradient(curve, grid), grid)
    density = np.clip(density, 0.0, None)
    area = float(_trapz(density, grid))
    if not np.isfinite(area) or area <= 0.0:
        return None
    density = density / area

    mean = float(_trapz(grid * density, grid))
    variance = float(_trapz((grid - mean) ** 2 * density, grid))
    std = float(np.sqrt(variance)) if variance > 0 else 0.0
    if std > 0:
        skew = float(_trapz(((grid - mean) / std) ** 3 * density, grid))
    else:
        skew = 0.0

    spot = float(snapshot.underlying_price)
    mask = grid < spot
    prob_below = float(_trapz(density[mask], grid[mask])) if mask.any() else 0.0
    prob_below = min(1.0, max(0.0, prob_below))

    return RndSummary(
        forward=mean,
        mean=mean,
        std=std,
        skew=skew,
        prob_below_spot=prob_below,
        n_strikes=len(strikes),
        normalized=True,
    )

