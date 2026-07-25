"""The paid model runs during market hours, or when a caller declares otherwise.

Outside the session there is no live tape to decide on, so a Grok call is spend
with nothing behind it. The gate downgrades to the deterministic agent instead —
it never blocks the decision, so callers of the HTTP boundary always get an
answer.

The Dojo is the exemption that makes this workable: its timers fire at 06:30 ET,
three hours before the open.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from spy_der.agents.deterministic import DeterministicDecisionAgent
from spy_der.agents.grok import GrokDecisionAgent
from spy_der.decisions import shadow
from spy_der.decisions.shadow import _ai_allowed_now, _default_trader_agent, ai_context

ET = ZoneInfo("America/New_York")

# A regular Wednesday session.
OPEN_MIDDAY = datetime(2026, 7, 22, 12, 0, tzinfo=ET)
BEFORE_OPEN = datetime(2026, 7, 22, 6, 30, tzinfo=ET)   # when the Dojo timer fires
AFTER_CLOSE = datetime(2026, 7, 22, 18, 0, tzinfo=ET)
WEEKEND = datetime(2026, 7, 25, 12, 0, tzinfo=ET)       # Saturday


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("SPY_DER_AI", "XAI_ENABLED", "SPY_DER_AI_MARKET_HOURS_ONLY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    # Guard against a leaked context from an earlier failure.
    assert shadow._AI_CONTEXT_DEPTH == 0
    yield


# --------------------------------------------------------------------------- #
# The gate itself                                                             #
# --------------------------------------------------------------------------- #
def test_open_market_allows_the_model() -> None:
    allowed, reason = _ai_allowed_now(OPEN_MIDDAY)
    assert allowed
    assert reason == "market_open"


def test_before_the_open_blocks_the_model() -> None:
    allowed, reason = _ai_allowed_now(BEFORE_OPEN)
    assert not allowed
    assert reason == "market_closed"


def test_after_the_close_blocks_the_model() -> None:
    assert _ai_allowed_now(AFTER_CLOSE) == (False, "market_closed")


def test_weekend_blocks_the_model() -> None:
    assert _ai_allowed_now(WEEKEND) == (False, "market_closed")


def test_killswitch_still_wins_during_market_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing SPY_DER_AI=0 killswitch must not be weakened by the gate."""
    monkeypatch.setenv("SPY_DER_AI", "0")
    assert _ai_allowed_now(OPEN_MIDDAY) == (False, "killswitch")


def test_killswitch_beats_an_explicit_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator killswitch outranks a caller declaring itself exempt."""
    monkeypatch.setenv("SPY_DER_AI", "0")
    with ai_context("dojo"):
        assert _ai_allowed_now(OPEN_MIDDAY) == (False, "killswitch")


def test_the_gate_can_be_lifted_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_MARKET_HOURS_ONLY", "0")
    allowed, reason = _ai_allowed_now(WEEKEND)
    assert allowed
    assert reason == "gate_disabled"


# --------------------------------------------------------------------------- #
# The Dojo exemption                                                          #
# --------------------------------------------------------------------------- #
def test_declared_context_runs_the_model_outside_hours() -> None:
    with ai_context("dojo"):
        allowed, reason = _ai_allowed_now(BEFORE_OPEN)
    assert allowed
    assert reason == "context:dojo"


def test_context_is_released_afterwards() -> None:
    with ai_context("dojo"):
        pass
    assert _ai_allowed_now(BEFORE_OPEN) == (False, "market_closed")


def test_context_is_released_even_when_the_body_raises() -> None:
    """A failed Dojo run must not leave the process permanently exempt."""
    with pytest.raises(RuntimeError), ai_context("dojo"):
        raise RuntimeError("phase blew up")
    assert shadow._AI_CONTEXT_DEPTH == 0
    assert _ai_allowed_now(BEFORE_OPEN) == (False, "market_closed")


def test_context_nests() -> None:
    with ai_context("dojo"):
        with ai_context("inner"):
            assert _ai_allowed_now(WEEKEND)[0]
        # Still exempt: the outer context has not exited.
        assert _ai_allowed_now(WEEKEND)[0]
    assert not _ai_allowed_now(WEEKEND)[0]


# --------------------------------------------------------------------------- #
# What the caller actually gets                                               #
# --------------------------------------------------------------------------- #
def test_agent_is_grok_during_market_hours() -> None:
    assert isinstance(_default_trader_agent(OPEN_MIDDAY), GrokDecisionAgent)


def test_agent_is_deterministic_out_of_hours() -> None:
    """Downgraded, not blocked — the HTTP boundary still answers."""
    assert isinstance(_default_trader_agent(AFTER_CLOSE), DeterministicDecisionAgent)


def test_agent_is_grok_out_of_hours_inside_a_dojo_run() -> None:
    with ai_context("dojo"):
        assert isinstance(_default_trader_agent(BEFORE_OPEN), GrokDecisionAgent)


def test_no_api_key_is_deterministic_regardless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with ai_context("dojo"):
        assert isinstance(_default_trader_agent(OPEN_MIDDAY), DeterministicDecisionAgent)


# --------------------------------------------------------------------------- #
# Failure mode of the calendar itself                                         #
# --------------------------------------------------------------------------- #
def test_calendar_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken calendar must not silently downgrade a live trading session.

    This gate is a spend control, not a safety control — every real limit lives
    in spy_der.execution.guard. Failing closed here would mean an
    exchange-calendar hiccup quietly turns off the AI mid-session.
    """
    def _boom(self, ts):  # type: ignore[no-untyped-def]
        raise RuntimeError("calendar data unavailable")

    from spy_der.market_data.calendar import MarketCalendar

    monkeypatch.setattr(MarketCalendar, "is_open", _boom)
    allowed, reason = _ai_allowed_now(WEEKEND)
    assert allowed
    assert reason == "market_open"


# --------------------------------------------------------------------------- #
# The Dojo wires the exemption for real                                       #
# --------------------------------------------------------------------------- #
def test_run_dojo_declares_the_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not just available — actually used by the Dojo entry point."""
    from spy_der.dojo import runner

    seen: list[bool] = []

    def _spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(_ai_allowed_now(BEFORE_OPEN)[0])
        return {"report_date": "2026-07-22", "summary": "", "flags": [], "metrics": {}}

    monkeypatch.setattr(runner, "_run_dojo_phases", _spy)
    runner.run_dojo()
    assert seen == [True], "run_dojo did not declare an AI context"
    # And it is released again afterwards.
    assert _ai_allowed_now(BEFORE_OPEN)[0] is False


def test_utc_timestamps_are_handled() -> None:
    """The decision path works in UTC; the calendar must convert, not assume ET."""
    utc_midday_et = OPEN_MIDDAY.astimezone(UTC)
    assert _ai_allowed_now(utc_midday_et) == (True, "market_open")


def test_env_key_absent_does_not_crash_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert _ai_allowed_now(OPEN_MIDDAY)[0] is True  # gate is orthogonal to the key
    assert os.environ.get("XAI_API_KEY") is None
