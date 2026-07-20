"""`spy-der ai-check` — end-to-end verification that the live AI answers.

Builds a small, realistic sample packet and runs it through the exact
production bridge (`decide_shadow_tick`) with a real Grok agent, so operators
can confirm ``XAI_API_KEY`` / ``XAI_MODEL`` are wired before relying on the
parallel track. Live trading stays disabled — this only exercises the decision
path. Use ``--offline`` to validate the plumbing with a mock agent (no key,
no network).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from spy_der.agents.grok import GrokConfig, GrokDecisionAgent
from spy_der.agents.mock import MockDecisionAgent
from spy_der.agents.protocols import DecisionAgent
from spy_der.contracts.agents import AgentEntryAction
from spy_der.integrations.zerodte.provider import (
    ShadowCandidateView,
    SpyDerShadowDecision,
    decide_shadow_tick,
)

__all__ = ["FAILURE_REASON_CODES", "AiCheckResult", "build_arg_parser", "main", "run_ai_check"]

# Reason codes the bridge / Grok adapter emit when the live path did not
# actually produce a model decision (transport missing, HTTP/parse failure,
# or an exception building the packet).
FAILURE_REASON_CODES = frozenset(
    {
        "grok_transport_missing",
        "grok_parse_or_transport_failure",
        "spy_der_bridge_error",
    }
)


@dataclass(frozen=True, slots=True)
class AiCheckResult:
    ok: bool
    provider: str
    model_id: str
    healthy: bool
    health_detail: str
    decision: SpyDerShadowDecision
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model_id": self.model_id,
            "healthy": self.healthy,
            "health_detail": self.health_detail,
            "detail": self.detail,
            "decision": self.decision.as_dict(),
        }


def _sample_candidates() -> tuple[ShadowCandidateView, ...]:
    today = datetime.now(tz=UTC).date()
    return (
        ShadowCandidateView(
            candidate_id="ai-check-put-credit",
            family="put_credit_spread",
            direction="bearish",
            maximum_loss=Decimal("120"),
            capital_required=Decimal("120"),
            geometry_hash="sha256:ai-check-put-credit",
            expiration=today,
            mid_price=Decimal("0.80"),
            fill_probability=0.9,
            utility=0.35,
            v3_rank=1,
        ),
        ShadowCandidateView(
            candidate_id="ai-check-call-credit",
            family="call_credit_spread",
            direction="bullish",
            maximum_loss=Decimal("140"),
            capital_required=Decimal("140"),
            geometry_hash="sha256:ai-check-call-credit",
            expiration=today,
            mid_price=Decimal("0.60"),
            fill_probability=0.8,
            utility=0.20,
            v3_rank=2,
        ),
    )


def _select_agent(*, offline: bool, model: str | None) -> DecisionAgent:
    if offline:
        # Deterministic round-trip proof; no key or network required.
        return MockDecisionAgent(
            action=AgentEntryAction.SELECT_CANDIDATE,
            candidate_id="ai-check-put-credit",
            size_scalar=0.25,
        )
    cfg = GrokConfig(model_id=model) if model else GrokConfig()
    return GrokDecisionAgent(cfg=cfg)


def run_ai_check(
    *,
    offline: bool = False,
    model: str | None = None,
    now: datetime | None = None,
) -> AiCheckResult:
    """Run one sample decision and classify whether the live AI answered."""
    now = now or datetime.now(tz=UTC)
    agent = _select_agent(offline=offline, model=model)
    health = agent.health()

    if not offline and not health.healthy:
        # No point calling: report the misconfiguration instead of a fallback.
        decision = SpyDerShadowDecision(
            action="ABSTAIN",
            candidate_id=None,
            size_scalar=0.0,
            structure=None,
            direction=None,
            confidence=0.0,
            uncertainty=1.0,
            rationale=f"agent_unhealthy:{health.detail}",
            reason_codes=("agent_unhealthy",),
            provider=agent.identity.provider,
            model_id=agent.identity.model_id,
        )
        return AiCheckResult(
            ok=False,
            provider=agent.identity.provider,
            model_id=agent.identity.model_id,
            healthy=False,
            health_detail=health.detail,
            decision=decision,
            detail=(
                "AI agent is not healthy. Set XAI_API_KEY (and optionally "
                "XAI_MODEL) or pass --offline to validate the plumbing."
            ),
        )

    decision = decide_shadow_tick(
        snapshot_id=f"ai-check-{now:%Y%m%dT%H%M%S}",
        symbol="SPY",
        session_date=now.date(),
        underlying_price=Decimal("600"),
        candidates=_sample_candidates(),
        now=now,
        agent=agent,
    )
    failed_codes = FAILURE_REASON_CODES.intersection(decision.reason_codes)
    ok = not failed_codes
    detail = (
        "live decision returned" if ok else f"decision path failed: {sorted(failed_codes)}"
    )
    return AiCheckResult(
        ok=ok,
        provider=decision.provider,
        model_id=decision.model_id,
        healthy=health.healthy,
        health_detail=health.detail,
        decision=decision,
        detail=detail,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="spy-der ai-check",
        description="Verify the live AI decision maker end-to-end (paper/shadow only).",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Use a mock agent (no XAI_API_KEY / network) to validate the plumbing.",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Override the model id for this check (else XAI_MODEL / config default).",
    )
    p.add_argument("--json", action="store_true", help="Emit the result as JSON only.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_ai_check(offline=args.offline, model=args.model)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] provider={result.provider} model={result.model_id}")
        print(f"  healthy={result.healthy} ({result.health_detail})")
        print(f"  action={result.decision.action} candidate={result.decision.candidate_id}")
        print(
            f"  confidence={result.decision.confidence} "
            f"uncertainty={result.decision.uncertainty}"
        )
        print(f"  reason_codes={list(result.decision.reason_codes)}")
        print(f"  rationale={result.decision.rationale}")
        print(f"  -> {result.detail}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
