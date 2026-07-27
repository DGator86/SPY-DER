"""Plain-English Dojo report copy for the Vercel / SPY-DER dashboards.

The Vercel Dojo tab reads SPY-DER ``latest.json``. This module turns phase
metrics into a stable ``human`` block so the UI can answer the questions
operators actually ask — what was checked, what data was used, why it stopped,
and what happens next — without dumping evaluator class names or file paths.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_human_report", "humanize_flag"]

_ARCHETYPE_LABELS: dict[str, str] = {
    "calm_pin": "calm pin",
    "grind_up": "grind up",
    "grind_down": "grind down",
    "range_chop": "range chop",
    "vol_expansion": "vol expansion",
    "squeeze_melt_up": "squeeze melt-up",
    "crash": "crash",
    "gap_shock": "gap shock",
}


def humanize_flag(flag: str) -> str:
    """Turn a machine flag into a short human label."""
    raw = str(flag or "").strip()
    if raw.startswith("weak_archetype:"):
        arch = raw.split(":", 1)[1]
        label = _ARCHETYPE_LABELS.get(arch, arch.replace("_", " "))
        return f"weak on {label}"
    if raw == "champion_promoted":
        return "safer setting promoted"
    if raw.startswith("promotion_rejected:"):
        gate = raw.split(":", 1)[1].replace("_", " ")
        return f"change blocked ({gate})"
    if raw == "universe_skipped_no_tape":
        return "sparring skipped — need more real sessions"
    if raw == "universe_unscored":
        return "synthetic worlds were not scored"
    return raw.replace("_", " ")


def build_human_report(
    *,
    recorded: dict[str, Any],
    sequential: dict[str, Any],
    learner: dict[str, Any],
    universe: dict[str, Any],
    promotion: dict[str, Any],
    flags: list[dict[str, Any]],
    summary: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``human`` object attached to every Dojo report."""
    cfg = config or {}
    remediation = universe.get("remediation") or {}
    focus = remediation.get("focus") if isinstance(remediation, dict) else None
    if not isinstance(focus, list):
        focus = []

    n_sessions = recorded.get("n_sessions")
    n_universes = universe.get("n_universes")
    generations = cfg.get("generations", universe.get("generations"))
    try:
        n_gen = int(generations) if generations is not None else None
    except (TypeError, ValueError):
        n_gen = None

    what_ran_bits: list[str] = []
    if recorded.get("status") == "ok" and n_sessions is not None:
        what_ran_bits.append(f"Checked {n_sessions} real past market sessions")
    elif recorded.get("status") in {"insufficient_data", "skipped"}:
        what_ran_bits.append("Not enough real past sessions to score yet")
    else:
        what_ran_bits.append("Checked stored market sessions")

    if universe.get("status") == "ok" and n_universes is not None:
        what_ran_bits.append(f"then stress-tested {n_universes} synthetic market worlds")
    elif universe.get("status") == "skipped":
        what_ran_bits.append("skipped synthetic stress tests (need more real tape first)")
    else:
        what_ran_bits.append("ran synthetic stress tests")

    focus_labels = [
        str(row.get("label") or row.get("archetype") or "").replace("_", " ")
        for row in focus[:3]
        if isinstance(row, dict)
    ]
    if focus_labels:
        next_step = (
            "Next run will practice more on "
            + ", ".join(focus_labels)
            + ". Dojo does not keep grinding until every market type looks good."
        )
    else:
        next_step = (
            "No elevated gaps to overweight. The next scheduled run will check again."
        )

    if n_gen is not None and n_gen > 0:
        stop_reason = (
            f"Finished its fixed budget ({n_gen} generation"
            f"{'s' if n_gen != 1 else ''}"
            + (
                f", up to {cfg.get('universes_per_gen')} worlds each"
                if cfg.get("universes_per_gen") is not None
                else ""
            )
            + "). It is a nightly exam, not an open-ended trainer."
        )
    else:
        stop_reason = (
            "Finished its fixed nightly budget. It does not keep running until "
            "results look great."
        )

    promo_status = str(promotion.get("status") or "")
    if promotion.get("enacted"):
        change_line = "A safer setting was validated and is now live."
    elif promo_status == "rejected":
        change_line = "A proposed change was tried and rejected — champion unchanged."
    elif promo_status in {"disabled", "skipped", "no_candidate", "not_actionable"}:
        outcome = str(learner.get("outcome") or learner.get("status") or "")
        if outcome in {"promotion_recommended", "staged"}:
            change_line = "A change was staged but not auto-promoted."
        else:
            change_line = "No live setting change this run."
    elif promo_status == "validated":
        change_line = "A change validated; waiting to write the champion."
    else:
        change_line = "No live setting change this run."

    headline = summary
    if isinstance(remediation, dict) and remediation.get("headline"):
        # Prefer remediation headline when gaps are the story.
        if focus_labels:
            headline = str(remediation["headline"])

    flag_labels = [
        humanize_flag(str(f.get("flag") if isinstance(f, dict) else f))
        for f in flags
    ]

    return {
        "headline": headline,
        "what_ran": ". ".join(what_ran_bits) + ".",
        "data_story": (
            "Used stored real market sessions plus generated stress worlds. "
            "Did not trade or score against the live market during this run."
        ),
        "stop_reason": stop_reason,
        "next_step": next_step,
        "change": change_line,
        "focus": focus,
        "flag_labels": flag_labels,
        "phases": {
            "recorded": _phase_line(
                "Real market tape",
                recorded,
                ok_detail=_recorded_detail(recorded),
            ),
            "sequential": _phase_line(
                "Blind-day check",
                sequential,
                ok_detail="Leak-free walk on held-out days.",
            ),
            "learner": _phase_line(
                "Adaptive change",
                learner,
                status_key="outcome",
                ok_detail=change_line,
            ),
            "universe": _phase_line(
                "Synthetic sparring",
                universe,
                ok_detail=_universe_detail(universe, focus_labels),
            ),
            "promotion": _phase_line(
                "Promotion",
                promotion,
                ok_detail=change_line,
            ),
        },
    }


