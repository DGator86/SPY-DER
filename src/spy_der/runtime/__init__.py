"""Runtime orchestration helpers (AI loop, cutover primary, dual-runtime parity)."""

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
from spy_der.runtime.primary import PrimaryResearchRuntime, PrimaryTickResult

__all__ = [
    "AiLoopTickResult",
    "DecisionDiff",
    "DualRuntimeParityReport",
    "ParityBucket",
    "ParityInputs",
    "PerformanceSample",
    "PrimaryResearchRuntime",
    "PrimaryTickResult",
    "ShadowAiLoop",
    "assert_identical_inputs",
    "compare_parity_buckets",
    "default_approved_exits",
    "measure_runtime",
    "rehearse_rollback",
    "run_dual_runtime_parity",
]
