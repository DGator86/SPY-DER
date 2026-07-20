"""Build AgentDecisionPacket from Phase 7-9 outputs."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from spy_der.agents.security import assert_no_secrets
from spy_der.contracts.agents import (
    AgentCandidateView,
    AgentDecisionPacket,
    DeploymentContext,
    ExitPolicySummary,
    ReadOnlyLegSummary,
    SnapshotSummary,
    make_packet_id,
    packet_hash,
)
from spy_der.contracts.candidates import Candidate, CandidateUniverse
from spy_der.contracts.economics import CandidateEconomics
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.policies import PolicyDecisionView, PolicyDisagreement
from spy_der.contracts.serialization import to_canonical_json
from spy_der.contracts.value import CandidateValueForecast, SnapshotRanking

__all__ = ["build_agent_candidate_view", "build_agent_decision_packet"]


def build_agent_candidate_view(
    candidate: Candidate,
    *,
    economics: CandidateEconomics | None = None,
    value: CandidateValueForecast | None = None,
    v3_rank: int | None = None,
    expected_regret: float | None = None,
    hard_vetoed: bool = False,
) -> AgentCandidateView:
    legs = tuple(
        ReadOnlyLegSummary(
            option_type=leg.option_type.value,
            strike=leg.strike,
            quantity=leg.quantity,
            expiration=leg.expiration,
        )
        for leg in candidate.legs
    )
    mid = economics.mid_price if economics else None
    natural = economics.natural_price if economics else None
    expected = economics.expected_fill_price if economics else None
    conservative = economics.conservative_fill_price if economics else None
    fill_p = economics.fill_probability if economics else 0.0
    fees = economics.fees if economics else Decimal("0")
    slip = (
        (economics.entry_slippage + economics.exit_slippage) if economics else Decimal("0")
    )
    util = value.utility if value else None
    p_util = value.p_positive_utility if value else None
    es = value.expected_shortfall if value else None
    ev = value.expected_net_pnl if value else (economics.expected_value if economics else None)
    unc = (
        max(
            value.model_uncertainty,
            value.forecast_uncertainty,
            value.execution_uncertainty,
            value.ood_score,
        )
        if value
        else 0.5
    )
    return AgentCandidateView(
        candidate_id=candidate.candidate_id,
        family=candidate.family,
        direction=candidate.direction,
        expiration=candidate.expiration,
        legs_summary=legs,
        maximum_loss=candidate.maximum_loss,
        capital_required=candidate.capital_required,
        geometry_hash=candidate.geometry_hash,
        maximum_profit=candidate.maximum_profit,
        breakevens=candidate.breakevens,
        mid_price=mid,
        natural_price=natural,
        expected_fill_price=expected,
        conservative_fill_price=conservative,
        fill_probability=fill_p,
        estimated_fees=fees,
        estimated_slippage=slip,
        executable_expected_pnl=ev,
        probability_positive_utility=p_util,
        expected_shortfall=es,
        candidate_utility=util,
        v3_rank=v3_rank,
        expected_regret=expected_regret,
        liquidity_status="ok" if candidate.quote_quality > 0 else "unknown",
        uncertainty=float(unc),
        evidence_ids=(f"geom:{candidate.geometry_hash}",),
        hard_vetoed=hard_vetoed,
    )


def build_agent_decision_packet(
    *,
    snapshot: CanonicalMarketSnapshot,
    universe: CandidateUniverse,
    created_at: datetime,
    ttl_seconds: int = 30,
    economics: dict[str, CandidateEconomics] | None = None,
    value_forecasts: dict[str, CandidateValueForecast] | None = None,
    ranking: SnapshotRanking | None = None,
    policy_views: tuple[PolicyDecisionView, ...] = (),
    policy_disagreement: PolicyDisagreement | None = None,
    hard_vetoes: tuple[str, ...] = (),
    risk_max_size_scalar: float = 1.0,
    approved_exit_policies: tuple[ExitPolicySummary, ...] = (),
    deployment: DeploymentContext | None = None,
    data_quality: float = 1.0,
    forecast_uncertainty: float = 0.0,
) -> AgentDecisionPacket:
    """Assemble a processed-output packet. Raises if secrets leak into payload."""
    eco = economics or {}
    vals = value_forecasts or {}
    rank_map: dict[str, int] = {}
    regret_map: dict[str, float] = {}
    if ranking is not None:
        for i, cid in enumerate(ranking.ordered_candidate_ids, start=1):
            rank_map[cid] = i
        regret_map = dict(ranking.expected_regret)

    views = tuple(
        build_agent_candidate_view(
            c,
            economics=eco.get(c.candidate_id),
            value=vals.get(c.candidate_id),
            v3_rank=rank_map.get(c.candidate_id),
            expected_regret=regret_map.get(c.candidate_id),
            hard_vetoed=bool(hard_vetoes),
        )
        for c in universe.candidates
    )
    summary = SnapshotSummary(
        snapshot_id=snapshot.snapshot_id,
        symbol=snapshot.underlying_symbol,
        session_date=snapshot.session_date,
        underlying_price=snapshot.underlying_price,
        minutes_to_close=snapshot.minutes_to_close,
    )
    evidence = tuple(sorted({eid for v in views for eid in v.evidence_ids}))
    body = {
        "snapshot_id": summary.snapshot_id,
        "symbol": summary.symbol,
        "session_date": summary.session_date.isoformat(),
        "candidate_ids": [v.candidate_id for v in views],
        "geometry_hashes": [v.geometry_hash for v in views],
        "hard_vetoes": list(hard_vetoes),
        "risk_max_size_scalar": risk_max_size_scalar,
        "policy_names": [p.policy_name for p in policy_views],
        "deployment_id": (deployment or DeploymentContext()).deployment_id,
    }
    # Security: packet body must not carry secrets.
    assert_no_secrets(body)
    # Touch canonical JSON to ensure serializable processed outputs only.
    _ = to_canonical_json(body)
    ph = packet_hash(body)
    pid = make_packet_id(summary.snapshot_id, ph)
    return AgentDecisionPacket(
        packet_id=pid,
        packet_hash=ph,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=int(ttl_seconds)),
        snapshot_summary=summary,
        candidates=views,
        risk_max_size_scalar=risk_max_size_scalar,
        hard_vetoes=hard_vetoes,
        policy_views=policy_views,
        policy_disagreement=policy_disagreement,
        approved_exit_policies=approved_exit_policies,
        deployment_context=deployment or DeploymentContext(),
        data_quality=data_quality,
        forecast_uncertainty=forecast_uncertainty,
        evidence_ids=evidence,
    )
