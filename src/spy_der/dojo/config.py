"""Dojo runtime configuration (SPY-DER owned paths)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spy_der.learning.promotion_trial import PromotionThresholds

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


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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
    #: Newest N *recorded sessions* to score (0 = all). Not a calendar-day
    #: window — see :func:`spy_der.dojo.recorded._filter_sessions`.
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
    # promotion phase
    #: Re-run the system under a recommended change and promote it when the
    #: re-run validates. Off means the Dojo stops at a staged candidate and
    #: waits for a human — set ``SPY_DER_DOJO_AUTO_PROMOTE=0`` as a kill switch.
    auto_promote: bool = _env_flag("SPY_DER_DOJO_AUTO_PROMOTE", True)
    #: Bars the re-run must clear. See learning.promotion_trial.
    promote_min_trades: int = 20
    promote_min_sessions: int = 3
    promote_min_pnl_edge: float = 0.0
    promote_max_win_rate_drop: float = 0.05
    promote_require_sequential: bool = True
    promote_require_universe: bool = True
    promote_cooldown_hours: float = 6.0

    def promotion_thresholds(self) -> PromotionThresholds:
        """Thresholds object for the promotion trial (import kept local)."""
        from spy_der.learning.promotion_trial import PromotionThresholds

        return PromotionThresholds(
            min_trades=self.promote_min_trades,
            min_sessions=self.promote_min_sessions,
            min_pnl_edge=self.promote_min_pnl_edge,
            max_win_rate_drop=self.promote_max_win_rate_drop,
            require_sequential=self.promote_require_sequential,
            require_universe=self.promote_require_universe,
            cooldown_hours=self.promote_cooldown_hours,
        )
