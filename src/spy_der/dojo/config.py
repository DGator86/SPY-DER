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
