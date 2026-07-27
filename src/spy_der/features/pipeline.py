"""The feature stage (master spec §22, §63 Phase 3).

Every piece of this existed already — GEX, the MTF matrix, RND, volatility,
flow — and nothing assembled them, so `spy-der engine` reported
``features: unavailable: no FeaturePipeline implementation is registered`` and
the deterministic layers downstream ran without any of it. This is the
assembly step.

The output is a :class:`FeatureBundle`: a flat, sorted ``(name, value)`` map
plus a deterministic id. Flatness is deliberate — the consumers are scorers,
gates and models that want named scalars, and a nested structure would push the
same flattening into each of them separately.

Three rules the whole bundle obeys:

* **Absent means absent.** A feature that cannot be computed is *omitted*, not
  zero-filled. Callers distinguish "RSI is 50" from "RSI is unknown" by key
  presence, which is the same cold-start contract the MTF matrix follows and the
  reason `spy_der.features.mtf.mtf_feature_map` drops ``None`` rather than
  coercing it (spec §7.5).
* **Identity is content-addressed.** ``bundle_id`` hashes the snapshot id, the
  pipeline version and the feature content, so replaying a recording reproduces
  the same bundle id — the same property the snapshot assembler guarantees
  (spec §15).
* **One bad feature family does not lose the rest.** Each family is computed
  independently; a family that raises is recorded in ``failed_families`` and the
  rest of the bundle still lands. A pathological chain should cost the RND
  summary, not the whole tick.

Feature names are namespaced by family (``gex.``, ``vol.``, ``rnd.``,
``flow.``, ``breadth.``, ``session.``) except the MTF matrix, which is already
namespaced by timeframe label (``1m.rsi``, ``4h.adx``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from spy_der.contracts.common import content_hash, deterministic_id
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.models import FeatureBundle
from spy_der.features.flow import compute_flow
from spy_der.features.gex import GexRankWindow, compute_oi_gex
from spy_der.features.mtf import DEFAULT_TIMEFRAMES, compute_mtf, mtf_feature_map
from spy_der.features.rnd import compute_rnd
from spy_der.features.volatility import compute_volatility

__all__ = ["FEATURE_PIPELINE_VERSION", "FeatureBuildResult", "SnapshotFeaturePipeline"]

log = logging.getLogger("spy_der.features")

#: Bumped when the feature *set* or any definition changes, because bundle
#: identity includes it: bundles built under different definitions must not
#: collide on id.
FEATURE_PIPELINE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class FeatureBuildResult:
    """The bundle plus what could not be produced.

    ``missing_families`` are families with no usable inputs (an empty chain has
    no GEX); ``failed_families`` are families whose computation raised. The
    distinction matters to an operator: the first is a data condition, the
    second is a defect.
    """

    bundle: FeatureBundle
    missing_families: tuple[str, ...] = ()
    failed_families: tuple[str, ...] = field(default=())

    @property
    def is_complete(self) -> bool:
        return not self.missing_families and not self.failed_families


class SnapshotFeaturePipeline:
    """Build a :class:`FeatureBundle` from one canonical snapshot.

    Satisfies :class:`spy_der.interfaces.FeaturePipeline`. Deterministic and
    offline: it reads only the snapshot, so the engine can run it under
    ``PrivateNetwork=true`` and reproduce it from a recording.
    """

    def __init__(
        self,
        *,
        timeframes: tuple[int, ...] = DEFAULT_TIMEFRAMES,
        gex_window: GexRankWindow | None = None,
    ) -> None:
        self.timeframes = timeframes
        # Optional because the rank window is stateful across snapshots; without
        # one the percentile is simply not emitted rather than reading a
        # meaningless 0.5 that a gate would treat as a real observation.
        self.gex_window = gex_window

    # -- interfaces.FeaturePipeline -----------------------------------------
    def build(self, snapshot: CanonicalMarketSnapshot) -> FeatureBundle:
        return self.build_detailed(snapshot).bundle

    def build_detailed(self, snapshot: CanonicalMarketSnapshot) -> FeatureBuildResult:
        features: dict[str, float] = {}
        missing: list[str] = []
        failed: list[str] = []

        for name, produce in (
            ("mtf", self._mtf),
            ("gex", self._gex),
            ("vol", self._volatility),
            ("rnd", self._rnd),
            ("flow", self._flow),
            ("breadth", self._breadth),
            ("vix", self._vix),
            ("session", self._session),
        ):
            try:
                produced = produce(snapshot)
            except Exception:
                # Deliberately broad, and logged with a traceback: one
                # pathological family must not cost the whole bundle, but it
                # must never pass silently either.
                log.exception(
                    "feature family %s failed for %s", name, snapshot.snapshot_id
                )
                failed.append(name)
                continue
            if not produced:
                missing.append(name)
                continue
            features.update(produced)

        ordered = tuple(sorted(features.items()))
        bundle_id = deterministic_id(
            "feat",
            content_hash(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "pipeline_version": FEATURE_PIPELINE_VERSION,
                    "features": ordered,
                }
            ),
        )
        return FeatureBuildResult(
            bundle=FeatureBundle(
                bundle_id=bundle_id,
                snapshot_id=snapshot.snapshot_id,
                features=ordered,
            ),
            missing_families=tuple(missing),
            failed_families=tuple(failed),
        )

    # -- families ------------------------------------------------------------
    def _mtf(self, snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        """Native indicators per timeframe, already keyed ``<label>.<field>``."""
        if not snapshot.bars_1m:
            return {}
        return mtf_feature_map(compute_mtf(snapshot.bars_1m, self.timeframes))

    def _gex(self, snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        levels = compute_oi_gex(snapshot)
        if levels is None:
            return {}
        spot = float(snapshot.underlying_price)
        out = {
            "gex.net_bn": levels.net_gex_bn,
            "gex.net_ratio": levels.net_ratio,
            "gex.gamma_sign": float(levels.gamma_sign),
            "gex.concentration": levels.gex_concentration,
            "gex.wall_concentration": levels.wall_concentration,
            "gex.n_contracts": float(levels.n_contracts),
            "gex.n_strikes": float(levels.n_strikes),
            "gex.gamma_flip": float(levels.gamma_flip),
            "gex.call_wall": float(levels.call_wall),
            "gex.put_wall": float(levels.put_wall),
        }
        if spot > 0:
            # Distances as fractions of spot: a 2-point cushion means something
            # different at 400 than at 700, and the gates threshold on the ratio.
            out["gex.flip_cushion"] = (spot - float(levels.gamma_flip)) / spot
            out["gex.call_wall_distance"] = (float(levels.call_wall) - spot) / spot
            out["gex.put_wall_distance"] = (spot - float(levels.put_wall)) / spot
            width = float(levels.call_wall) - float(levels.put_wall)
            out["gex.channel_width"] = width / spot
        if self.gex_window is not None:
            # Always record the print so the window warms, but only publish the
            # rank once it is warm: `rank` returns a placeholder 0.5 before
            # then, and a gate cannot tell that from a genuinely median print.
            rank = self.gex_window.rank(
                levels.net_gex_bn, snapshot.timestamp.timestamp()
            )
            if self.gex_window.is_warm:
                out["gex.pct_rank"] = rank
        return out

    def _volatility(self, snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        summary = compute_volatility(
            snapshot, session_open_price=_session_open_price(snapshot)
        )
        if summary is None:
            return {}
        out = {
            "vol.atm_strike": float(summary.atm_strike),
            "vol.atm_straddle": float(summary.atm_straddle),
            "vol.expected_move": float(summary.expected_move),
            "vol.expected_move_pct": summary.expected_move_pct,
        }
        if summary.expected_move_consumed is not None:
            out["vol.expected_move_consumed"] = summary.expected_move_consumed
        return out

    def _rnd(self, snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        summary = compute_rnd(snapshot)
        if summary is None:
            return {}
        return {
            "rnd.forward": summary.forward,
            "rnd.mean": summary.mean,
            "rnd.std": summary.std,
            "rnd.skew": summary.skew,
            "rnd.prob_below_spot": summary.prob_below_spot,
            "rnd.n_strikes": float(summary.n_strikes),
            "rnd.normalized": 1.0 if summary.normalized else 0.0,
        }

    def _flow(self, snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        state = compute_flow(snapshot)
        if not state.is_observed:
            return {}
        out = {
            "flow.call_volume": float(state.call_volume),
            "flow.put_volume": float(state.put_volume),
            "flow.total_volume": float(state.total_volume),
            "flow.total_open_interest": float(state.total_open_interest),
        }
        if state.pcr_volume is not None:
            out["flow.pcr_volume"] = state.pcr_volume
        if state.volume_oi_ratio is not None:
            out["flow.volume_oi_ratio"] = state.volume_oi_ratio
        return out

    @staticmethod
    def _breadth(snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        state = snapshot.breadth
        if state is None or not state.is_observed:
            return {}
        out: dict[str, float] = {"breadth.sectors_observed": float(state.sectors_observed)}
        for name in ("rsp_spy_div", "sector_align", "top10_pressure"):
            value = getattr(state, name)
            if value is not None:
                out[f"breadth.{name}"] = float(value)
        return out

    @staticmethod
    def _vix(snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        term = snapshot.volatility_term_structure
        if term is None:
            return {}
        out = {"vix.vix": term.vix}
        for name in ("vix9d", "vix3m", "vvix"):
            value = getattr(term, name)
            if value is not None:
                out[f"vix.{name}"] = float(value)
        if term.contango is not None:
            out["vix.contango"] = term.contango
        if term.near_term_stress is not None:
            out["vix.near_term_stress"] = term.near_term_stress
        if term.vvix is not None and term.vvix_baseline:
            out["vix.vvix_elevation"] = term.vvix / term.vvix_baseline - 1.0
        return out

    @staticmethod
    def _session(snapshot: CanonicalMarketSnapshot) -> dict[str, float]:
        """Where in the session this is, and how much to trust it."""
        out: dict[str, float] = {
            "session.underlying_price": float(snapshot.underlying_price),
            "session.data_quality_penalty": snapshot.data_quality.penalty,
            "session.is_live": 1.0 if snapshot.is_live else 0.0,
            "session.contracts": float(snapshot.chain_coverage.contracts_total),
            "session.strikes": float(snapshot.chain_coverage.strikes_total),
        }
        if snapshot.minutes_from_open is not None:
            out["session.minutes_from_open"] = float(snapshot.minutes_from_open)
        if snapshot.minutes_to_close is not None:
            out["session.minutes_to_close"] = float(snapshot.minutes_to_close)
        if snapshot.underlying_bid is not None and snapshot.underlying_ask is not None:
            bid, ask = float(snapshot.underlying_bid), float(snapshot.underlying_ask)
            mid = 0.5 * (bid + ask)
            out["session.underlying_spread"] = ask - bid
            if mid > 0:
                out["session.underlying_spread_pct"] = (ask - bid) / mid
        return out


def _session_open_price(snapshot: CanonicalMarketSnapshot) -> Decimal | None:
    """Open of the session's first bar, for expected-move consumption.

    Bars can include pre-market minutes, so this takes the first bar *at or
    after* the open when the calendar knows where the open was; the earliest bar
    otherwise. Using an overnight bar's open would inflate consumption on every
    gap day.
    """
    if not snapshot.bars_1m:
        return None
    if snapshot.minutes_from_open is None:
        return snapshot.bars_1m[0].open
    session_start = snapshot.timestamp.timestamp() - snapshot.minutes_from_open * 60
    for bar in snapshot.bars_1m:
        if bar.timestamp.timestamp() >= session_start:
            return bar.open
    return snapshot.bars_1m[0].open
