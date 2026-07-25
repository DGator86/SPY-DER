"""TEMPORARY 0DTE compatibility surface — deleted at cutover.

0DTE is a legacy implementation being absorbed into SPY-DER and retired; it is
**not** a permanent upstream. This package exists only so the interim bridge
(0DTE PR #150) keeps working while the full-stack migration completes. It holds
no SPY-DER-owned logic:

* ``decide_shadow_tick`` / ``ShadowCandidateView`` / ``SpyDerShadowDecision``
  are re-exports of :mod:`spy_der.decisions.shadow`, which owns them.
* ``MarketPacket`` / ``DashboardPacket`` are re-exports of
  :mod:`spy_der.contracts.integration`. After cutover the market packet is an
  internal boundary or external API schema, not a cross-repository dependency.
* ``FileMarketExperienceProvider`` reads recorded 0DTE files for replay parity.
  SPY-DER's own journal supersedes it — see :mod:`spy_der.journal`.

Synthetic universes are **no longer** sourced here: the Dojo calls
:class:`spy_der.synthetic.SyntheticUniverseProvider` natively.

Removal is step 10 of ``docs/CUTOVER_PLAN.md``. Import from the owning module,
not from this package, in all new code.
"""

from spy_der.agents.usage import snapshot as usage_snapshot
from spy_der.decisions.shadow import (
    PARALLEL_TRACK_ID,
    PARALLEL_TRACK_LABEL,
    ShadowCandidateView,
    SpyDerShadowDecision,
    decide_shadow_tick,
    parallel_track_payload,
)
from spy_der.integrations.zerodte.client import (
    DEFAULT_DECISION_URL,
    HttpDecisionClient,
)
from spy_der.integrations.zerodte.contracts import (
    DASHBOARD_SCHEMA,
    MARKET_PACKET_SCHEMA,
    DashboardPacket,
    MarketPacket,
    dashboard_packet_from_dict,
    market_packet_from_dict,
)
from spy_der.integrations.zerodte.prediction import (
    PREDICTION_PROMPT_VERSION,
    PREDICTION_SCHEMA,
    KeyLevel,
    ShadowMarketView,
    SpyDerPrediction,
    predict_shadow_tick,
)
from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider
from spy_der.integrations.zerodte.result_publisher import (
    publish_dashboard_packet,
    read_dashboard_packet,
)

__all__ = [
    "DASHBOARD_SCHEMA",
    "DEFAULT_DECISION_URL",
    "MARKET_PACKET_SCHEMA",
    "PARALLEL_TRACK_ID",
    "PARALLEL_TRACK_LABEL",
    "PREDICTION_PROMPT_VERSION",
    "PREDICTION_SCHEMA",
    "DashboardPacket",
    "FileMarketExperienceProvider",
    "HttpDecisionClient",
    "KeyLevel",
    "MarketPacket",
    "ShadowCandidateView",
    "ShadowMarketView",
    "SpyDerPrediction",
    "SpyDerShadowDecision",
    "dashboard_packet_from_dict",
    "decide_shadow_tick",
    "market_packet_from_dict",
    "parallel_track_payload",
    "predict_shadow_tick",
    "publish_dashboard_packet",
    "read_dashboard_packet",
    "usage_snapshot",
]
