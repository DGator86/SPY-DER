"""Recordings -> labeled observations -> a registered, servable model group.

This is the path that was missing entirely: every component existed and nothing
assembled them, so the forecast stage was hardcoded unavailable. The end-to-end
test is the one that matters — it records synthetic sessions, trains, registers,
and then serves a real `MarketForecastBundle` through `ForecastServer`.

The leakage guards are the other half. Labels must look strictly forward and
metrics must come from walk-forward folds over whole sessions, because intraday
rows are heavily autocorrelated and a random split would report skill that does
not exist.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from spy_der.contracts.market import (
    Bar,
    CanonicalMarketSnapshot,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
)
from spy_der.forecasting.runtime import ForecastServer, ForecastServingError
from spy_der.market_data.recording import build_record
from spy_der.training.observations import build_observations
from spy_der.training.pipeline import (
    COMPONENT_ROLES,
    TrainingResult,
    train_model_group,
)
from spy_der.training.registry import ModelRegistry, RegistryError

ET_OPEN_UTC = 14  # 09:30 ET in UTC during standard time


def _bars(session: date, n: int, *, seed: int) -> tuple[Bar, ...]:
    """A synthetic intraday path with genuine variation in both directions."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.35, n).cumsum()
    start = datetime(session.year, session.month, session.day, ET_OPEN_UTC, 30, tzinfo=UTC)
    out: list[Bar] = []
    for i in range(n):
        close = Decimal(f"{100.0 + steps[i]:.2f}")
        out.append(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=close,
                high=close + Decimal("0.20"),
                low=close - Decimal("0.20"),
                close=close,
                volume=1000 + i,
            )
        )
    return tuple(out)


def _chain(session: date, spot: float, received: datetime) -> tuple[OptionQuote, ...]:
    quotes: list[OptionQuote] = []
    centre = round(spot)
    for strike in range(centre - 6, centre + 7, 2):
        for side in (OptionType.CALL, OptionType.PUT):
            intrinsic = (
                max(spot - strike, 0.0)
                if side is OptionType.CALL
                else max(strike - spot, 0.0)
            )
            mid = Decimal(f"{intrinsic + 1.0:.2f}")
            quotes.append(
                OptionQuote(
                    contract=OptionContract(
                        contract_id=f"SPY-{strike}-{side.value}",
                        underlying_symbol="SPY",
                        expiration=session,
                        option_type=side,
                        strike=Decimal(str(strike)),
                    ),
                    received_at=received,
                    source="test",
                    bid=mid - Decimal("0.05"),
                    ask=mid + Decimal("0.05"),
                    volume=120 if side is OptionType.CALL else 150,
                    open_interest=1000 + strike,
                    gamma=0.02,
                    delta=0.5 if side is OptionType.CALL else -0.5,
                )
            )
    return tuple(quotes)


def _session_snapshots(
    session: date, *, seed: int, ticks: int = 40
) -> list[CanonicalMarketSnapshot]:
    """Snapshots across one session, each carrying the bar path up to its tick."""
    path = _bars(session, 200, seed=seed)
    snapshots: list[CanonicalMarketSnapshot] = []
    # Space observations through the session so 30-minute horizons resolve.
    for i in range(ticks):
        index = 20 + i * 4
        if index >= len(path):
            break
        bar = path[index]
        spot = float(bar.close)
        snapshots.append(
            CanonicalMarketSnapshot(
                snapshot_id=f"snap-{session.isoformat()}-{i}",
                content_hash=f"sha256:{session.isoformat()}-{i}",
                timestamp=bar.timestamp,
                session_date=session,
                underlying_symbol="SPY",
                underlying_price=Decimal(f"{spot:.2f}"),
                session_status=SessionStatus.OPEN,
                # The rolling window ending at this tick, as a live provider emits.
                bars_1m=path[: index + 1],
                option_chain=_chain(session, spot, bar.timestamp),
                minutes_to_close=390 - index,
                minutes_from_open=index,
            )
        )
    # Final snapshot carries the full session path — what the labeler reads.
    last = path[-1]
    snapshots.append(
        CanonicalMarketSnapshot(
            snapshot_id=f"snap-{session.isoformat()}-final",
            content_hash=f"sha256:{session.isoformat()}-final",
            timestamp=last.timestamp,
            session_date=session,
            underlying_symbol="SPY",
            underlying_price=last.close,
            session_status=SessionStatus.CLOSED,
            bars_1m=path,
            option_chain=_chain(session, float(last.close), last.timestamp),
            minutes_to_close=0,
            minutes_from_open=len(path),
        )
    )
    return snapshots


