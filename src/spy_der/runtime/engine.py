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
* `forecast` runs when a trained model group is configured
  (`--forecast-group` or `SPY_DER_FORECAST_GROUP` from the env file, produced
  by `spy-der train`), serving through `ForecastServer` and journaling
  `FORECAST_GENERATED`. With no group it stays fail-closed and journals
  `FORECAST_UNAVAILABLE` rather than serving `heuristic_bundle`'s neutral 0.5,
  which is marked research-only and which downstream stages would read as a
  real forecast

The engine is idempotent: it skips snapshots whose candidate artifact is
already on disk, so a restart resumes rather than duplicating.
"""

from __future__ import annotations

import argparse
import logging
import os
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
from spy_der.forecasting.runtime import ForecastServer, ForecastServingError
from spy_der.journal.store import SqliteJournalStore
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed
from spy_der.runtime.artifacts import StageArtifactStore
from spy_der.runtime.heartbeat import write_heartbeat
from spy_der.training.registry import ModelRegistry

__all__ = ["EngineConfig", "EngineService", "build_arg_parser", "main"]

log = logging.getLogger("spy_der.engine")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"
DEPLOYMENT_ID = "spy-der-engine"

#: Set in `/etc/spy-der/spy-der.env` after `spy-der-train` registers a group.
#: The systemd unit already loads that file; flags still win when passed.
ENV_FORECAST_GROUP = "SPY_DER_FORECAST_GROUP"
ENV_FORECAST_LOAD_MODE = "SPY_DER_FORECAST_LOAD_MODE"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    state_root: str = DEFAULT_STATE_ROOT
    interval_seconds: float = 30.0
    #: Stop after this many passes. 0 means run until signalled (the unit's case).
    max_passes: int = 0
    #: Restrict to one session (YYYY-MM-DD); empty means every recorded session.
    session: str = ""
    #: Registered model group to serve forecasts from. Empty keeps the stage
    #: fail-closed, which is the correct state until `spy-der train` has run.
    forecast_group_id: str = ""
    #: Registry load mode. `shadow` by design: the engine observes, and serving
    #: in `champion` mode is a promotion decision, not an engine flag.
    forecast_load_mode: str = "shadow"

    @property
    def model_dir(self) -> Path:
        return Path(self.state_root) / "models"

    @property
    def market_dir(self) -> Path:
        return Path(self.state_root) / "market"

    @property
    def journal_path(self) -> Path:
        return Path(self.state_root) / "journal" / "journal.db"


def _stage_availability(forecast: str) -> dict[str, str]:
    """Which deterministic stages can run in this deployment.

    Resolved once per process rather than per snapshot: a missing model registry
    is a standing condition, and journaling it 390 times a session would bury
    the events that describe actual work.
    """
    return {
        "candidates": "available",
        "features": "available",
        "forecast": forecast,
    }


_NO_FORECAST_GROUP = (
    "unavailable: no trained model group is configured; "
    "refusing the research-only heuristic path"
)


@dataclass(frozen=True, slots=True)
class _Stores:
    """The stage artifact stores, grouped so adding a stage is one field."""

    candidates: StageArtifactStore
    features: StageArtifactStore
    forecasts: StageArtifactStore


@dataclass
class EngineService:
    """Run the deterministic stages over recorded snapshots."""

    config: EngineConfig
    _stop: bool = False
    _seen: dict[str, set[str]] = field(default_factory=dict)
    features: SnapshotFeaturePipeline = field(default_factory=SnapshotFeaturePipeline)
    _forecaster: ForecastServer | None = field(default=None, init=False, repr=False)

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

    def _open_forecaster(self) -> tuple[ForecastServer | None, str]:
        """Load the configured model group; ``(None, reason)`` when it cannot serve.

        A load failure is a *reported* unavailability, not a crash and not a
        silent downgrade: the engine keeps running the stages that do work, and
        every snapshot journals why the forecast is missing.
        """
        cfg = self.config
        if not cfg.forecast_group_id:
            return None, _NO_FORECAST_GROUP
        try:
            server = ForecastServer(
                registry=ModelRegistry(str(cfg.model_dir)),
                group_id=cfg.forecast_group_id,
                load_mode=cfg.forecast_load_mode,
            ).load()
        except ForecastServingError as exc:
            return None, f"unavailable: {exc}"
        return server, f"available: group {cfg.forecast_group_id} ({cfg.forecast_load_mode})"

    # -- run --------------------------------------------------------------- #
    def run(self) -> int:
        cfg = self.config
        self._forecaster, forecast_state = self._open_forecaster()
        stages = _stage_availability(forecast_state)
        for name, state in sorted(stages.items()):
            log.info("stage %s: %s", name, state)

        journal = self._open_journal()
        stores = _Stores(
            candidates=StageArtifactStore(cfg.state_root, "candidates"),
            features=StageArtifactStore(cfg.state_root, "features"),
            forecasts=StageArtifactStore(cfg.state_root, "forecasts"),
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
    ) -> Any:
        """Build and record the feature bundle for ``snapshot``; ``None`` on failure.

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
            return None

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
        return result.bundle

    def _run_forecast_stage(
        self,
        journal: SqliteJournalStore,
        stores: _Stores,
        stages: dict[str, str],
        session: str,
        snapshot: CanonicalMarketSnapshot,
        bundle: Any,
    ) -> None:
        """Serve a forecast for ``snapshot``, or journal why it could not be.

        Fail-closed at every step. No configured group, no feature row, or a
        serving error all produce `FORECAST_UNAVAILABLE` with the reason — never
        a neutral value that a downstream stage would read as a real forecast.
        """
        reason = ""
        if self._forecaster is None:
            reason = stages.get("forecast", _NO_FORECAST_GROUP)
        elif bundle is None or not bundle.features:
            # A forecast needs features; without them there is nothing to serve
            # from, and the feature stage has already journaled its own failure.
            reason = "unavailable: no feature bundle for this snapshot"

        if not reason:
            try:
                forecast = self._forecaster.predict(  # type: ignore[union-attr]
                    snapshot_id=snapshot.snapshot_id,
                    ts=snapshot.timestamp.isoformat(),
                    session_date=snapshot.session_date.isoformat(),
                    symbol=snapshot.underlying_symbol,
                    feature_row=dict(bundle.features),
                    data_quality=1.0 - snapshot.data_quality.penalty,
                    # feature_coverage is a [0,1] fraction and the engine has no
                    # denominator for it — the expected feature set varies by
                    # snapshot. Left unset rather than filled with a count.
                )
            except Exception as exc:
                # Deliberately broad: a serving fault on one snapshot must not
                # stop the engine, and it is journaled rather than swallowed.
                log.warning("forecast failed for %s: %s", snapshot.snapshot_id, exc)
                reason = f"unavailable: {type(exc).__name__}: {exc}"

        if reason:
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.FORECAST_UNAVAILABLE.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot.snapshot_id,
                    occurred_at=snapshot.timestamp,
                    payload={"reason": reason},
                    deployment_id=DEPLOYMENT_ID,
                    snapshot_id=snapshot.snapshot_id,
                )
            )
            return

        stores.forecasts.append(
            session,
            artifact_id=snapshot.snapshot_id,
            schema_version=forecast.schema_version,
            payload=forecast,
        )
        journal.append(
            JournalEvent(
                event_type=JournalEventType.FORECAST_GENERATED.value,
                aggregate_type=AggregateType.SYSTEM.value,
                aggregate_id=snapshot.snapshot_id,
                occurred_at=snapshot.timestamp,
                payload={
                    "session_date": session,
                    "model_group_id": forecast.model_group_id,
                    "p_up_30m": forecast.p_up_30m,
                    "expected_return_30m": forecast.expected_return_30m,
                    "uncertainty": forecast.uncertainty,
                },
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

        bundle = self._run_feature_stage(journal, stores, session, snapshot)

        self._run_forecast_stage(journal, stores, stages, session, snapshot, bundle)

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
    p.add_argument(
        "--forecast-group",
        default=os.environ.get(ENV_FORECAST_GROUP, ""),
        help=(
            "registered model group to serve forecasts from (see spy-der-train); "
            f"default: ${ENV_FORECAST_GROUP} from the environment"
        ),
    )
    p.add_argument(
        "--forecast-load-mode",
        default=os.environ.get(ENV_FORECAST_LOAD_MODE, "shadow"),
        help=(
            "registry load mode for the forecast group "
            f"(default: ${ENV_FORECAST_LOAD_MODE} or shadow)"
        ),
    )
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
            forecast_group_id=args.forecast_group,
            forecast_load_mode=args.forecast_load_mode,
        )
    )
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
