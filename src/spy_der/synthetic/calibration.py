"""Regime calibration — fit the synthetic world's transitions to real tape.

Ported from 0DTE ``regime_calibration`` (DGator86/0DTE @ 6393cd1).

The canonical transition matrices in :mod:`spy_der.synthetic.archetypes` are
priors. This module labels observed snapshots — recorded live sessions *or* a
generated world, which is how the labeler itself gets scored — and re-estimates
both Markov layers empirically. The result feeds back into
:class:`~spy_der.synthetic.world.MarkovWorld` as the base layer, with any
generation jitter applied on top.

Labeling a latent regime from one-minute features is approximate by
construction; :func:`labeler_accuracy` measures how approximate against a
world's own ground truth, so a calibration is never trusted blindly.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from spy_der.synthetic.archetypes import (
    ARCH_TRANSITION,
    ARCHETYPES,
    MINUTES_PER_YEAR,
    REGIME_TRANSITION,
    REGIMES,
)

__all__ = [
    "Calibration",
    "LabelConfig",
    "RegimeFeatures",
    "SessionContext",
    "calibrate_from_world",
    "estimate_transitions",
    "label_archetype",
    "label_regime",
    "labeler_accuracy",
]


@dataclass(frozen=True, slots=True)
class RegimeFeatures:
    """Observable window features for one minute."""

    rv_recent: float
    ret_recent: float
    gex_sign: float = 0.0


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Per-session scale so a minute is judged RELATIVE to the day it lives in.

    A pin minute inside a crash day is calm *for that day* even though its
    absolute vol is high. Absolute features track the archetype's vol level, not
    the minute's regime, so session-relative scaling is essential.
    """

    rv_median: float  # median trailing rv over the session
    window: int = 15  # feature window (minutes)


@dataclass(frozen=True, slots=True)
class LabelConfig:
    """Thresholds mapping session-relative features to simulator states.

    Tuned against the simulator's own ground truth; exposed so a recalibration
    can retune against real internals without a code change.
    """

    breakout_rv_z: float = 1.7  # rv >= this * session median = breakout
    compression_rv_z: float = 0.6  # rv <= this * session median = compression
    trend_z: float = 0.6  # |move| >= this * expected window move = drift
    abs_rv_ref: float = 0.14  # fallback session rv when no context given
    abs_move_ref: float = 0.0009  # fallback expected window move
    # archetype layer (day aggregates; net/gap are stride-robust, rv relative)
    arch_hi_rv_z: float = 1.20  # day rv >= this * cross-session median = stressed
    arch_lo_rv_z: float = 0.85  # day rv <= this * median = quiet
    arch_gap_abs: float = 0.006  # |overnight gap| above this = gap_shock
    arch_crash_net: float = 0.008  # day net <= -this + stressed = crash
    arch_squeeze_net: float = 0.008  # day net >= this + stressed = squeeze
    arch_grind_net: float = 0.004  # |day net| above this = grind
    arch_calm_gex: float = 0.4  # mean gex sign above this + quiet = calm_pin


def label_regime(
    features: RegimeFeatures,
    ctx: SessionContext | None = None,
    cfg: LabelConfig | None = None,
) -> str:
    """Map observable features to one of the five regimes, session-relative.

    Priority: breakout (locally much more volatile) > compression (locally much
    calmer) > directional drift (a move beyond the day's expected window move) >
    pin.
    """
    cfg = cfg or LabelConfig()
    if ctx is not None and ctx.rv_median > 0:
        rv_z = features.rv_recent / ctx.rv_median
        move_scale = ctx.rv_median / math.sqrt(MINUTES_PER_YEAR) * math.sqrt(ctx.window)
    else:
        rv_z = features.rv_recent / cfg.abs_rv_ref
        move_scale = cfg.abs_move_ref
    ret_n = features.ret_recent / move_scale if move_scale > 0 else 0.0

    if rv_z >= cfg.breakout_rv_z:
        return "breakout"
    if rv_z <= cfg.compression_rv_z:
        return "compression"
    if ret_n >= cfg.trend_z:
        return "drift_up"
    if ret_n <= -cfg.trend_z:
        return "drift_down"
    return "pin"


