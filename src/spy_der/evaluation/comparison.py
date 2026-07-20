"""Native/controlled/policy/agent comparison and ablations (spec §56)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from spy_der.contracts.common import content_hash
from spy_der.evaluation.metrics import EvaluationResult, TradeOutcome, evaluate_trades
from spy_der.replay.deterministic import ReplayInputManifest, ensure_matching_manifests

__all__ = [
    "AblationId",
    "ComparisonKind",
    "ComparisonManifest",
    "ComparisonReport",
    "VariantResult",
    "compare_agents",
    "compare_controlled",
    "compare_native",
    "compare_policies",
    "run_ablations",
]


class ComparisonKind(StrEnum):
    NATIVE = "native"
    CONTROLLED = "controlled"
    POLICY = "policy"
    AGENT = "agent"
    ABLATION = "ablation"


class AblationId(StrEnum):
    WITHOUT_V2 = "system_b_without_v2"
    WITHOUT_V3 = "system_b_without_v3"
    WITHOUT_EMPIRICAL_FILLS = "system_b_without_empirical_fills"
    WITHOUT_PATH_FORECASTING = "system_b_without_path_forecasting"
    WITHOUT_GEX_VARIANTS = "system_b_without_gex_variants"
    WITHOUT_OBSERVATION_ONLY = "system_b_without_observation_only_signals"
    LEGACY_ONLY = "system_b_legacy_only"
    V2_ONLY = "system_b_v2_only"
    V3_ONLY = "system_b_v3_only"
    ENSEMBLE = "system_b_deterministic_ensemble"
    GROK = "system_b_grok"


@dataclass(frozen=True, slots=True)
class ComparisonManifest:
    """Fail-closed comparison identity (spec §56)."""

    system_a_commit: str
    system_b_commit: str
    snapshot_ids: tuple[str, ...]
    feature_version: str
    candidate_version: str
    economics_version: str
    fee_version: str
    slippage_version: str
    fill_model_version: str
    risk_configuration: str
    exit_registry: str
    settlement_source: str
    account_size: str
    random_seed: str
    deployment_ids: tuple[str, ...] = ()

    @property
    def manifest_hash(self) -> str:
        return content_hash(
            {
                "a": self.system_a_commit,
                "b": self.system_b_commit,
                "snapshots": list(self.snapshot_ids),
                "feature": self.feature_version,
                "candidate": self.candidate_version,
                "economics": self.economics_version,
                "fee": self.fee_version,
                "slippage": self.slippage_version,
                "fill": self.fill_model_version,
                "risk": self.risk_configuration,
                "exit": self.exit_registry,
                "settlement": self.settlement_source,
                "account": self.account_size,
                "seed": self.random_seed,
                "deployments": list(self.deployment_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class VariantResult:
    variant_id: str
    metrics: EvaluationResult
    outcomes: tuple[TradeOutcome, ...] = ()


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    kind: ComparisonKind
    manifest: ComparisonManifest
    baseline: VariantResult
    candidates: tuple[VariantResult, ...]
    delta_net_pnl: Mapping[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "delta_net_pnl",
            {
                c.variant_id: str(c.metrics.net_pnl - self.baseline.metrics.net_pnl)
                for c in self.candidates
            },
        )


def _require_same_manifest(a: ComparisonManifest, b: ComparisonManifest) -> None:
    if a.manifest_hash != b.manifest_hash:
        raise ValueError("comparison manifest mismatch")


def compare_native(
    *,
    manifest: ComparisonManifest,
    system_a: Sequence[TradeOutcome],
    system_b: Sequence[TradeOutcome],
) -> ComparisonReport:
    """Native comparison: identical raw inputs, native pipelines."""
    base = VariantResult("system_a_native", evaluate_trades(system_a), tuple(system_a))
    cand = VariantResult("system_b_native", evaluate_trades(system_b), tuple(system_b))
    return ComparisonReport(
        kind=ComparisonKind.NATIVE,
        manifest=manifest,
        baseline=base,
        candidates=(cand,),
        notes=("native_pipeline_difference",),
    )


def compare_controlled(
    *,
    manifest: ComparisonManifest,
    replay_manifest_a: ReplayInputManifest,
    replay_manifest_b: ReplayInputManifest,
    baseline_outcomes: Sequence[TradeOutcome],
    candidate_outcomes: Sequence[TradeOutcome],
    candidate_id: str = "system_b_controlled",
) -> ComparisonReport:
    """Controlled comparison: shared snapshot/candidates/economics/risk/fills."""
    ensure_matching_manifests(replay_manifest_a, replay_manifest_b)
    base = VariantResult(
        "controlled_baseline",
        evaluate_trades(baseline_outcomes),
        tuple(baseline_outcomes),
    )
    cand = VariantResult(
        candidate_id,
        evaluate_trades(candidate_outcomes),
        tuple(candidate_outcomes),
    )
    return ComparisonReport(
        kind=ComparisonKind.CONTROLLED,
        manifest=manifest,
        baseline=base,
        candidates=(cand,),
        notes=("decision_quality_isolated",),
    )


def compare_policies(
    *,
    manifest: ComparisonManifest,
    policy_outcomes: Mapping[str, Sequence[TradeOutcome]],
    baseline_policy: str,
) -> ComparisonReport:
    if baseline_policy not in policy_outcomes:
        raise ValueError(f"baseline policy missing: {baseline_policy}")
    base_out = policy_outcomes[baseline_policy]
    base = VariantResult(baseline_policy, evaluate_trades(base_out), tuple(base_out))
    cands = tuple(
        VariantResult(pid, evaluate_trades(outs), tuple(outs))
        for pid, outs in sorted(policy_outcomes.items())
        if pid != baseline_policy
    )
    return ComparisonReport(
        kind=ComparisonKind.POLICY,
        manifest=manifest,
        baseline=base,
        candidates=cands,
        notes=("policy_comparison",),
    )


def compare_agents(
    *,
    manifest: ComparisonManifest,
    agent_outcomes: Mapping[str, Sequence[TradeOutcome]],
    baseline_agent: str,
) -> ComparisonReport:
    if baseline_agent not in agent_outcomes:
        raise ValueError(f"baseline agent missing: {baseline_agent}")
    base_out = agent_outcomes[baseline_agent]
    base = VariantResult(baseline_agent, evaluate_trades(base_out), tuple(base_out))
    cands = tuple(
        VariantResult(aid, evaluate_trades(outs), tuple(outs))
        for aid, outs in sorted(agent_outcomes.items())
        if aid != baseline_agent
    )
    return ComparisonReport(
        kind=ComparisonKind.AGENT,
        manifest=manifest,
        baseline=base,
        candidates=cands,
        notes=("agent_comparison",),
    )


def run_ablations(
    *,
    manifest: ComparisonManifest,
    full_system: Sequence[TradeOutcome],
    ablations: Mapping[AblationId | str, Sequence[TradeOutcome]],
) -> ComparisonReport:
    base = VariantResult(
        "system_b_full",
        evaluate_trades(full_system),
        tuple(full_system),
    )
    cands = tuple(
        VariantResult(
            aid.value if isinstance(aid, AblationId) else str(aid),
            evaluate_trades(outs),
            tuple(outs),
        )
        for aid, outs in sorted(
            ablations.items(),
            key=lambda kv: kv[0].value if isinstance(kv[0], AblationId) else str(kv[0]),
        )
    )
    return ComparisonReport(
        kind=ComparisonKind.ABLATION,
        manifest=manifest,
        baseline=base,
        candidates=cands,
        notes=("ablation_suite",),
    )
