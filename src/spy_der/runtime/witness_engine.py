"""Deterministic engine extension that consumes frozen Beta witness artifacts.

No network access is introduced here. Beta was sampled by the market runtime and
is replayed from ``witnesses/beta/<session>.jsonl``. The witness is attached as
shadow evidence only: it cannot alter Alpha P, choose a structure, size risk, or
place a trade until separate out-of-sample promotion evidence authorizes a
blend policy.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import signal
from dataclasses import dataclass, field
from typing import Any

from spy_der.contracts.events import AggregateType, JournalEvent, JournalEventType
from spy_der.contracts.forecasts import MarketForecastBundle
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.journal.store import SqliteJournalStore
from spy_der.runtime.artifacts import StageArtifactStore
from spy_der.runtime.engine import (
    _NO_FORECAST_GROUP,
    _Stores,
    DEFAULT_STATE_ROOT,
    DEPLOYMENT_ID,
    ENV_FORECAST_GROUP,
    ENV_FORECAST_LOAD_MODE,
    EngineConfig,
    EngineService,
)

log = logging.getLogger("spy_der.engine")


@dataclass
class WitnessEngineService(EngineService):
    """EngineService that carries Beta into the immutable forecast as shadow evidence."""

    _beta_cache: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def _beta_record(self, session: str, snapshot_id: str) -> dict[str, Any] | None:
        if session not in self._beta_cache:
            records: dict[str, dict[str, Any]] = {}
            store = StageArtifactStore(self.config.state_root, "witnesses/beta")
            try:
                for payload in store.read(session):
                    if not isinstance(payload, dict):
                        continue
                    market_snapshot_id = str(payload.get("market_snapshot_id") or "")
                    if market_snapshot_id:
                        records[market_snapshot_id] = payload
            except Exception as exc:
                # A witness artifact defect never mutates Alpha P. The primary
                # engine remains available and the witness is simply absent.
                log.warning("Beta witness tape unavailable for %s: %s", session, exc)
            self._beta_cache[session] = records
        return self._beta_cache[session].get(snapshot_id)

    @staticmethod
    def _shadow_payload(
        forecast: MarketForecastBundle,
        record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if record is None:
            return {
                "available": False,
                "reason": "no_frozen_beta_record",
                "trading_authority": False,
            }
        if not bool(record.get("available")):
            return {
                "available": False,
                "reason": str(record.get("unavailable_reason") or "beta_unavailable"),
                "trading_authority": False,
            }
        witness = record.get("witness")
        if not isinstance(witness, dict):
            return {
                "available": False,
                "reason": "malformed_beta_witness",
                "trading_authority": False,
            }

        rows = witness.get("horizons")
        if not isinstance(rows, list):
            rows = []
        horizons: dict[str, Any] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                horizon = int(row.get("horizon_minutes"))
                probability_up = float(row.get("probability_up"))
                expected_simple = float(row.get("expected_return"))
                confidence = float(row.get("confidence"))
                sample_count = int(row.get("sample_count"))
            except (TypeError, ValueError):
                continue
            if horizon not in {5, 15, 30}:
                continue
            if not bool(row.get("model_ready")) or sample_count <= 0:
                continue
            finite_values = (probability_up, expected_simple, confidence)
            if not all(math.isfinite(value) for value in finite_values):
                continue
            if not 0.0 <= probability_up <= 1.0 or not 0.0 <= confidence <= 1.0:
                continue
            expected_log = math.log1p(expected_simple) if expected_simple > -1.0 else None
            alpha_p = getattr(forecast, f"p_up_{horizon}m", None)
            alpha_er = getattr(forecast, f"expected_return_{horizon}m", None)
            horizons[f"{horizon}m"] = {
                "probability_up": probability_up,
                "expected_return": expected_log,
                "confidence": confidence,
                "sample_count": sample_count,
                "alpha_probability_up": alpha_p,
                "alpha_expected_return": alpha_er,
                "probability_disagreement": (
                    abs(probability_up - float(alpha_p)) if alpha_p is not None else None
                ),
                "expected_return_disagreement": (
                    abs(expected_log - float(alpha_er))
                    if expected_log is not None and alpha_er is not None
                    else None
                ),
            }

        return {
            "available": bool(horizons),
            "reason": "" if horizons else "no_model_ready_beta_horizon",
            "trading_authority": False,
            "blend_weight": 0.0,
            "source_timestamp": witness.get("source_timestamp"),
            "source_version": witness.get("source_version"),
            "coverage_ratio": witness.get("coverage_ratio"),
            "covered_weight": witness.get("covered_weight"),
            "horizons": horizons,
        }

    @classmethod
    def _attach_beta_shadow(
        cls,
        forecast: MarketForecastBundle,
        record: dict[str, Any] | None,
    ) -> MarketForecastBundle:
        shadow = cls._shadow_payload(forecast, record)
        data = forecast.to_dict()
        diagnostics = dict(data.get("diagnostics") or {})
        diagnostics["beta_witness"] = shadow
        data["diagnostics"] = diagnostics
        model_versions = dict(data.get("model_versions") or {})
        if shadow.get("source_version"):
            model_versions["beta_witness_shadow"] = str(shadow["source_version"])
        data["model_versions"] = model_versions
        # Beta is ancillary shadow evidence, so preserve the Alpha forecast id
        # while recomputing the payload hash for the enriched immutable object.
        data["content_hash"] = ""
        return MarketForecastBundle.from_dict(data)

    def _run_forecast_stage(
        self,
        journal: SqliteJournalStore,
        stores: _Stores,
        stages: dict[str, str],
        session: str,
        snapshot: CanonicalMarketSnapshot,
        bundle: Any,
    ) -> None:
        reason = ""
        if self._forecaster is None:
            reason = stages.get("forecast", _NO_FORECAST_GROUP)
        elif bundle is None or not bundle.features:
            reason = "unavailable: no feature bundle for this snapshot"

        forecast: MarketForecastBundle | None = None
        if not reason:
            try:
                forecast = self._forecaster.predict(  # type: ignore[union-attr]
                    snapshot_id=snapshot.snapshot_id,
                    ts=snapshot.timestamp.isoformat(),
                    session_date=snapshot.session_date.isoformat(),
                    symbol=snapshot.underlying_symbol,
                    feature_row=dict(bundle.features),
                    data_quality=1.0 - snapshot.data_quality.penalty,
                )
                forecast = self._attach_beta_shadow(
                    forecast,
                    self._beta_record(session, snapshot.snapshot_id),
                )
            except Exception as exc:
                log.warning("forecast failed for %s: %s", snapshot.snapshot_id, exc)
                reason = f"unavailable: {type(exc).__name__}: {exc}"

        if reason or forecast is None:
            journal.append(
                JournalEvent(
                    event_type=JournalEventType.FORECAST_UNAVAILABLE.value,
                    aggregate_type=AggregateType.SYSTEM.value,
                    aggregate_id=snapshot.snapshot_id,
                    occurred_at=snapshot.timestamp,
                    payload={"reason": reason or "unavailable: forecast not produced"},
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
        beta_shadow = forecast.diagnostics.get("beta_witness", {})
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
                    "beta_witness_available": bool(beta_shadow.get("available")),
                    "beta_witness_trading_authority": False,
                },
                deployment_id=DEPLOYMENT_ID,
                snapshot_id=snapshot.snapshot_id,
            )
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "SPY-DER deterministic engine — replays recorded snapshots through "
            "deterministic stages and frozen forecast witnesses."
        )
    )
    parser.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--max-passes", type=int, default=0, help="0 = run until signalled")
    parser.add_argument("--once", action="store_true", help="single pass, then exit")
    parser.add_argument("--session", default="", help="restrict to one YYYY-MM-DD session")
    parser.add_argument(
        "--forecast-group",
        default=os.environ.get(ENV_FORECAST_GROUP, ""),
        help=f"registered model group; default: ${ENV_FORECAST_GROUP}",
    )
    parser.add_argument(
        "--forecast-load-mode",
        default=os.environ.get(ENV_FORECAST_LOAD_MODE, "shadow"),
        help=f"registry load mode; default: ${ENV_FORECAST_LOAD_MODE} or shadow",
    )
    parser.add_argument("--config", default=None, help="reserved (not read yet)")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    if args.config:
        log.warning("--config is accepted but not read yet; using flags and environment")

    service = WitnessEngineService(
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
