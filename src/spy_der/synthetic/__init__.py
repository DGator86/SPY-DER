"""SPY-DER-owned synthetic universe production.

Absorbed from 0DTE ``matrix_universe`` / ``synthetic_world`` /
``regime_calibration`` and 0DTE's ``integrations/spy_der/synthetic.py`` bridge.
The Dojo now calls :class:`SyntheticUniverseProvider` here, so synthetic-universe
production has no cross-repository runtime dependency:

    spy_der.synthetic
            |
    SyntheticUniverseProvider
            |
          Dojo

Layers, outermost first:

* :mod:`~spy_der.synthetic.archetypes` — every generative constant, plus the
  config hash that fingerprints them.
* :mod:`~spy_der.synthetic.chains` — 3-state Markov + OU driver variables.
* :mod:`~spy_der.synthetic.world` — the coupled world; a ``MarketDataProvider``.
* :mod:`~spy_der.synthetic.pricing` — forward-Black pricing and smile shape.
* :mod:`~spy_der.synthetic.universe` — specs, the combinatoric lattice, coverage.
* :mod:`~spy_der.synthetic.provider` — worlds to ``MarketPacket``s + outcomes.
* :mod:`~spy_der.synthetic.calibration` — fit the layers to real tape.
* :mod:`~spy_der.synthetic.evolution` — spend the next generation on weaknesses.
"""

from spy_der.synthetic.archetypes import (
    ARCH_TRANSITION,
    ARCHETYPES,
    REGIME_TRANSITION,
    REGIMES,
    SIMULATOR_VERSION,
    simulator_config,
    simulator_config_hash,
)
from spy_der.synthetic.calibration import (
    Calibration,
    LabelConfig,
    calibrate_from_observations,
    calibrate_from_world,
    labeler_accuracy,
)
from spy_der.synthetic.chains import VariableChain
from spy_der.synthetic.evolution import (
    ArchetypeScore,
    EvolutionPlan,
    evolve_catalog,
    next_generation_weights,
    scores_from_archetype_matrix,
)
from spy_der.synthetic.provider import (
    SyntheticUniverseProvider,
    SyntheticUniverseResult,
    coerce_universe_spec,
)
from spy_der.synthetic.universe import (
    CoverageMatrix,
    UniverseCatalog,
    UniverseSpec,
    merge_coverage,
)
from spy_der.synthetic.world import MarkovWorld, SituationLabel, WorldTick

__all__ = [
    "ARCHETYPES",
    "ARCH_TRANSITION",
    "REGIMES",
    "REGIME_TRANSITION",
    "SIMULATOR_VERSION",
    "ArchetypeScore",
    "Calibration",
    "CoverageMatrix",
    "EvolutionPlan",
    "LabelConfig",
    "MarkovWorld",
    "SituationLabel",
    "SyntheticUniverseProvider",
    "SyntheticUniverseResult",
    "UniverseCatalog",
    "UniverseSpec",
    "VariableChain",
    "WorldTick",
    "calibrate_from_observations",
    "calibrate_from_world",
    "coerce_universe_spec",
    "evolve_catalog",
    "labeler_accuracy",
    "merge_coverage",
    "next_generation_weights",
    "scores_from_archetype_matrix",
    "simulator_config",
    "simulator_config_hash",
]
