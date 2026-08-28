"""Independent forecast witnesses for Alpha V2."""

from spy_der.forecasting.witnesses.beta import (
    BETA_WITNESS_VERSION,
    BetaHorizonWitness,
    BetaStateClient,
    BetaWitnessError,
    BetaWitnessSnapshot,
    parse_beta_state,
)
from spy_der.forecasting.witnesses.calibration import (
    ORIENTATION_CALIBRATION_VERSION,
    WitnessCalibration,
    WitnessCalibrationEvidence,
    WitnessObservation,
    evaluate_witness_calibration,
    fit_witness_calibration,
)

__all__ = [
    "BETA_WITNESS_VERSION",
    "ORIENTATION_CALIBRATION_VERSION",
    "BetaHorizonWitness",
    "BetaStateClient",
    "BetaWitnessError",
    "BetaWitnessSnapshot",
    "WitnessCalibration",
    "WitnessCalibrationEvidence",
    "WitnessObservation",
    "evaluate_witness_calibration",
    "fit_witness_calibration",
    "parse_beta_state",
]
