"""State-conditioned empirical path simulation (master spec §29).

Bounded Phase 6 form migrated from System A ``prediction/path_model.py`` and
``path_model_v3.py``: residual library, deterministic snapshot-derived seeds,
adverse-first barrier scoring, and ``PathForecastV3``. Full conditioning
backoff hierarchy is simplified to unconditioned block bootstrap with an
explicit Gaussian Level-6 fallback.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "PATH_MODEL_VERSION",
    "PathEventResult",
    "PathForecastV3",
    "PathModelV3Config",
    "ResidualLibrary",
    "build_residual_library",
    "derive_path_seed",
    "forecast_from_paths",
    "score_path_events",
    "simulate_paths_v3",
    "standardize_returns",
]

PATH_MODEL_VERSION = "v3.0.0"


@dataclass
class PathModelV3Config:
    block_min: int = 5
    block_max: int = 15
    n_paths_shadow: int = 500
    n_paths_offline: int = 2000
    n_paths_test: int = 100
    min_library_residuals: int = 30
    allow_gaussian_fallback: bool = True
    same_step_adverse_first: bool = True
    epsilon: float = 1e-12

    def n_paths_for(self, mode: str = "test") -> int:
        return {
            "shadow": self.n_paths_shadow,
            "offline": self.n_paths_offline,
            "test": self.n_paths_test,
        }.get(mode, self.n_paths_test)


@dataclass
class ResidualLibrary:
    residuals: np.ndarray
    session_id: np.ndarray
    session_spans: list[tuple[int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.residuals = np.asarray(self.residuals, dtype=float)
        self.session_id = np.asarray(self.session_id)
        if not self.session_spans:
            self.session_spans = _infer_session_spans(self.session_id)

    def __len__(self) -> int:
        return int(self.residuals.size)


def _infer_session_spans(session_id: np.ndarray) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    if session_id.size == 0:
        return spans
    start = 0
    cur = session_id[0]
    for i in range(1, len(session_id)):
        if session_id[i] != cur:
            spans.append((start, i))
            start = i
            cur = session_id[i]
    spans.append((start, len(session_id)))
    return spans


def standardize_returns(returns: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    r = np.asarray(returns, dtype=float)
    mu = float(np.nanmean(r))
    sd = float(np.nanstd(r))
    if not math.isfinite(sd) or sd < eps:
        sd = eps
    out = (r - mu) / sd
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def build_residual_library(returns_by_session: Mapping[str, Sequence[float]]) -> ResidualLibrary:
    residuals: list[float] = []
    session_ids: list[str] = []
    for sid, rets in sorted(returns_by_session.items()):
        arr = standardize_returns(np.asarray(rets, dtype=float))
        residuals.extend(float(x) for x in arr)
        session_ids.extend([str(sid)] * len(arr))
    return ResidualLibrary(
        residuals=np.asarray(residuals, dtype=float),
        session_id=np.asarray(session_ids),
    )


def derive_path_seed(
    snapshot_id: str,
    *,
    model_version: str = PATH_MODEL_VERSION,
    horizon: str = "30m",
    configuration_hash: str = "",
) -> int:
    material = f"{snapshot_id}|{model_version}|{horizon}|{configuration_hash}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (2**31 - 1)


def config_hash(cfg: PathModelV3Config) -> str:
    payload = f"{cfg.block_min}|{cfg.block_max}|{cfg.min_library_residuals}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class PathEventResult:
    p_target_first: float
    p_stop_first: float
    p_neither: float
    p_touch_call_wall: float
    p_touch_put_wall: float
    p_cross_gamma_flip: float
    p_call_wall_first: float
    p_put_wall_first: float
    p_neither_wall: float
    p_range_survive: float
    terminal_mean: float
    terminal_std: float
    mfe_mean: float
    mae_mean: float
    n_paths: int
    n_steps: int
    ambiguous_same_step_rate: float
    model_version: str = PATH_MODEL_VERSION
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_path_events(
    paths: np.ndarray,
    *,
    spot: float,
    call_wall: float | None = None,
    put_wall: float | None = None,
    gamma_flip: float | None = None,
    target: float | None = None,
    stop: float | None = None,
    lower: float | None = None,
    upper: float | None = None,
) -> PathEventResult:
    """Score barrier events; same-step ambiguity is adverse-first (stop)."""
    paths = np.asarray(paths, dtype=float)
    n_paths, cols = paths.shape
    n_steps = cols - 1
    if n_steps < 1:
        raise ValueError("paths must include at least one step")

    hit_target = np.zeros(n_paths, dtype=bool)
    hit_stop = np.zeros(n_paths, dtype=bool)
    amb_count = 0
    live = np.ones(n_paths, dtype=bool)
    if target is not None and stop is not None:
        up = target >= spot
        for t in range(1, cols):
            if not live.any():
                break
            prev, curr = paths[:, t - 1], paths[:, t]
            lo = np.minimum(prev, curr)
            hi = np.maximum(prev, curr)
            if up:
                t_hit = hi >= target
                s_hit = lo <= stop
            else:
                t_hit = lo <= target
                s_hit = hi >= stop
            both = live & t_hit & s_hit
            only_t = live & t_hit & ~s_hit
            only_s = live & s_hit & ~t_hit
            amb_count += int(both.sum())
            hit_stop |= both | only_s
            hit_target |= only_t
            live &= ~(both | only_t | only_s)

    def _mean_or_nan(mask_ok: bool, arr: np.ndarray) -> float:
        return float(np.clip(arr.mean(), 0.0, 1.0)) if mask_ok else float("nan")

    p_t = _mean_or_nan(target is not None, hit_target)
    p_s = _mean_or_nan(stop is not None, hit_stop)
    p_n = _mean_or_nan(target is not None, ~(hit_target | hit_stop))

    touch_c = np.zeros(n_paths, dtype=bool)
    touch_p = np.zeros(n_paths, dtype=bool)
    first_c = np.zeros(n_paths, dtype=bool)
    first_p = np.zeros(n_paths, dtype=bool)
    wall_done = np.zeros(n_paths, dtype=bool)
    if call_wall is not None or put_wall is not None:
        for t in range(1, cols):
            prev, curr = paths[:, t - 1], paths[:, t]
            lo = np.minimum(prev, curr)
            hi = np.maximum(prev, curr)
            c_hit = (hi >= call_wall) if call_wall is not None else np.zeros(n_paths, dtype=bool)
            p_hit = (lo <= put_wall) if put_wall is not None else np.zeros(n_paths, dtype=bool)
            touch_c |= c_hit
            touch_p |= p_hit
            both = ~wall_done & c_hit & p_hit
            only_c = ~wall_done & c_hit & ~p_hit
            only_p = ~wall_done & p_hit & ~c_hit
            first_p |= both | only_p
            first_c |= only_c
            wall_done |= both | only_c | only_p

    cross_flip = np.zeros(n_paths, dtype=bool)
    if gamma_flip is not None:
        side0 = np.sign(spot - gamma_flip)
        for t in range(1, cols):
            side = np.sign(paths[:, t] - gamma_flip)
            if side0 == 0:
                cross_flip |= paths[:, t] != gamma_flip
            else:
                cross_flip |= side == -side0

    survive = np.ones(n_paths, dtype=bool)
    if lower is not None and upper is not None:
        for t in range(1, cols):
            prev, curr = paths[:, t - 1], paths[:, t]
            lo = np.minimum(prev, curr)
            hi = np.maximum(prev, curr)
            survive &= (lo > lower) & (hi < upper)

    terminal = paths[:, -1]
    rets = paths / spot - 1.0
    return PathEventResult(
        p_target_first=p_t,
        p_stop_first=p_s,
        p_neither=p_n,
        p_touch_call_wall=float(touch_c.mean()) if call_wall is not None else float("nan"),
        p_touch_put_wall=float(touch_p.mean()) if put_wall is not None else float("nan"),
        p_cross_gamma_flip=float(cross_flip.mean()) if gamma_flip is not None else float("nan"),
        p_call_wall_first=float(first_c.mean()) if call_wall is not None else float("nan"),
        p_put_wall_first=float(first_p.mean()) if put_wall is not None else float("nan"),
        p_neither_wall=(
            float((~wall_done).mean())
            if (call_wall is not None or put_wall is not None)
            else float("nan")
        ),
        p_range_survive=(
            float(survive.mean()) if (lower is not None and upper is not None) else float("nan")
        ),
        terminal_mean=float(terminal.mean()),
        terminal_std=float(terminal.std()),
        mfe_mean=float(rets.max(axis=1).mean()),
        mae_mean=float(rets.min(axis=1).mean()),
        n_paths=n_paths,
        n_steps=n_steps,
        ambiguous_same_step_rate=float(amb_count) / max(n_paths, 1),
    )


@dataclass(frozen=True)
class PathForecastV3:
    p_target_first: float
    p_stop_first: float
    p_neither: float
    p_touch_call_wall: float | None
    p_touch_put_wall: float | None
    p_cross_gamma_flip: float | None
    p_call_wall_first: float | None
    p_put_wall_first: float | None
    p_neither_wall: float | None
    p_range_survive: float | None
    terminal_quantiles: dict[float, float]
    mfe_quantiles: dict[float, float]
    mae_quantiles: dict[float, float]
    terminal_mean: float
    terminal_std: float
    effective_support: float
    source_session_concentration: float
    conditioning_backoff_level: int
    uncertainty: float
    n_paths: int
    n_steps: int
    model_version: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _gaussian_paths(
    spot: float,
    n_paths: int,
    n_steps: int,
    sigma_per_min: float,
    mean_per_min: float,
    rng: np.random.Generator,
) -> np.ndarray:
    shocks = rng.normal(mean_per_min, sigma_per_min, size=(n_paths, n_steps))
    log_paths = np.cumsum(shocks, axis=1)
    prices = spot * np.exp(np.concatenate([np.zeros((n_paths, 1)), log_paths], axis=1))
    return np.asarray(prices, dtype=float)


def _eligible_starts(library: ResidualLibrary, block_len: int) -> list[int]:
    starts: list[int] = []
    for a, b in library.session_spans:
        if b - a >= block_len:
            starts.extend(range(a, b - block_len + 1))
    return starts


def simulate_paths_v3(
    spot: float,
    n_steps: int,
    sigma_per_min: float,
    *,
    library: ResidualLibrary | None = None,
    mean_per_min: float = 0.0,
    cfg: PathModelV3Config | None = None,
    snapshot_id: str = "test",
    horizon: str = "30m",
    mode: str = "test",
) -> tuple[np.ndarray, dict[str, Any]]:
    """Deterministic residual block-bootstrap paths; Gaussian is Level-6 fallback."""
    cfg = cfg or PathModelV3Config()
    n_paths = cfg.n_paths_for(mode)
    seed = derive_path_seed(
        snapshot_id,
        model_version=PATH_MODEL_VERSION,
        horizon=horizon,
        configuration_hash=config_hash(cfg),
    )
    rng = np.random.default_rng(seed)
    diag: dict[str, Any] = {
        "seed": seed,
        "conditioning_backoff_level": 5,
        "effective_support": 0.0,
        "source_session_concentration": 1.0,
    }

    use_gaussian = (
        library is None
        or len(library) < cfg.min_library_residuals
        or not library.session_spans
    )
    if use_gaussian:
        if not cfg.allow_gaussian_fallback:
            raise RuntimeError("path library insufficient and gaussian fallback disabled")
        diag["conditioning_backoff_level"] = 6
        diag["fallback"] = "gaussian"
        paths_g = _gaussian_paths(spot, n_paths, n_steps, sigma_per_min, mean_per_min, rng)
        return np.asarray(paths_g, dtype=float), diag

    assert library is not None
    block_len = int(rng.integers(cfg.block_min, cfg.block_max + 1))
    starts = _eligible_starts(library, block_len)
    if not starts:
        diag["conditioning_backoff_level"] = 6
        diag["fallback"] = "gaussian_no_eligible_blocks"
        paths_g = _gaussian_paths(spot, n_paths, n_steps, sigma_per_min, mean_per_min, rng)
        return np.asarray(paths_g, dtype=float), diag

    paths = np.empty((n_paths, n_steps + 1), dtype=float)
    paths[:, 0] = spot
    chosen_sessions: list[str] = []
    for i in range(n_paths):
        price = float(spot)
        filled = 0
        while filled < n_steps:
            start = int(rng.choice(starts))
            block = library.residuals[start : start + block_len]
            chosen_sessions.append(str(library.session_id[start]))
            for r in block:
                if filled >= n_steps:
                    break
                # Scale standardized residual by local sigma
                ret = mean_per_min + float(r) * sigma_per_min
                price = price * math.exp(ret)
                filled += 1
                paths[i, filled] = price

    # Session concentration of first-block sources
    if chosen_sessions:
        from collections import Counter

        counts = Counter(chosen_sessions[:n_paths])
        top = max(counts.values())
        diag["source_session_concentration"] = float(top / max(n_paths, 1))
        diag["effective_support"] = float(len(counts))
    return paths, diag


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def forecast_from_paths(
    paths: np.ndarray,
    *,
    spot: float,
    target: float | None = None,
    stop: float | None = None,
    call_wall: float | None = None,
    put_wall: float | None = None,
    gamma_flip: float | None = None,
    range_low: float | None = None,
    range_high: float | None = None,
    diagnostics: dict[str, Any] | None = None,
    uncertainty: float = 0.0,
) -> PathForecastV3:
    diag = dict(diagnostics or {})
    n_paths, cols = paths.shape
    n_steps = cols - 1
    terminals = paths[:, -1]
    q_grid = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
    terminal_q = {q: float(np.quantile(terminals, q)) for q in q_grid}
    start = paths[:, 0:1]
    mfe = np.max(paths - start, axis=1)
    mae = np.max(start - paths, axis=1)
    events = score_path_events(
        paths,
        spot=spot,
        target=target,
        stop=stop,
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=gamma_flip,
        lower=range_low,
        upper=range_high,
    )
    # Renormalize target/stop/neither when present
    p_t, p_s, p_n = events.p_target_first, events.p_stop_first, events.p_neither
    if all(math.isfinite(x) for x in (p_t, p_s, p_n)):
        z = p_t + p_s + p_n
        if z > 0:
            p_t, p_s, p_n = p_t / z, p_s / z, p_n / z
    else:
        p_t = p_s = p_n = 0.0
        p_n = 1.0

    return PathForecastV3(
        p_target_first=float(p_t),
        p_stop_first=float(p_s),
        p_neither=float(p_n),
        p_touch_call_wall=_finite_or_none(events.p_touch_call_wall),
        p_touch_put_wall=_finite_or_none(events.p_touch_put_wall),
        p_cross_gamma_flip=_finite_or_none(events.p_cross_gamma_flip),
        p_call_wall_first=_finite_or_none(events.p_call_wall_first),
        p_put_wall_first=_finite_or_none(events.p_put_wall_first),
        p_neither_wall=_finite_or_none(events.p_neither_wall),
        p_range_survive=_finite_or_none(events.p_range_survive),
        terminal_quantiles=terminal_q,
        mfe_quantiles={q: float(np.quantile(mfe, q)) for q in q_grid},
        mae_quantiles={q: float(np.quantile(mae, q)) for q in q_grid},
        terminal_mean=float(np.mean(terminals)),
        terminal_std=float(np.std(terminals)),
        effective_support=float(diag.get("effective_support", 0.0)),
        source_session_concentration=float(diag.get("source_session_concentration", 1.0)),
        conditioning_backoff_level=int(diag.get("conditioning_backoff_level", 5)),
        uncertainty=float(uncertainty),
        n_paths=int(n_paths),
        n_steps=int(n_steps),
        model_version=PATH_MODEL_VERSION,
        diagnostics={**diag, "ambiguous_same_step_rate": float(events.ambiguous_same_step_rate)},
    )
