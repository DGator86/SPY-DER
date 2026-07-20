"""Session-grouped expanding folds (master spec §25 / System A prediction/crossfit.py).

Complete trading sessions are the primary non-splittable group. Outer expanding
folds hold out complete test sessions with a whole-session embargo. Nested
hyperparameter search over estimators is deferred; this module establishes the
fold substrate and OOF index helpers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "FoldDefinition",
    "NestedCrossFitConfig",
    "build_expanding_session_folds",
    "build_nested_session_folds",
    "session_masks",
]


@dataclass(frozen=True, slots=True)
class FoldDefinition:
    fold_id: str
    train_sessions: tuple[str, ...]
    validation_sessions: tuple[str, ...]
    calibration_sessions: tuple[str, ...]
    embargoed_sessions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NestedCrossFitConfig:
    outer_folds: int = 4
    inner_folds: int = 3
    embargo_sessions: int = 1
    min_train_sessions: int = 8
    min_validation_sessions: int = 2
    calibration_frac: float = 0.2
    random_state: int = 42


def build_expanding_session_folds(
    sessions: Sequence[str],
    *,
    n_folds: int = 4,
    embargo_sessions: int = 1,
    min_train_sessions: int = 8,
    min_validation_sessions: int = 2,
) -> list[dict[str, tuple[str, ...]]]:
    """Expanding walk-forward folds over chronologically sorted sessions."""
    uniq = sorted(set(sessions))
    if len(uniq) < min_train_sessions + embargo_sessions + min_validation_sessions:
        return []
    folds: list[dict[str, tuple[str, ...]]] = []
    # Leave room for at least one validation block after the warm train window.
    usable = len(uniq) - min_train_sessions
    if usable < min_validation_sessions:
        return []
    step = max(min_validation_sessions, usable // max(n_folds, 1))
    cursor = min_train_sessions
    fold_i = 0
    while cursor < len(uniq) and fold_i < n_folds:
        train_end = cursor
        embargo_end = min(len(uniq), train_end + embargo_sessions)
        test_end = min(len(uniq), embargo_end + step)
        train = tuple(uniq[:train_end])
        embargoed = tuple(uniq[train_end:embargo_end])
        test = tuple(uniq[embargo_end:test_end])
        if len(train) < min_train_sessions or len(test) < 1:
            break
        folds.append(
            {
                "train_sessions": train,
                "embargoed_sessions": embargoed,
                "test_sessions": test,
            }
        )
        cursor = test_end
        fold_i += 1
    return folds


def build_nested_session_folds(
    sessions: Sequence[str],
    cfg: NestedCrossFitConfig | None = None,
) -> list[FoldDefinition]:
    """Expanding outer folds with trailing calibration sessions carved from train."""
    cfg = cfg or NestedCrossFitConfig()
    outer = build_expanding_session_folds(
        sessions,
        n_folds=cfg.outer_folds,
        embargo_sessions=cfg.embargo_sessions,
        min_train_sessions=cfg.min_train_sessions,
        min_validation_sessions=cfg.min_validation_sessions,
    )
    folds: list[FoldDefinition] = []
    for i, of in enumerate(outer):
        train = list(of["train_sessions"])
        test = list(of["test_sessions"])
        embargoed = list(of["embargoed_sessions"])
        n_cal = min(
            max(cfg.min_validation_sessions, 1),
            max(0, round(len(train) * cfg.calibration_frac)),
        )
        if len(train) - n_cal < cfg.min_train_sessions:
            n_cal = 0
        if n_cal > 0:
            fit_end = len(train) - n_cal - cfg.embargo_sessions
            if fit_end < cfg.min_train_sessions:
                cal: list[str] = []
                fit_train = train
            else:
                fit_train = train[:fit_end]
                cal_embargo = train[fit_end : len(train) - n_cal]
                cal = train[len(train) - n_cal :]
                embargoed = list(dict.fromkeys(embargoed + cal_embargo))
        else:
            cal = []
            fit_train = train
        folds.append(
            FoldDefinition(
                fold_id=f"outer-{i}",
                train_sessions=tuple(fit_train),
                validation_sessions=tuple(test),
                calibration_sessions=tuple(cal),
                embargoed_sessions=tuple(embargoed),
            )
        )
    return folds


def session_masks(
    sessions: Sequence[str],
    selected: Sequence[str],
) -> np.ndarray:
    """Boolean mask of rows whose session is in ``selected``."""
    selected_set = set(selected)
    return np.asarray([s in selected_set for s in sessions], dtype=bool)
