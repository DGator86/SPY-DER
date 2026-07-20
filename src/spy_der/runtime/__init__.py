"""Runtime orchestration helpers (dual-runtime parity, etc.)."""

from spy_der.runtime.parity import (
    DecisionDiff,
    DualRuntimeParityReport,
    ParityBucket,
    ParityInputs,
    PerformanceSample,
    assert_identical_inputs,
    compare_parity_buckets,
    measure_runtime,
    rehearse_rollback,
    run_dual_runtime_parity,
)

__all__ = [
    "DecisionDiff",
    "DualRuntimeParityReport",
    "ParityBucket",
    "ParityInputs",
    "PerformanceSample",
    "assert_identical_inputs",
    "compare_parity_buckets",
    "measure_runtime",
    "rehearse_rollback",
    "run_dual_runtime_parity",
]
