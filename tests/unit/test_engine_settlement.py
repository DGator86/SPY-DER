"""`spy-der engine` and `spy-der settlement` over recorded sessions.

Both units shipped for releases invoking commands that did not exist. These
cover the pipeline they now drive: recording -> candidates + journal ->
settled outcomes, all deterministic and offline.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from spy_der.contracts.candidates import FACTORY_VERSION
from spy_der.contracts.events import JournalEventType
from spy_der.contracts.market import (
    CanonicalMarketSnapshot,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
)
from spy_der.journal.store import SqliteJournalStore
from spy_der.market_data.recording import build_record
from spy_der.market_data.replay import ReplayFeed
from spy_der.runtime.artifacts import StageArtifactStore
from spy_der.runtime.engine import EngineConfig, EngineService
from spy_der.runtime.settlement import SettlementConfig, SettlementService

SESSION = "2026-01-05"


def _quote(strike: str, option_type: OptionType) -> OptionQuote:
    exp = date(2026, 1, 5)
    right = "C" if option_type is OptionType.CALL else "P"
    dist = abs(Decimal(strike) - Decimal("100"))
    mid = max(Decimal("0.15"), Decimal("4") - dist * Decimal("0.5"))
    return OptionQuote(
        contract=OptionContract(
            contract_id=f"SPY{exp:%y%m%d}{right}{int(Decimal(strike) * 1000):08d}",
            underlying_symbol="SPY",
            expiration=exp,
            option_type=option_type,
            strike=Decimal(strike),
        ),
        received_at=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        source="test",
        bid=mid - Decimal("0.05"),
        ask=mid + Decimal("0.05"),
        mark=mid,
    )


def _snapshot(
    snapshot_id: str, *, status: SessionStatus, price: str = "100"
) -> CanonicalMarketSnapshot:
    chain: list[OptionQuote] = []
    for k in [str(x) for x in range(97, 104)]:
        chain.append(_quote(k, OptionType.CALL))
        chain.append(_quote(k, OptionType.PUT))
    return CanonicalMarketSnapshot(
        snapshot_id=snapshot_id,
        content_hash=f"sha256:{snapshot_id}",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal(price),
        session_status=status,
        option_chain=tuple(chain),
        minutes_to_close=90,
    )


def _record_session(
    root: Path, snapshots: list[CanonicalMarketSnapshot], session: str = SESSION
) -> Path:
    market = root / "market"
    market.mkdir(parents=True, exist_ok=True)
    path = market / f"{session}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for seq, snap in enumerate(snapshots):
            handle.write(json.dumps(build_record(seq, snap), sort_keys=True))
            handle.write("\n")
    return path


def _closed_session(root: Path) -> None:
    _record_session(
        root,
        [
            _snapshot("snap-1", status=SessionStatus.OPEN),
            _snapshot("snap-2", status=SessionStatus.CLOSED, price="101.5"),
        ],
    )


def _run_engine(root: Path, **kw) -> EngineService:
    service = EngineService(config=EngineConfig(state_root=str(root), max_passes=1, **kw))
    service.run()
    return service


def _run_settlement(root: Path, **kw) -> SettlementService:
    service = SettlementService(
        config=SettlementConfig(state_root=str(root), max_passes=1, **kw)
    )
    service.run()
    return service


def _journal(root: Path) -> SqliteJournalStore:
    return SqliteJournalStore(root / "journal" / "journal.db")


def _types(root: Path) -> Counter:
    return Counter(e.event_type for e in _journal(root).iter_events())


# --------------------------------------------------------------------------- #
# Engine                                                                      #
# --------------------------------------------------------------------------- #
def test_engine_forecast_group_defaults_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unit loads spy-der.env; operators should not need an ExecStart drop-in."""
    from spy_der.runtime.engine import build_arg_parser

    monkeypatch.setenv("SPY_DER_FORECAST_GROUP", "group-from-env")
    monkeypatch.setenv("SPY_DER_FORECAST_LOAD_MODE", "research")
    args = build_arg_parser().parse_args([])
    assert args.forecast_group == "group-from-env"
    assert args.forecast_load_mode == "research"

    args = build_arg_parser().parse_args(
        ["--forecast-group", "group-from-flag", "--forecast-load-mode", "shadow"]
    )
    assert args.forecast_group == "group-from-flag"
    assert args.forecast_load_mode == "shadow"


