"""Provider-neutral AI agent framework (master spec §37-§45).

The AI is the decision maker: entry, exit/manage, tracker, and analyzer.
"""

from spy_der.agents.authority import (
    AiAnalysisSnapshot,
    AiDecisionAuthority,
    AiTrackerState,
    EntryDecisionResult,
    PositionDecisionResult,
)
from spy_der.agents.comparison import ShadowComparisonResult, compare_agents
from spy_der.agents.deterministic import DeterministicDecisionAgent
from spy_der.agents.grok import GrokDecisionAgent
from spy_der.agents.mock import MockDecisionAgent
from spy_der.agents.packet import build_agent_decision_packet
from spy_der.agents.parser import parse_agent_json, parse_position_json
from spy_der.agents.position_packet import (
    build_open_position_view,
    build_position_decision_packet,
)
from spy_der.agents.prompts import build_entry_prompt, build_position_prompt
from spy_der.agents.recorded import RecordedDecisionAgent
from spy_der.agents.registry import AgentRegistry, default_agent_registry
from spy_der.agents.runtime import FailClosedAgentRuntime
from spy_der.agents.transport import HttpGrokTransport, make_http_grok_transport

__all__ = [
    "AgentRegistry",
    "AiAnalysisSnapshot",
    "AiDecisionAuthority",
    "AiTrackerState",
    "DeterministicDecisionAgent",
    "EntryDecisionResult",
    "FailClosedAgentRuntime",
    "GrokDecisionAgent",
    "HttpGrokTransport",
    "MockDecisionAgent",
    "PositionDecisionResult",
    "RecordedDecisionAgent",
    "ShadowComparisonResult",
    "build_agent_decision_packet",
    "build_entry_prompt",
    "build_open_position_view",
    "build_position_decision_packet",
    "build_position_prompt",
    "compare_agents",
    "default_agent_registry",
    "make_http_grok_transport",
    "parse_agent_json",
    "parse_position_json",
]
