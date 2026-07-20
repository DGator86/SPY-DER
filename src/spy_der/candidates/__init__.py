"""Deterministic candidate factory (master spec §31, §32)."""

from spy_der.candidates.dominance import apply_deterministic_dominance
from spy_der.candidates.factory import CandidateFactoryService, generate_candidate_universe
from spy_der.candidates.geometry import FactoryConfig, GeometrySpec, enumerate_geometries
from spy_der.candidates.payoff import prove_bounded_loss, terminal_payoff
from spy_der.candidates.registry import APPROVED_FAMILIES, REJECTED_FAMILIES, is_approved_family

__all__ = [
    "APPROVED_FAMILIES",
    "REJECTED_FAMILIES",
    "CandidateFactoryService",
    "FactoryConfig",
    "GeometrySpec",
    "apply_deterministic_dominance",
    "enumerate_geometries",
    "generate_candidate_universe",
    "is_approved_family",
    "prove_bounded_loss",
    "terminal_payoff",
]
