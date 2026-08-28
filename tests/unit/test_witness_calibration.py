from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from spy_der.contracts.common import ValidationError
from spy_der.forecasting.witnesses.calibration import (
    WitnessObservation,
    evaluate_witness_calibration,
    fit_witness_calibration,
)


def _inverted_rows(start: datetime, *, sessions: int, per_session: int) -> list[WitnessObservation]:
    rows: list[WitnessObservation] = []
    for session in range(sessions):
        day = start + timedelta(days=session)
        for index in range(per_session):
            fraction = (index + 1) / (per_session + 1)
            probability = 0.10 + 0.80 * fraction
            expected_return = (probability - 0.5) * 0.01
            forecast_at = day + timedelta(hours=14, minutes=index)
            rows.append(
                WitnessObservation(
                    session_date=day.date().isoformat(),
                    forecast_at=forecast_at,
                    realized_at=forecast_at + timedelta(minutes=30),
                    probability_up=probability,
                    expected_return=expected_return,
                    realized_up=probability < 0.5,
                    realized_return=-expected_return,
                )
            )
    return rows


def test_calibration_can_learn_inverted_orientation_without_hard_coded_flip() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    training = _inverted_rows(start, sessions=10, per_session=20)
    as_of = max(row.realized_at for row in training)

    calibration = fit_witness_calibration(
        witness_name="alpha_legacy",
        horizon_minutes=30,
        observations=training,
        as_of=as_of,
    )

    assert calibration.orientation == "inverted"
    assert calibration.probability_slope < 0
    assert calibration.return_slope < 0
    assert calibration.calibrate_probability(0.80) < 0.50
    assert calibration.calibrate_probability(0.20) > 0.50
    assert calibration.calibrate_expected_return(0.002) < 0


def test_training_fit_has_no_blend_authority_and_later_evidence_can_qualify() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    training = _inverted_rows(start, sessions=10, per_session=20)
    fitted_through = max(row.realized_at for row in training)
    calibration = fit_witness_calibration(
        witness_name="alpha_legacy",
        horizon_minutes=30,
        observations=training,
        as_of=fitted_through,
    )

    validation_start = fitted_through + timedelta(days=1)
    validation = _inverted_rows(validation_start, sessions=5, per_session=20)
    evidence = evaluate_witness_calibration(
        calibration,
        validation,
        as_of=max(row.realized_at for row in validation),
        minimum_samples=50,
        minimum_sessions=3,
        maximum_brier=0.20,
        minimum_accuracy=0.80,
        minimum_brier_improvement=0.01,
    )

    assert evidence.blend_eligible is True
    assert evidence.calibrated_brier < evidence.raw_brier
    assert evidence.calibrated_accuracy > 0.80


def test_validation_must_be_strictly_later_than_fit_window() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    rows = _inverted_rows(start, sessions=5, per_session=10)
    fitted_through = max(row.realized_at for row in rows)
    calibration = fit_witness_calibration(
        witness_name="beta",
        horizon_minutes=15,
        observations=rows,
        as_of=fitted_through,
    )

    with pytest.raises(ValidationError, match="later than"):
        evaluate_witness_calibration(
            calibration,
            rows,
            as_of=fitted_through,
            minimum_samples=1,
            minimum_sessions=1,
        )


def test_fit_rejects_outcomes_not_yet_available_at_as_of() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    rows = _inverted_rows(start, sessions=1, per_session=4)
    with pytest.raises(ValidationError, match="unavailable at as_of"):
        fit_witness_calibration(
            witness_name="beta",
            horizon_minutes=15,
            observations=rows,
            as_of=rows[0].forecast_at,
        )
