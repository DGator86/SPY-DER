"""Robustness gaps — the archetypes the system loses money in, remembered.

The universe panel has always been able to say "crash is underwater". Nothing
read it: the finding became a warn flag on a dashboard and a line of prose in
the lesson store, and the next cycle diagnosed the same aggregate P&L as if the
gap did not exist. This module is the missing hop — gaps are persisted as
structured failure episodes and read back as diagnoses, so a weakness found on
one run is what the next run trains against.

Lattice *sampling* pressure is a separate mechanism
(:mod:`spy_der.dojo.curriculum_weights`): that decides how many worlds a weak
archetype gets drawn, this decides whether the learner tries to fix it.

Gaps live in the episode store (:mod:`spy_der.learning.memories`) rather than in
the Dojo report, because they have to outlive a single report and accumulate:
an archetype that is weak three runs running is a different problem from one
bad night.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from spy_der.learning.memories import (
    append_failure_episode,
    load_failure_episodes,
)

__all__ = [
    "GAP_EPISODE_PREFIX",
    "ArchetypeGap",
    "clear_archetype_gap",
    "load_archetype_gaps",
    "record_archetype_gaps",
    "weakest_archetypes",
]

#: Episode id prefix. One episode per archetype, replaced on each observation,
#: so the store holds the current picture rather than an unbounded log.
GAP_EPISODE_PREFIX = "weak-archetype-"

#: Sessions an archetype needs before its P&L is treated as a gap rather than
#: noise. A single synthetic session at -108 says almost nothing.
DEFAULT_MIN_SESSIONS = 3

#: How long a remembered gap stays actionable.
DEFAULT_MAX_AGE_DAYS = 14


@dataclass(frozen=True, slots=True)
class ArchetypeGap:
    """One archetype the system is losing money in, with its evidence."""

    archetype: str
    mean_session_pnl: float
    total_pnl: float
    n_sessions: int
    n_universes: int
    observed_at: datetime
    report_date: str = ""

    @property
    def severity(self) -> float:
        """How badly it loses, discounted by how little evidence there is."""
        confidence = min(1.0, self.n_sessions / float(DEFAULT_MIN_SESSIONS * 2))
        return abs(min(0.0, self.mean_session_pnl)) * confidence

    def to_details(self) -> dict[str, Any]:
        return {
            "phase": "universe",
            "kind": "weak_archetype",
            "archetype": self.archetype,
            "mean_session_pnl": self.mean_session_pnl,
            "total_pnl": self.total_pnl,
            "n_sessions": self.n_sessions,
            "n_universes": self.n_universes,
            "report_date": self.report_date,
        }

    @classmethod
    def from_details(
        cls, details: dict[str, Any], observed_at: datetime
    ) -> ArchetypeGap | None:
        archetype = str(details.get("archetype") or "")
        if not archetype or details.get("kind") != "weak_archetype":
            return None
        try:
            return cls(
                archetype=archetype,
                mean_session_pnl=float(details.get("mean_session_pnl") or 0.0),
                total_pnl=float(details.get("total_pnl") or 0.0),
                n_sessions=int(details.get("n_sessions") or 0),
                n_universes=int(details.get("n_universes") or 0),
                observed_at=observed_at,
                report_date=str(details.get("report_date") or ""),
            )
        except (TypeError, ValueError):
            return None


def record_archetype_gaps(
    state_root: str | Path,
    universe_result: dict[str, Any] | None,
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    report_date: str = "",
) -> list[ArchetypeGap]:
    """Persist every losing archetype from a universe panel. Worst first.

    Also *clears* archetypes this panel scored that are no longer losing.
    Without that a repaired archetype keeps steering diagnoses and the
    ``archetype_repair`` target until it ages out — the Dojo would spend two
    more weeks fixing something it already fixed.
    """
    matrix = (universe_result or {}).get("archetype_matrix") or {}
    gaps: list[ArchetypeGap] = []
    recovered: list[str] = []
    for archetype, metrics in matrix.items():
        if not isinstance(metrics, dict):
            continue
        mean = metrics.get("mean_session_pnl")
        n_sessions = int(metrics.get("n_sessions") or 0)
        if mean is None or n_sessions < min_sessions:
            # Not scored deeply enough to say either way — leave any prior
            # verdict standing rather than clearing a gap on thin evidence.
            continue
        if float(mean) >= 0:
            recovered.append(str(archetype))
            continue
        gaps.append(
            ArchetypeGap(
                archetype=str(archetype),
                mean_session_pnl=float(mean),
                total_pnl=float(metrics.get("total_pnl") or 0.0),
                n_sessions=n_sessions,
                n_universes=int(metrics.get("n_universes") or 0),
                observed_at=datetime.now(UTC),
                report_date=report_date,
            )
        )
    gaps.sort(key=lambda g: -g.severity)
    for gap in gaps:
        append_failure_episode(
            state_root,
            episode_id=f"{GAP_EPISODE_PREFIX}{gap.archetype}",
            summary=(
                f"{gap.archetype}: mean session P&L {gap.mean_session_pnl:+.4f} "
                f"over {gap.n_sessions} synthetic session(s)"
            ),
            details=gap.to_details(),
        )
    for archetype in recovered:
        clear_archetype_gap(state_root, archetype, report_date=report_date)
    return gaps


def clear_archetype_gap(
    state_root: str | Path,
    archetype: str,
    *,
    report_date: str = "",
) -> None:
    """Retire an archetype's gap by overwriting it with a ``recovered`` marker.

    The episode store replaces by id, so writing a non-gap record in the same
    slot both removes it from :func:`load_archetype_gaps` (which requires
    ``kind == "weak_archetype"``) and leaves the recovery in the audit trail.
    """
    append_failure_episode(
        state_root,
        episode_id=f"{GAP_EPISODE_PREFIX}{archetype}",
        summary=f"{archetype}: no longer losing — gap cleared",
        details={
            "phase": "universe",
            "kind": "recovered_archetype",
            "archetype": archetype,
            "report_date": report_date,
        },
    )


def load_archetype_gaps(
    state_root: str | Path,
    *,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
) -> list[ArchetypeGap]:
    """Remembered gaps, worst first, dropping ones that have gone stale."""
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    gaps: list[ArchetypeGap] = []
    for episode in load_failure_episodes(state_root):
        if not episode.episode_id.startswith(GAP_EPISODE_PREFIX):
            continue
        observed = episode.created_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        if observed < cutoff:
            continue
        gap = ArchetypeGap.from_details(episode.details, observed)
        if gap is not None:
            gaps.append(gap)
    gaps.sort(key=lambda g: -g.severity)
    return gaps


def weakest_archetypes(
    gaps: Sequence[ArchetypeGap], *, limit: int = 2
) -> tuple[str, ...]:
    """The archetypes worth diagnosing this cycle, worst first.

    Bounded on purpose: one cycle stages one challenger, so a list of eight
    gaps would only ever act on the first. The rest stay in the store and come
    up as the ones ahead of them are repaired.
    """
    return tuple(gap.archetype for gap in gaps[: max(0, limit)])
