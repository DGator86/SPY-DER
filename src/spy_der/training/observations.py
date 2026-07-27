"""Turn recorded sessions into labeled training observations (spec §25, §54).

The last structural gap in the forecast path. Every piece existed — the models
with their `fit`, the `SessionLabeler`, the feature pipeline, the fold builder,
the registry — and nothing turned a session recording into rows a model could be
fitted on. This module is that step; :mod:`spy_der.training.pipeline` fits and
registers what it produces.

One observation is one recorded snapshot: its feature bundle is the input row,
and its labels come from the *forward* path of the same session. Three rules
keep that honest:

* **Labels look strictly forward.** :class:`SessionLabeler` is built from the
  session's completed bar path and every horizon is measured after the
  observation timestamp. The structural levels a label depends on — the walls,
  the gamma flip — are frozen at their observation-time values, so a label can
  never be computed against a level the model would not have seen.
* **The bar path comes from the last snapshot of the session.** Each snapshot
  carries a rolling window of 1-minute bars ending at its own tick, so the final
  snapshot holds the fullest path. Assembling the path from each observation's
  own bars would truncate every horizon at the observation itself and silently
  produce all-missing labels.
* **A horizon past the close is missing, never truncated.** That is the
  labeler's own contract, and it is why late-session observations legitimately
  carry fewer targets rather than a clipped value that reads as a real outcome.

Observations with no usable label for a given target are dropped per target
rather than globally, so a model whose target is well-populated is not starved
by one that is not.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.market_parse import SnapshotParseError, snapshot_from_dict
from spy_der.evaluation.labels import SessionLabeler
from spy_der.features.pipeline import SnapshotFeaturePipeline
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed

__all__ = [
    "LabeledObservation",
    "ObservationSet",
    "build_observations",
    "load_session_snapshots",
]

log = logging.getLogger("spy_der.training")


@dataclass(frozen=True, slots=True)
class LabeledObservation:
    """One snapshot's features plus the forward outcomes measured against it."""

    snapshot_id: str
    session_date: str
    timestamp: str
    features: dict[str, float]
    labels: dict[str, Any]

    def label(self, name: str) -> Any:
        return self.labels.get(name)


@dataclass
class ObservationSet:
    """Labeled observations plus what was skipped building them."""

    observations: list[LabeledObservation] = field(default_factory=list)
    sessions: tuple[str, ...] = ()
    skipped_sessions: tuple[tuple[str, str], ...] = ()
    unparseable_snapshots: int = 0

    def __len__(self) -> int:
        return len(self.observations)

    def target(self, name: str) -> tuple[list[dict[str, float]], list[Any], list[str]]:
        """``(rows, y, sessions)`` for ``name``, dropping unlabeled observations.

        Dropping per target rather than globally: a late-session observation has
        no 60-minute outcome but a perfectly good 5-minute one, and discarding it
        everywhere would throw away the end of every session.
        """
        rows: list[dict[str, float]] = []
        y: list[Any] = []
        sessions: list[str] = []
        for observation in self.observations:
            value = observation.labels.get(name)
            if value is None:
                continue
            rows.append(observation.features)
            y.append(value)
            sessions.append(observation.session_date)
        return rows, y, sessions

    def describe(self) -> str:
        text = f"{len(self.observations)} observation(s) over {len(self.sessions)} session(s)"
        if self.skipped_sessions:
            text += "; skipped: " + ", ".join(f"{s} ({why})" for s, why in self.skipped_sessions)
        if self.unparseable_snapshots:
            text += f"; {self.unparseable_snapshots} unparseable snapshot(s)"
        return text


def load_session_snapshots(path: Path) -> tuple[list[CanonicalMarketSnapshot], int]:
    """Rebuild every snapshot in one recording; returns ``(snapshots, skipped)``.

    A snapshot that fails to parse is counted and skipped rather than aborting
    the session: one bad record should cost one observation, not a training day.
    """
    feed = ReplayFeed.from_file(path)
    snapshots: list[CanonicalMarketSnapshot] = []
    skipped = 0
    for payload in feed.replay():
        try:
            snapshots.append(snapshot_from_dict(payload))
        except SnapshotParseError as exc:
            log.warning("snapshot in %s could not be rebuilt: %s", path.name, exc)
            skipped += 1
    return snapshots, skipped


