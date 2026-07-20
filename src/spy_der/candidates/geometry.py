"""Legal geometry enumeration from an option chain (spec §31)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from spy_der.contracts.candidates import CandidateFamily, CandidateLeg
from spy_der.contracts.market import OptionQuote, OptionType

__all__ = [
    "FactoryConfig",
    "GeometrySpec",
    "enumerate_geometries",
]


@dataclass(frozen=True, slots=True)
class FactoryConfig:
    spread_widths: tuple[Decimal, ...] = (
        Decimal("1"),
        Decimal("2"),
        Decimal("3"),
        Decimal("5"),
    )
    short_min_otm: Decimal = Decimal("0.0015")
    short_max_otm: Decimal = Decimal("0.030")
    long_min_otm: Decimal = Decimal("-0.005")
    long_max_otm: Decimal = Decimal("0.015")
    condor_max_skew: Decimal = Decimal("0.010")
    families: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class GeometrySpec:
    family: CandidateFamily
    legs: tuple[CandidateLeg, ...]
    expiration: date


def _index_chain(
    chain: tuple[OptionQuote, ...],
) -> tuple[date | None, dict[tuple[OptionType, Decimal], OptionQuote], list[Decimal]]:
    by_key: dict[tuple[OptionType, Decimal], OptionQuote] = {}
    expirations: set[date] = set()
    strikes: set[Decimal] = set()
    for quote in chain:
        contract = quote.contract
        expirations.add(contract.expiration)
        strikes.add(contract.strike)
        by_key[(contract.option_type, contract.strike)] = quote
    expiration = min(expirations) if len(expirations) == 1 else None
    return expiration, by_key, sorted(strikes)


def _leg(quote: OptionQuote, quantity: int) -> CandidateLeg:
    c = quote.contract
    return CandidateLeg(
        option_type=c.option_type,
        strike=c.strike,
        quantity=quantity,
        expiration=c.expiration,
        contract_id=c.contract_id,
        multiplier=c.multiplier,
    )


def _otm_strikes(
    strikes: list[Decimal],
    spot: Decimal,
    side: OptionType,
    cfg: FactoryConfig,
) -> list[Decimal]:
    out: list[Decimal] = []
    for strike in strikes:
        if side is OptionType.CALL:
            rel = (strike - spot) / spot
        else:
            rel = (spot - strike) / spot
        if cfg.short_min_otm <= rel <= cfg.short_max_otm:
            out.append(strike)
    return out


def _long_strikes(
    strikes: list[Decimal],
    spot: Decimal,
    side: OptionType,
    cfg: FactoryConfig,
) -> list[Decimal]:
    out: list[Decimal] = []
    for strike in strikes:
        if side is OptionType.CALL:
            rel = (strike - spot) / spot
        else:
            rel = (spot - strike) / spot
        if cfg.long_min_otm <= rel <= cfg.long_max_otm:
            out.append(strike)
    return out


def _allowed(family: CandidateFamily, cfg: FactoryConfig) -> bool:
    if cfg.families is None:
        return True
    return family.value in cfg.families


def enumerate_geometries(
    chain: tuple[OptionQuote, ...],
    *,
    spot: Decimal,
    cfg: FactoryConfig | None = None,
) -> list[GeometrySpec]:
    """Enumerate approved bounded-loss geometries present on the chain."""
    config = cfg or FactoryConfig()
    if spot <= 0:
        return []
    expiration, by_key, strikes = _index_chain(chain)
    if expiration is None or not strikes:
        return []

    strike_set = set(strikes)
    out: list[GeometrySpec] = []

    def add(family: CandidateFamily, quotes_qty: list[tuple[OptionQuote, int]]) -> None:
        if not _allowed(family, config):
            return
        legs = tuple(_leg(q, qty) for q, qty in quotes_qty)
        out.append(GeometrySpec(family=family, legs=legs, expiration=expiration))

    # Credit verticals
    for kind, family in (
        (OptionType.PUT, CandidateFamily.BULL_PUT_CREDIT_SPREAD),
        (OptionType.CALL, CandidateFamily.BEAR_CALL_CREDIT_SPREAD),
    ):
        for ks_short in _otm_strikes(strikes, spot, kind, config):
            for width in config.spread_widths:
                ks_long = ks_short - width if kind is OptionType.PUT else ks_short + width
                if ks_long not in strike_set:
                    continue
                short_q = by_key.get((kind, ks_short))
                long_q = by_key.get((kind, ks_long))
                if short_q is None or long_q is None:
                    continue
                add(family, [(short_q, -1), (long_q, +1)])

    # Debit verticals
    for kind, family in (
        (OptionType.CALL, CandidateFamily.CALL_DEBIT_SPREAD),
        (OptionType.PUT, CandidateFamily.PUT_DEBIT_SPREAD),
    ):
        for k_buy in _long_strikes(strikes, spot, kind, config):
            for width in config.spread_widths:
                k_sell = k_buy + width if kind is OptionType.CALL else k_buy - width
                if k_sell not in strike_set:
                    continue
                buy_q = by_key.get((kind, k_buy))
                sell_q = by_key.get((kind, k_sell))
                if buy_q is None or sell_q is None:
                    continue
                add(family, [(buy_q, +1), (sell_q, -1)])

    # Long singles
    for k in _long_strikes(strikes, spot, OptionType.CALL, config):
        q = by_key.get((OptionType.CALL, k))
        if q is not None:
            add(CandidateFamily.LONG_CALL, [(q, +1)])
    for k in _long_strikes(strikes, spot, OptionType.PUT, config):
        q = by_key.get((OptionType.PUT, k))
        if q is not None:
            add(CandidateFamily.LONG_PUT, [(q, +1)])

    # Iron condor
    puts = _otm_strikes(strikes, spot, OptionType.PUT, config)
    calls = _otm_strikes(strikes, spot, OptionType.CALL, config)
    for width in config.spread_widths:
        for kp in puts:
            kpl = kp - width
            if kpl not in strike_set:
                continue
            for kc in calls:
                kcl = kc + width
                if kcl not in strike_set:
                    continue
                skew = abs((spot - kp) - (kc - spot)) / spot
                if skew > config.condor_max_skew:
                    continue
                qp = by_key.get((OptionType.PUT, kp))
                qpl = by_key.get((OptionType.PUT, kpl))
                qc = by_key.get((OptionType.CALL, kc))
                qcl = by_key.get((OptionType.CALL, kcl))
                if None in (qp, qpl, qc, qcl):
                    continue
                assert qp is not None and qpl is not None and qc is not None and qcl is not None
                add(
                    CandidateFamily.IRON_CONDOR,
                    [(qp, -1), (qpl, +1), (qc, -1), (qcl, +1)],
                )

    # Iron butterfly
    atm = min(strikes, key=lambda k: abs(k - spot))
    for width in config.spread_widths:
        kpl, kcl = atm - width, atm + width
        if kpl not in strike_set or kcl not in strike_set:
            continue
        qp = by_key.get((OptionType.PUT, atm))
        qpl = by_key.get((OptionType.PUT, kpl))
        qc = by_key.get((OptionType.CALL, atm))
        qcl = by_key.get((OptionType.CALL, kcl))
        if None in (qp, qpl, qc, qcl):
            continue
        assert qp is not None and qpl is not None and qc is not None and qcl is not None
        add(
            CandidateFamily.IRON_BUTTERFLY,
            [(qp, -1), (qpl, +1), (qc, -1), (qcl, +1)],
        )

    # Bounded put broken-wing butterfly
    for near in config.spread_widths:
        for far in config.spread_widths:
            if far <= near:
                continue
            upper = atm + near
            lower = atm - far
            if upper not in strike_set or lower not in strike_set:
                continue
            qu = by_key.get((OptionType.PUT, upper))
            qb = by_key.get((OptionType.PUT, atm))
            ql = by_key.get((OptionType.PUT, lower))
            if None in (qu, qb, ql):
                continue
            assert qu is not None and qb is not None and ql is not None
            add(
                CandidateFamily.BOUNDED_BROKEN_WING_BUTTERFLY,
                [(qu, +1), (qb, -2), (ql, +1)],
            )

    # Long straddle (ATM call + put)
    qc_atm = by_key.get((OptionType.CALL, atm))
    qp_atm = by_key.get((OptionType.PUT, atm))
    if qc_atm is not None and qp_atm is not None:
        add(CandidateFamily.LONG_STRADDLE, [(qc_atm, +1), (qp_atm, +1)])

    # Long strangle
    for kc in calls:
        call_dist = (kc - spot) / spot
        if call_dist <= 0:
            continue
        for kp in puts:
            put_dist = (spot - kp) / spot
            if put_dist <= 0:
                continue
            if abs(call_dist - put_dist) / call_dist >= Decimal("0.5"):
                continue
            qc = by_key.get((OptionType.CALL, kc))
            qp = by_key.get((OptionType.PUT, kp))
            if qc is None or qp is None:
                continue
            add(CandidateFamily.LONG_STRANGLE, [(qc, +1), (qp, +1)])

    # Bounded backspreads (net long options)
    for k_sell in _long_strikes(strikes, spot, OptionType.CALL, config):
        for k_buy in _otm_strikes(strikes, spot, OptionType.CALL, config):
            if k_buy <= k_sell:
                continue
            qs = by_key.get((OptionType.CALL, k_sell))
            qb = by_key.get((OptionType.CALL, k_buy))
            if qs is None or qb is None:
                continue
            add(CandidateFamily.BOUNDED_BACKSPREAD_CALL, [(qs, -1), (qb, +2)])
    for k_sell in _long_strikes(strikes, spot, OptionType.PUT, config):
        for k_buy in _otm_strikes(strikes, spot, OptionType.PUT, config):
            if k_buy >= k_sell:
                continue
            qs = by_key.get((OptionType.PUT, k_sell))
            qb = by_key.get((OptionType.PUT, k_buy))
            if qs is None or qb is None:
                continue
            add(CandidateFamily.BOUNDED_BACKSPREAD_PUT, [(qs, -1), (qb, +2)])

    # Deterministic order: family, then geometry key.
    out.sort(
        key=lambda spec: (
            spec.family.value,
            tuple(
                (leg.option_type.value, str(leg.strike), leg.quantity) for leg in spec.legs
            ),
        )
    )
    return out
