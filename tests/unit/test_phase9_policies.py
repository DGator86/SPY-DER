"""Phase 9 policy adapters, disagreement, and deterministic synthesis."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    CandidateRanking,
    CandidateUniverse,
    DebitCredit,
    HardVeto,
    LegacyDecisionView,
    MarketForecastBundle,
    OptionType,
    PolicyAction,
    PolicyMode,
    RiskEnvelope,
    StrategyPermissions,
    StructuralState,
    SystemAction,
    V3DecisionView,
)
from spy_der.policies import (
    EnsemblePolicy,
    EnsemblePolicyConfig,
    LegacyPolicy,
    V2Policy,
    V3Policy,
    compute_policy_disagreement,
)
from spy_der.synthesis.deterministic import DeterministicDecisionAgent, build_policy_packet
from spy_der.synthesis.engine import synthesize_decision


def _candidate(cid: str, max_loss: str = "10", direction: str = "bullish") -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-p9",
        family="long_call",
        direction=direction,
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("100"),
                quantity=1,
                expiration=exp,
                contract_id=cid,
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal(max_loss),
        breakevens=(),
        capital_required=Decimal(max_loss),
        terminal_payoff_hash="sha256:pay",
        geometry_hash=f"sha256:{cid}",
    )


def _forecast(**kwargs: object) -> MarketForecastBundle:
    base = dict(
        snapshot_id="snap-p9",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        symbol="SPY",
        model_version="v2",
        p_up_30m=0.65,
        uncertainty=0.2,
        data_quality=0.9,
    )
    base.update(kwargs)
    return MarketForecastBundle(**base)  # type: ignore[arg-type]


def _legacy(*, veto: bool = False) -> LegacyDecisionView:
    return LegacyDecisionView(
        structural_state=StructuralState(state_id="s1", regime="neutral"),
        permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
        hard_vetoes=(HardVeto(code="HALT", reason="halt"),) if veto else (),
    )


def _packet(**kwargs: object):
    universe = CandidateUniverse(
        universe_id="u1",
        candidates=(_candidate("c1"), _candidate("c2", direction="bearish")),
    )
    base = dict(
        snapshot_id="snap-p9",
        legacy=_legacy(),
        forecast=_forecast(),
        universe=universe,
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("25")),
        ranking=CandidateRanking(ordered_candidate_ids=("c2", "c1")),
        required_inputs_present=True,
    )
    base.update(kwargs)
    return build_policy_packet(
        snapshot_id=str(base["snapshot_id"]),
        legacy=base["legacy"],
        forecast=base["forecast"],
        universe=base["universe"],
        envelope=base["envelope"],
        ranking=base["ranking"],
        required_inputs_present=bool(base["required_inputs_present"]),
    )


def test_legacy_policy_respects_hard_veto() -> None:
    view = LegacyPolicy().evaluate(_packet(legacy=_legacy(veto=True)))
    assert view.action is PolicyAction.ABSTAIN
    assert "HALT" in view.hard_vetoes


def test_v2_policy_selects_directional_family() -> None:
    view = V2Policy().evaluate(_packet())
    assert view.action is PolicyAction.SELECT_CANDIDATE
    assert view.candidate_id == "c1"  # bullish with p_up=0.65


def test_v3_policy_uses_ranking() -> None:
    view = V3Policy().evaluate(_packet())
    assert view.action is PolicyAction.SELECT_CANDIDATE
    assert view.candidate_id == "c2"


def test_disagreement_detects_candidate_conflict() -> None:
    packet = _packet()
    views = (
        LegacyPolicy().evaluate(packet),
        V2Policy().evaluate(packet),
        V3Policy().evaluate(packet),
    )
    disagreement = compute_policy_disagreement(views)
    assert disagreement.disagree
    assert disagreement.candidate_conflict or disagreement.action_conflict


def test_ensemble_shadow_is_legacy_authoritative() -> None:
    ens = EnsemblePolicy(EnsemblePolicyConfig(mode=PolicyMode.SHADOW))
    view = ens.evaluate(_packet())
    assert view.policy_name == "ensemble"
    assert view.candidate_id == "c1"
    assert any(r.startswith("source:") for r in view.reason_codes)


def test_deterministic_agent_and_engine() -> None:
    packet = _packet()
    decision, views, disagreement = DeterministicDecisionAgent().decide(packet)
    assert len(views) == 3
    assert isinstance(disagreement.disagree, bool)
    assert decision.action in {SystemAction.SELECT_CANDIDATE, SystemAction.ABSTAIN}

    syn = synthesize_decision(
        legacy=_legacy(),
        forecast=_forecast(),
        universe=CandidateUniverse(universe_id="u1", candidates=(_candidate("c1"),)),
        v3_view=V3DecisionView(ranking=CandidateRanking(ordered_candidate_ids=("c1",))),
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("25")),
        required_inputs_present=True,
    )
    assert syn.action is SystemAction.SELECT_CANDIDATE
    assert syn.selected_candidate_id == "c1"
