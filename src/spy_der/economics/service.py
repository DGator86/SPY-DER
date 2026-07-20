"""Executable economics service (spec §33; System A execution_cost + estimate_v3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from spy_der.contracts.candidates import Candidate, CandidateUniverse
from spy_der.contracts.economics import ECONOMICS_VERSION, CandidateEconomics
from spy_der.contracts.market import CanonicalMarketSnapshot, OptionQuote
from spy_der.economics.fill_prior import FillPriorConfig, fill_fraction_for
from spy_der.economics.pricing import (
    fill_price,
    half_spread_cost,
    mid_price,
    natural_price,
    quote_map,
    relative_spread,
)

__all__ = [
    "EconomicsConfig",
    "calculate_candidate_economics",
    "calculate_universe_economics",
    "expected_order_value",
]


@dataclass(frozen=True, slots=True)
class EconomicsConfig:
    fill: FillPriorConfig | None = None
    conservative_fill_boost: float = 0.25
    fee_per_leg_entry: Decimal = Decimal("0.0035")
    fee_per_leg_exit: Decimal = Decimal("0.0035")
    fee_per_contract_entry: Decimal = Decimal("0")
    fee_per_contract_exit: Decimal = Decimal("0")
    exit_fill_boost: float = 0.10
    stop_exit_fill_boost: float = 0.25
    economics_version: str = ECONOMICS_VERSION


def expected_order_value(
    p_fill: float,
    expected_net_pnl_given_fill: float,
    opportunity_cost_unfilled: float = 0.0,
) -> float:
    p = min(max(float(p_fill), 0.0), 1.0)
    return p * float(expected_net_pnl_given_fill) + (1.0 - p) * float(opportunity_cost_unfilled)


def _clip01(x: float) -> float:
    return min(max(float(x), 0.0), 1.0)


def _quote_age(quotes: dict[str, OptionQuote], candidate: Candidate) -> float | None:
    ages = [
        q.age_seconds
        for leg in candidate.legs
        if (q := quotes.get(leg.contract_id)) is not None and q.age_seconds is not None
    ]
    return max(ages) if ages else None


def calculate_candidate_economics(
    candidate: Candidate,
    snapshot: CanonicalMarketSnapshot,
    *,
    cfg: EconomicsConfig | None = None,
    p_fill: float | None = None,
    expected_fill_fraction: float | None = None,
    expected_net_pnl: Decimal | None = None,
    expected_shortfall: Decimal | None = None,
    data_quality_penalty: float = 0.0,
) -> CandidateEconomics:
    """Build CandidateEconomics for one immutable candidate."""
    config = cfg or EconomicsConfig()
    quotes = quote_map(snapshot.option_chain)
    mid = mid_price(candidate.legs, quotes)
    nat = natural_price(candidate.legs, quotes)
    if mid is not None and nat is not None and nat > mid:
        nat = mid

    n = len(candidate.legs)
    rel = relative_spread(candidate, quotes)
    age = _quote_age(quotes, candidate)
    mtc = float(snapshot.minutes_to_close) if snapshot.minutes_to_close is not None else None

    if expected_fill_fraction is None:
        frac_exp, fill_diag = fill_fraction_for(
            candidate.family,
            n_legs=n,
            quote_age_seconds=age,
            minutes_to_close=mtc,
            relative_spread=rel,
            cfg=config.fill,
        )
        fallback = "deterministic_prior"
    else:
        frac_exp = _clip01(expected_fill_fraction)
        fill_diag = {"fill_fraction": frac_exp, "source": "caller"}
        fallback = "caller_supplied"

    frac_con = _clip01(frac_exp + config.conservative_fill_boost)
    exp_px = fill_price(mid, nat, frac_exp) if mid is not None and nat is not None else None
    con_px = fill_price(mid, nat, frac_con) if mid is not None and nat is not None else None
    if exp_px is not None and mid is not None:
        exp_px = min(exp_px, mid)
    if con_px is not None and exp_px is not None:
        con_px = min(con_px, exp_px)

    hs = half_spread_cost(candidate.legs, quotes) or Decimal("0")
    fees_in = Decimal(n) * config.fee_per_leg_entry + config.fee_per_contract_entry
    fees_out = Decimal(n) * config.fee_per_leg_exit + config.fee_per_contract_exit
    fees = fees_in + fees_out
    frac_exit = _clip01(frac_exp + config.exit_fill_boost)
    frac_stop = _clip01(frac_exp + config.stop_exit_fill_boost)
    entry_slip = (mid - exp_px) if mid is not None and exp_px is not None else Decimal("0")
    exit_slip = Decimal(str(frac_exit)) * hs
    stop_slip = Decimal(str(frac_stop)) * hs

    # Fill probability prior when empirical model absent.
    fill_prob = _clip01(p_fill if p_fill is not None else max(0.05, 1.0 - 0.5 * frac_exp))

    max_loss = candidate.maximum_loss
    ev: Decimal | None = None
    rod: float | None = None
    if expected_net_pnl is not None:
        eov = expected_order_value(fill_prob, float(expected_net_pnl))
        ev = Decimal(str(round(eov, 8)))
        if max_loss > 0:
            rod = float(ev / max_loss)

    liq = float(candidate.quote_quality)
    quality_flags: list[str] = []
    if age is not None and age >= 5.0:
        quality_flags.append("stale_quote")
    if rel is not None and rel >= 0.15:
        quality_flags.append("wide_spread")

    return CandidateEconomics(
        candidate_id=candidate.candidate_id,
        economics_version=config.economics_version,
        mid_price=mid,
        natural_price=nat,
        expected_fill_price=exp_px,
        conservative_fill_price=con_px,
        fill_probability=fill_prob,
        expected_fill_fraction=frac_exp,
        fees=fees,
        entry_slippage=max(entry_slip, Decimal("0")),
        exit_slippage=max(exit_slip, Decimal("0")),
        stop_slippage=max(stop_slip, Decimal("0")),
        liquidity_score=min(max(liq, 0.0), 1.0),
        quote_quality=tuple(quality_flags),
        maximum_loss=max_loss,
        maximum_profit=candidate.maximum_profit,
        return_on_defined_risk=rod,
        expected_value=ev,
        expected_shortfall=expected_shortfall,
        data_quality_penalty=float(data_quality_penalty),
        fallback_level=fallback,
        diagnostics=tuple(
            (str(k), str(v)) for k, v in fill_diag.items() if not isinstance(v, dict)
        ),
    )


def calculate_universe_economics(
    universe: CandidateUniverse,
    snapshot: CanonicalMarketSnapshot,
    *,
    cfg: EconomicsConfig | None = None,
) -> tuple[CandidateEconomics, ...]:
    return tuple(
        calculate_candidate_economics(c, snapshot, cfg=cfg) for c in universe.candidates
    )
