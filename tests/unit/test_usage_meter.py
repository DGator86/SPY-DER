"""Grok token/cost usage meter tests."""

from __future__ import annotations

import json

import pytest

from spy_der.agents import usage


def _raw(*, pt: int, ct: int) -> str:
    return json.dumps(
        {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
        }
    )


def setup_function() -> None:
    usage.reset()


def test_record_from_raw_accumulates_tokens_and_cost() -> None:
    usage.record_from_raw("grok-4.5", _raw(pt=1_000_000, ct=1_000_000))
    snap = usage.snapshot()
    assert snap["calls"] == 1
    assert snap["prompt_tokens"] == 1_000_000
    assert snap["completion_tokens"] == 1_000_000
    assert snap["total_tokens"] == 2_000_000
    # grok-4.5 = $2/Mtok in + $6/Mtok out => $8 for 1M+1M.
    assert snap["est_cost_usd"] == pytest.approx(8.0)
    assert "grok-4.5" in snap["by_model"]
    assert snap["by_model"]["grok-4.5"]["calls"] == 1


def test_budget_fraction_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_DAILY_BUDGET", "16")
    usage.record_from_raw("grok-4.5", _raw(pt=1_000_000, ct=1_000_000))  # $8
    snap = usage.snapshot()
    assert snap["daily_budget_usd"] == 16.0
    assert snap["budget_used_frac"] == pytest.approx(0.5)


def test_no_budget_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_DAILY_BUDGET", raising=False)
    usage.record("grok-4.5", 100, 100)
    snap = usage.snapshot()
    assert snap["daily_budget_usd"] is None
    assert snap["budget_used_frac"] is None


def test_per_model_breakdown() -> None:
    usage.record_from_raw("grok-4.5", _raw(pt=1000, ct=1000))
    usage.record_from_raw("grok-4.20-0309-non-reasoning", _raw(pt=2000, ct=0))
    snap = usage.snapshot()
    assert snap["calls"] == 2
    assert set(snap["by_model"]) == {"grok-4.5", "grok-4.20-0309-non-reasoning"}


def test_ignores_missing_or_malformed_usage() -> None:
    usage.record_from_raw("grok-4.5", "not json at all")
    usage.record_from_raw("grok-4.5", json.dumps({"choices": []}))  # no usage block
    assert usage.snapshot()["calls"] == 0


def test_unknown_model_uses_conservative_default() -> None:
    usage.record_from_raw("some-mystery-model", _raw(pt=1_000_000, ct=0))
    # default input price is $2/Mtok.
    assert usage.snapshot()["est_cost_usd"] == pytest.approx(2.0)
