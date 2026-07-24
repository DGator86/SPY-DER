"""Concrete CandidateEvaluator — scores decisions against OutcomePackets."""

from __future__ import annotations

from collections.abc import Sequence

from spy_der.contracts.integration import OutcomePacket
from spy_der.dojo.evaluation import RichEvaluationReport, evaluate_decisions_rich
from spy_der.dojo.protocols import DecisionRecord

__all__ = ["OutcomeMatchingEvaluator", "default_evaluator"]


class OutcomeMatchingEvaluator:
    """SPY-DER-owned evaluator: match decisions to outcome packets.

    This is the default CandidateEvaluator used by the Dojo when no external
    0DTE backtest adapter is injected. It never imports 0DTE internals.
    """

    def evaluate(
        self,
        decisions: Sequence[DecisionRecord],
        outcomes: Sequence[OutcomePacket],
    ) -> RichEvaluationReport:
        return evaluate_decisions_rich(decisions, outcomes)


def default_evaluator() -> OutcomeMatchingEvaluator:
    return OutcomeMatchingEvaluator()
