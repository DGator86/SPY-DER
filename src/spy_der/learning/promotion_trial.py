"""Automatic promotion trial — re-run the system under the recommended change.

The learner ends a cycle by recommending a config delta. Parking that in
``pending_review/`` and waiting for a person is a decision the Dojo can make for
itself, provided the recommendation is *earned* rather than asserted: the delta
is installed as the candidate champion, the recorded tape and the blind-day
sequential walk are scored again with it, and it is promoted only if every gate
below passes against the incumbent it wants to replace.

The gates are deliberately boring, and every one of them is a reason to *not*
promote:

``actionable``         the change touches at least one live decision knob
``evidence``           enough scored sessions and trades to mean anything
``pnl_edge``           candidate beats the incumbent on the same tape
``win_rate``           and does not buy that P&L with a worse hit rate
``forward_transfer``   positive mean transfer on leak-free blind days
``retention``          no forgetting regression on the retention panel
``universe``           no robustness collapse across synthetic archetypes
``archetype_repair``   a change staged to fix crash has to fix *crash*
``cooldown``           not promoting on top of a promotion that just landed

A trial that fails any gate leaves ``champion.json`` exactly where it was and
says which gate stopped it. Nothing here writes the champion — that is
:func:`spy_der.learning.promotion.auto_promote_pending`, which requires this
report to say ``validated``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from spy_der.decisions.knobs import DecisionKnobs, actionable_knobs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spy_der.dojo.config import DojoConfig
    from spy_der.dojo.protocols import (
        CandidateEvaluator,
        DecisionAuthority,
        MarketExperienceProvider,
    )

# Dojo phases are imported inside run_promotion_trial: spy_der.dojo's package
# __init__ imports the runner, which imports this module, so a module-level
# dojo import here breaks `import spy_der.learning` on its own.

__all__ = [
    "PromotionThresholds",
    "PromotionTrial",
    "TrialGate",
    "run_promotion_trial",
]


@dataclass(frozen=True, slots=True)
class PromotionThresholds:
    """Bars a challenger clears before it may replace the champion."""

    #: Minimum matched trades the candidate must have on the recorded tape.
    min_trades: int = 20
    #: Minimum scored sessions behind those trades.
    min_sessions: int = 3
    #: Candidate total P&L must exceed the incumbent's by more than this.
    min_pnl_edge: float = 0.0
    #: Allowed win-rate give-back versus the incumbent.
    max_win_rate_drop: float = 0.05
    #: Block promotion when the blind-day walk produced no evidence. With this
    #: off, a candidate may promote on recorded tape alone.
    require_sequential: bool = True
    #: Block promotion when the synthetic panel scored a challenger but the
    #: totals cannot be compared. A panel that did not run at all (daily timers
    #: skip the lattice) never blocks — recorded tape and blind days still gate.
    require_universe: bool = True
    #: Hours that must pass between two automatic promotions.
    cooldown_hours: float = 6.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_trades": self.min_trades,
            "min_sessions": self.min_sessions,
            "min_pnl_edge": self.min_pnl_edge,
            "max_win_rate_drop": self.max_win_rate_drop,
            "require_sequential": self.require_sequential,
            "require_universe": self.require_universe,
            "cooldown_hours": self.cooldown_hours,
        }


@dataclass(frozen=True, slots=True)
class TrialGate:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PromotionTrial:
    """Outcome of re-running the system under a recommended change."""

    status: str
    candidate_id: str | None
    knobs: dict[str, Any]
    #: Archetype this change was staged to repair, if any.
    target_archetype: str | None = None
    gates: tuple[TrialGate, ...] = ()
    incumbent: dict[str, Any] = field(default_factory=dict)
    candidate: dict[str, Any] = field(default_factory=dict)
    sequential: dict[str, Any] = field(default_factory=dict)
    universe: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def validated(self) -> bool:
        return self.status == "validated"

    @property
    def blocking_gate(self) -> str | None:
        for gate in self.gates:
            if not gate.passed:
                return gate.name
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate_id": self.candidate_id,
            "knobs": dict(self.knobs),
            "target_archetype": self.target_archetype,
            "gates": [g.to_dict() for g in self.gates],
            "incumbent": dict(self.incumbent),
            "candidate": dict(self.candidate),
            "sequential": dict(self.sequential),
            "universe": dict(self.universe),
            "thresholds": dict(self.thresholds),
            "blocking_gate": self.blocking_gate,
            "note": self.note,
            "evaluated_at": datetime.now(UTC).isoformat(),
        }


def _headline(evaluation: dict[str, Any] | None) -> dict[str, Any]:
    """The few numbers a promotion decision actually turns on."""
    ev = evaluation or {}
    return {
        "status": ev.get("status"),
        "trades": ev.get("trades") or ev.get("n_matched") or 0,
        "total_pnl": ev.get("total_pnl"),
        "win_rate": ev.get("win_rate"),
        "mean_session_pnl": ev.get("mean_session_pnl"),
        "session_win_rate": ev.get("session_win_rate"),
        "dir_hit": ev.get("dir_hit"),
        "n_sessions": ev.get("n_sessions"),
    }


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _hours_since(timestamp: Any) -> float | None:
    if not timestamp:
        return None
    try:
        when = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds() / 3600.0


def _cooldown_gate(
    current_champion: dict[str, Any] | None,
    thresholds: PromotionThresholds,
) -> TrialGate:
    elapsed = _hours_since((current_champion or {}).get("promoted_at"))
    if elapsed is None:
        return TrialGate("cooldown", True, "no prior automatic promotion")
    if elapsed >= thresholds.cooldown_hours:
        return TrialGate(
            "cooldown", True, f"{elapsed:.1f}h since last promotion"
        )
    return TrialGate(
        "cooldown",
        False,
        f"last promotion {elapsed:.1f}h ago (< {thresholds.cooldown_hours:.1f}h) — "
        "three Dojo timers fire per day; one promotion at a time",
    )


def _evidence_gate(
    candidate: dict[str, Any],
    thresholds: PromotionThresholds,
) -> TrialGate:
    trades = int(candidate.get("trades") or 0)
    sessions = int(candidate.get("n_sessions") or 0)
    if candidate.get("status") != "ok":
        return TrialGate(
            "evidence",
            False,
            f"candidate re-run status {candidate.get('status')!r} — nothing scored",
        )
    if trades < thresholds.min_trades or sessions < thresholds.min_sessions:
        return TrialGate(
            "evidence",
            False,
            f"{trades} trades over {sessions} sessions "
            f"(need {thresholds.min_trades}/{thresholds.min_sessions})",
        )
    return TrialGate(
        "evidence", True, f"{trades} trades over {sessions} sessions"
    )


def _pnl_gate(
    candidate: dict[str, Any],
    incumbent: dict[str, Any],
    thresholds: PromotionThresholds,
) -> TrialGate:
    cand = _as_float(candidate.get("total_pnl"))
    inc = _as_float(incumbent.get("total_pnl"))
    if cand is None or inc is None:
        return TrialGate("pnl_edge", False, "P&L unmeasured on one side")
    edge = cand - inc
    if edge <= thresholds.min_pnl_edge:
        return TrialGate(
            "pnl_edge",
            False,
            f"candidate {cand:+.4f} vs champion {inc:+.4f} (edge {edge:+.4f})",
        )
    return TrialGate(
        "pnl_edge",
        True,
        f"candidate {cand:+.4f} vs champion {inc:+.4f} (edge {edge:+.4f})",
    )


def _win_rate_gate(
    candidate: dict[str, Any],
    incumbent: dict[str, Any],
    thresholds: PromotionThresholds,
) -> TrialGate:
    cand = _as_float(candidate.get("win_rate"))
    inc = _as_float(incumbent.get("win_rate"))
    if inc is None:
        # Incumbent took no trades — P&L edge already carried the comparison.
        return TrialGate("win_rate", True, "champion took no trades to compare")
    if cand is None:
        return TrialGate("win_rate", False, "candidate win rate unmeasured")
    drop = inc - cand
    if drop > thresholds.max_win_rate_drop:
        return TrialGate(
            "win_rate",
            False,
            f"{cand:.2f} vs {inc:.2f} — gives back {drop:.2f} "
            f"(max {thresholds.max_win_rate_drop:.2f})",
        )
    return TrialGate("win_rate", True, f"{cand:.2f} vs champion {inc:.2f}")


def _sequential_gate(
    sequential: dict[str, Any],
    thresholds: PromotionThresholds,
) -> TrialGate:
    status = sequential.get("status")
    if status != "ok":
        detail = f"blind-day walk {status!r}: {sequential.get('note', '')}".strip()
        return TrialGate("forward_transfer", not thresholds.require_sequential, detail)
    mean_ft = _as_float(sequential.get("mean_forward_transfer"))
    if mean_ft is None:
        return TrialGate(
            "forward_transfer",
            not thresholds.require_sequential,
            "no scored blind days",
        )
    if mean_ft < 0:
        return TrialGate(
            "forward_transfer",
            False,
            f"mean forward transfer {mean_ft:+.4f} on blind days",
        )
    return TrialGate(
        "forward_transfer", True, f"mean forward transfer {mean_ft:+.4f}"
    )


def _retention_gate(sequential: dict[str, Any]) -> TrialGate:
    retention = sequential.get("retention")
    if not isinstance(retention, dict):
        return TrialGate("retention", True, "no retention panel scored")
    if retention.get("ok") is False:
        return TrialGate(
            "retention", False, str(retention.get("detail") or "retention regression")
        )
    return TrialGate(
        "retention", True, str(retention.get("detail") or "retention held")
    )


def _archetype_repair_gate(
    universe: dict[str, Any],
    target: str | None,
) -> TrialGate:
    """A change staged to fix an archetype has to actually fix that archetype.

    The aggregate can improve while the gap the change was staged for gets
    worse — abstaining more in calm markets would do it. This is the gate that
    makes "train the weak archetypes" mean something: the candidate is scored on
    the target archetype's own ticks and has to beat the incumbent there.
    """
    if not target:
        return TrialGate("archetype_repair", True, "not an archetype-targeted change")
    panels = universe.get("archetype_authorities")
    if not isinstance(panels, dict):
        return TrialGate(
            "archetype_repair", True, f"no per-archetype panel for {target}"
        )
    panel = panels.get(target)
    if not isinstance(panel, dict):
        return TrialGate(
            "archetype_repair", True, f"{target} not visited by this lattice"
        )
    challenger = panel.get("challenger")
    champion = panel.get("champion")
    if not isinstance(challenger, dict) or not isinstance(champion, dict):
        return TrialGate(
            "archetype_repair", True, f"{target} not scored for both authorities"
        )
    cand = _as_float(challenger.get("total_pnl"))
    inc = _as_float(champion.get("total_pnl"))
    if cand is None or inc is None:
        return TrialGate("archetype_repair", True, f"{target} P&L unmeasured")
    if cand <= inc:
        return TrialGate(
            "archetype_repair",
            False,
            f"{target} {cand:+.4f} vs champion {inc:+.4f} — "
            "the gap it was staged to repair did not improve",
        )
    return TrialGate(
        "archetype_repair",
        True,
        f"{target} {inc:+.4f} → {cand:+.4f} ({cand - inc:+.4f})",
    )


def _universe_gate(
    universe: dict[str, Any],
    thresholds: PromotionThresholds,
) -> TrialGate:
    """The synthetic panel already scored champion vs challenger — reuse it."""
    authorities = universe.get("authorities")
    if not isinstance(authorities, dict):
        return TrialGate("universe", True, "no scored synthetic panel")
    challenger = authorities.get("challenger")
    champion = authorities.get("champion")
    if not isinstance(challenger, dict) or not isinstance(champion, dict):
        return TrialGate("universe", True, "no challenger/champion pair scored")
    cand = _as_float(challenger.get("total_pnl"))
    inc = _as_float(champion.get("total_pnl"))
    if cand is None or inc is None:
        return TrialGate("universe", not thresholds.require_universe, "panel unscored")
    if cand < inc:
        return TrialGate(
            "universe",
            False,
            f"synthetic panel {cand:+.4f} vs champion {inc:+.4f} "
            f"over {challenger.get('n_universes', 0)} universes",
        )
    return TrialGate(
        "universe",
        True,
        f"synthetic panel {cand:+.4f} vs champion {inc:+.4f}",
    )


def run_promotion_trial(
    cfg: DojoConfig,
    *,
    changes: dict[str, Any] | None,
    candidate_id: str | None,
    experience: MarketExperienceProvider | None = None,
    evaluator: CandidateEvaluator | None = None,
    universe_result: dict[str, Any] | None = None,
    current_champion: dict[str, Any] | None = None,
    thresholds: PromotionThresholds | None = None,
    incumbent_authority: DecisionAuthority | None = None,
    target_archetype: str | None = None,
) -> PromotionTrial:
    """Score the recommended change as a candidate champion and rule on it."""
    from spy_der.dojo.authority import ChallengerDecisionAuthority, default_authorities
    from spy_der.dojo.evaluation import OutcomeCandidateEvaluator
    from spy_der.dojo.recorded import run_recorded_phase
    from spy_der.dojo.sequential import SequentialDojoConfig, run_sequential_dojo

    thresholds = thresholds or PromotionThresholds()
    knobs = actionable_knobs(changes)
    universe_summary = _universe_authority_summary(universe_result)

    def _stopped(status: str, gate: TrialGate, note: str) -> PromotionTrial:
        return PromotionTrial(
            status=status,
            candidate_id=candidate_id,
            knobs=knobs,
            target_archetype=target_archetype,
            gates=(gate,),
            thresholds=thresholds.to_dict(),
            universe=universe_summary,
            note=note,
        )

    if not knobs or DecisionKnobs.from_mapping(knobs).is_noop:
        return _stopped(
            "not_actionable",
            TrialGate(
                "actionable",
                False,
                "recommended change touches no live decision knob "
                f"({sorted((changes or {}).keys()) or 'empty'})",
            ),
            "nothing to enact — the hypothesis holds the champion rather than "
            "changing it",
        )

    if experience is None:
        return _stopped(
            "skipped",
            TrialGate(
                "evidence", False, "no MarketExperienceProvider to re-run against"
            ),
            "promotion trial needs recorded tape to re-run",
        )

    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()
    # Falling back to a champion built from this run's configs_dir matters: a
    # champion authority seeded from the default state root would score a
    # different config than the one the candidate is trying to replace.
    incumbent = (
        incumbent_authority
        or default_authorities(configs_dir=cfg.configs_dir)["champion"]
    )
    candidate_authority = ChallengerDecisionAuthority(
        changes=dict(knobs), authority_name="candidate"
    )

    # Re-run the recorded tape with both authorities on identical snapshots.
    rerun = run_recorded_phase(
        cfg,
        experience,
        authorities={"champion": incumbent, "candidate": candidate_authority},
        evaluator=scorer,
    )
    reports = rerun.get("authorities") or {}
    candidate_head = _headline(reports.get("candidate"))
    incumbent_head = _headline(reports.get("champion"))
    if rerun.get("status") == "insufficient_data":
        candidate_head["status"] = "insufficient_data"

    # Blind-day walk with the candidate in the champion seat: forward transfer
    # is then candidate-minus-incumbent per unseen day, which is the question.
    sequential = run_sequential_dojo(
        experience,
        cfg=SequentialDojoConfig(min_warm_sessions=max(2, cfg.min_sessions - 1)),
        authorities={"champion": candidate_authority, "baseline": incumbent},
        evaluator=scorer,
    )

    gates = (
        TrialGate("actionable", True, f"live knobs {sorted(knobs)}"),
        _evidence_gate(candidate_head, thresholds),
        _pnl_gate(candidate_head, incumbent_head, thresholds),
        _win_rate_gate(candidate_head, incumbent_head, thresholds),
        _sequential_gate(sequential, thresholds),
        _retention_gate(sequential),
        _universe_gate(universe_result or {}, thresholds),
        _archetype_repair_gate(universe_result or {}, target_archetype),
        _cooldown_gate(current_champion, thresholds),
    )
    passed = all(g.passed for g in gates)
    blocking = next((g for g in gates if not g.passed), None)
    return PromotionTrial(
        status="validated" if passed else "rejected",
        candidate_id=candidate_id,
        knobs=knobs,
        target_archetype=target_archetype,
        gates=gates,
        incumbent=incumbent_head,
        candidate=candidate_head,
        sequential={
            "status": sequential.get("status"),
            "n_scored": sequential.get("n_scored"),
            "mean_forward_transfer": sequential.get("mean_forward_transfer"),
            "retention": sequential.get("retention"),
        },
        universe=universe_summary,
        thresholds=thresholds.to_dict(),
        note=(
            "re-ran recorded tape and blind days with the recommended change "
            "installed — every gate passed"
            if passed
            else f"blocked by {blocking.name}: {blocking.detail}"
            if blocking
            else "rejected"
        ),
    )


def _universe_authority_summary(universe: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the panel totals the gate used; drop the rest of the phase."""
    if not isinstance(universe, dict):
        return {}
    authorities = universe.get("authorities")
    if not isinstance(authorities, dict):
        return {"status": universe.get("status")}
    return {
        "status": universe.get("status"),
        "n_scored_universes": universe.get("n_scored_universes"),
        "authorities": {
            name: {
                "total_pnl": totals.get("total_pnl"),
                "trades": totals.get("trades"),
                "n_universes": totals.get("n_universes"),
            }
            for name, totals in authorities.items()
            if isinstance(totals, dict)
        },
    }
