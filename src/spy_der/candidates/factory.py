"""Deterministic candidate factory (master spec §31).

Generation sequence: validate chain → load families → enumerate geometry →
normalize legs → validate → terminal payoff → prove max loss → IDs →
quote refs → immutable universe. No EV, ranking, or policy selection.
"""

from __future__ import annotations

from decimal import Decimal

from spy_der.candidates.dominance import apply_deterministic_dominance
from spy_der.candidates.geometry import FactoryConfig, GeometrySpec, enumerate_geometries
from spy_der.candidates.payoff import entry_credit_from_mids, prove_bounded_loss
from spy_der.candidates.registry import (
    APPROVED_FAMILIES,
    family_direction,
    is_approved_family,
)
from spy_der.contracts.candidates import (
    FACTORY_VERSION,
    Candidate,
    CandidateUniverse,
    DebitCredit,
    geometry_hash,
    make_candidate_id,
    normalize_legs,
)
from spy_der.contracts.market import CanonicalMarketSnapshot, OptionQuote

__all__ = ["CandidateFactoryService", "generate_candidate_universe"]


def _quote_mid(quote: OptionQuote) -> Decimal | None:
    if quote.mark is not None:
        return quote.mark
    if quote.bid is not None and quote.ask is not None:
        return (quote.bid + quote.ask) / Decimal("2")
    if quote.last is not None:
        return quote.last
    return None


def _quote_quality(quote: OptionQuote) -> Decimal:
    if quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid:
        spread = quote.ask - quote.bid
        # Higher is better: inverse spread, capped.
        return Decimal("1") / (Decimal("1") + spread)
    return Decimal("0")


def _mid_map(chain: tuple[OptionQuote, ...]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for quote in chain:
        mid = _quote_mid(quote)
        if mid is not None:
            out[quote.contract.contract_id] = mid
    return out


def _entry_type(entry_credit: Decimal) -> DebitCredit:
    if entry_credit > 0:
        return DebitCredit.CREDIT
    if entry_credit < 0:
        return DebitCredit.DEBIT
    return DebitCredit.EVEN


def _build_candidate(
    *,
    snapshot: CanonicalMarketSnapshot,
    spec: GeometrySpec,
    mids: dict[str, Decimal],
    chain_by_id: dict[str, OptionQuote],
) -> Candidate | None:
    family = spec.family.value
    if not is_approved_family(family):
        return None
    legs = normalize_legs(spec.legs)
    if any(leg.multiplier <= 0 for leg in legs):
        return None
    if len({leg.expiration for leg in legs}) != 1:
        return None

    entry_credit = entry_credit_from_mids(legs, mids)
    if entry_credit is None:
        return None

    proof = prove_bounded_loss(legs, entry_credit=entry_credit)
    if proof.unbounded or proof.maximum_loss < 0:
        return None
    # Reject zero-risk noise (no capital and no credit/debit).
    if proof.maximum_loss == 0 and entry_credit == 0:
        return None

    ghash = geometry_hash(
        family=family,
        expiration=spec.expiration,
        legs=legs,
        factory_version=FACTORY_VERSION,
    )
    cid = make_candidate_id(
        snapshot_id=snapshot.snapshot_id,
        factory_version=FACTORY_VERSION,
        geometry=ghash,
    )
    refs = tuple(sorted(leg.contract_id for leg in legs if leg.contract_id))
    qualities = [
        _quote_quality(chain_by_id[leg.contract_id])
        for leg in legs
        if leg.contract_id in chain_by_id
    ]
    quality = min(qualities) if qualities else Decimal("0")
    return Candidate(
        candidate_id=cid,
        snapshot_id=snapshot.snapshot_id,
        family=family,
        direction=family_direction(family).value,
        expiration=spec.expiration,
        legs=legs,
        entry_type=_entry_type(entry_credit),
        maximum_profit=proof.maximum_profit,
        maximum_loss=proof.maximum_loss,
        breakevens=proof.breakevens,
        capital_required=proof.capital_required,
        terminal_payoff_hash=proof.payoff_hash,
        geometry_hash=ghash,
        quote_snapshot_refs=refs,
        entry_credit=entry_credit,
        quote_quality=quality,
    )


def generate_candidate_universe(
    snapshot: CanonicalMarketSnapshot,
    *,
    cfg: FactoryConfig | None = None,
    apply_dominance: bool = True,
) -> CandidateUniverse:
    """Build an immutable approved-family candidate universe for ``snapshot``."""
    config = cfg or FactoryConfig(families=APPROVED_FAMILIES)
    chain = snapshot.option_chain
    if not chain:
        return CandidateUniverse(snapshot_id=snapshot.snapshot_id, candidates=())

    spot = snapshot.underlying_price
    specs = enumerate_geometries(chain, spot=spot, cfg=config)
    mids = _mid_map(chain)
    by_id = {q.contract.contract_id: q for q in chain}

    built: list[Candidate] = []
    for spec in specs:
        cand = _build_candidate(snapshot=snapshot, spec=spec, mids=mids, chain_by_id=by_id)
        if cand is not None:
            built.append(cand)

    if apply_dominance:
        built = apply_deterministic_dominance(built)
    else:
        built.sort(key=lambda c: c.candidate_id)

    return CandidateUniverse(
        snapshot_id=snapshot.snapshot_id,
        factory_version=FACTORY_VERSION,
        candidates=tuple(built),
    )


class CandidateFactoryService:
    """Protocol-compatible factory wrapper (``interfaces.CandidateFactory``)."""

    def __init__(self, cfg: FactoryConfig | None = None) -> None:
        self._cfg = cfg

    def generate(self, snapshot: CanonicalMarketSnapshot) -> CandidateUniverse:
        return generate_candidate_universe(snapshot, cfg=self._cfg)
