"""Independent forecast witnesses for Alpha V2."""

from spy_der.forecasting.witnesses.beta import (
    BETA_WITNESS_VERSION,
    BetaHorizonWitness,
    BetaStateClient,
    BetaWitnessError,
    BetaWitnessSnapshot,
    parse_beta_state,
)

__all__ = [
    "BETA_WITNESS_VERSION",
    "BetaHorizonWitness",
    "BetaStateClient",
    "BetaWitnessError",
    "BetaWitnessSnapshot",
    "parse_beta_state",
]
