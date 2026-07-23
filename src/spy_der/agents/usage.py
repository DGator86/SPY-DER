"""In-process token/cost meter for Grok API usage.

Every Grok call (trader, reviewer, predictor) routes through
``GrokDecisionAgent._call``; that records the response's ``usage`` block here so
the 0DTE dashboard can show a live model-usage bar. Counters bucket by UTC day
and reset on rollover. Cost is an *estimate* from a per-model price table
(USD per million tokens) — override the daily budget with ``XAI_DAILY_BUDGET``.

Thread-safe and dependency-free; disabled paths simply never record.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "USAGE_SCHEMA",
    "record",
    "record_from_raw",
    "reset",
    "snapshot",
]

USAGE_SCHEMA = "spy_der.usage.v1"

# (model-id substring, input $/Mtok, output $/Mtok). First match wins; the
# default is deliberately conservative (flagship rates) so cost is never
# under-reported for an unrecognised model id.
_PRICING: tuple[tuple[str, float, float], ...] = (
    ("grok-4.5", 2.00, 6.00),
    ("grok-4.3", 1.25, 2.50),
    ("grok-4.1", 0.20, 0.50),
    ("fast", 0.20, 0.50),
    ("non-reasoning", 0.50, 1.50),
)
_DEFAULT_PRICE = (2.00, 6.00)


def _price_for(model_id: str) -> tuple[float, float]:
    mid = model_id.lower()
    for key, price_in, price_out in _PRICING:
        if key in mid:
            return price_in, price_out
    return _DEFAULT_PRICE


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _daily_budget() -> float | None:
    raw = os.environ.get("XAI_DAILY_BUDGET", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


@dataclass
class _ModelUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class _Meter:
    day: str = field(default_factory=_today)
    since: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    by_model: dict[str, _ModelUsage] = field(default_factory=dict)

    def roll(self, day: str) -> None:
        if day != self.day:
            self.day = day
            self.since = datetime.now(tz=UTC).isoformat()
            self.by_model = {}


_lock = threading.Lock()
_meter = _Meter()


def record(model_id: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Add one call's token usage (and its estimated cost) to today's meter."""
    if prompt_tokens < 0 or completion_tokens < 0:
        return
    with _lock:
        _meter.roll(_today())
        mu = _meter.by_model.setdefault(model_id or "unknown", _ModelUsage())
        price_in, price_out = _price_for(model_id)
        mu.calls += 1
        mu.prompt_tokens += prompt_tokens
        mu.completion_tokens += completion_tokens
        mu.cost_usd += prompt_tokens / 1e6 * price_in + completion_tokens / 1e6 * price_out


def record_from_raw(model_id: str, raw: str) -> None:
    """Parse an OpenAI-style ``usage`` block from a raw response and record it."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(data, dict):
        return
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return
    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return
    record(model_id, prompt_tokens, completion_tokens)


def reset() -> None:
    global _meter
    with _lock:
        _meter = _Meter()


def snapshot() -> dict[str, Any]:
    """Today's usage totals + per-model breakdown for the dashboard."""
    with _lock:
        _meter.roll(_today())
        by_model: dict[str, dict[str, Any]] = {}
        calls = prompt = completion = 0
        cost = 0.0
        for mid, mu in _meter.by_model.items():
            by_model[mid] = {
                "calls": mu.calls,
                "prompt_tokens": mu.prompt_tokens,
                "completion_tokens": mu.completion_tokens,
                "est_cost_usd": round(mu.cost_usd, 4),
            }
            calls += mu.calls
            prompt += mu.prompt_tokens
            completion += mu.completion_tokens
            cost += mu.cost_usd
        budget = _daily_budget()
        used_frac = round(cost / budget, 4) if budget else None
        return {
            "schema": USAGE_SCHEMA,
            "day": _meter.day,
            "since": _meter.since,
            "calls": calls,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "est_cost_usd": round(cost, 4),
            "daily_budget_usd": budget,
            "budget_used_frac": used_frac,
            "by_model": by_model,
        }
