"""Provider-neutral AI agent framework (master spec §37-§45)."""

from spy_der.agents.comparison import ShadowComparisonResult, compare_agents
from spy_der.agents.deterministic import DeterministicDecisionAgent
from spy_der.agents.grok import GrokDecisionAgent
from spy_der.agents.mock import MockDecisionAgent
from spy_der.agents.packet import build_agent_decision_packet
from spy_der.agents.parser import parse_agent_json
from spy_der.agents.prompts import build_entry_prompt
from spy_der.agents.recorded import RecordedDecisionAgent
from spy_der.agents.registry import AgentRegistry
from spy_der.agents.runtime import FailClosedAgentRuntime

__all__ = [
    "AgentRegistry",
    "DeterministicDecisionAgent",
    "FailClosedAgentRuntime",
    "GrokDecisionAgent",
    "MockDecisionAgent",
    "RecordedDecisionAgent",
    "ShadowComparisonResult",
    "build_agent_decision_packet",
    "build_entry_prompt",
    "compare_agents",
    "parse_agent_json",
]
