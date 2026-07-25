"""Hierarchical-Markov synthetic world generation.

Ported from 0DTE ``matrix_universe.MarkovWorldFeed`` and
``synthetic_world.CoupledSyntheticFeed`` (DGator86/0DTE @ 6393cd1).

The world is **coupled**, not a random walk with a frozen surface: net GEX
drives the price dynamics, and the option chain is repriced every tick off the
current spot and time-to-close with a regime-dependent vol-risk premium.

    net GEX (OU, regime-switching)  --->  price dynamics
          gex > 0  ->  mean-reversion toward the pin (suppressed vol)
          gex <= 0 ->  persistent directional drift (elevated vol)
    price + regime                  --->  chain repriced EVERY tick off current
                                          spot, T = time to 16:00, with a
                                          regime-dependent vol-risk premium
    the realized path               --->  settlement = the day's close

So the pipeline's predictions are testable in principle on this feed: premium
selling should earn the VRP on pin days and get hurt on trend days. It is still
a model — passing here does not prove live edge; failing here means the
machinery cannot exploit a world built from its own thesis.

Unlike the 0DTE provider it replaces, :class:`MarkovWorld` implements
:class:`~spy_der.market_data.providers.base.MarketDataProvider` and emits
:class:`~spy_der.market_data.providers.base.RawTick`. Synthetic data therefore
traverses the *same* ``CompositeFeed -> CanonicalSnapshotAssembler`` path as
live provider data, instead of being injected downstream as pre-built packets.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from spy_der.contracts.market import Bar, OptionContract, OptionQuote, OptionType
from spy_der.market_data.providers.base import MarketDataProvider, RawTick
from spy_der.synthetic.archetypes import (
    ARCH_TRANSITION,
    BREAKOUT_P_UP,
    GEN_PARAMS,
    MINUTES_PER_DAY,
    MINUTES_PER_YEAR,
    REGIME_TRANSITION,
    REGIMES,
    skew_state,
    tilt_row,
)
from spy_der.synthetic.chains import VariableChain, normalize_row
from spy_der.synthetic.pricing import (
    black_call_forward,
    black_greeks_forward,
    black_put_forward,
    smile_vol,
)
from spy_der.synthetic.universe import UniverseSpec, dirichlet_rows

__all__ = ["MarkovWorld", "SituationLabel", "WorldTick"]

ET = ZoneInfo("America/New_York")
_CLOSE_MINUTE = 390  # 09:30 + 390 minutes = 16:00 ET


@dataclass(frozen=True, slots=True)
class SituationLabel:
    """Ground-truth ``(session, archetype, regime)`` label for one minute."""

    session: str
    archetype: str
    regime: str


@dataclass(frozen=True, slots=True)
class WorldTick:
    """One generated minute: price, latent drivers, and dealer map.

    These are the *ground-truth* latents. They are what parity and Dojo
    evaluation score the pipeline's estimates against; the pipeline itself only
    ever sees the :class:`RawTick` derived from them.
    """

    timestamp: dt.datetime
    session: str
    archetype: str
    regime: str
    spot: float
    pin: float
    net_gex: float
    realized_vol: float
    implied_vol: float
    vrp: float
    skew: float
    gamma_flip: float
    call_wall: float
    put_wall: float
    minutes_to_close: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "session": self.session,
            "archetype": self.archetype,
            "regime": self.regime,
            "spot": self.spot,
            "pin": self.pin,
            "net_gex": self.net_gex,
            "realized_vol": self.realized_vol,
            "implied_vol": self.implied_vol,
            "vrp": self.vrp,
            "skew": self.skew,
            "gamma_flip": self.gamma_flip,
            "call_wall": self.call_wall,
            "put_wall": self.put_wall,
            "minutes_to_close": self.minutes_to_close,
        }


def _initial_regime(archetype: str, rng: np.random.Generator) -> str:
    """Archetype-preferred opening regime, chosen with ``prefer_prob``."""
    rules = GEN_PARAMS["initial_regime"]
    preferred = str(rules["prefer"].get(archetype, "pin"))
    if float(rng.random()) < float(rules["prefer_prob"]):
        return preferred
    others = [r for r in REGIMES if r != preferred]
    return str(rng.choice(others))


class MarkovWorld(MarketDataProvider):
    """A deterministic synthetic market that satisfies ``MarketDataProvider``.

    Same :class:`UniverseSpec` in, identical world out. Each driver variable
    gets an independent RNG stream; per-tick ``(archetype, regime)`` labels are
    retained in :attr:`situation_log` and day-level archetypes in
    :attr:`day_archetype`, so evaluation can score against ground truth.
    """

    def __init__(
        self,
        spec: UniverseSpec,
        *,
        symbol: str = "SPY",
        arch_transition: dict[str, dict[str, float]] | None = None,
        regime_transition: dict[str, dict[str, dict[str, float]]] | None = None,
    ) -> None:
        # Optional CALIBRATED transition matrices (see
        # spy_der.synthetic.calibration) replace the canonical priors as the
        # base layer; Dirichlet jitter, if any, is applied on top.
        self.spec = spec
        self.symbol = symbol
        self._arch_override = arch_transition
        self._regime_override = regime_transition
        self.situation_log: list[SituationLabel] = []
        self.day_archetype: dict[str, str] = {}
        self.day_close: dict[str, float] = {}
        self.regime_minutes: dict[str, dict[str, int]] = {}
        self._ticks: list[WorldTick] = []
        self._by_ts: dict[dt.datetime, WorldTick] = {}
        self._generate()

    # -- MarketDataProvider --------------------------------------------------
    @property
    def name(self) -> str:
        return f"synthetic:{self.spec.universe_id}"

    def fetch(self, timestamp: dt.datetime) -> RawTick | None:
        """Return the synthetic observation at ``timestamp``, or ``None``."""
        tick = self._by_ts.get(timestamp)
        if tick is None:
            return None
        return self.raw_tick(tick)

    # -- world access --------------------------------------------------------
    def timestamps(self) -> tuple[dt.datetime, ...]:
        """Every generated minute, honoring ``spec.tick_stride``."""
        return tuple(t.timestamp for t in self._ticks[:: self.spec.tick_stride])

    def ticks(self) -> tuple[WorldTick, ...]:
        """Ground-truth latents for every generated minute (stride applied)."""
        return tuple(self._ticks[:: self.spec.tick_stride])

    def world_tick(self, timestamp: dt.datetime) -> WorldTick | None:
        return self._by_ts.get(timestamp)

    def settlement(self, session: str) -> float | None:
        """The realized close for ``session`` — the settlement truth."""
        return self.day_close.get(session)

    def coverage(self) -> dict[str, dict[str, int]]:
        """``archetype -> regime -> minutes`` occupancy for this world."""
        return {a: dict(r) for a, r in self.regime_minutes.items()}

    # -- generation ----------------------------------------------------------
    def _transition_layers(
        self, streams: list[np.random.Generator]
    ) -> tuple[
        dict[str, dict[str, float]],
        dict[str, dict[str, dict[str, float]]],
    ]:
        base_arch: dict[str, dict[str, float]] = self._arch_override or {
            k: dict(v) for k, v in ARCH_TRANSITION.items()
        }
        base_regime: dict[str, dict[str, dict[str, float]]] = self._regime_override or {
            a: {r: dict(row) for r, row in rows.items()}
            for a, rows in REGIME_TRANSITION.items()
        }
        if self.spec.transition_jitter <= 0.0:
            return base_arch, base_regime
        rng_jit = streams[8]
        arch_t = dirichlet_rows(base_arch, rng_jit, self.spec.transition_jitter)
        regime_t = {
            archetype: dirichlet_rows(rows, rng_jit, self.spec.transition_jitter)
            for archetype, rows in base_regime.items()
        }
        return arch_t, regime_t

    def _generate(self) -> None:
        sp = self.spec
        master = np.random.default_rng(sp.seed)
        # Independent stream per layer and per variable. `spawn` is
        # index-stable: the first 8 children are identical whether 8 or 9 are
        # drawn, so jitter=0 worlds match earlier generations exactly.
        streams = list(master.spawn(9))
        rng_arch, rng_regime, rng_path, rng_micro = streams[:4]
        arch_t, regime_t = self._transition_layers(streams)

        chains = {
            "gex": VariableChain("gex", streams[4]),
            "rv": VariableChain("rv", streams[5], scale=sp.vol_mult),
            "vrp": VariableChain("vrp", streams[6]),
            "skew": VariableChain("skew", streams[7]),
            "drift": VariableChain("drift", np.random.default_rng(sp.seed + 99)),
        }

        gp = GEN_PARAMS
        sess, gapp, pp = gp["session"], gp["gap"], gp["price_process"]
        dealer, floors = gp["dealer_map"], gp["floors"]

        spot = sp.base_spot
        pin = float(round(spot))
        year, month, day = (int(x) for x in str(sess["start_date"]).split("-"))
        day0 = dt.date(year, month, day)
        archetype = sp.start_archetype

        offset = 0
        made = 0
        while made < sp.days:
            session_date = day0 + dt.timedelta(days=offset)
            offset += 1
            if session_date.weekday() >= 5:
                continue
            made += 1
            iso = session_date.isoformat()

            if made > 1:
                row = arch_t[archetype]
                archetype = str(rng_arch.choice(list(row), p=normalize_row(row)))
            self.day_archetype[iso] = archetype
            occupancy = self.regime_minutes.setdefault(
                archetype, dict.fromkeys(REGIMES, 0)
            )

            # Overnight gap, archetype-conditioned. gap_shock gaps BOTH ways
            # (down-biased 60/40) — a shock archetype, not a crash rehearsal.
            gap_vol = float(gapp["base_vol"]) * sp.gap_mult
            gap_mu = float(gapp["mu_by_archetype"].get(archetype, 0.0))
            if archetype == "gap_shock":
                sign = -1.0 if float(rng_path.random()) < float(gapp["gap_shock_p_down"]) else 1.0
                gap_mu = float(gapp["gap_shock_mu"]) * sign
            spot *= math.exp(float(rng_path.normal(gap_mu, gap_vol)))
            pin = float(
                round(
                    pin
                    + int(
                        rng_path.integers(
                            int(sess["pin_overnight_drift_lo"]),
                            int(sess["pin_overnight_drift_hi"]),
                        )
                    )
                )
            )

            regime = _initial_regime(archetype, rng_regime)
            open_dt = dt.datetime(
                session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=ET
            )
            breakout_dir = self._breakout_direction(archetype, rng_path)

            for minute in range(MINUTES_PER_DAY):
                base_row = regime_t[archetype][regime]
                row = tilt_row(base_row, regime, sp.persistence_tilt)
                new_regime = str(rng_regime.choice(list(row), p=normalize_row(row)))
                if new_regime == "breakout" and regime != "breakout":
                    breakout_dir = self._breakout_direction(archetype, rng_path)
                regime = new_regime
                occupancy[regime] = occupancy.get(regime, 0) + 1

                net_gex = chains["gex"].step(regime)
                realized_vol = max(chains["rv"].step(regime), float(floors["rv"]))
                vrp = max(chains["vrp"].step(regime), float(floors["vrp"]))
                # skew tracks the intended/latent move direction, not the
                # realized minute return (see archetypes.skew_state).
                skew = chains["skew"].step(regime, skew_state(regime, breakout_dir))
                drift = chains["drift"].step(regime)

                sig_min = realized_vol / math.sqrt(MINUTES_PER_YEAR)
                z = float(rng_micro.standard_normal())
                step = self._price_step(
                    regime=regime,
                    pp=pp,
                    spot=spot,
                    pin=pin,
                    sig_min=sig_min,
                    z=z,
                    drift=drift,
                    breakout_dir=breakout_dir,
                )
                spot *= 1.0 + step

                # The flip sits below spot when dealers are long gamma and
                # chases from above when they are short — the live convention.
                if net_gex > 0.0:
                    gamma_flip = pin + float(dealer["flip_long_offset"])
                else:
                    gamma_flip = spot + float(dealer["flip_short_offset"])

                timestamp = open_dt + dt.timedelta(minutes=minute)
                self._ticks.append(
                    WorldTick(
                        timestamp=timestamp,
                        session=iso,
                        archetype=archetype,
                        regime=regime,
                        spot=spot,
                        pin=pin,
                        net_gex=net_gex,
                        realized_vol=realized_vol,
                        implied_vol=realized_vol * vrp,
                        vrp=vrp,
                        skew=skew,
                        gamma_flip=gamma_flip,
                        call_wall=pin + float(dealer["call_wall_offset"]),
                        put_wall=pin - float(dealer["put_wall_offset"]),
                        minutes_to_close=_CLOSE_MINUTE - minute,
                    )
                )
                self.situation_log.append(SituationLabel(iso, archetype, regime))

            self.day_close[iso] = spot

        self._by_ts = {t.timestamp: t for t in self._ticks}

    @staticmethod
    def _breakout_direction(archetype: str, rng: np.random.Generator) -> float:
        """``+1`` (up) or ``-1`` (down), biased by the day's archetype."""
        p_up = BREAKOUT_P_UP.get(archetype, 0.5)
        return 1.0 if float(rng.random()) < p_up else -1.0

    @staticmethod
    def _price_step(
        *,
        regime: str,
        pp: dict[str, Any],
        spot: float,
        pin: float,
        sig_min: float,
        z: float,
        drift: float,
        breakout_dir: float,
    ) -> float:
        """One minute's fractional return under the regime's price process."""
        if regime == "pin":
            return float(pp["pin_pull"]) * (pin - spot) / spot + sig_min * z
        if regime == "compression":
            return (
                float(pp["compression_pull"]) * (pin - spot) / spot
                + float(pp["compression_noise"]) * sig_min * z
            )
        if regime == "drift_up":
            return (float(pp["drift_base"]) + max(drift, 0.0)) * sig_min + float(
                pp["drift_noise"]
            ) * sig_min * z
        if regime == "drift_down":
            return -(float(pp["drift_base"]) + max(-drift, 0.0)) * sig_min + float(
                pp["drift_noise"]
            ) * sig_min * z
        # breakout
        return breakout_dir * float(pp["breakout_drift"]) * sig_min + float(
            pp["breakout_noise"]
        ) * sig_min * z

    # -- chain repricing -----------------------------------------------------
    def raw_tick(self, tick: WorldTick) -> RawTick:
        """Reprice the chain off ``tick`` and wrap it as a :class:`RawTick`."""
        chain = self.option_chain(tick)
        bar = self._bar(tick)
        return RawTick(
            provider=self.name,
            symbol=self.symbol,
            observed_at=tick.timestamp,
            underlying_price=_dec(tick.spot),
            underlying_bid=_dec(tick.spot * (1.0 - 0.00005)),
            underlying_ask=_dec(tick.spot * (1.0 + 0.00005)),
            bars_1m=(bar,),
            option_chain=chain,
            has_chain=bool(chain),
        )

    def _bar(self, tick: WorldTick) -> Bar:
        """A single-minute OHLCV bar consistent with the tick's spot and vol."""
        barp = GEN_PARAMS["bars"]
        sigma = tick.realized_vol / math.sqrt(MINUTES_PER_YEAR)
        half = max(tick.spot * sigma, tick.spot * float(barp["spread_sigma"])) * 0.5
        stressed = tick.regime in ("breakout", "drift_down", "drift_up")
        volume = int(barp["volume_high"]) if stressed else int(barp["volume_low"])
        return Bar(
            timestamp=tick.timestamp,
            open=_dec(tick.spot - half * 0.5),
            high=_dec(tick.spot + half),
            low=_dec(tick.spot - half),
            close=_dec(tick.spot),
            volume=volume,
        )

    def option_chain(self, tick: WorldTick) -> tuple[OptionQuote, ...]:
        """Reprice the full 0DTE chain off the tick's spot, vol surface and T.

        Strikes span ``+/- strike_span`` around spot at ``strike_step``. Each
        strike's total vol comes from the smile
        ``s(k) = s_atm - skew*k + curv*k^2 + wing*|k|``; stress archetypes
        fatten both the curvature and the wings.
        """
        chain_cfg = GEN_PARAMS["chain"]
        session_date = dt.date.fromisoformat(tick.session)
        # T in years off the SAME 390-minute convention as the vol scaling, so
        # the surface and the path share one clock.
        minutes_left = max(tick.minutes_to_close, 0)
        years = max(minutes_left, 1) / MINUTES_PER_YEAR
        rate = float(chain_cfg["rate"])
        forward = tick.spot * math.exp(rate * years)
        s_atm = tick.implied_vol * math.sqrt(years)

        stressed = tick.archetype in set(GEN_PARAMS["snapshot_map"]["stressed_archetypes"])
        curv = float(chain_cfg["smile_curvature"])
        wing = float(chain_cfg["wing_lift"])
        if stressed:
            curv *= float(chain_cfg["stress_curv_mult"])
            wing *= float(chain_cfg["stress_wing_mult"])
        min_vol = float(chain_cfg["min_vol"])
        span = float(chain_cfg["strike_span"])
        step = float(chain_cfg["strike_step"])
        spread_base = float(chain_cfg["spread_base"])
        spread_factor = float(chain_cfg["spread_factor"])

        quotes: list[OptionQuote] = []
        low = math.floor((tick.spot - span) / step) * step
        n_strikes = round((2.0 * span) / step) + 1
        for i in range(n_strikes):
            strike = low + i * step
            if strike <= 0.0:
                continue
            k = math.log(strike / forward)
            total_vol = smile_vol(s_atm, tick.skew, curv, wing, k, min_vol)
            annualized = total_vol / math.sqrt(years)
            for option_type in (OptionType.CALL, OptionType.PUT):
                is_call = option_type is OptionType.CALL
                fwd_value = (
                    black_call_forward(forward, strike, total_vol)
                    if is_call
                    else black_put_forward(forward, strike, total_vol)
                )
                mark = max(fwd_value * math.exp(-rate * years), 0.0)
                half_spread = 0.5 * (spread_base + spread_factor * abs(k) * tick.spot)
                delta, gamma, theta, vega = black_greeks_forward(
                    forward, strike, total_vol, years=years, is_call=is_call
                )
                # Open interest peaks at the pin and decays with |K - pin|:
                # the dealer map the world's GEX process assumes.
                oi = max(round(20_000.0 * math.exp(-abs(strike - tick.pin) / 6.0)), 1)
                quotes.append(
                    OptionQuote(
                        contract=OptionContract(
                            contract_id=(
                                f"{self.symbol}{session_date:%y%m%d}"
                                f"{'C' if is_call else 'P'}{round(strike * 1000):08d}"
                            ),
                            underlying_symbol=self.symbol,
                            expiration=session_date,
                            option_type=option_type,
                            strike=_dec(strike),
                        ),
                        received_at=tick.timestamp,
                        source=self.name,
                        bid=_dec(max(mark - half_spread, 0.0)),
                        ask=_dec(mark + half_spread),
                        mark=_dec(mark),
                        last=_dec(mark),
                        volume=oi // 10,
                        open_interest=oi,
                        implied_volatility=annualized,
                        delta=delta,
                        gamma=gamma,
                        theta=theta,
                        vega=vega,
                        observed_at=tick.timestamp,
                        age_seconds=0.0,
                    )
                )
        return tuple(quotes)


def _dec(value: float) -> Decimal:
    """Quantize to cents — the precision a real chain quotes at."""
    return Decimal(f"{value:.4f}")
