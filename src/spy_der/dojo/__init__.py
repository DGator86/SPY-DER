"""SPY-DER Dojo — AI training, evaluation, and curriculum.

The Dojo consumes 0DTE market experience only through
``spy_der.dojo.protocols`` providers and
``spy_der.contracts.integration`` packets. It must not import 0DTE
``backtest``, ``journal``, ``matrix_universe``, or ``walk_forward``.
"""

from spy_der.dojo.config import DojoConfig
from spy_der.dojo.evaluator import OutcomeMatchingEvaluator, default_evaluator
from spy_der.dojo.runner import run_dojo
from spy_der.dojo.sequential import SequentialDojoConfig, run_sequential_dojo

__all__ = [
    "DojoConfig",
    "OutcomeMatchingEvaluator",
    "SequentialDojoConfig",
    "default_evaluator",
    "run_dojo",
    "run_sequential_dojo",
]
