"""Dojo runtime configuration (SPY-DER owned paths)."""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "DEFAULT_CONFIGS_DIR",
    "DEFAULT_LIVE_STATE",
    "DEFAULT_REPORTS_DIR",
    "DEFAULT_STATE_ROOT",
    "DojoConfig",
]

DEFAULT_STATE_ROOT = os.environ.get("SPY_DER_STATE_ROOT", "/var/lib/spy-der")
DEFAULT_REPORTS_DIR = os.path.join(DEFAULT_STATE_ROOT, "reports", "dojo")
DEFAULT_CONFIGS_DIR = os.path.join(DEFAULT_STATE_ROOT, "configs")
DEFAULT_LIVE_STATE = os.path.join(DEFAULT_STATE_ROOT, "live_state.json")


@dataclass
class DojoConfig:
    """Config for a Dojo run. Paths default under /var/lib/spy-der."""

    reports_dir: str = DEFAULT_REPORTS_DIR
    configs_dir: str = DEFAULT_CONFIGS_DIR
    live_state_path: str = DEFAULT_LIVE_STATE
    report_date: str | None = None
    # phase toggles
    skip_recorded: bool = False
    skip_learner: bool = False
    skip_universe: bool = False
    #: When True (default), refuse the universe lattice if recorded tape is
    #: insufficient. A full-lattice weekend run with zero sessions is a
    #: multi-hour no-op — generate nothing until there is tape to train against.
    #: Set force_universe=True to override (tests / explicit synthetic-only runs).
    force_universe: bool = False
    # recorded-phase
    wf_folds: int = 3
    min_ticks: int = 100
    min_sessions: int = 3
    recent_days: int = 0
    # learner phase
    learn_trials: int = 15
    learn_holdout: float = 0.25
    # universe phase
    universes_per_gen: int = 6
    generations: int = 2
    full_lattice: bool = False
    universe_days: int = 8
    catalog_seed: int = 20260723
    #: Minutes between synthetic snapshots. Universe sparring now generates real
    #: worlds and runs the real candidate factory (see spy_der.synthetic), so
    #: this is the main throughput dial: stride 15 over the defaults above is
    #: ~2.5k scored snapshots, sized for the nightly timer rather than a unit
    #: test. Tests should bound universes_per_gen / universe_days instead of
    #: raising the stride, so they still exercise real geometry.
    universe_snapshot_stride: int = 15