def _recorded_detail(recorded: dict[str, Any]) -> str:
    evaluation = recorded.get("evaluation") or {}
    bits: list[str] = []
    if recorded.get("n_sessions") is not None:
        bits.append(f"{recorded['n_sessions']} sessions")
    if evaluation.get("trades") is not None:
        bits.append(f"{evaluation['trades']} trades")
    if evaluation.get("total_pnl") is not None:
        try:
            bits.append(f"P&L {float(evaluation['total_pnl']):+.2f}")
        except (TypeError, ValueError):
            pass
    return " · ".join(bits) if bits else "Scored against settled outcomes."


def _universe_detail(universe: dict[str, Any], focus_labels: list[str]) -> str:
    n = universe.get("n_universes")
    base = f"{n} synthetic worlds" if n is not None else "Synthetic stress worlds"
    if focus_labels:
        return f"{base} · next focus: {', '.join(focus_labels)}"
    return base


def _phase_line(
    title: str,
    phase: dict[str, Any],
    *,
    status_key: str = "status",
    ok_detail: str = "",
) -> dict[str, str]:
    status = str(phase.get(status_key) or phase.get("status") or "unknown")
    if status in {"ok", "validated"} or phase.get("enacted") is True:
        detail = ok_detail or "OK"
        tone = "ok"
        label = "OK"
    elif status in {"skipped", "insufficient_data", "disabled", "no_candidate", "not_actionable"}:
        detail = str(phase.get("note") or phase.get("reason") or "Skipped")
        tone = "warn"
        label = "Skipped"
    elif status in {"rejected", "unscored", "error", "failed", "promotion_failed"}:
        detail = str(phase.get("note") or status.replace("_", " "))
        tone = "bad"
        label = status.replace("_", " ")
    else:
        detail = str(phase.get("note") or status.replace("_", " "))
        tone = "warn"
        label = status.replace("_", " ")
    return {"title": title, "label": label, "detail": detail, "tone": tone}
