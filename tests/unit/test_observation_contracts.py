from __future__ import annotations

import pytest

from spy_der.contracts.common import ValidationError
from spy_der.contracts.observations import MeasurementBundle, MeasurementValue


def test_missing_measurement_requires_reason() -> None:
    with pytest.raises(ValidationError, match="missing value requires missing_reason"):
        MeasurementValue(variable_id="V1001", name="breadth", value=None)


def test_present_measurement_cannot_claim_missing_reason() -> None:
    with pytest.raises(ValidationError, match="cannot have value and missing_reason"):
        MeasurementValue(
            variable_id="V1001",
            name="breadth",
            value=0.63,
            missing_reason="stale",
        )


def test_measurement_bundle_identity_is_deterministic() -> None:
    values = (
        MeasurementValue(
            variable_id="V1001",
            name="breadth",
            value=0.63,
            unit="fraction",
            normalization="raw_fraction",
            quality=0.99,
            source_ids=("constituent-tape",),
        ),
        MeasurementValue(
            variable_id="V1002",
            name="breadth_acceleration",
            value=None,
            unit="fraction",
            horizon="5m",
            normalization="delta",
            quality=0.50,
            source_ids=("constituent-tape",),
            missing_reason="insufficient_history",
        ),
    )
    kwargs = {
        "snapshot_id": "snap-1",
        "ts": "2026-08-27T14:30:00-04:00",
        "values": values,
        "dictionary_version": "utpm-2026-08-27",
        "normalization_state_version": "walkforward-fold-17",
        "data_quality": 0.95,
        "feature_coverage": 0.50,
    }
    first = MeasurementBundle(**kwargs)
    second = MeasurementBundle(**kwargs)
    assert first.bundle_id == second.bundle_id
    assert first.get("V1001") == 0.63
    assert first.get("V1002") is None
    assert first.get("not-present") is None
    assert first.missing_variable_ids == ("V1002",)


def test_measurement_bundle_rejects_duplicate_variable_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate variable_ids"):
        MeasurementBundle(
            snapshot_id="snap-1",
            ts="2026-08-27T14:30:00-04:00",
            values=(
                MeasurementValue(variable_id="V1", name="a", value=1.0),
                MeasurementValue(variable_id="V1", name="b", value=2.0),
            ),
            dictionary_version="dict-v1",
            normalization_state_version="norm-v1",
        )
