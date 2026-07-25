"""Settlement and outcome labeling (`spy-der settlement`).

Drives `spy-der-settlement.service`: close a session out against its realized
settlement price and write outcomes into the journal under the originating
`snapshot_id`.

Nothing on this box takes live positions — `deploy/spy-der.env.example` pins
`SPY_DER_EXECUTION_MODE=shadow` with `SPY_DER_ALLOW_LIVE=0`, and no unit writes
a filled position. So every candidate the engine generated settles as a
**counterfactual**: what the outcome would have been had it been taken.
`spy_der.evaluation.settlement.settle_session` already models exactly this split
(`traded` versus `blocked`), and counterfactual outcomes are what the Dojo's
recorded phase learns from. When an execution stage later writes real fills,
they populate the `traded` side without changing this shape.

Two deliberate choices worth stating:

*Settlement price* comes from the session's own recording — the underlying price
on the final snapshot — which is `SettlementSource.SESSION_CLOSE`. The config
template names a dedicated `settlement_provider: yahoo`, but that adapter is
unbuilt and this service runs with no live vendor. Deriving the price from the
tape keeps settlement deterministic and reproducible, and it is the same number
a replay would compute.

*Candidates are regenerated from the tape* rather than parsed back out of the
engine's `candidates/` artifacts. `generate_candidate_universe` is deterministic
and the snapshot round-trip through `snapshot_from_dict` is byte-identical, so
regeneration yields the same universe by construction — and it avoids a second
deserializer that could drift from the factory. The journal still decides *which*
snapshots to settle: only those the engine recorded a `CANDIDATES_GENERATED`
event for.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import signal
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.contracts.candidates import Candidate
from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.contracts.market import CanonicalMarketSnapshot, SessionStatus
from spy_der.contracts.market_parse import SnapshotParseError, snapshot_from_dict
from spy_der.contracts.outcomes import SettlementSource
from spy_der.evaluation.settlement import settle_session
from spy_der.journal.store import SqliteJournalStore
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed
from spy_der.runtime.artifacts import StageArtifactStore

__all__ = ["SettlementConfig", "SettlementService", "build_arg_parser", "main"]

log = logging.getLogger("spy_der.settlement")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"
DEPLOYMENT_ID = "spy-der-settlement"
ACCOUNT_ID = "spy_der_shadow"

#: Why the candidate was not taken. Shadow mode never routes an order, so this
#: is the honest reason rather than a policy veto.
NOT_TAKEN_REASON = "shadow_mode_no_execution"


@dataclass(frozen=True, slots=True)
class SettlementConfig:
    state_root: str = DEFAULT_STATE_ROOT
    interval_seconds: float = 300.0
    max_passes: int = 0
    session: str = ""

    @property
    def market_dir(self) -> Path:
        return Path(self.state_root) / "market"

    @property
    def journal_path(self) -> Path:
        return Path(self.state_root) / "journal" / "journal.db"


@dataclass
class SettlementService:
    """Settle closed sessions and label their outcomes."""

    config: SettlementConfig
    _stop: bool = False
    _settled: set[str] = field(default_factory=set)

    def request_stop(self, *_args: object) -> None:
        self._stop = True

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

    def _already_settled(self, journal: SqliteJournalStore) -> set[str]:
        """Sessions with a settlement-complete marker in the journal."""
        done: set[str] = set()
        for event in journal.iter_events(
            event_type=JournalEventType.OUTCOME_SETTLED.value
        ):
            session = event.payload.get("session_date")
            if session:
                done.add(str(session))
        for event in journal.iter_events(aggregate_id="settlement"):
            session = event.payload.get("session_date")
            if session:
                done.add(str(session))
        return done

    def _engine_snapshots(self, journal: SqliteJournalStore, session: str) -> set[str]:
        """Snapshot ids the engine recorded a candidate universe for."""
        out: set[str] = set()
        for event in journal.iter_events(
            event_type=JournalEventType.CANDIDATES_GENERATED.value
        ):
            if str(event.payload.get("session_date", "")) != session:
                continue
            if event.snapshot_id:
                out.add(str(event.snapshot_id))
        return out

    def run(self) -> int:
        cfg = self.config
        journal = self._open_journal()
        artifacts = StageArtifactStore(cfg.state_root, "settlements")
        self._settled |= self._already_settled(journal)

        passes = 0
        while not self._stop:
            settled = 0
            for session in self._sessions():
                if self._stop:
                    break
                if session in self._settled:
                    continue
                if self._settle(journal, artifacts, session):
                    settled += 1
            passes += 1
            if settled:
                log.info("pass %d settled %d session(s)", passes, settled)
            if cfg.max_passes and passes >= cfg.max_passes:
                break
            if self._stop:
                break
            time.sleep(cfg.interval_seconds)

        log.info("settlement stopped after %d pass(es)", passes)
        return 0

    def _settle(
        self,
        journal: SqliteJournalStore,
        artifacts: StageArtifactStore,
        session: str,
    ) -> bool:
        path = self.config.market_dir / f"{session}.jsonl"
        try:
            feed = ReplayFeed.from_file(path)
        except CorruptRecordingError as exc:
            log.error("recording %s failed integrity checks: %s", session, exc)
            return False
        except OSError as exc:
            log.error("recording %s is unreadable: %s", session, exc)
            return False

        payloads = list(feed.replay())
        if not payloads:
            return False

        try:
            final = snapshot_from_dict(payloads[-1])
        except SnapshotParseError as exc:
            log.error("final snapshot of %s could not be rebuilt: %s", session, exc)
            return False

        if not self._session_is_over(final, session):
            log.info("session %s is not closed yet; leaving it unsettled", session)
            return False

        wanted = self._engine_snapshots(journal, session)
        if not wanted:
            log.info("no candidate universes recorded for %s; nothing to settle", session)
            return False

        settlement_price = final.underlying_price
        # settle_session's `blocked` shape: (candidate, entry_credit, reason, policy_id)
        blocked: list[tuple[Candidate, Decimal, str, str]] = []
        for payload in payloads:
            snapshot_id = str(payload.get("snapshot_id", ""))
            if snapshot_id not in wanted:
                continue
            try:
                snapshot = snapshot_from_dict(payload)
            except SnapshotParseError as exc:
                log.error("snapshot %s could not be rebuilt: %s", snapshot_id, exc)
                continue
            universe = generate_candidate_universe(snapshot)
            for candidate in universe.candidates:
                blocked.append(
                    (candidate, candidate.entry_credit, NOT_TAKEN_REASON, "shadow")
                )

        if not blocked:
            log.info("no candidates to settle for %s", session)
            return False

        batch = settle_session(
            session_date=session,
            settlement_price=Decimal(str(settlement_price)),
            traded=(),
            blocked=blocked,
            journal=journal,
            account_id=ACCOUNT_ID,
            deployment_id=DEPLOYMENT_ID,
            source=SettlementSource.SESSION_CLOSE,
        )

        artifacts.append(
            session,
            artifact_id=f"settlement-{session}",
            schema_version="spyder.settlement.v1",
            payload={
                "session_date": session,
                "settlement_price": str(settlement_price),
                "source": SettlementSource.SESSION_CLOSE.value,
                "counterfactuals": len(batch.counterfactuals),
                "outcomes": len(batch.outcomes),
                "snapshots_settled": len(wanted),
            },
        )
        journal.append(
            JournalEvent(
                event_type=JournalEventType.OUTCOME_SETTLED.value,
                aggregate_type=AggregateType.SYSTEM.value,
                aggregate_id="settlement",
                payload={
                    "session_date": session,
                    "settlement_price": str(settlement_price),
                    "counterfactuals": len(batch.counterfactuals),
                    "traded_outcomes": len(batch.outcomes),
                },
                deployment_id=DEPLOYMENT_ID,
            )
        )
        self._settled.add(session)
        log.info(
            "settled %s at %s: %d counterfactual(s) over %d snapshot(s)",
            session,
            settlement_price,
            len(batch.counterfactuals),
            len(wanted),
        )
        return True

    @staticmethod
    def _session_is_over(final_snapshot: CanonicalMarketSnapshot, session: str) -> bool:
        """Never settle a session that may still be trading.

        Closed either because the tape says so, or because the session date is
        in the past — a recording that simply stopped mid-session on a past day
        is still safe to settle against its last observed price.
        """
        if final_snapshot.session_status is SessionStatus.CLOSED:
            return True
        try:
            session_date = dt.date.fromisoformat(session)
        except ValueError:
            return False
        return session_date < dt.datetime.now(tz=dt.UTC).date()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "SPY-DER settlement and outcome labeling — settles closed sessions "
            "against the recorded close and journals the outcomes."
        )
    )
    p.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    p.add_argument("--interval", type=float, default=300.0)
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

    service = SettlementService(
        config=SettlementConfig(
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