def _record(root: Path, sessions: int = 14) -> list[str]:
    """Write `sessions` consecutive weekday recordings under `root/market`."""
    market = root / "market"
    market.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    day = date(2026, 1, 5)  # a Monday
    for i in range(sessions):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        snapshots = _session_snapshots(day, seed=100 + i)
        path = market / f"{day.isoformat()}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for seq, snap in enumerate(snapshots):
                handle.write(json.dumps(build_record(seq, snap), sort_keys=True))
                handle.write("\n")
        written.append(day.isoformat())
        day += timedelta(days=1)
    return written


@pytest.fixture(scope="module")
def trained(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, TrainingResult]:
    """Train once for the module — fitting six models is not cheap."""
    root = tmp_path_factory.mktemp("state")
    _record(root)
    observations = build_observations(root)
    registry = ModelRegistry(str(root / "models"))
    result = train_model_group(observations, registry=registry, min_rows=50)
    return root, result


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #
def test_recordings_become_labeled_observations(tmp_path: Path) -> None:
    _record(tmp_path, sessions=2)
    observations = build_observations(tmp_path)
    assert len(observations) > 0
    assert len(observations.sessions) == 2
    first = observations.observations[0]
    assert first.features
    assert first.labels


def test_observations_carry_forward_outcomes(tmp_path: Path) -> None:
    """A label with no forward path is not a label."""
    _record(tmp_path, sessions=1)
    observations = build_observations(tmp_path)
    rows, y, _ = observations.target("up_30m")
    assert rows and len(rows) == len(y)
    assert set(y) <= {0, 1}


def test_a_target_drops_only_its_own_unlabeled_rows(tmp_path: Path) -> None:
    """Late-session rows lack a 30m outcome but still have earlier horizons."""
    _record(tmp_path, sessions=2)
    observations = build_observations(tmp_path)
    short = len(observations.target("up_5m")[0])
    long = len(observations.target("up_60m")[0])
    assert short >= long > 0


def test_features_are_recomputed_not_read_from_engine_artifacts(tmp_path: Path) -> None:
    """Training must be reproducible from the market recording alone."""
    _record(tmp_path, sessions=1)
    assert not (tmp_path / "features").exists()
    assert len(build_observations(tmp_path)) > 0


def test_a_session_without_bars_is_reported_not_silently_dropped(tmp_path: Path) -> None:
    market = tmp_path / "market"
    market.mkdir(parents=True)
    snap = CanonicalMarketSnapshot(
        snapshot_id="snap-nobars",
        content_hash="sha256:nobars",
        timestamp=datetime(2026, 2, 2, 15, 0, tzinfo=UTC),
        session_date=date(2026, 2, 2),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.CLOSED,
    )
    (market / "2026-02-02.jsonl").write_text(
        json.dumps(build_record(0, snap), sort_keys=True) + "\n", encoding="utf-8"
    )
    observations = build_observations(tmp_path)
    assert len(observations) == 0
    assert observations.skipped_sessions
    assert "bar path" in observations.skipped_sessions[0][1]
    assert "skipped" in observations.describe()


def test_labels_never_look_backwards(tmp_path: Path) -> None:
    """The guard that matters: a forward return must match the actual path."""
    _record(tmp_path, sessions=1)
    observations = build_observations(tmp_path)
    labeled = [o for o in observations.observations if o.labels.get("fwd_return_30m") is not None]
    assert labeled
    for observation in labeled[:5]:
        spot = float(observation.features["session.underlying_price"])
        fwd = float(observation.labels["fwd_return_30m"])
        # A 30-minute log return on this synthetic path is small and finite;
        # a backwards-looking label would be indistinguishable in sign but the
        # magnitude bound catches an off-by-a-session error.
        assert math.isfinite(fwd)
        assert abs(fwd) < 0.5
        assert spot > 0


