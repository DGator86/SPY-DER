"""Phase 9 policy-synthesis parity (master spec §36, §65)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from spy_der.contracts import (
    Candidate,
    CandidateLeg,
    CandidateRanking,
    CandidateUniverse,
    DebitCredit,
    LegacyDecisionView,
    MarketForecastBundle,
    OptionType,
    PolicyMode,
    RiskEnvelope,
    StrategyPermissions,
    StructuralState,
    to_canonical_json,
)
from spy_der.policies import EnsemblePolicy, EnsemblePolicyConfig, compute_policy_disagreement
from spy_der.synthesis.deterministic import DeterministicDecisionAgent, build_policy_packet

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase9" / "policy_synthesis.json"


def _candidate(cid: str, direction: str = "bullish") -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-p9-parity",
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
        maximum_loss=Decimal("10"),
        breakevens=(),
        capital_required=Decimal("10"),
        terminal_payoff_hash="sha256:pay",
        geometry_hash=f"sha256:{cid}",
    )


def test_policy_synthesis_parity() -> None:
    universe = CandidateUniverse(
        universe_id="u-parity",
        candidates=(_candidate("c1"), _candidate("c2", "bearish")),
    )
    forecast = MarketForecastBundle(
        snapshot_id="snap-p9-parity",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        symbol="SPY",
        model_version="v2",
        p_up_30m=0.65,
        uncertainty=0.2,
        data_quality=0.9,
    )
    legacy = LegacyDecisionView(
        structural_state=StructuralState(state_id="s1", regime="neutral"),
        permissions=StrategyPermissions(options_allowed=True, new_positions_allowed=True),
    )
    packet = build_policy_packet(
        snapshot_id="snap-p9-parity",
        legacy=legacy,
        forecast=forecast,
        universe=universe,
        envelope=RiskEnvelope(max_defined_risk_per_trade=Decimal("25")),
        ranking=CandidateRanking(ordered_candidate_ids=("c2", "c1")),
        required_inputs_present=True,
    )
    ens = EnsemblePolicy(EnsemblePolicyConfig(mode=PolicyMode.SHADOW))
    views = ens.evaluate_all(packet)
    auth = ens.evaluate(packet)
    dis = compute_policy_disagreement(views)
    decision, _, _ = DeterministicDecisionAgent(
        EnsemblePolicyConfig(mode=PolicyMode.SHADOW)
    ).decide(packet)
    payload = {
        "authoritative": {
            "action": auth.action.value,
            "candidate_id": auth.candidate_id,
            "reason_codes": list(auth.reason_codes),
        },
        "views": [
            {
                "policy_name": v.policy_name,
                "action": v.action.value,
                "candidate_id": v.candidate_id,
            }
            for v in views
        ],
        "disagreement": {
            "disagree": dis.disagree,
            "action_conflict": dis.action_conflict,
            "candidate_conflict": dis.candidate_conflict,
            "reasons": list(dis.reasons),
        },
        "system_decision": {
            "action": decision.action.value,
            "selected_candidate_id": decision.selected_candidate_id,
        },
    }
    actual = json.loads(to_canonical_json(payload))
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert actual == expected
