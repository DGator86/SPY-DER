from __future__ import annotations

from datetime import UTC, datetime

from spy_der.forecasting.witnesses.beta import (
    BetaHorizonWitness,
    BetaWitnessError,
    BetaWitnessSnapshot,
)
from spy_der.runtime.artifacts import StageArtifactStore
from spy_der.runtime.market_service import MarketServiceConfig
from spy_der.runtime.witness_market_service import WitnessMarketService


class _GoodClient:
    def fetch(self, *, as_of: datetime) -> BetaWitnessSnapshot:
        return BetaWitnessSnapshot(
            source_timestamp=as_of,
            status="LIVE",
            stale_seconds=0.0,
            coverage_ratio=0.98,
            covered_weight=0.97,
            horizons=(
                BetaHorizonWitness(
                    horizon_minutes=15,
                    probability_up=0.61,
                    expected_return=0.0004,
                    confidence=0.7,
                    model_ready=True,
                    sample_count=800,
                ),
            ),
        )


class _BadClient:
    def fetch(self, *, as_of: datetime) -> BetaWitnessSnapshot:
        del as_of
        raise BetaWitnessError("stale")


def test_market_service_freezes_available_beta_witness(tmp_path) -> None:
    service = WitnessMarketService(MarketServiceConfig(state_root=str(tmp_path)))
    service._beta_client = _GoodClient()  # type: ignore[assignment]
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)

    service._record_beta(snapshot_id="snap-1", session="2026-08-27", as_of=now)

    rows = list(StageArtifactStore(tmp_path, "witnesses/beta").read("2026-08-27"))
    assert len(rows) == 1
    assert rows[0]["market_snapshot_id"] == "snap-1"
    assert rows[0]["available"] is True
    assert rows[0]["witness"]["horizons"][0]["horizon_minutes"] == 15


def test_market_service_records_beta_unavailability_without_fallback(tmp_path) -> None:
    service = WitnessMarketService(MarketServiceConfig(state_root=str(tmp_path)))
    service._beta_client = _BadClient()  # type: ignore[assignment]
    now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)

    service._record_beta(snapshot_id="snap-2", session="2026-08-27", as_of=now)

    rows = list(StageArtifactStore(tmp_path, "witnesses/beta").read("2026-08-27"))
    assert len(rows) == 1
    assert rows[0]["available"] is False
    assert rows[0]["witness"] is None
    assert "stale" in rows[0]["unavailable_reason"]