def _session_labeler(snapshots: Sequence[CanonicalMarketSnapshot]) -> SessionLabeler | None:
    """Build the labeler from the session's fullest bar path.

    The last snapshot holds the longest window; see the module docstring for why
    per-observation bars would truncate every horizon.
    """
    path: list[Any] = []
    for snapshot in reversed(snapshots):
        if snapshot.bars_1m:
            path = list(snapshot.bars_1m)
            break
    if len(path) < 2:
        return None

    return SessionLabeler(
        ts=np.asarray(
            [np.datetime64(b.timestamp.replace(tzinfo=None), "ns") for b in path],
            dtype="datetime64[ns]",
        ),
        high=np.asarray([float(b.high) for b in path], dtype=float),
        low=np.asarray([float(b.low) for b in path], dtype=float),
        close=np.asarray([float(b.close) for b in path], dtype=float),
    )


def _label_inputs(features: dict[str, float]) -> dict[str, float | None]:
    """Structural levels frozen at observation time, for the label call."""
    return {
        "call_wall": features.get("gex.call_wall"),
        "put_wall": features.get("gex.put_wall"),
        "gamma_flip": features.get("gex.gamma_flip"),
        "implied_remaining_move": features.get("vol.expected_move_pct"),
    }


def _session_files(market_dir: Path, sessions: Sequence[str] | None) -> Iterator[Path]:
    if not market_dir.is_dir():
        return
    for path in sorted(market_dir.glob("*.jsonl")):
        if sessions is None or path.stem in set(sessions):
            yield path


def build_observations(
    state_root: str | Path,
    *,
    sessions: Sequence[str] | None = None,
    pipeline: SnapshotFeaturePipeline | None = None,
) -> ObservationSet:
    """Build labeled observations from the recordings under ``state_root``.

    Features are recomputed from each snapshot rather than read from the
    engine's `features/` artifacts: training must be reproducible from the
    market recording alone, and reading a derived artifact would make the
    training set depend on which engine version happened to write it.
    """
    market_dir = Path(state_root) / "market"
    features = pipeline or SnapshotFeaturePipeline()

    result = ObservationSet()
    observed_sessions: list[str] = []
    skipped: list[tuple[str, str]] = []

    for path in _session_files(market_dir, sessions):
        session = path.stem
        try:
            snapshots, unparseable = load_session_snapshots(path)
        except CorruptRecordingError as exc:
            log.error("recording %s failed integrity checks: %s", session, exc)
            skipped.append((session, "corrupt recording"))
            continue
        except OSError as exc:
            log.error("recording %s is unreadable: %s", session, exc)
            skipped.append((session, "unreadable"))
            continue

        result.unparseable_snapshots += unparseable
        if not snapshots:
            skipped.append((session, "no usable snapshots"))
            continue

        labeler = _session_labeler(snapshots)
        if labeler is None:
            # Without a bar path there is nothing to measure outcomes against.
            # This is the expected state for a session recorded before bars were
            # populated, so it is reported rather than treated as corruption.
            skipped.append((session, "no bar path to label against"))
            continue

        produced = 0
        for snapshot in snapshots:
            row = dict(features.build(snapshot).features)
            if not row:
                continue
            levels = _label_inputs(row)
            labels = labeler.label_observation(
                snapshot.timestamp,
                float(snapshot.underlying_price),
                call_wall=levels["call_wall"],
                put_wall=levels["put_wall"],
                gamma_flip=levels["gamma_flip"],
                implied_remaining_move=levels["implied_remaining_move"],
            )
            result.observations.append(
                LabeledObservation(
                    snapshot_id=snapshot.snapshot_id,
                    session_date=session,
                    timestamp=snapshot.timestamp.isoformat(),
                    features=row,
                    labels=labels,
                )
            )
            produced += 1

        if produced:
            observed_sessions.append(session)
        else:
            skipped.append((session, "no observation produced a feature row"))

    result.sessions = tuple(observed_sessions)
    result.skipped_sessions = tuple(skipped)
    return result
