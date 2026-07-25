"""Native SPY-DER synthetic-universe provider.

Replaces 0DTE's ``integrations/spy_der/synthetic.py``. The Dojo's universe phase
now calls a SPY-DER implementation, so synthetic-universe production carries no
cross-repository runtime dependency.

Two differences from the bridge it replaces are deliberate:

1. **Real candidates.** The 0DTE provider emitted a single ``"unknown"``-family
   placeholder candidate per snapshot, so universe sparring could not score
   candidate geometry at all. This provider reprices a real chain each tick and
   runs SPY-DER's own :func:`~spy_der.candidates.factory.generate_candidate_universe`
   over it, so the Dojo sees the same geometry the production pipeline builds.

2. **One canonical path.** Synthetic ticks traverse
   ``MarkovWorld -> CompositeFeed -> CanonicalSnapshotAssembler`` — the same
   ingestion path as live providers — instead of being injected downstream as
   pre-built packets. A bug in assembly therefore shows up in synthetic sparring
   rather than hiding until live.

Settlement is the world's realized close, so outcomes are ground truth rather
than an estimate.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.candidates.geometry import FactoryConfig
from spy_der.candidates.payoff import terminal_payoff
from spy_der.contracts.candidates import Candidate
from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.composite import CompositeFeed
from spy_der.market_data.providers.base import MarketDataProvider, RawTick
from spy_der.synthetic.universe import UniverseSpec
from spy_der.synthetic.world import MarkovWorld, WorldTick

__all__ = [
    "DOJO_FACTORY_CONFIG",
    "SyntheticSettlementProvider",
    "SyntheticUniverseProvider",
    "SyntheticUniverseResult",
    "coerce_universe_spec",
]

#: A 390-minute session at every minute is far more experience than a Dojo
#: generation needs, and repricing a 100-quote chain per minute dominates the
#: run time. Default to one snapshot every 15 minutes; callers wanting the full
#: tape can pass ``snapshot_stride=1``.
_DEFAULT_SNAPSHOT_STRIDE = 15
#: Cap the candidate universe surfaced per snapshot. Dominance filtering has
#: already ordered them, so the head is the interesting part.
_DEFAULT_MAX_CANDIDATES = 24

#: Enumeration band tuned for Dojo throughput. The synthetic chain spans +/-4%
#: of spot, and the default :class:`FactoryConfig` enumerates ~1400 candidates
#: from it — dominance filtering is O(n^2), so that costs ~3 s per snapshot.
#: Tightening the OTM bands to the range a 0DTE structure actually trades in
#: yields ~280 candidates at ~0.14 s while still covering all 13 approved
#: families. Pass an explicit ``factory_config`` to widen it (parity runs do).
DOJO_FACTORY_CONFIG = FactoryConfig(
    spread_widths=(Decimal("1"), Decimal("2"), Decimal("5")),
    short_min_otm=Decimal("0.002"),
    short_max_otm=Decimal("0.012"),
    long_min_otm=Decimal("-0.002"),
    long_max_otm=Decimal("0.008"),
)


def coerce_universe_spec(specification: Any) -> UniverseSpec:
    """Accept a :class:`UniverseSpec` or any duck-typed protocol equivalent.

    The Dojo's ``UniverseSpec`` protocol only guarantees ``universe_id`` and
    ``archetype``, so a caller may hand over a minimal object; the missing
    lattice coordinates fall back to the canonical baseline (tilt 1.0, vol 1.0,
    generation 0, jitter 0.0).
    """
    if isinstance(specification, UniverseSpec):
        return specification
    universe_id = str(getattr(specification, "universe_id", "synth"))
    archetype = str(
        getattr(specification, "start_archetype", None)
        or getattr(specification, "archetype", None)
        or "calm_pin"
    )
    seed_attr = getattr(specification, "seed", None)
    seed = int(seed_attr) if seed_attr is not None else abs(hash(universe_id)) % 1_000_003
    return UniverseSpec(
        universe_id=universe_id,
        seed=seed,
        days=int(getattr(specification, "days", 1)),
        start_archetype=archetype,
        persistence_tilt=float(getattr(specification, "persistence_tilt", 1.0)),
        vol_mult=float(getattr(specification, "vol_mult", 1.0)),
        gap_mult=float(getattr(specification, "gap_mult", 1.0)),
        tick_stride=int(getattr(specification, "tick_stride", 1)),
        base_spot=float(getattr(specification, "base_spot", 600.0)),
        generation=int(getattr(specification, "generation", 0)),
        transition_jitter=float(getattr(specification, "transition_jitter", 0.0)),
    )


class SyntheticSettlementProvider(MarketDataProvider):
    """Settlement-reference clock for a synthetic world.

    ``CompositeFeed`` marks the ``settlement`` component MISSING when no
    settlement provider is wired, which costs every snapshot a 0.5 data-quality
    penalty and makes ``is_live`` false — so a synthetic snapshot would look
    degraded for a reason that has nothing to do with the world.

    This reports settlement *availability* only. ``CompositeFeed`` consumes just
    the provider name and ``observed_at`` from a settlement tick, so the world's
    realized close is deliberately **not** exposed here: an intraday snapshot
    must never be able to see its own settlement.
    """

    def __init__(self, world: MarkovWorld) -> None:
        self._world = world

    @property
    def name(self) -> str:
        return f"synthetic-settlement:{self._world.spec.universe_id}"

    def fetch(self, timestamp: datetime) -> RawTick | None:
        tick = self._world.world_tick(timestamp)
        if tick is None:
            return None
        # Spot as the settlement reference, no chain and no bars. The realized
        # close stays in the world; only the reference the feed needs is here.
        return RawTick(
            provider=self.name,
            symbol=self._world.symbol,
            observed_at=timestamp,
            underlying_price=Decimal(f"{tick.spot:.4f}"),
            has_chain=False,
        )


@dataclass(frozen=True, slots=True)
class SyntheticUniverseResult:
    """Packets plus the ground truth needed to score them."""

    spec: UniverseSpec
    packets: tuple[MarketPacket, ...]
    outcomes: tuple[OutcomePacket, ...]
    coverage: dict[str, dict[str, int]]
    settlements: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "n_packets": len(self.packets),
            "n_outcomes": len(self.outcomes),
            "coverage": {a: dict(r) for a, r in sorted(self.coverage.items())},
            "settlements": dict(sorted(self.settlements.items())),
        }


class SyntheticUniverseProvider:
    """Generate ``MarketPacket`` streams from SPY-DER-owned synthetic worlds.

    Satisfies ``spy_der.dojo.protocols.SyntheticUniverseProvider``.
    """

    def __init__(
        self,
        *,
        symbol: str = "SPY",
        max_ticks: int | None = None,
        snapshot_stride: int = _DEFAULT_SNAPSHOT_STRIDE,
        max_candidates: int = _DEFAULT_MAX_CANDIDATES,
        factory_config: FactoryConfig | None = None,
        calendar: MarketCalendar | None = None,
    ) -> None:
        if snapshot_stride < 1:
            msg = "snapshot_stride must be >= 1"
            raise ValueError(msg)
        self.symbol = symbol
        self.max_ticks = max_ticks
        self.snapshot_stride = snapshot_stride
        self.max_candidates = max_candidates
        self._factory_config = factory_config or DOJO_FACTORY_CONFIG
        self._calendar = calendar

    # -- protocol surface ----------------------------------------------------
    def generate(self, specification: Any) -> Iterable[MarketPacket]:
        """Yield ``MarketPacket``s for the universe described by ``specification``."""
        return self.generate_result(specification).packets

    # -- full result ---------------------------------------------------------
    def generate_result(self, specification: Any) -> SyntheticUniverseResult:
        """Packets, ground-truth outcomes, coverage and settlements together."""
        spec = coerce_universe_spec(specification)
        world = MarkovWorld(spec, symbol=self.symbol)
        feed = CompositeFeed(
            [world],
            settlement_provider=SyntheticSettlementProvider(world),
            calendar=self._calendar,
        )

        packets: list[MarketPacket] = []
        outcomes: list[OutcomePacket] = []
        for index, tick in enumerate(self._selected_ticks(world)):
            snapshot = feed.snapshot(tick.timestamp)
            if snapshot is None:
                continue
            packet, candidates = self._packet_with_candidates(
                spec, world, tick, snapshot, index
            )
            packets.append(packet)
            settle = world.settlement(tick.session)
            if settle is not None:
                outcomes.append(self._outcome(packet, tick, settle, candidates))

        return SyntheticUniverseResult(
            spec=spec,
            packets=tuple(packets),
            outcomes=tuple(outcomes),
            coverage=world.coverage(),
            settlements=dict(world.day_close),
        )

    # -- internals -----------------------------------------------------------
    def _selected_ticks(self, world: MarkovWorld) -> Iterator[WorldTick]:
        emitted = 0
        for i, tick in enumerate(world.ticks()):
            if i % self.snapshot_stride:
                continue
            if self.max_ticks is not None and emitted >= self.max_ticks:
                return
            emitted += 1
            yield tick

    def _packet(
        self,
        spec: UniverseSpec,
        world: MarkovWorld,
        tick: WorldTick,
        snapshot: CanonicalMarketSnapshot,
        index: int,
    ) -> MarketPacket:
        packet, _candidates = self._packet_with_candidates(
            spec, world, tick, snapshot, index
        )
        return packet

    def _packet_with_candidates(
        self,
        spec: UniverseSpec,
        world: MarkovWorld,
        tick: WorldTick,
        snapshot: CanonicalMarketSnapshot,
        index: int,
    ) -> tuple[MarketPacket, tuple[Candidate, ...]]:
        universe = generate_candidate_universe(snapshot, cfg=self._factory_config)
        candidates = tuple(universe.candidates[: self.max_candidates])
        views = tuple(self._candidate_view(c) for c in candidates)
        session = tick.timestamp.date()
        snapshot_id = f"{spec.universe_id}-{session.isoformat()}-{index:05d}"
        packet = MarketPacket(
            schema_version=MARKET_PACKET_SCHEMA,
            snapshot_id=snapshot_id,
            session_date=session,
            symbol=self.symbol,
            underlying_price=snapshot.underlying_price,
            data_quality=1.0 - float(snapshot.data_quality.penalty),
            forecast_uncertainty=self._uncertainty(tick),
            hard_vetoes=(),
            forecast=self._forecast(spec, world, tick, snapshot),
            candidates=views,
            track_record={},
            generated_at=tick.timestamp,
            risk_max_size_scalar=1.0,
        )
        return packet, candidates

    @staticmethod
    def _candidate_view(candidate: Candidate) -> MarketCandidateView:
        return MarketCandidateView(
            candidate_id=candidate.candidate_id,
            family=candidate.family,
            direction=candidate.direction,
            maximum_loss=candidate.maximum_loss,
            capital_required=candidate.capital_required,
            geometry_hash=candidate.geometry_hash,
            expiration=candidate.expiration,
            mid_price=candidate.entry_credit,
            fill_probability=1.0,
            hard_vetoed=False,
        )

    @staticmethod
    def _uncertainty(tick: WorldTick) -> float:
        """Map realized vol onto ``[0, 1]`` as a coarse uncertainty proxy.

        The synthetic world knows its own realized vol exactly, so this is the
        honest quantity to expose: 8% annualized -> ~0.15, 40% -> ~0.75.
        """
        return min(max(tick.realized_vol / 0.53, 0.0), 1.0)

    def _forecast(
        self,
        spec: UniverseSpec,
        world: MarkovWorld,
        tick: WorldTick,
        snapshot: CanonicalMarketSnapshot,
    ) -> dict[str, Any]:
        """Latent world state, labeled as ground truth.

        ``truth`` is separated from the rest so an evaluator can score a
        prediction against it without accidentally feeding it to the agent — see
        ``spy_der.dojo.evaluation``.
        """
        return {
            "source": "spy_der.synthetic",
            "universe_id": spec.universe_id,
            "generation": spec.generation,
            "spot": float(snapshot.underlying_price),
            "net_gex": tick.net_gex,
            "gamma_flip": tick.gamma_flip,
            "call_wall": tick.call_wall,
            "put_wall": tick.put_wall,
            "implied_vol": tick.implied_vol,
            "minutes_to_close": tick.minutes_to_close,
            "truth": {
                "archetype": tick.archetype,
                "regime": tick.regime,
                "realized_vol": tick.realized_vol,
                "vrp": tick.vrp,
                "skew": tick.skew,
                "pin": tick.pin,
                "settlement": world.settlement(tick.session),
            },
        }

    @staticmethod
    def _outcome(
        packet: MarketPacket,
        tick: WorldTick,
        settlement: float,
        candidates: tuple[Candidate, ...] = (),
    ) -> OutcomePacket:
        """Ground-truth outcome: settlement + per-candidate terminal P&L.

        Earlier builds shipped ``realized_pnl=None`` and dropped the candidate
        map, so the Dojo's evaluator matched zero trades after spending the
        whole lattice generating packets. Terminal P&L is computed here from
        the real candidate legs against the world's realized close.
        """
        move = settlement - tick.spot
        settle_px = Decimal(str(settlement))
        by_candidate: dict[str, str] = {}
        for candidate in candidates:
            pnl = terminal_payoff(
                candidate.legs,
                entry_credit=candidate.entry_credit,
                spot=settle_px,
            )
            by_candidate[candidate.candidate_id] = str(pnl)
        true_direction = (
            "bullish" if move > 0 else ("bearish" if move < 0 else "flat")
        )
        # Prefer the first (dominance-ordered) candidate as the settlement pnl
        # so evaluators that only read realized_pnl still get a number.
        head_pnl = None
        if by_candidate:
            head_id = candidates[0].candidate_id
            head_pnl = Decimal(by_candidate[head_id])
        return OutcomePacket(
            schema_version=OUTCOME_PACKET_SCHEMA,
            snapshot_id=packet.snapshot_id,
            session_date=packet.session_date,
            symbol=packet.symbol,
            candidate_id=candidates[0].candidate_id if candidates else None,
            action="settle",
            realized_pnl=head_pnl,
            settled=True,
            labels={
                "settlement": settlement,
                "spot_at_snapshot": tick.spot,
                "realized_move": move,
                "direction": "up" if move > 0 else ("down" if move < 0 else "flat"),
                "true_direction": true_direction,
                "archetype": tick.archetype,
                "regime": tick.regime,
                "minutes_to_close": tick.minutes_to_close,
                "realized_pnl_by_candidate": by_candidate,
            },
            settled_at=tick.timestamp,
        )


def settlement_pnl(
    candidate: MarketCandidateView,
    settlement: Decimal,
) -> Decimal | None:
    """Placeholder hook kept out of the provider's critical path.

    Terminal P&L for a candidate is
    :func:`spy_der.candidates.payoff.terminal_payoff`'s job — the provider
    deliberately does not duplicate that math. Returns ``None`` so callers must
    go through the payoff module.
    """
    del candidate, settlement
    return None


def sessions_of(result: SyntheticUniverseResult) -> tuple[date, ...]:
    """Distinct sessions represented in a generated universe."""
    return tuple(sorted({p.session_date for p in result.packets}))
