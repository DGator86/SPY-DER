"""SPY-DER decision authority — entry / position / policy selection.

This package is the SPY-DER-owned home of the decision path. The hierarchy
enforced across :mod:`spy_der.candidates`, :mod:`spy_der.risk`, this package and
:mod:`spy_der.execution` is:

    candidate engine
          |
    deterministic eligibility     (spy_der.risk.firewall)
          |
    SPY-DER decision authority    (this package)
          |
    deterministic execution guard (spy_der.execution.guard)
          |
    shadow or live executor       (spy_der.execution)

The AI may choose among *valid* candidates. It cannot widen the valid set, and
it is not the last word: the guard re-checks hard vetoes, maximum loss, position
limits, size caps, session restrictions, liquidity and live-trading permission
after the decision is made.
"""

from spy_der.agents.authority import AiDecisionAuthority
from spy_der.agents.protocols import DecisionAgent
from spy_der.decisions.shadow import (
    PARALLEL_TRACK_ID,
    PARALLEL_TRACK_LABEL,
    ShadowCandidateView,
    SpyDerShadowDecision,
    decide_shadow_tick,
    parallel_track_payload,
    reset_shadow_tick_cache,
)

__all__ = [
    "PARALLEL_TRACK_ID",
    "PARALLEL_TRACK_LABEL",
    "AiDecisionAuthority",
    "DecisionAgent",
    "ShadowCandidateView",
    "SpyDerShadowDecision",
    "decide_shadow_tick",
    "parallel_track_payload",
    "reset_shadow_tick_cache",
]
