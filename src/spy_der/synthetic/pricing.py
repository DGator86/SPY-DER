"""Black (forward) option pricing and smile shape for synthetic chains.

Ported from 0DTE ``rnd_extractor._bs_call_fwd`` / ``matrix_universe._smile_vol``
(DGator86/0DTE @ 6393cd1). The conventions are preserved verbatim so a
synthetic chain repriced here matches the one System A produced:

* Undiscounted (forward) Black pricing parameterized by **total** vol
  ``s = sigma * sqrt(T)``, not annualized sigma.
* Per-strike vol from log-moneyness ``k = ln(K/F)``::

      s(k) = s_atm - skew*k + curv*k^2 + wing*|k|

  The linear term is the direction-coherent skew; the quadratic term is smile
  convexity; the ``|k|`` term lifts both wings. Shape beyond a single slope, so
  the Dojo can exercise convexity- and wing-sensitive structures.

``math.erf`` supplies the normal CDF, so this module has no SciPy dependency.
"""

from __future__ import annotations

import math

__all__ = [
    "black_call_forward",
    "black_greeks_forward",
    "black_put_forward",
    "norm_cdf",
    "norm_pdf",
    "smile_vol",
]

_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """Standard normal CDF via ``math.erf`` (no SciPy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def smile_vol(
    s_atm: float,
    skew: float,
    curv: float,
    wing: float,
    k: float,
    min_vol: float,
) -> float:
    """Per-strike total vol from log-moneyness ``k = ln(K/F)``.

    Positive ``skew`` raises put-strike vol (put-heavy); negative is a call bid.
    """
    return max(s_atm - skew * k + curv * k * k + wing * abs(k), min_vol)


def black_call_forward(forward: float, strike: float, total_vol: float) -> float:
    """Undiscounted (forward) Black call value with total vol ``s``."""
    if forward <= 0.0 or strike <= 0.0:
        return max(forward - strike, 0.0)
    if total_vol <= 0.0:
        return max(forward - strike, 0.0)
    d1 = math.log(forward / strike) / total_vol + 0.5 * total_vol
    d2 = d1 - total_vol
    return forward * norm_cdf(d1) - strike * norm_cdf(d2)


def black_put_forward(forward: float, strike: float, total_vol: float) -> float:
    """Forward Black put value — put-call parity on undiscounted values."""
    return black_call_forward(forward, strike, total_vol) - forward + strike


def black_greeks_forward(
    forward: float,
    strike: float,
    total_vol: float,
    *,
    years: float,
    is_call: bool,
) -> tuple[float, float, float, float]:
    """``(delta, gamma, theta, vega)`` for a forward-Black option.

    ``delta`` and ``gamma`` are with respect to the forward. ``theta`` is per
    calendar year (negative for long options) and ``vega`` is per 1.00 of
    annualized vol, both derived from ``total_vol = sigma * sqrt(years)``.
    Degenerate inputs (zero vol or zero time) return zeroed greeks apart from a
    step delta, which is what an expiring option actually has.
    """
    if forward <= 0.0 or strike <= 0.0 or total_vol <= 0.0 or years <= 0.0:
        if is_call:
            return (1.0 if forward > strike else 0.0, 0.0, 0.0, 0.0)
        return (-1.0 if forward < strike else 0.0, 0.0, 0.0, 0.0)

    d1 = math.log(forward / strike) / total_vol + 0.5 * total_vol
    d2 = d1 - total_vol
    pdf_d1 = norm_pdf(d1)
    sigma = total_vol / math.sqrt(years)

    delta = norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0
    gamma = pdf_d1 / (forward * total_vol)
    # d/dT of the forward price at fixed sigma; negative for long options.
    theta = -(forward * pdf_d1 * sigma) / (2.0 * math.sqrt(years))
    vega = forward * pdf_d1 * math.sqrt(years)
    # d2 participates only through the pricing identity above; keeping the bind
    # explicit documents that the greeks are the same parameterization.
    del d2
    return (delta, gamma, theta, vega)
