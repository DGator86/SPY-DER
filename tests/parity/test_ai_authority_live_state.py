"""Parity freeze for AI authority live_state dashboard shape."""

from __future__ import annotations

import json
from pathlib import Path

from spy_der.agents import MockDecisionAgent
from spy_der.contracts import AgentEntryAction
from spy_der.runtime import ShadowAiLoop

BASELINE = Path("baseline/expected_outputs/ai_authority/live_state.json")


def test_live_state_shape_matches_baseline() -> None:
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    loop = ShadowAiLoop.with_agent(
        MockDecisionAgent(action=AgentEntryAction.NO_EDGE)
    )
    state = loop.live_state()
    assert state["track"] == expected["track"]
    assert state["role"] == expected["role"]
    assert state["account_id"] == expected["account_id"]
    assert state["mode"] == expected["mode"]
    assert sorted(state["agent"].keys()) == sorted(expected["agent_keys"])
    assert sorted(state["tracker"].keys()) == sorted(expected["tracker_keys"])