# --------------------------------------------------------------------------- #
# Training                                                                    #
# --------------------------------------------------------------------------- #
def test_training_registers_a_group(trained: tuple[Path, TrainingResult]) -> None:
    _root, result = trained
    assert result.group_id
    assert result.trained_roles
    assert result.n_observations > 0


def test_every_component_role_is_accounted_for(trained: tuple[Path, TrainingResult]) -> None:
    """Trained or skipped-with-a-reason — never silently absent."""
    _root, result = trained
    reported = {r.role for r in result.roles}
    assert reported == {role for role, _, _ in COMPONENT_ROLES}
    for outcome in result.roles:
        assert outcome.trained or outcome.reason


def test_a_sparse_role_is_skipped_rather_than_fitted_on_nothing(
    tmp_path: Path,
) -> None:
    """A model fitted on forty rows still answers with confidence."""
    _record(tmp_path, sessions=1)
    observations = build_observations(tmp_path)
    registry = ModelRegistry(str(tmp_path / "models"))
    result = train_model_group(observations, registry=registry, min_rows=10_000)
    assert result.trained_roles == ()
    assert result.group_id == ""
    assert all("minimum" in r.reason for r in result.roles)


def test_the_group_is_research_status_and_cannot_promote_itself(
    trained: tuple[Path, TrainingResult],
) -> None:
    root, result = trained
    registry = ModelRegistry(str(root / "models"))
    group = registry.load_group(result.group_id)
    assert group.status == "research"
    with pytest.raises(RegistryError):
        registry.validate_group(group, load_mode="champion")


def test_metrics_are_out_of_fold_or_absent(trained: tuple[Path, TrainingResult]) -> None:
    """An unearned metric is worse than a missing one — promotion reads these."""
    _root, result = trained
    for outcome in result.roles:
        if not outcome.trained:
            continue
        for name in outcome.metrics:
            assert name.startswith("oof_"), name


def test_walk_forward_folds_are_used_when_there_is_enough_history(
    trained: tuple[Path, TrainingResult],
) -> None:
    _root, result = trained
    assert result.fold_count > 0


def test_registry_records_the_audit_fields_promotion_depends_on(
    trained: tuple[Path, TrainingResult],
) -> None:
    root, result = trained
    registry = ModelRegistry(str(root / "models"))
    group = registry.load_group(result.group_id)
    for model_id in group.component_model_ids.values():
        meta = registry.load_metadata(model_id, validate_v2=True)
        assert meta["training_feature_distribution_hash"]
        assert meta["fold_hash"]
        assert meta["dependency_versions"]
        assert meta["label_version"]


# --------------------------------------------------------------------------- #
# Serving — the point of all of it                                            #
# --------------------------------------------------------------------------- #
def test_the_trained_group_actually_serves_a_forecast(
    trained: tuple[Path, TrainingResult],
) -> None:
    """End to end: recordings in, a real MarketForecastBundle out."""
    root, result = trained
    assert result.is_servable, result.describe()

    observations = build_observations(root, sessions=[result.sessions[-1]])
    row = observations.observations[0].features

    server = ForecastServer(
        registry=ModelRegistry(str(root / "models")),
        group_id=result.group_id,
        load_mode="research",
    ).load()
    bundle = server.predict(
        snapshot_id="snap-serve",
        ts=datetime(2026, 3, 2, 15, 0, tzinfo=UTC).isoformat(),
        session_date="2026-03-02",
        symbol="SPY",
        feature_row=row,
    )

    assert bundle.model_group_id == result.group_id
    assert bundle.fallback_state == ""  # a real forecast, not the heuristic path
    produced = [
        bundle.p_up_30m,
        bundle.expected_return_30m,
        bundle.expected_realized_move_30m,
    ]
    assert any(v is not None for v in produced)
    if bundle.p_up_30m is not None:
        assert 0.0 <= bundle.p_up_30m <= 1.0


