from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spy_der.contracts import JournalEvent
from spy_der.evaluation.metrics import EvaluationResult
from spy_der.replay.deterministic import (
    ReplayInputManifest,
    ensure_matching_manifests,
    journal_hash,
)


class SystemVariant(StrEnum):
    SYSTEM_A = "SYSTEM_A"
    SYSTEM_B = "SYSTEM_B"
    SYSTEM_B_NO_LEGACY = "SYSTEM_B_NO_LEGACY"
    SYSTEM_B_NO_V2 = "SYSTEM_B_NO_V2"
    SYSTEM_B_NO_V3 = "SYSTEM_B_NO_V3"


@dataclass(frozen=True, slots=True)
class ReplayRunResult:
    variant: SystemVariant
    events: tuple[JournalEvent, ...]
    journal_sha256: str


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    baseline: ReplayRunResult
    candidate: ReplayRunResult
    metrics: EvaluationResult


def compare_runs(
    baseline_manifest: ReplayInputManifest,
    candidate_manifest: ReplayInputManifest,
    baseline: ReplayRunResult,
    candidate: ReplayRunResult,
) -> ComparisonResult:
    ensure_matching_manifests(baseline_manifest, candidate_manifest)
    if baseline.journal_sha256 != journal_hash(baseline.events):
        msg = "baseline journal hash mismatch"
        raise ValueError(msg)
    if candidate.journal_sha256 != journal_hash(candidate.events):
        msg = "candidate journal hash mismatch"
        raise ValueError(msg)
    return ComparisonResult(baseline=baseline, candidate=candidate, metrics=EvaluationResult())
