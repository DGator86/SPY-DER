"""Runtime orchestration helpers (AI loop, dual-runtime parity, etc.)."""

from spy_der.runtime.ai_loop import AiLoopTickResult, ShadowAiLoop, default_approved_exits
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
    "AiLoopTickResult",
    "DecisionDiff",
    "DualRuntimeParityReport",
    "ParityBucket",
    "ParityInputs",
    "PerformanceSample",
    "ShadowAiLoop",
    "assert_identical_inputs",
    "compare_parity_buckets",
    "default_approved_exits",
    "measure_runtime",
    "rehearse_rollback",
    "run_dual_runtime_parity",
]