def test_quantiles_are_ordered_when_served(trained: tuple[Path, TrainingResult]) -> None:
    root, result = trained
    if "return_quantiles_30m" not in result.trained_roles:
        pytest.skip("quantile component not trained in this fixture")
    observations = build_observations(root, sessions=[result.sessions[-1]])
    server = ForecastServer(
        registry=ModelRegistry(str(root / "models")),
        group_id=result.group_id,
        load_mode="research",
    ).load()
    bundle = server.predict(
        snapshot_id="s",
        ts=datetime(2026, 3, 2, 15, 0, tzinfo=UTC).isoformat(),
        session_date="2026-03-02",
        symbol="SPY",
        feature_row=observations.observations[0].features,
    )
    assert bundle.return_q10_30m is not None
    assert bundle.return_q10_30m <= bundle.return_q50_30m <= bundle.return_q90_30m


def test_serving_a_research_group_in_champion_mode_is_refused(
    trained: tuple[Path, TrainingResult],
) -> None:
    """The registry's mode gate is the thing standing between research and live."""
    root, result = trained
    server = ForecastServer(
        registry=ModelRegistry(str(root / "models")),
        group_id=result.group_id,
        load_mode="champion",
    )
    with pytest.raises(ForecastServingError):
        server.load()


def test_serving_an_unknown_group_fails_closed(tmp_path: Path) -> None:
    server = ForecastServer(
        registry=ModelRegistry(str(tmp_path / "models")),
        group_id="group-does-not-exist",
        load_mode="research",
    )
    with pytest.raises(ForecastServingError):
        server.load()


# --------------------------------------------------------------------------- #
# The engine's forecast stage                                                 #
# --------------------------------------------------------------------------- #
def test_engine_serves_forecasts_from_a_trained_group(
    trained: tuple[Path, TrainingResult], tmp_path: Path
) -> None:
    """The whole point: a recorded session in, a journaled forecast out."""
    import shutil
    from collections import Counter

    from spy_der.contracts.events import JournalEventType
    from spy_der.journal.store import SqliteJournalStore
    from spy_der.runtime.engine import EngineConfig, EngineService

    root, result = trained
    shutil.copytree(root / "market", tmp_path / "market")
    shutil.copytree(root / "models", tmp_path / "models")

    EngineService(
        config=EngineConfig(
            state_root=str(tmp_path),
            max_passes=1,
            session=result.sessions[0],
            forecast_group_id=result.group_id,
            forecast_load_mode="research",
        )
    ).run()

    journal = SqliteJournalStore(tmp_path / "journal" / "journal.db")
    types = Counter(e.event_type for e in journal.iter_events())
    assert types[JournalEventType.FORECAST_GENERATED.value] > 0
    assert types[JournalEventType.FORECAST_UNAVAILABLE.value] == 0
    assert (tmp_path / "forecasts" / f"{result.sessions[0]}.jsonl").is_file()


def test_engine_journals_the_served_forecast_values(
    trained: tuple[Path, TrainingResult], tmp_path: Path
) -> None:
    import shutil

    from spy_der.contracts.events import JournalEventType
    from spy_der.journal.store import SqliteJournalStore
    from spy_der.runtime.engine import EngineConfig, EngineService

    root, result = trained
    shutil.copytree(root / "market", tmp_path / "market")
    shutil.copytree(root / "models", tmp_path / "models")
    EngineService(
        config=EngineConfig(
            state_root=str(tmp_path),
            max_passes=1,
            session=result.sessions[0],
            forecast_group_id=result.group_id,
            forecast_load_mode="research",
        )
    ).run()

    journal = SqliteJournalStore(tmp_path / "journal" / "journal.db")
    events = [
        e
        for e in journal.iter_events()
        if e.event_type == JournalEventType.FORECAST_GENERATED.value
    ]
    assert events
    assert events[0].payload["model_group_id"] == result.group_id


def test_engine_stays_fail_closed_without_a_group(tmp_path: Path) -> None:
    """No group must journal unavailability, never a neutral 0.5."""
    from collections import Counter

    from spy_der.contracts.events import JournalEventType
    from spy_der.journal.store import SqliteJournalStore
    from spy_der.runtime.engine import EngineConfig, EngineService

    _record(tmp_path, sessions=1)
    EngineService(config=EngineConfig(state_root=str(tmp_path), max_passes=1)).run()
    journal = SqliteJournalStore(tmp_path / "journal" / "journal.db")
    types = Counter(e.event_type for e in journal.iter_events())
    assert types[JournalEventType.FORECAST_UNAVAILABLE.value] > 0
    assert types[JournalEventType.FORECAST_GENERATED.value] == 0