def label_archetype(
    day_returns: Sequence[float],
    day_rv: float,
    gap: float,
    mean_gex_sign: float,
    rv_ref: float | None = None,
    cfg: LabelConfig | None = None,
) -> str:
    """Map a session's aggregate to one of the eight archetypes.

    ``net`` (total day move) and ``|gap|`` are stride-robust and do the heavy
    separation; rv is only used relative to ``rv_ref`` (the cross-session
    median) so the stressed/quiet cut does not depend on sampling stride.
    """
    cfg = cfg or LabelConfig()
    net = float(sum(day_returns)) if day_returns else 0.0
    ref = rv_ref or day_rv or 1e-9
    hi_vol = day_rv >= cfg.arch_hi_rv_z * ref
    lo_vol = day_rv <= cfg.arch_lo_rv_z * ref
    if abs(gap) >= cfg.arch_gap_abs:
        return "gap_shock"
    if hi_vol and net <= -cfg.arch_crash_net:
        return "crash"
    if hi_vol and net >= cfg.arch_squeeze_net:
        return "squeeze_melt_up"
    if hi_vol:
        return "vol_expansion"
    if net >= cfg.arch_grind_net:
        return "grind_up"
    if net <= -cfg.arch_grind_net:
        return "grind_down"
    if lo_vol and mean_gex_sign > cfg.arch_calm_gex:
        return "calm_pin"
    return "range_chop"


def estimate_transitions(
    sequences: Iterable[Sequence[str]],
    states: Sequence[str],
    smoothing: float = 0.5,
    prior: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    """Empirical row-stochastic transition matrix from label sequences.

    ``smoothing`` is a Laplace alpha added to every count; keeping it above zero
    means an unobserved transition never gets a hard zero, so a calibrated
    matrix cannot trap the generator in a state. Rows whose source state was
    never observed fall back to ``prior`` verbatim (renormalized), else to a
    smoothing-only uniform row.

    Deterministic; no RNG.
    """
    counts: dict[str, dict[str, float]] = {
        s: dict.fromkeys(states, 0.0) for s in states
    }
    seen = dict.fromkeys(states, False)
    for sequence in sequences:
        for a, b in itertools.pairwise(sequence):
            if a in counts and b in counts[a]:
                counts[a][b] += 1.0
                seen[a] = True

    out: dict[str, dict[str, float]] = {}
    for s in states:
        if not seen[s] and prior and s in prior:
            row = {t: float(prior[s].get(t, 0.0)) for t in states}
            total = sum(row.values()) or 1.0
            out[s] = {t: v / total for t, v in row.items()}
            continue
        smoothed = {t: counts[s][t] + smoothing for t in states}
        total = sum(smoothed.values())
        out[s] = {t: v / total for t, v in smoothed.items()}
    return out


@dataclass(frozen=True, slots=True)
class Calibration:
    """Both empirically re-estimated Markov layers plus provenance."""

    arch_transition: dict[str, dict[str, float]]
    regime_transition: dict[str, dict[str, dict[str, float]]]
    n_sessions: int
    n_minutes: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "arch_transition": self.arch_transition,
            "regime_transition": self.regime_transition,
            "n_sessions": self.n_sessions,
            "n_minutes": self.n_minutes,
            "source": self.source,
        }


def _rv_window(returns: Sequence[float], end: int, window: int) -> float:
    """Annualized realized vol over the ``window`` minutes ending at ``end``."""
    lo = max(end - window, 0)
    seg = returns[lo:end]
    if len(seg) < 2:
        return 0.0
    mean = sum(seg) / len(seg)
    var = sum((r - mean) ** 2 for r in seg) / (len(seg) - 1)
    return math.sqrt(max(var, 0.0) * MINUTES_PER_YEAR)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


@dataclass(frozen=True, slots=True)
class _LabeledSession:
    session: str
    archetype: str
    regimes: list[str]


def _label_sessions(
    observations: Sequence[tuple[str, float, float]],
    *,
    window: int,
    cfg: LabelConfig,
) -> list[_LabeledSession]:
    """Label ``(session, spot, gex)`` observations into per-session sequences."""
    by_session: dict[str, list[tuple[float, float]]] = {}
    for session, spot, gex in observations:
        by_session.setdefault(session, []).append((spot, gex))

    ordered = sorted(by_session)
    day_rvs: dict[str, float] = {}
    day_returns: dict[str, list[float]] = {}
    for session in ordered:
        spots = [s for s, _ in by_session[session]]
        rets = [
            math.log(spots[i] / spots[i - 1])
            for i in range(1, len(spots))
            if spots[i] > 0 and spots[i - 1] > 0
        ]
        day_returns[session] = rets
        day_rvs[session] = _rv_window(rets, len(rets), max(len(rets), 2))
    rv_ref = _median([day_rvs[s] for s in ordered]) or None

    out: list[_LabeledSession] = []
    prev_close: float | None = None
    for session in ordered:
        rows = by_session[session]
        spots = [s for s, _ in rows]
        gexes = [g for _, g in rows]
        rets = day_returns[session]
        gap = 0.0
        if prev_close and prev_close > 0 and spots:
            gap = math.log(spots[0] / prev_close)
        mean_gex_sign = (
            sum(1.0 if g > 0 else -1.0 for g in gexes) / len(gexes) if gexes else 0.0
        )
        archetype = label_archetype(
            rets, day_rvs[session], gap, mean_gex_sign, rv_ref=rv_ref, cfg=cfg
        )

        rv_series = [_rv_window(rets, i, window) for i in range(1, len(rets) + 1)]
        ctx = SessionContext(rv_median=_median([v for v in rv_series if v > 0]), window=window)
        regimes: list[str] = []
        for i in range(1, len(rets) + 1):
            lo = max(i - window, 0)
            ret_recent = sum(rets[lo:i])
            regimes.append(
                label_regime(
                    RegimeFeatures(
                        rv_recent=rv_series[i - 1],
                        ret_recent=ret_recent,
                        gex_sign=1.0 if gexes[min(i, len(gexes) - 1)] > 0 else -1.0,
                    ),
                    ctx,
                    cfg,
                )
            )
        out.append(_LabeledSession(session=session, archetype=archetype, regimes=regimes))
        if spots:
            prev_close = spots[-1]
    return out


