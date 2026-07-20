"""Deterministic fill-fraction priors (System A prediction/models/fill.py)."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_FILL_BY_N_LEGS",
    "FAMILY_FILL_PRIOR",
    "FillPriorConfig",
    "base_fill_fraction",
    "fill_fraction_for",
]

DEFAULT_FILL_BY_N_LEGS: dict[int, float] = {1: 0.35, 2: 0.50, 3: 0.55, 4: 0.65}

FAMILY_FILL_PRIOR: dict[str, float] = {
    "long_call": 0.35,
    "long_put": 0.35,
    "bull_put_credit_spread": 0.50,
    "bear_call_credit_spread": 0.50,
    "put_credit": 0.50,
    "call_credit": 0.50,
    "call_debit_spread": 0.50,
    "put_debit_spread": 0.50,
    "long_call_spread": 0.50,
    "long_put_spread": 0.50,
    "long_strangle": 0.55,
    "long_straddle": 0.55,
    "bounded_broken_wing_butterfly": 0.55,
    "broken_wing": 0.55,
    "bounded_backspread_call": 0.55,
    "bounded_backspread_put": 0.55,
    "backspread_call": 0.55,
    "backspread_put": 0.55,
    "iron_butterfly": 0.65,
    "iron_fly": 0.65,
    "iron_condor": 0.65,
}


@dataclass
class FillPriorConfig:
    by_n_legs: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_FILL_BY_N_LEGS))
    by_family: dict[str, float] = field(default_factory=lambda: dict(FAMILY_FILL_PRIOR))
    late_day_penalty: float = 0.10
    late_day_minutes_to_close: float = 60.0
    stale_quote_penalty: float = 0.10
    stale_quote_seconds: float = 5.0
    wide_spread_penalty: float = 0.10
    wide_spread_rel: float = 0.15
    high_vol_penalty: float = 0.05
    high_vol_threshold: float = 0.25
    min_fraction: float = 0.0
    max_fraction: float = 1.0


def base_fill_fraction(
    family: str,
    n_legs: int,
    cfg: FillPriorConfig | None = None,
) -> float:
    config = cfg or FillPriorConfig()
    if family in config.by_family:
        return float(config.by_family[family])
    if n_legs in config.by_n_legs:
        return float(config.by_n_legs[n_legs])
    key = min(max(int(n_legs), 1), 4)
    return float(config.by_n_legs.get(key, 0.50))


def fill_fraction_for(
    family: str,
    *,
    n_legs: int = 2,
    quote_age_seconds: float | None = None,
    minutes_to_close: float | None = None,
    relative_spread: float | None = None,
    realized_vol: float | None = None,
    cfg: FillPriorConfig | None = None,
) -> tuple[float, dict[str, object]]:
    """Return (fill_fraction, diagnostics). Penalties only worsen fill."""
    config = cfg or FillPriorConfig()
    base = base_fill_fraction(family, n_legs, config)
    penalties: dict[str, float] = {}
    total_pen = 0.0
    if minutes_to_close is not None and minutes_to_close <= config.late_day_minutes_to_close:
        penalties["late_day"] = config.late_day_penalty
        total_pen += config.late_day_penalty
    if quote_age_seconds is not None and quote_age_seconds >= config.stale_quote_seconds:
        penalties["stale_quote"] = config.stale_quote_penalty
        total_pen += config.stale_quote_penalty
    if relative_spread is not None and relative_spread >= config.wide_spread_rel:
        penalties["wide_spread"] = config.wide_spread_penalty
        total_pen += config.wide_spread_penalty
    if realized_vol is not None and realized_vol >= config.high_vol_threshold:
        penalties["high_vol"] = config.high_vol_penalty
        total_pen += config.high_vol_penalty
    frac = min(max(base + total_pen, config.min_fraction), config.max_fraction)
    return frac, {
        "base": base,
        "n_legs": n_legs,
        "family": family,
        "penalties": penalties,
        "total_penalty": total_pen,
        "fill_fraction": frac,
    }
