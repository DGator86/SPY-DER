"""Deterministic ensemble policy (spec §36)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from spy_der.contracts.policies import (
    PolicyAction,
    PolicyDecisionView,
    PolicyIdentity,
    PolicyInputPacket,
    PolicyMode,
)
from spy_der.policies.disagreement import compute_policy_disagreement
from spy_der.policies.legacy import LegacyPolicy
from spy_der.policies.v2 import V2Policy
from spy_der.policies.v3 import V3Policy

__all__ = ["ENSEMBLE_POLICY_VERSION", "EnsemblePolicy", "EnsemblePolicyConfig"]

ENSEMBLE_POLICY_VERSION = "ensemble-policy.v1"


@dataclass(frozen=True, slots=True)
class EnsemblePolicyConfig:
    mode: PolicyMode = PolicyMode.SHADOW
    # When disagreement on SELECT, abstain unless champion mode picks V3.
    abstain_on_disagreement: bool = True


class EnsemblePolicy:
    """Run Legacy/V2/V3 and synthesize an authoritative view."""

    def __init__(
        self,
        cfg: EnsemblePolicyConfig | None = None,
        *,
        legacy: LegacyPolicy | None = None,
        v2: V2Policy | None = None,
        v3: V3Policy | None = None,
    ) -> None:
        self.cfg = cfg or EnsemblePolicyConfig()
        self.legacy = legacy or LegacyPolicy()
        self.v2 = v2 or V2Policy()
        self.v3 = v3 or V3Policy()

    @property
    def identity(self) -> PolicyIdentity:
        return PolicyIdentity(name="ensemble", version=ENSEMBLE_POLICY_VERSION)

    def evaluate_all(
        self,
        packet: PolicyInputPacket,
    ) -> tuple[PolicyDecisionView, ...]:
        return (
            self.legacy.evaluate(packet),
            self.v2.evaluate(packet),
            self.v3.evaluate(packet),
        )

    def evaluate(self, packet: PolicyInputPacket) -> PolicyDecisionView:
        views = self.evaluate_all(packet)
        disagreement = compute_policy_disagreement(views)
        legacy_view, _v2_view, v3_view = views

        if self.cfg.mode is PolicyMode.LEGACY:
            auth = legacy_view
            source = "legacy"
        elif self.cfg.mode is PolicyMode.CHAMPION:
            auth = v3_view
            source = "v3"
            joined = " ".join(auth.reason_codes)
            if auth.action is PolicyAction.ABSTAIN and "missing" in joined:
                auth = legacy_view
                source = "fallback_legacy"
        else:
            # Shadow: Legacy authoritative; disagreement recorded in reasons.
            auth = legacy_view
            source = "legacy_shadow"

        reason: tuple[str, ...]
        if (
            disagreement.disagree
            and self.cfg.mode is PolicyMode.SHADOW
            and auth.action is PolicyAction.SELECT_CANDIDATE
        ):
            reason = (*auth.reason_codes, "ensemble_disagreement")
        else:
            reason = auth.reason_codes

        if (
            self.cfg.mode is PolicyMode.CHAMPION
            and disagreement.candidate_conflict
            and auth.action is PolicyAction.SELECT_CANDIDATE
        ):
            selects = [v for v in views if v.action is PolicyAction.SELECT_CANDIDATE]
            if selects:
                counts = Counter(v.candidate_id for v in selects if v.candidate_id)
                winner, votes = counts.most_common(1)[0]
                if votes >= 2:
                    return PolicyDecisionView(
                        policy_name="ensemble",
                        policy_version=ENSEMBLE_POLICY_VERSION,
                        action=PolicyAction.SELECT_CANDIDATE,
                        candidate_id=winner,
                        size_cap=auth.size_cap,
                        confidence=min(auth.confidence + 0.1, 1.0),
                        uncertainty=max(auth.uncertainty - 0.1, 0.0),
                        supporting_evidence=("majority_vote",),
                        hard_vetoes=auth.hard_vetoes,
                        reason_codes=("ensemble_majority", source),
                    )

        return PolicyDecisionView(
            policy_name="ensemble",
            policy_version=ENSEMBLE_POLICY_VERSION,
            action=auth.action,
            candidate_id=auth.candidate_id,
            size_cap=auth.size_cap,
            confidence=auth.confidence,
            uncertainty=auth.uncertainty,
            supporting_evidence=auth.supporting_evidence,
            contradictory_evidence=tuple(disagreement.disagreeing_policies),
            hard_vetoes=auth.hard_vetoes,
            reason_codes=(*reason, f"source:{source}"),
        )