def test_engine_writes_a_candidate_artifact_per_snapshot(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    artifact = tmp_path / "candidates" / f"{SESSION}.jsonl"
    assert artifact.is_file()
    assert len(ReplayFeed.from_file(artifact)) == 2


def test_engine_artifact_verifies_through_replayfeed(tmp_path: Path) -> None:
    """Stage artifacts reuse the recording envelope so replay can verify them."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    feed = ReplayFeed.from_file(tmp_path / "candidates" / f"{SESSION}.jsonl")
    assert feed.snapshot_ids() == ("snap-1", "snap-2")
    universes = list(feed.replay())
    assert universes[0]["factory_version"] == FACTORY_VERSION
    assert universes[0]["candidates"]


def test_engine_journals_candidates_generated(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    assert _types(tmp_path)[JournalEventType.CANDIDATES_GENERATED.value] == 2


def test_engine_journals_forecast_unavailable_rather_than_a_neutral_forecast(
    tmp_path: Path,
) -> None:
    """The fail-closed rule: no model group means no forecast, said out loud."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    types = _types(tmp_path)
    assert types[JournalEventType.FORECAST_UNAVAILABLE.value] == 2
    assert types[JournalEventType.FORECAST_GENERATED.value] == 0


def test_engine_does_not_journal_a_lifecycle_event_as_a_decision(tmp_path: Path) -> None:
    """Borrowing SYSTEM_DECIDED for startup would corrupt decision queries."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    assert _types(tmp_path)[JournalEventType.SYSTEM_DECIDED.value] == 0


def test_engine_journal_chain_verifies(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    assert _journal(tmp_path).verify_chain()


def test_engine_is_idempotent_across_restarts(tmp_path: Path) -> None:
    """A restart must resume, not duplicate every artifact and event."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    first = _types(tmp_path)[JournalEventType.CANDIDATES_GENERATED.value]
    _run_engine(tmp_path)  # fresh service, same state root
    artifact = tmp_path / "candidates" / f"{SESSION}.jsonl"
    assert len(ReplayFeed.from_file(artifact)) == 2
    assert _types(tmp_path)[JournalEventType.CANDIDATES_GENERATED.value] == first


def test_engine_picks_up_snapshots_appended_after_a_pass(tmp_path: Path) -> None:
    _record_session(tmp_path, [_snapshot("snap-1", status=SessionStatus.OPEN)])
    _run_engine(tmp_path)
    _record_session(
        tmp_path,
        [
            _snapshot("snap-1", status=SessionStatus.OPEN),
            _snapshot("snap-2", status=SessionStatus.CLOSED, price="101.5"),
        ],
    )
    _run_engine(tmp_path)
    assert len(ReplayFeed.from_file(tmp_path / "candidates" / f"{SESSION}.jsonl")) == 2


def test_engine_skips_a_corrupt_recording_without_dying(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _record_session(
        tmp_path, [_snapshot("other-1", status=SessionStatus.CLOSED)], session="2026-01-06"
    )
    bad = tmp_path / "market" / "2026-01-06.jsonl"
    record = json.loads(bad.read_text(encoding="utf-8").strip())
    record["snapshot"]["underlying_price"] = "999"  # hash no longer matches
    bad.write_text(json.dumps(record) + "\n", encoding="utf-8")

    _run_engine(tmp_path)
    # The good session still processed.
    assert len(ReplayFeed.from_file(tmp_path / "candidates" / f"{SESSION}.jsonl")) == 2
    assert not (tmp_path / "candidates" / "2026-01-06.jsonl").exists()


def test_engine_respects_a_session_filter(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _record_session(
        tmp_path, [_snapshot("other-1", status=SessionStatus.CLOSED)], session="2026-01-06"
    )
    _run_engine(tmp_path, session="2026-01-06")
    assert (tmp_path / "candidates" / "2026-01-06.jsonl").is_file()
    assert not (tmp_path / "candidates" / f"{SESSION}.jsonl").exists()


def test_engine_with_no_recordings_is_a_clean_noop(tmp_path: Path) -> None:
    _run_engine(tmp_path)
    assert not (tmp_path / "candidates").exists()


# --------------------------------------------------------------------------- #
# Settlement                                                                  #
# --------------------------------------------------------------------------- #
def test_settlement_settles_engine_candidates_as_counterfactuals(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    _run_settlement(tmp_path)
    types = _types(tmp_path)
    assert types[JournalEventType.COUNTERFACTUAL_SETTLED.value] > 0
    assert types[JournalEventType.OUTCOME_SETTLED.value] == 1  # session marker


def test_settlement_uses_the_recorded_close_as_the_price(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    _run_settlement(tmp_path)
    artifact = next(iter(StageArtifactStore(tmp_path, "settlements").read(SESSION)))
    assert artifact["settlement_price"] == "101.5"
    assert artifact["source"] == "session_close"


def test_settlement_refuses_a_session_still_trading(tmp_path: Path) -> None:
    """Settling an open session would label outcomes against a mid-session price."""
    today = dt.datetime.now(tz=UTC).date().isoformat()
    _record_session(
        tmp_path, [_snapshot("snap-1", status=SessionStatus.OPEN)], session=today
    )
    _run_engine(tmp_path, session=today)
    _run_settlement(tmp_path, session=today)
    assert _types(tmp_path)[JournalEventType.COUNTERFACTUAL_SETTLED.value] == 0


def test_settlement_settles_a_past_session_that_never_recorded_a_close(
    tmp_path: Path,
) -> None:
    """A recording that stopped mid-session on a past day is still settleable."""
    _record_session(tmp_path, [_snapshot("snap-1", status=SessionStatus.OPEN)])
    _run_engine(tmp_path)
    _run_settlement(tmp_path)
    assert _types(tmp_path)[JournalEventType.COUNTERFACTUAL_SETTLED.value] > 0


def test_settlement_without_an_engine_run_settles_nothing(tmp_path: Path) -> None:
    """Settlement follows the journal: no CANDIDATES_GENERATED, nothing to settle."""
    _closed_session(tmp_path)
    _run_settlement(tmp_path)
    assert _types(tmp_path)[JournalEventType.COUNTERFACTUAL_SETTLED.value] == 0


def test_settlement_is_idempotent(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    _run_settlement(tmp_path)
    first = _types(tmp_path)[JournalEventType.COUNTERFACTUAL_SETTLED.value]
    _run_settlement(tmp_path)  # fresh service, same state root
    assert _types(tmp_path)[JournalEventType.COUNTERFACTUAL_SETTLED.value] == first


def test_settlement_journal_chain_verifies(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    _run_settlement(tmp_path)
    assert _journal(tmp_path).verify_chain()


def test_outcomes_are_keyed_to_the_originating_snapshot(tmp_path: Path) -> None:
    """The unit's contract: outcomes go in under the originating snapshot_id."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    _run_settlement(tmp_path)
    settled = [
        e
        for e in _journal(tmp_path).iter_events()
        if e.event_type == JournalEventType.COUNTERFACTUAL_SETTLED.value
    ]
    assert settled
    assert {e.snapshot_id for e in settled} <= {"snap-1", "snap-2"}


# --------------------------------------------------------------------------- #
# Stage artifact store                                                        #
# --------------------------------------------------------------------------- #
def test_artifact_sequence_continues_across_process_restarts(tmp_path: Path) -> None:
    """A restarted writer must not restart seq at 0 and fake a sequence gap."""
    first = StageArtifactStore(tmp_path, "candidates")
    first.append(SESSION, artifact_id="a", schema_version="v1", payload={"n": 1})
    second = StageArtifactStore(tmp_path, "candidates")
    second.append(SESSION, artifact_id="b", schema_version="v1", payload={"n": 2})
    feed = ReplayFeed.from_file(tmp_path / "candidates" / f"{SESSION}.jsonl")
    assert len(feed) == 2  # ReplayFeed fails closed on a seq gap


def test_artifact_read_of_a_missing_session_is_empty(tmp_path: Path) -> None:
    assert list(StageArtifactStore(tmp_path, "candidates").read("2020-01-01")) == []


def test_artifact_sessions_lists_written_sessions(tmp_path: Path) -> None:
    store = StageArtifactStore(tmp_path, "candidates")
    store.append("2026-01-05", artifact_id="a", schema_version="v1", payload={})
    store.append("2026-01-06", artifact_id="b", schema_version="v1", payload={})
    assert store.sessions() == ["2026-01-05", "2026-01-06"]


def test_artifact_payload_round_trips_through_replay(tmp_path: Path) -> None:
    store = StageArtifactStore(tmp_path, "candidates")
    store.append(SESSION, artifact_id="a", schema_version="v1", payload={"k": Decimal("1.5")})
    assert list(store.read(SESSION)) == [{"k": "1.5"}]


# --------------------------------------------------------------------------- #
# Determinism                                                                 #
# --------------------------------------------------------------------------- #
def test_engine_output_is_deterministic_across_state_roots(tmp_path: Path) -> None:
    """Same tape in, same candidates out — the property the parity gates assume."""
    roots = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        _closed_session(root)
        _run_engine(root)
        roots.append((root / "candidates" / f"{SESSION}.jsonl").read_text(encoding="utf-8"))
    assert roots[0] == roots[1]


def test_replayed_snapshot_matches_a_direct_generation(tmp_path: Path) -> None:
    from spy_der.candidates.factory import generate_candidate_universe
    from spy_der.contracts.serialization import to_canonical_json

    snapshot = _snapshot("snap-1", status=SessionStatus.CLOSED)
    _record_session(tmp_path, [snapshot])
    _run_engine(tmp_path)
    from_engine = next(iter(StageArtifactStore(tmp_path, "candidates").read(SESSION)))
    direct = json.loads(to_canonical_json(generate_candidate_universe(snapshot)))
    assert from_engine == direct


def test_snapshot_unchanged_by_replace_helper() -> None:
    """Guard the fixture itself: `replace` must not drop the option chain."""
    base = _snapshot("s", status=SessionStatus.OPEN)
    assert replace(base, snapshot_id="t").option_chain == base.option_chain


# --------------------------------------------------------------------------- #
# Feature stage                                                               #
# --------------------------------------------------------------------------- #
def test_engine_writes_a_feature_bundle_per_snapshot(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    artifact = tmp_path / "features" / f"{SESSION}.jsonl"
    assert artifact.is_file()
    assert len(ReplayFeed.from_file(artifact)) == 2


def test_engine_journals_features_computed(tmp_path: Path) -> None:
    """The stage is no longer reported unavailable — it produces real events."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    assert _types(tmp_path)[JournalEventType.FEATURES_COMPUTED.value] == 2
    assert _types(tmp_path)[JournalEventType.FEATURE_STAGE_FAILED.value] == 0


def test_feature_bundles_verify_through_replayfeed(tmp_path: Path) -> None:
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    feed = ReplayFeed.from_file(tmp_path / "features" / f"{SESSION}.jsonl")
    bundles = list(feed.replay())
    assert all(b["snapshot_id"] for b in bundles)
    assert all(b["features"] for b in bundles)


def test_the_journal_records_which_families_were_unavailable(tmp_path: Path) -> None:
    """Which families are absent varies with the data; that variation is the diagnostic."""
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    events = [
        e
        for e in _journal(tmp_path).iter_events()
        if e.event_type == JournalEventType.FEATURES_COMPUTED.value
    ]
    assert events
    assert "missing_families" in events[0].payload
    assert events[0].payload["feature_count"] > 0


def test_a_feature_failure_does_not_cost_the_candidate_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidates are the decision surface; losing them to a feature defect is worse."""

    def boom(_self: object, _snapshot: object) -> object:
        raise ValueError("feature stage exploded")

    monkeypatch.setattr(
        "spy_der.features.pipeline.SnapshotFeaturePipeline.build_detailed", boom
    )
    _closed_session(tmp_path)
    _run_engine(tmp_path)
    types = _types(tmp_path)
    assert types[JournalEventType.FEATURE_STAGE_FAILED.value] == 2
    assert types[JournalEventType.CANDIDATES_GENERATED.value] == 2
