"""Generative constants for SPY-DER synthetic universes.

Ported from 0DTE ``matrix_universe`` (DGator86/0DTE @ 6393cd1) under the
full-stack migration: SPY-DER owns synthetic-universe production outright, so
the constants live here rather than behind a cross-repository provider.

The values are preserved verbatim from System A so a universe generated at a
given ``(seed, archetype, tilt, vol, jitter, generation)`` reproduces the same
world it did before the migration. :func:`simulator_config_hash` fingerprints
the actual constants, so a forgotten ``SIMULATOR_VERSION`` bump cannot hide a
changed model.

Two Markov layers stack:

* **Layer 2 — day archetypes** (:data:`ARCHETYPES`): ordinary tapes persist,
  stress tapes decay back toward calm, matching how vol regimes actually resolve.
* **Layer 1 — intraday regimes** (:data:`REGIMES`): per-minute rows conditioned
  on the day's archetype.

Driver variables (gex / rv / vrp / skew / drift) then run their own 3-state
chains conditioned on the regime — that residual randomness is what makes the
lattice combinatoric rather than a deterministic function of the regime.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from spy_der.contracts.common import content_hash

__all__ = [
    "ARCHETYPES",
    "ARCH_TRANSITION",
    "BREAKOUT_P_UP",
    "GEN_PARAMS",
    "MINUTES_PER_DAY",
    "MINUTES_PER_YEAR",
    "REGIMES",
    "SIMULATOR_VERSION",
    "VAR_NAMES",
    "VAR_OU_NOISE",
    "VAR_OU_THETA",
    "VAR_PREFERENCE",
    "VAR_PULL",
    "VAR_STATES",
    "VAR_STAY",
    "VAR_TARGETS",
    "simulator_config",
    "simulator_config_hash",
    "skew_state",
    "tilt_row",
]

#: Human-readable tag; bump on any generative change. It is a label, NOT the
#: source of truth — :func:`simulator_config_hash` fingerprints the constants.
SIMULATOR_VERSION: Final = "2026.07.26"

MINUTES_PER_DAY: Final = 390
#: 390 minutes x 252 sessions — the System A annualization convention.
MINUTES_PER_YEAR: Final = 98280.0

ARCHETYPES: Final[tuple[str, ...]] = (
    "calm_pin",  # long-gamma, vol suppressed, price pinned
    "grind_up",  # persistent low-vol upward drift
    "grind_down",  # persistent low-vol downward drift
    "range_chop",  # two-sided, regime flips intraday
    "vol_expansion",  # short-gamma, elevated vol, direction unstable
    "squeeze_melt_up",  # short-gamma chase higher, vol up AND price up
    "crash",  # short-gamma cascade lower, heavy vol, gap risk
    "gap_shock",  # large overnight gap then intraday normalization
)

REGIMES: Final[tuple[str, ...]] = (
    "pin",
    "drift_up",
    "drift_down",
    "compression",
    "breakout",
)

VAR_STATES: Final[tuple[str, ...]] = ("low", "mid", "high")
VAR_NAMES: Final[tuple[str, ...]] = ("gex", "rv", "vrp", "skew", "drift")

# --------------------------------------------------------------------------- #
# Layer 2 — day-to-day archetype transitions (row-stochastic)                 #
# --------------------------------------------------------------------------- #
ARCH_TRANSITION: Final[Mapping[str, Mapping[str, float]]] = MappingProxyType(
    {
        "calm_pin": MappingProxyType(
            {
                "calm_pin": 0.55, "grind_up": 0.12, "grind_down": 0.08,
                "range_chop": 0.12, "vol_expansion": 0.08,
                "squeeze_melt_up": 0.02, "crash": 0.01, "gap_shock": 0.02,
            }
        ),
        "grind_up": MappingProxyType(
            {
                "calm_pin": 0.20, "grind_up": 0.45, "grind_down": 0.05,
                "range_chop": 0.12, "vol_expansion": 0.08,
                "squeeze_melt_up": 0.06, "crash": 0.02, "gap_shock": 0.02,
            }
        ),
        "grind_down": MappingProxyType(
            {
                "calm_pin": 0.15, "grind_up": 0.05, "grind_down": 0.40,
                "range_chop": 0.12, "vol_expansion": 0.15,
                "squeeze_melt_up": 0.02, "crash": 0.08, "gap_shock": 0.03,
            }
        ),
        "range_chop": MappingProxyType(
            {
                "calm_pin": 0.22, "grind_up": 0.10, "grind_down": 0.10,
                "range_chop": 0.40, "vol_expansion": 0.10,
                "squeeze_melt_up": 0.03, "crash": 0.02, "gap_shock": 0.03,
            }
        ),
        "vol_expansion": MappingProxyType(
            {
                "calm_pin": 0.10, "grind_up": 0.05, "grind_down": 0.12,
                "range_chop": 0.15, "vol_expansion": 0.35,
                "squeeze_melt_up": 0.08, "crash": 0.10, "gap_shock": 0.05,
            }
        ),
        "squeeze_melt_up": MappingProxyType(
            {
                "calm_pin": 0.15, "grind_up": 0.25, "grind_down": 0.03,
                "range_chop": 0.12, "vol_expansion": 0.20,
                "squeeze_melt_up": 0.20, "crash": 0.02, "gap_shock": 0.03,
            }
        ),
        "crash": MappingProxyType(
            {
                "calm_pin": 0.05, "grind_up": 0.03, "grind_down": 0.20,
                "range_chop": 0.07, "vol_expansion": 0.30,
                "squeeze_melt_up": 0.05, "crash": 0.22, "gap_shock": 0.08,
            }
        ),
        "gap_shock": MappingProxyType(
            {
                "calm_pin": 0.25, "grind_up": 0.10, "grind_down": 0.10,
                "range_chop": 0.20, "vol_expansion": 0.20,
                "squeeze_melt_up": 0.05, "crash": 0.05, "gap_shock": 0.05,
            }
        ),
    }
)

# --------------------------------------------------------------------------- #
# Layer 1 — per-minute intraday regime transitions, per archetype             #
# --------------------------------------------------------------------------- #
# Persistence lives on the diagonal; UniverseSpec.persistence_tilt scales it
# (see `tilt_row`). Rows are per-minute, so a .985 diagonal means a ~65-minute
# mean regime duration.
# Rows are given in REGIMES column order — (pin, drift_up, drift_down,
# compression, breakout) — so the diagonal reads down the table and the
# persistence structure is visible at a glance.
_REGIME_ROWS: Final[dict[str, dict[str, tuple[float, ...]]]] = {
    "calm_pin": {
        "pin": (0.990, 0.002, 0.002, 0.005, 0.001),
        "drift_up": (0.030, 0.960, 0.002, 0.005, 0.003),
        "drift_down": (0.030, 0.002, 0.960, 0.005, 0.003),
        "compression": (0.015, 0.002, 0.002, 0.978, 0.003),
        "breakout": (0.040, 0.010, 0.010, 0.010, 0.930),
    },
    "grind_up": {
        "pin": (0.975, 0.015, 0.002, 0.005, 0.003),
        "drift_up": (0.010, 0.980, 0.002, 0.005, 0.003),
        "drift_down": (0.020, 0.020, 0.950, 0.005, 0.005),
        "compression": (0.008, 0.015, 0.002, 0.970, 0.005),
        "breakout": (0.015, 0.030, 0.005, 0.010, 0.940),
    },
    "grind_down": {
        "pin": (0.975, 0.002, 0.015, 0.005, 0.003),
        "drift_up": (0.020, 0.950, 0.020, 0.005, 0.005),
        "drift_down": (0.010, 0.002, 0.980, 0.005, 0.003),
        "compression": (0.008, 0.002, 0.015, 0.970, 0.005),
        "breakout": (0.015, 0.005, 0.030, 0.010, 0.940),
    },
    "range_chop": {
        "pin": (0.970, 0.010, 0.010, 0.008, 0.002),
        "drift_up": (0.025, 0.940, 0.020, 0.010, 0.005),
        "drift_down": (0.025, 0.020, 0.940, 0.010, 0.005),
        "compression": (0.015, 0.008, 0.008, 0.962, 0.007),
        "breakout": (0.050, 0.015, 0.015, 0.010, 0.910),
    },
    "vol_expansion": {
        "pin": (0.940, 0.015, 0.020, 0.005, 0.020),
        "drift_up": (0.010, 0.950, 0.015, 0.005, 0.020),
        "drift_down": (0.010, 0.010, 0.955, 0.005, 0.020),
        "compression": (0.010, 0.010, 0.010, 0.940, 0.030),
        "breakout": (0.010, 0.015, 0.020, 0.005, 0.950),
    },
    "squeeze_melt_up": {
        "pin": (0.940, 0.035, 0.005, 0.005, 0.015),
        "drift_up": (0.008, 0.972, 0.003, 0.002, 0.015),
        "drift_down": (0.015, 0.040, 0.930, 0.005, 0.010),
        "compression": (0.008, 0.025, 0.002, 0.950, 0.015),
        "breakout": (0.008, 0.040, 0.005, 0.002, 0.945),
    },
    "crash": {
        "pin": (0.920, 0.005, 0.045, 0.005, 0.025),
        "drift_up": (0.010, 0.920, 0.045, 0.005, 0.020),
        "drift_down": (0.005, 0.005, 0.970, 0.002, 0.018),
        "compression": (0.005, 0.005, 0.040, 0.930, 0.020),
        "breakout": (0.005, 0.008, 0.042, 0.002, 0.943),
    },
    "gap_shock": {
        "pin": (0.960, 0.010, 0.010, 0.010, 0.010),
        "drift_up": (0.030, 0.940, 0.010, 0.010, 0.010),
        "drift_down": (0.030, 0.010, 0.940, 0.010, 0.010),
        "compression": (0.020, 0.008, 0.008, 0.954, 0.010),
        "breakout": (0.040, 0.010, 0.010, 0.010, 0.930),
    },
}

REGIME_TRANSITION_RAW: Final[dict[str, dict[str, dict[str, float]]]] = {
    archetype: {
        regime: dict(zip(REGIMES, row, strict=True))
        for regime, row in rows.items()
    }
    for archetype, rows in _REGIME_ROWS.items()
}

REGIME_TRANSITION: Final[Mapping[str, Mapping[str, Mapping[str, float]]]] = MappingProxyType(
    {
        arch: MappingProxyType({r: MappingProxyType(dict(row)) for r, row in rows.items()})
        for arch, rows in REGIME_TRANSITION_RAW.items()
    }
)

# --------------------------------------------------------------------------- #
# Driver variables                                                            #
# --------------------------------------------------------------------------- #
# Skew convention: chain pricing uses s(K) = s_atm - skew*ln(K/F), so POSITIVE
# skew raises put-strike vol (put-heavy) and negative skew is a call bid. Hence
# drift_down/breakout steepen the put skew ("high") and drift_up flattens it
# toward the calls ("low").
VAR_PREFERENCE: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {
        "pin": MappingProxyType(
            {"gex": "high", "rv": "low", "vrp": "high", "skew": "mid", "drift": "mid"}
        ),
        "drift_up": MappingProxyType(
            {"gex": "mid", "rv": "mid", "vrp": "mid", "skew": "low", "drift": "high"}
        ),
        "drift_down": MappingProxyType(
            {"gex": "low", "rv": "mid", "vrp": "mid", "skew": "high", "drift": "low"}
        ),
        "compression": MappingProxyType(
            {"gex": "high", "rv": "low", "vrp": "high", "skew": "mid", "drift": "mid"}
        ),
        # breakout skew is a PLACEHOLDER: the live value is chosen per-minute by
        # `skew_state` from the intended move direction, because an up-breakout
        # bids the calls while a down-breakout steepens the puts. "high" here is
        # only the down-breakout / fallback default.
        "breakout": MappingProxyType(
            {"gex": "low", "rv": "high", "vrp": "low", "skew": "high", "drift": "mid"}
        ),
    }
)

VAR_TARGETS: Final[Mapping[str, Mapping[str, float]]] = MappingProxyType(
    {
        "gex": MappingProxyType({"low": -1.6e9, "mid": 0.4e9, "high": 3.2e9}),
        "rv": MappingProxyType({"low": 0.08, "mid": 0.14, "high": 0.26}),
        # implied / realized
        "vrp": MappingProxyType({"low": 0.92, "mid": 1.10, "high": 1.28}),
        # call-heavy .. put-heavy
        "skew": MappingProxyType({"low": -0.055, "mid": 0.028, "high": 0.075}),
        # fraction of minute-vol
        "drift": MappingProxyType({"low": -0.10, "mid": 0.0, "high": 0.10}),
    }
)

# skew theta is high (0.08 ~= 33% of the gap closed in ~5 min, ~65% in ~12 min)
# so the direction-selected target is actually reached inside a typical 11-20
# min breakout. gex/rv/vrp/drift keep their slower discrete-state cadence.
VAR_OU_THETA: Final[Mapping[str, float]] = MappingProxyType(
    {"gex": 0.06, "rv": 0.04, "vrp": 0.03, "skew": 0.08, "drift": 0.05}
)
VAR_OU_NOISE: Final[Mapping[str, float]] = MappingProxyType(
    {"gex": 0.12e9, "rv": 0.004, "vrp": 0.01, "skew": 0.003, "drift": 0.01}
)
#: Per-minute probability a variable keeps its discrete state.
VAR_STAY: Final = 0.985
#: Extra probability mass moved toward the regime's preferred state.
VAR_PULL: Final = 0.010

#: Per-archetype probability that a breakout resolves UP. Directional
#: archetypes bias both the price path and (via `skew_state`) the smile so the
#: two stay coherent — a crash breaks down and carries a put skew; a squeeze
#: breaks up and bids the calls. Everything else is a symmetric coin.
BREAKOUT_P_UP: Final[Mapping[str, float]] = MappingProxyType(
    {"crash": 0.12, "grind_down": 0.34, "squeeze_melt_up": 0.88, "grind_up": 0.66}
)

#: Every material generative constant in ONE place: the single source of truth
#: read by the generation code AND serialized by :func:`simulator_config`.
#: Changing a value here moves both the generated worlds and the config hash
#: together, so a stored report can never silently diverge from the code that
#: produced it.
GEN_PARAMS: Final[dict[str, Any]] = {
    "session": {
        "start_date": "2026-06-01",
        "lookback_minutes": 2340,
        "pin_overnight_drift_lo": -2,
        "pin_overnight_drift_hi": 3,
    },
    "floors": {"rv": 0.04, "vrp": 0.75},
    "gap": {
        "base_vol": 0.003,
        "mu_by_archetype": {"crash": -0.008, "squeeze_melt_up": 0.005},
        "gap_shock_mu": 0.012,
        "gap_shock_p_down": 0.6,
    },
    "price_process": {
        "pin_pull": 0.012,
        "compression_pull": 0.006,
        "compression_noise": 0.6,
        "drift_base": 0.06,
        "drift_noise": 1.0,
        "breakout_drift": 0.12,
        "breakout_noise": 1.4,
    },
    "dealer_map": {
        "flip_long_offset": -4.0,  # pin + offset when net gamma > 0
        "flip_short_offset": 2.0,  # spot + offset when net gamma <= 0
        "call_wall_offset": 5.0,
        "put_wall_offset": 5.0,
    },
    "bars": {"spread_sigma": 0.0004, "volume_low": 2000, "volume_high": 30000},
    "chain": {
        "strike_span": 25.0,
        "strike_step": 1.0,
        "rate": 0.05,
        "min_vol": 0.0006,
        "spread_base": 0.012,
        "spread_factor": 0.002,
        # smile SHAPE beyond the single skew slope: a quadratic curvature term
        # (both wings lift -> convex smile) and a linear wing lift on |k|.
        # Stress archetypes fatten both, so the Dojo can surface convexity- and
        # wing-shaped failure modes, not just slope.
        "smile_curvature": 0.9,
        "wing_lift": 0.20,
        "stress_curv_mult": 2.2,
        "stress_wing_mult": 1.8,
    },
    "snapshot_map": {
        "iv_to_points": 100.0,
        "vix9d_trend": 1.06,
        "vix9d_calm": 0.94,
        "vix3m_trend": 0.95,
        "vix3m_calm": 1.12,
        "vvix_stressed": 112.0,
        "vvix_trend": 103.0,
        "vvix_calm": 90.0,
        "vvix_baseline": 95.0,
        "tick_stressed": 820.0,
        "tick_trend": 700.0,
        "tick_calm": 450.0,
        "stressed_archetypes": ["crash", "vol_expansion", "gap_shock"],
    },
    "initial_regime": {
        "prefer": {
            "calm_pin": "pin",
            "grind_up": "drift_up",
            "grind_down": "drift_down",
            "range_chop": "pin",
            "vol_expansion": "breakout",
            "squeeze_melt_up": "drift_up",
            "crash": "drift_down",
            "gap_shock": "compression",
        },
        "prefer_prob": 0.7,
    },
    "jitter": {"per_generation": 0.02, "cap": 0.10},
}


def skew_state(regime: str, breakout_dir: float) -> str:
    """Direction-aware skew preference.

    Positive skew is put-heavy (see :data:`VAR_TARGETS`), so an INTENDED upside
    move flattens toward a call bid (``"low"``) and an intended downside move
    steepens the put wing (``"high"``). Drift regimes carry their own
    direction; breakout takes it from the active latent ``breakout_dir``; pin
    and compression stay symmetric.

    This follows the intended/latent direction of the regime, not each minute's
    realized return — a single breakout minute's noise (1.4·sigma) dwarfs its
    0.12·sigma directional drift, so per-minute returns often oppose the skew
    tilt even though the aggregate path leans the intended way.
    """
    if regime == "drift_up":
        return "low"
    if regime == "drift_down":
        return "high"
    if regime == "breakout":
        return "low" if breakout_dir > 0 else "high"
    return "mid"  # pin, compression: symmetric


def tilt_row(row: Mapping[str, float], state: str, tilt: float) -> dict[str, float]:
    """Scale a regime row's diagonal by ``tilt`` and renormalize.

    ``tilt > 1`` makes regimes stickier, ``tilt < 1`` choppier. The diagonal is
    clamped to ``[1e-6, 0.999]`` so a row always stays a proper distribution
    with escape mass available.
    """
    if tilt == 1.0:
        return dict(row)
    stay = min(max(row.get(state, 0.0) * tilt, 1e-6), 0.999)
    leftover = 1.0 - stay
    off = {k: v for k, v in row.items() if k != state}
    off_total = sum(off.values())
    out: dict[str, float] = {state: stay}
    if off_total <= 0.0:
        share = leftover / max(len(off), 1)
        out.update({k: share for k in off})
        return out
    out.update({k: leftover * (v / off_total) for k, v in off.items()})
    return out


def _plain(value: Any) -> Any:
    """Recursively convert MappingProxyType/tuple into plain JSON-able types."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def simulator_config() -> dict[str, Any]:
    """Complete, canonical snapshot of EVERY generative constant.

    Both transition matrices, the variable tables, the breakout-direction
    table, and all the :data:`GEN_PARAMS` blocks. Together with a universe's
    ``(seed, archetype, tilt, vol, jitter, generation)`` this determines the
    world for a given code version.
    """
    return {
        "simulator_version": SIMULATOR_VERSION,
        "owner": "spy_der.synthetic",
        "archetypes": list(ARCHETYPES),
        "regimes": list(REGIMES),
        "var_states": list(VAR_STATES),
        "arch_transition": _plain(ARCH_TRANSITION),
        "regime_transition": _plain(REGIME_TRANSITION),
        "var_preference": _plain(VAR_PREFERENCE),
        "var_targets": _plain(VAR_TARGETS),
        "var_ou_theta": _plain(VAR_OU_THETA),
        "var_ou_noise": _plain(VAR_OU_NOISE),
        "var_stay": VAR_STAY,
        "var_pull": VAR_PULL,
        "breakout_p_up": _plain(BREAKOUT_P_UP),
        "gen_params": _plain(GEN_PARAMS),
        "minutes_per_day": MINUTES_PER_DAY,
        "minutes_per_year": MINUTES_PER_YEAR,
    }


def simulator_config_hash() -> str:
    """Content hash of :func:`simulator_config` — the generative fingerprint."""
    return content_hash(simulator_config())
