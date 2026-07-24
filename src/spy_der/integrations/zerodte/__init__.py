"""0DTE integration surface — contracts, HTTP client, decision provider.

Production boundary: 0DTE should call the local HTTP decision service
(``HttpDecisionClient``) or read ``DashboardPacket`` files. The in-process
``decide_shadow_tick`` helper remains for SPY-DER-internal / migration use.
"""

from spy_der.agents.usage import snapshot as usage_snapshot
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
from spy_der.integrations.zerodte.provider import (
    PARALLEL_TRACK_ID,
    PARALLEL_TRACK_LABEL,
    ShadowCandidateView,
    SpyDerShadowDecision,
    decide_shadow_tick,
    parallel_track_payload,
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
