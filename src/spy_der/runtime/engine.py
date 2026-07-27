"""Deterministic engine (`spy-der engine`).

Drives `spy-der-engine.service`, which ships `PrivateNetwork=true`: everything
here is reproducible from a recorded snapshot, with no network and no AI.

The engine consumes the recordings `spy-der market` writes, runs the
deterministic stages over each snapshot, and records two things:

* the candidate universe, as a stage artifact under `candidates/`
  (see `spy_der.runtime.artifacts` for why it uses the recording envelope)
* what happened, as hash-chained events in the journal

**Stage availability is reported, never faked.** `JournalEventType` already
carries `FORECAST_UNAVAILABLE` and `FEATURE_STAGE_FAILED` as first-class
outcomes, which is the design saying a stage may legitimately not run. Today:

* `candidates` runs for real — `generate_candidate_universe` is deterministic
  and complete
* `features` runs for real — `SnapshotFeaturePipeline` assembles the GEX, MTF,
  RND, volatility, flow, breadth and volatility-surface families into a
  `FeatureBundle`, recorded under `features/` and journaled as
  `FEATURES_COMPUTED`
* `forecast` needs a trained model group from `spy_der.training.registry`.
  `ForecastServer` is fail-closed by design and `heuristic_bundle` is marked
  research-only, so with no registry configured the engine journals
  `FORECAST_UNAVAILABLE` rather than serving a neutral 0.5 that downstream
  stages would read as a real forecast

The engine is idempotent: it skips snapshots whose candidate artifact is
already on disk, so a restart resumes rather than duplicating.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.contracts.candidates import FACTORY_VERSION
from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.market_parse import SnapshotParseError, snapshot_from_dict
from spy_der.features.pipeline import (
    FEATURE_PIPELINE_VERSION,
    SnapshotFeaturePipeline,
)
from spy_der.journal.store import SqliteJournalStore
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed
from spy_der.runtime.artifacts import StageArtifactStore
from spy_der.runtime.heartbeat import write_heartbeat

__all__ = ["EngineConfig", "EngineService", "build_arg_parser", "main"]

log = logging.getLogger("spy_der.engine")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"
DEPLOYMENT_ID = "spy-der-engine"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    state_root: str = DEFAULT_STATE_ROOT
    interval_seconds: float = 30.0
    #: Stop after this many passes. 0 means run until signalled (the unit's case).
    max_passes: int = 0
    #: Restrict to one session (YYYY-MM-DD); empty means every recorded session.
    session: str = ""

    @property
    def market_dir(self) -> Path:
        return Path(self.state_root) / "market"

    @property
    def journal_path(self) -> Path:
        return Path(self.state_root) / "journal" / "journal.db"


def _stage_availability() -> dict[str, str]:
    """Which deterministic stages can run in this deployment.

    Resolved once per process rather than per snapshot: a missing model registry
    is a standing condition, and journaling it 390 times a session would bury
    the events that describe actual work.
    """
    return {
        "candidates": "available",
        "features": "available",
        "forecast": (
            "unavailable: no trained model group is configured; "
            "refusing the research-only heuristic path"
        ),
    }


@dataclass(frozen=True, slots=True)
class _Stores:
    """The stage artifact stores, grouped so adding a stage is one field."""

    candidates: StageArtifactStore
    features: StageArtifactStore


@dataclass
class EngineService:
    """Run the deterministic stages over recorded snapshots."""

    config: EngineConfig
    _stop: bool = False
    _seen: dict[str, set[str]] = field(default_factory=dict)
    features: SnapshotFeaturePipeline = field(default_factory=SnapshotFeaturePipeline)

    def request_stop(self, *_args: object) -> None:
        self._stop = True

    # -- setup ------------------------------------------------------------- #
    def _open_journal(self) -> SqliteJournalStore:
        path = self.config.journal_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteJournalStore(path)

    def _sessions(self) -> list[str]:
        if not self.config.market_dir.is_dir():
            return []
        names = sorted(p.stem for p in self.config.market_dir.glob("*.jsonl"))
        if self.config.session:
            return [n for n in names if n == self.config.session]
        return names

    def _already_processed(self, artifacts: StageArtifactStore, session: str) -> set[str]:
        """Snapshot ids whose candidate artifact is already recorded."""
        if session in self._seen:
            return self._seen[session]
        done: set[str] = set()
        try:
            for payload in artifacts.read(session):
                snapshot_id = payload.get("snapshot_id")
                if snapshot_id:
                    done.add(str(snapshot_id))
        except CorruptRecordingError as exc:
            # Refuse to append to a corrupt artifact file: the seq/hash chain is
            # already broken and adding to it would hide the break.
            log.error("candidate artifacts for %s are corrupt: %s", session, exc)
            raise
        self._seen[session] = done
        return done

    # -- run --------------------------------------------------------------- #
    def run(self) -> int:
        cfg = self.config
        stages = _stage_availability()
        for name, state in sorted(stages.items()):
            log.info("stage %s: %s", name, state)

        journal = self._open_journal()
        stores = _Stores(
            candidates=StageArtifactStore(cfg.state_root, "candidates"),
            features=StageArtifactStore(cfg.state_root, "features"),
        )
        # Startup is logged, not journaled: the journal's event types describe
        # pipeline outcomes, and borrowing one (SYSTEM_DECIDED) for a lifecycle
        # marker would corrupt any query for real decisions. Stage availability
        # still reaches the journal per snapshot via FORECAST_UNAVAILABLE.

        passes = 0
        while not self._stop:
            processed = self._pass(journal, stores, stages)
            passes += 1
            write_heartbeat(
                cfg.state_root,
                "engine",
                interval_seconds=cfg.interval_seconds,
                detail=f"pass {passes}: {processed} snapshot(s)",
                extra={"passes": passes, "stages": stages},
            )
            if processed:
                log.info("pass %d processed %d snapshot(s)", passes, processed)
            if cfg.max_passes and passes >= cfg.max_passes:
                break
            if self._stop:
                break
            time.sleep(cfg.interval_seconds)

        log.info("engine stopped after %d pass(es)", passes)
        return 0

    def _pass(
        self,
        journal: SqliteJournalStore,
        stores: _Stores,
        stages: dict[str, str],
    ) -> int:
        processed = 0
        for session in self._sessions():
            if self._stop:
                break
            processed += self._process_session(journal, stores, stages, session)
        return processed

    def _process_session(
        self,
        journal: SqliteJournalStore,
        stores: _Stores,
        stages: dict[str, str],
        session: str,
    ) -> int:
        path = self.config.market_dir / f"{session}.jsonl"
        try:
            feed = ReplayFeed.from_file(path)
        except CorruptRecordingError as exc:
            # One bad recording must not take down the engine; it is logged and
            # skipped so later sessions still process.
            log.error("recording %s failed integrity checks: %s", session, exc)
            return 0
        except OSError as exc:
            log.error("recording %s is unreadable: %s", session, exc)
            return 0

        try:
            done = self._already_processed(stores.candidates, session)
        except CorruptRecordingError:
            return 0

        processed = 0
        for payload in feed.replay():
            if self._stop:
                break
            snapshot_id = str(payload.get("snapshot_id", ""))
            if not snapshot_id or snapshot_id in done:
                continue
            if self._process_snapshot(journal, stores, stages, session, payload):
                done.add(snapshot_id)
                processed += 1
        return processed

    def _run_feature_stage(
        self,
        journal: SqliteJournalStore,
        stores: _Stores,
        session: str,
        snapshot: CanonicalMarketSnapshot,
    ) -> None:
        """Build and record the feature bundle for ``snapshot``.

        Never raises: a feature failure is journaled as `FEATURE_STAGE_FAILED`
        and the snapshot still gets its candidate universe. Features inform the
        decision; candidates *are* the decision surface, and losing them to a
        feature defect would be the more expensive failure.
        """
        try:
            result = self.features.build_detailed(snapshot)
        except Exception as exc:
            log.exception("feature stage failed for %s", snapshot.snapshot_id)
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.FEATURE_STAGE_FAILED.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot.snapshot_id,
                    occurred_at=snapshot.timestamp,
                    payload={"error": f"{type(exc).__name__}: {exc}"},
                    deployment_id=DEPLOYMENT_ID,
                    snapshot_id=snapshot.snapshot_id,
                )
            )
            return

        stores.features.append(
            session,
            artifact_id=result.bundle.bundle_id,
            schema_version=FEATURE_PIPELINE_VERSION,
            payload=result.bundle,
        )
        journal.append(
            JournalEvent(
                event_type=JournalEventType.FEATURES_COMPUTED.value,
                aggregate_type=AggregateType.SYSTEM.value,
                aggregate_id=snapshot.snapshot_id,
                occurred_at=snapshot.timestamp,
                payload={
                    "session_date": session,
                    "pipeline_version": FEATURE_PIPELINE_VERSION,
                    "bundle_id": result.bundle.bundle_id,
                    "feature_count": len(result.bundle.features),
                    # Recorded per snapshot rather than summarized: which
                    # families were unavailable varies tick to tick with the
                    # data, and that variation is the diagnostic.
                    "missing_families": list(result.missing_families),
                    "failed_families": list(result.failed_families),
                },
                deployment_id=DEPLOYMENT_ID,
                snapshot_id=snapshot.snapshot_id,
            )
        )
        if result.failed_families:
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.FEATURE_STAGE_FAILED.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot.snapshot_id,
                    occurred_at=snapshot.timestamp,
                    payload={"failed_families": list(result.failed_families)},
                    deployment_id=DEPLOYMENT_ID,
                    snapshot_id=snapshot.snapshot_id,
                )
            )

    def _process_snapshot(
        self,
        journal: SqliteJournalStore,
        stores: _Stores,
        stages: dict[str, str],
        session: str,
        payload: dict[str, Any],
    ) -> bool:
        snapshot_id = str(payload.get("snapshot_id", ""))
        try:
            snapshot = snapshot_from_dict(payload)
        except SnapshotParseError as exc:
            log.error("snapshot %s could not be rebuilt: %s", snapshot_id, exc)
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.SNAPSHOT_REJECTED.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot_id or "unknown",
                    payload={"reason": str(exc)},
                    deployment_id=DEPLOYMENT_ID,
                    snapshot_id=snapshot_id or None,
                )
            )
            return False

        self._run_feature_stage(journal, stores, session, snapshot)

        # The forecast stage is fail-closed: unavailable is journaled, never
        # replaced with a neutral value downstream would read as a forecast.
        if stages.get("forecast", "").startswith("unavailable"):
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.FORECAST_UNAVAILABLE.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot.snapshot_id,
                    occurred_at=snapshot.timestamp,
                    payload={"reason": stages["forecast"]},
                    deployment_id=DEPLOYMENT_ID,
                    snapshot_id=snapshot.snapshot_id,
                )
            )

        try:
            universe = generate_candidate_universe(snapshot)
        except Exception as exc:
            # Deliberately broad: one pathological chain must not stop the
            # engine, and the failure is journaled rather than swallowed.
            log.exception("candidate generation failed for %s", snapshot.snapshot_id)
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.CANDIDATE_REJECTED.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot.snapshot_id,
                    payload={"error": f"{type(exc).__name__}: {exc}"},
                    deployment_id=DEPLOYMENT_ID,
                    snapshot_id=snapshot.snapshot_id,
                )
            )
            return False

        stores.candidates.append(
            session,
            artifact_id=universe.snapshot_id,
            schema_version=FACTORY_VERSION,
            payload=universe,
        )
        journal.append(
            JournalEvent(
                event_type=JournalEventType.CANDIDATES_GENERATED.value,
                aggregate_type=AggregateType.SYSTEM.value,
                aggregate_id=snapshot.snapshot_id,
                occurred_at=snapshot.timestamp,
                payload={
                    "session_date": session,
                    "factory_version": universe.factory_version,
                    "candidate_count": len(universe.candidates),
                    "candidate_ids": [c.candidate_id for c in universe.candidates],
                },
                deployment_id=DEPLOYMENT_ID,
                snapshot_id=snapshot.snapshot_id,
            )
        )
        return True


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "SPY-DER deterministic engine — replays recorded snapshots through "
            "the deterministic stages. No network, no AI."
        )
    )
    p.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--max-passes", type=int, default=0, help="0 = run until signalled")
    p.add_argument("--once", action="store_true", help="single pass, then exit")
    p.add_argument("--session", default="", help="restrict to one YYYY-MM-DD session")
    # Accepted so the systemd unit's --config is not a hard error before the
    # config loader lands; the file is not read yet (same as `spy-der market`).
    p.add_argument("--config", default=None, help="reserved (not read yet)")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    if args.config:
        log.warning("--config is accepted but not read yet; using flags and environment")

    service = EngineService(
        config=EngineConfig(
            state_root=args.state_root,
            interval_seconds=args.interval,
            max_passes=1 if args.once else max(args.max_passes, 0),
            session=args.session,
        )
    )
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