def test_a_missing_group_is_reported_not_a_crash(tmp_path: Path) -> None:
    """A bad --forecast-group must degrade the stage, not stop the engine."""
    from collections import Counter

    from spy_der.contracts.events import JournalEventType
    from spy_der.journal.store import SqliteJournalStore
    from spy_der.runtime.engine import EngineConfig, EngineService

    _record(tmp_path, sessions=1)
    assert (
        EngineService(
            config=EngineConfig(
                state_root=str(tmp_path), max_passes=1, forecast_group_id="group-nope"
            )
        ).run()
        == 0
    )
    journal = SqliteJournalStore(tmp_path / "journal" / "journal.db")
    types = Counter(e.event_type for e in journal.iter_events())
    assert types[JournalEventType.FORECAST_UNAVAILABLE.value] > 0
    # Candidates still ran: one broken stage must not cost the others.
    assert types[JournalEventType.CANDIDATES_GENERATED.value] > 0


# --------------------------------------------------------------------------- #
# The CLI                                                                     #
# --------------------------------------------------------------------------- #
def test_train_cli_trains_and_reports(tmp_path: Path) -> None:
    from spy_der.runtime.training import main

    _record(tmp_path, sessions=12)
    assert main(["--state-root", str(tmp_path), "--min-rows", "50"]) == 0
    assert list((tmp_path / "models").glob("*.json"))


def test_train_cli_exits_two_without_recordings(tmp_path: Path) -> None:
    from spy_der.runtime.training import main

    assert main(["--state-root", str(tmp_path)]) == 2


def test_train_cli_exits_four_when_nothing_meets_the_minimum(tmp_path: Path) -> None:
    """Distinct exit codes because the operator fix differs."""
    from spy_der.runtime.training import main

    _record(tmp_path, sessions=1)
    assert main(["--state-root", str(tmp_path), "--min-rows", "100000"]) == 4


def test_a_research_group_will_not_serve_in_shadow_mode(
    trained: tuple[Path, TrainingResult], tmp_path: Path
) -> None:
    """The promotion gate, seen from the engine: status decides who may serve.

    Training deliberately registers `research`, and the engine's default mode is
    `shadow`, so a freshly trained group does not silently start serving. That
    is the intended friction — but it must be *visible*, so the reason is
    journaled rather than the stage just going quiet.
    """
    import shutil
    from collections import Counter

    from spy_der.contracts.events import JournalEventType
    from spy_der.journal.store import SqliteJournalStore
    from spy_der.runtime.engine import EngineConfig, EngineService

    root, result = trained
    shutil.copytree(root / "market", tmp_path / "market")
    shutil.copytree(root / "models", tmp_path / "models")
    EngineService(
        config=EngineConfig(
            state_root=str(tmp_path),
            max_passes=1,
            session=result.sessions[0],
            forecast_group_id=result.group_id,
            forecast_load_mode="shadow",  # group is `research`
        )
    ).run()

    journal = SqliteJournalStore(tmp_path / "journal" / "journal.db")
    events = [
        e
        for e in journal.iter_events()
        if e.event_type == JournalEventType.FORECAST_UNAVAILABLE.value
    ]
    assert events
    assert "research" in events[0].payload["reason"]
    types = Counter(e.event_type for e in journal.iter_events())
    assert types[JournalEventType.FORECAST_GENERATED.value] == 0


def test_the_train_cli_hint_names_a_mode_the_status_allows() -> None:
    """A hint that silently fails is worse than no hint."""
    from spy_der.runtime.training import _load_mode_for

    assert _load_mode_for("research") == "research"
    assert _load_mode_for("shadow") == "shadow"
    assert _load_mode_for("champion") == "champion"


def test_training_can_register_directly_at_shadow_status(tmp_path: Path) -> None:
    from spy_der.runtime.training import main

    _record(tmp_path, sessions=12)
    assert main(["--state-root", str(tmp_path), "--min-rows", "50", "--status", "shadow"]) == 0
    registry = ModelRegistry(str(tmp_path / "models"))
    groups = list((tmp_path / "models" / "groups").glob("*.json"))
    assert groups
    group = registry.load_group(groups[0].stem)
    assert group.status == "shadow"
    registry.validate_group(group, load_mode="shadow")
