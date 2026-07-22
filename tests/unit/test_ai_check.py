"""Tests for the live-AI upgrade (grok-4.5 + XAI_MODEL override) and ai-check."""

from __future__ import annotations

from typing import Any

import pytest

from spy_der.agents.grok import GrokConfig, GrokDecisionAgent
from spy_der.cli import main as cli_main
from spy_der.runtime.ai_check import run_ai_check


def test_default_grok_model_is_trader_non_reasoning() -> None:
    agent = GrokDecisionAgent(cfg=GrokConfig(auto_http=False))
    assert agent.model_id == "grok-4.20-0309-non-reasoning"
    assert agent.identity.model_id == "grok-4.20-0309-non-reasoning"


def test_xai_model_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_MODEL", "grok-4-fast")
    agent = GrokDecisionAgent(cfg=GrokConfig(auto_http=False))
    assert agent.model_id == "grok-4-fast"
    assert agent.identity.model_id == "grok-4-fast"


def test_xai_api_base_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_BASE", "https://api.example.test/v1/chat/completions")
    captured: dict[str, str] = {}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        captured["url"] = url
        captured["model"] = str(body["model"])
        return '{"action":"NO_EDGE","reason_codes":["t"],"rationale":"t"}'

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    # Build a minimal packet via the public bridge path.
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from spy_der.integrations.zerodte import ShadowCandidateView, decide_shadow_tick

    decide_shadow_tick(
        snapshot_id="s",
        symbol="SPY",
        session_date=datetime.now(tz=UTC).date(),
        underlying_price=600,
        candidates=(
            ShadowCandidateView(
                candidate_id="c0",
                family="put_credit_spread",
                direction="bearish",
                maximum_loss=Decimal("120"),
                capital_required=Decimal("120"),
                geometry_hash="sha256:test",
                expiration=date(2026, 7, 22),
            ),
        ),
        now=datetime.now(tz=UTC),
        agent=agent,
    )
    assert captured["url"] == "https://api.example.test/v1/chat/completions"


def test_ai_check_offline_ok() -> None:
    result = run_ai_check(offline=True)
    assert result.ok is True
    assert result.decision.action == "TRADE"
    assert result.provider == "mock"


def test_ai_check_reports_unhealthy_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    result = run_ai_check(offline=False)
    assert result.ok is False
    assert result.healthy is False
    assert "XAI_API_KEY" in result.detail


def test_ai_check_cli_offline_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["ai-check", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