def calibrate_from_observations(
    observations: Sequence[tuple[str, float, float]],
    *,
    window: int = 15,
    smoothing: float = 0.5,
    cfg: LabelConfig | None = None,
    source: str = "observations",
) -> Calibration:
    """Calibrate both layers from ``(session_iso, spot, net_gex)`` rows.

    This is the real-data path: the same routine consumes recorded SPY-DER
    market snapshots on the VPS and a generated world in tests, so the labeler
    can be scored before it is trusted.
    """
    cfg = cfg or LabelConfig()
    labeled = _label_sessions(observations, window=window, cfg=cfg)

    arch_sequence = [ls.archetype for ls in labeled]
    arch_transition = estimate_transitions(
        [arch_sequence], list(ARCHETYPES), smoothing=smoothing, prior=ARCH_TRANSITION
    )

    by_archetype: dict[str, list[list[str]]] = {}
    for ls in labeled:
        by_archetype.setdefault(ls.archetype, []).append(ls.regimes)

    regime_transition: dict[str, dict[str, dict[str, float]]] = {}
    for archetype in ARCHETYPES:
        prior = REGIME_TRANSITION[archetype]
        regime_transition[archetype] = estimate_transitions(
            by_archetype.get(archetype, []),
            list(REGIMES),
            smoothing=smoothing,
            prior=prior,
        )

    return Calibration(
        arch_transition=arch_transition,
        regime_transition=regime_transition,
        n_sessions=len(labeled),
        n_minutes=sum(len(ls.regimes) for ls in labeled),
        source=source,
    )


def calibrate_from_world(
    world: Any,
    *,
    window: int = 15,
    smoothing: float = 0.5,
    cfg: LabelConfig | None = None,
) -> Calibration:
    """Calibrate from a generated :class:`~spy_der.synthetic.world.MarkovWorld`.

    Used to validate the calibration path end to end: the world's ground-truth
    labels are available, so :func:`labeler_accuracy` can score what the
    observable-feature labeler recovered.
    """
    observations = [(t.session, t.spot, t.net_gex) for t in world.ticks()]
    return calibrate_from_observations(
        observations,
        window=window,
        smoothing=smoothing,
        cfg=cfg,
        source=f"world:{world.spec.universe_id}",
    )


def labeler_accuracy(
    world: Any,
    *,
    window: int = 15,
    cfg: LabelConfig | None = None,
) -> dict[str, Any]:
    """Score the observable-feature labeler against a world's ground truth.

    Latent-state recovery from one-minute features is approximate by
    construction; this quantifies it so a calibration's weight can be set
    honestly instead of assumed.
    """
    cfg = cfg or LabelConfig()
    ticks = world.ticks()
    observations = [(t.session, t.spot, t.net_gex) for t in ticks]
    labeled = _label_sessions(observations, window=window, cfg=cfg)

    truth_arch = {}
    truth_regimes: dict[str, list[str]] = {}
    for tick in ticks:
        truth_arch[tick.session] = tick.archetype
        truth_regimes.setdefault(tick.session, []).append(tick.regime)

    arch_hits = sum(1 for ls in labeled if truth_arch.get(ls.session) == ls.archetype)
    regime_hits = 0
    regime_total = 0
    for ls in labeled:
        truth = truth_regimes.get(ls.session, [])
        # Labeled regimes start at the second minute (they need a return), so
        # align against truth[1:].
        for predicted, actual in zip(ls.regimes, truth[1:], strict=False):
            regime_total += 1
            if predicted == actual:
                regime_hits += 1

    return {
        "n_sessions": len(labeled),
        "archetype_accuracy": arch_hits / len(labeled) if labeled else None,
        "n_minutes": regime_total,
        "regime_accuracy": regime_hits / regime_total if regime_total else None,
    }
