"""Decision authority facades — entry / position / policies.

Implementation currently lives under ``spy_der.agents``; this package is the
stable ownership-boundary import path for decision intelligence.
"""

from spy_der.agents.authority import AiDecisionAuthority
from spy_der.agents.protocols import DecisionAgent

__all__ = ["AiDecisionAuthority", "DecisionAgent"]
