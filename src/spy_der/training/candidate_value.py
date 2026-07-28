"""Fit the candidate-value model on the P&L a position would have realized.

The gap this closes is the one that made SPY-DER able to *avoid* losing trades
without being able to *choose* winning ones.

:func:`~spy_der.economics.service.calculate_candidate_economics` computes an
``expected_value`` only when its caller supplies ``expected_net_pnl``, and that
number comes from :class:`~spy_der.candidate_value.models.value.CandidateValueModel`.
Nothing ever fitted one. So every candidate reached the decision layer with
``utility=None``, and
:class:`~spy_der.agents.deterministic.DeterministicDecisionAgent` — which ranks
on utility — fell through to its tiebreak and sorted by candidate id. The Dojo
scored that alphabetical pick and called the winner a champion; the live agent,
having no expected value to weigh, could only fall back on "this family has been
losing, so stand down".

The missing ingredient was never the model. It was a training target: what each
candidate *actually paid*. :mod:`spy_der.dojo.native_tape` produces that now, so
this module turns recordings into rows the model can be fitted on:

    snapshot → generate_candidate_universe → calculate_universe_economics
             → build_feature_row                     (the input row)
             → simulate_managed_exit                 (the target)

One observation is one candidate at one tick. ``y_pnl`` is the P&L at the exit
that would actually have fired under the production exit policy; ``y_profit`` is
whether that was positive.

**Not the terminal payoff.** SPY-DER never holds to settlement — its live exits
are ``trail``, ``target`` and ``eod`` — and the error is not symmetric: a credit
structure usually pins near max profit at expiry while the managed version gets
stopped out on the excursion along the way, so an expiry-value target teaches
the model to love exactly the family that keeps getting stopped. See
:mod:`spy_der.evaluation.managed_outcome` for the two costs that buys.

Three properties keep it honest, and they are the same three the forecast
pipeline holds to:

* **Out-of-fold metrics, never in-sample.** Every reported number comes from
  walk-forward folds over whole sessions with an embargo
  (:mod:`spy_der.training.folds`). Candidates within a session share a market
  and are massively correlated — a random split would leak the session's
  outcome across the boundary and report skill that does not exist. Worse here
  than for the forecast models: every candidate at a tick settles against *one*
  closing price, so a random split can put the same settlement on both sides.
* **Skill against a no-features baseline.** A mean-absolute-error of 0.42 says
  nothing until you know that predicting the unconditional median scores 0.41.
  Every metric is paired with a ``*_skill`` term, and a model that cannot beat
  the constant it replaces is reported as such rather than registered as an
  improvement.
* **`research` status.** The registry's mode gates mean a `research` model
  cannot be served in `candidate` or `champion` mode. Training cannot promote
  itself.

An important consequence: the target is **policy-dependent**. The label means
"value under this exit policy", so changing the ratios makes every fitted model
stale. The policy is therefore part of ``label_version`` in the registry, which
turns silent staleness into a load-time mismatch.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from spy_der.candidate_value.models.value import (
    CandidateValueConfig,
    CandidateValueModel,
    build_feature_row,
)
from spy_der.candidates.factory import generate_candidate_universe
from spy_der.contracts.positions import ExitPolicy
from spy_der.contracts.value import CANDIDATE_VALUE_VERSION
from spy_der.economics.service import calculate_universe_economics
from spy_der.evaluation.managed_outcome import simulate_managed_exit
from spy_der.evaluation.settlement import (
    session_bar_path,
    session_settlement_price,
)
from spy_der.market_data.replay import CorruptRecordingError
from spy_der.training.folds import build_expanding_session_folds
from spy_der.training.observations import load_session_snapshots

__all__ = [
    "MIN_ROWS",
    "MIN_SESSIONS_FOR_FOLDS",
    "CandidateValueObservations",
    "CandidateValueTrainingResult",
    "build_candidate_observations",
    "train_candidate_value_model",
]

log = logging.getLogger("spy_der.training.candidate_value")

#: Rows below which fitting is refused. A HuberRegressor over ~20 features will
#: happily fit 50 rows and generalize to nothing.
MIN_ROWS = 400

#: Sessions below which no walk-forward fold can be formed; the model is still
#: fitted but registered with empty metrics rather than in-sample ones.
MIN_SESSIONS_FOR_FOLDS = 11

#: Wall-clock minutes between sampled ticks. Candidate generation plus economics
#: costs a few hundred milliseconds per snapshot, and every tick contributes one
#: row *per candidate*, so sampling every minute buys correlated rows rather
#: than information.
DEFAULT_INTERVAL_MINUTES = 5


@dataclass
class CandidateValueObservations:
    """Per-candidate feature rows with the P&L each one settled at."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    y_pnl: list[float] = field(default_factory=list)
    y_profit: list[int] = field(default_factory=list)
    row_sessions: list[str] = field(default_factory=list)
    sessions: tuple[str, ...] = ()
    skipped_sessions: tuple[tuple[str, str], ...] = ()
    #: Rows whose position closed on an exit rather than running to expiry.
    managed_rows: int = 0
    #: The policy the targets were simulated under. The label means "value
    #: under this policy", so a model fitted here is stale the moment it
    #: changes — which is why it goes into `label_version`.
    exit_policy: ExitPolicy = field(default_factory=ExitPolicy)

    def __len__(self) -> int:
        return len(self.rows)

    def describe(self) -> str:
        text = (
            f"{len(self.rows)} candidate row(s) over {len(self.sessions)} session(s)"
        )
        if self.rows:
            wins = sum(self.y_profit)
            text += f"; {wins} closed positive ({wins / len(self.rows):.1%})"
            text += (
                f"; {self.managed_rows} exited early "
                f"({self.managed_rows / len(self.rows):.1%})"
            )
        if self.skipped_sessions:
            text += "; skipped: " + ", ".join(
                f"{s} ({why})" for s, why in self.skipped_sessions
            )
        return text


def _sample(snapshots: Sequence[Any], interval_minutes: int) -> list[Any]:
    """Thin the tape by wall clock, not by record index.

    Recording cadence differs between the 0DTE import and SPY-DER's own
    service; striding every Nth record would make the row count depend on the
    recorder rather than on the market.
    """
    if interval_minutes <= 0:
        return list(snapshots)
    spacing = interval_minutes * 60
    out: list[Any] = []
    last: float | None = None
    for snapshot in snapshots:
        stamp = snapshot.timestamp.timestamp()
        if last is None or stamp - last >= spacing:
            out.append(snapshot)
            last = stamp
    return out


def build_candidate_observations(
    state_root: str | Path,
    *,
    sessions: Sequence[str] | None = None,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    policy: ExitPolicy | None = None,
) -> CandidateValueObservations:
    """Build per-candidate training rows from the recordings under ``state_root``.

    Candidates and economics are recomputed from each snapshot rather than read
    from the engine's `candidates/` artifacts: training must be reproducible
    from the market recording alone, or the training set silently depends on
    whichever engine version happened to write the artifact.
    """
    market_dir = Path(state_root) / "market"
    exit_policy = policy or ExitPolicy()
    result = CandidateValueObservations()
    result.exit_policy = exit_policy
    observed: list[str] = []
    skipped: list[tuple[str, str]] = []

    if not market_dir.is_dir():
        result.skipped_sessions = ((str(market_dir), "no market directory"),)
        return result

    wanted = set(sessions) if sessions is not None else None
    for path in sorted(market_dir.glob("*.jsonl")):
        label = path.stem
        if wanted is not None and label not in wanted:
            continue
        try:
            session = date.fromisoformat(label)
        except ValueError:
            skipped.append((label, "filename is not a session date"))
            continue
        try:
            snaps, unparseable = load_session_snapshots(path)
        except CorruptRecordingError as exc:
            log.error("recording %s failed integrity checks: %s", label, exc)
            skipped.append((label, "corrupt recording"))
            continue
        except OSError as exc:
            log.error("recording %s is unreadable: %s", label, exc)
            skipped.append((label, "unreadable"))
            continue
        if unparseable:
            log.warning("%d snapshot(s) in %s could not be rebuilt", unparseable, label)
        if not snaps:
            skipped.append((label, "no usable snapshots"))
            continue

        bar_path = session_bar_path(snaps)
        settle = session_settlement_price(bar_path)
        if settle is None:
            # No close, no settlement, no target. Fitting against a midday quote
            # would teach the model that every position expires at lunchtime.
            skipped.append((label, "never reached the close"))
            continue

        produced = 0
        for snapshot in _sample(snaps, interval_minutes):
            universe = generate_candidate_universe(snapshot)
            if not universe.candidates:
                continue
            economics = {
                e.candidate_id: e
                for e in calculate_universe_economics(universe, snapshot)
            }
            for candidate in universe.candidates:
                econ = economics.get(candidate.candidate_id)
                if econ is None:
                    continue
                outcome = simulate_managed_exit(
                    candidate,
                    chain=snapshot.option_chain,
                    bars=bar_path,
                    observed_at=snapshot.timestamp,
                    session=session,
                    settlement=settle,
                    policy=exit_policy,
                )
                if outcome is None:
                    continue
                pnl = float(outcome.realized_pnl)
                result.rows.append(build_feature_row(candidate, econ))
                result.y_pnl.append(pnl)
                result.y_profit.append(1 if pnl > 0.0 else 0)
                if outcome.was_managed:
                    result.managed_rows += 1
                result.row_sessions.append(label)
                produced += 1

        if produced:
            observed.append(label)
        else:
            skipped.append((label, "no candidate produced a settled row"))

    result.sessions = tuple(observed)
    result.skipped_sessions = tuple(skipped)
    return result


@dataclass
class CandidateValueTrainingResult:
    model_id: str | None
    n_rows: int
    n_sessions: int
    status: str
    oof_metrics: dict[str, float]
    skipped_reason: str = ""

    def describe(self) -> str:
        if self.model_id is None:
            return f"not registered: {self.skipped_reason}"
        text = f"{self.model_id} ({self.status}) on {self.n_rows} rows"
        if self.oof_metrics:
            skills = {k: v for k, v in self.oof_metrics.items() if k.endswith("_skill")}
            if skills:
                text += "; " + ", ".join(f"{k}={v:+.3f}" for k, v in sorted(skills.items()))
        else:
            text += "; no out-of-fold metrics (too few sessions to form a fold)"
        return text


def _label_version(policy: ExitPolicy) -> str:
    """Label identity, stamped with the exit policy that produced it.

    The target is what a position realized under a specific policy. Two models
    fitted under different ratios answer different questions, and without this
    the registry would happily serve one in place of the other.
    """
    return (
        f"managed.v1:{policy.policy_id}"
        f":tp{policy.take_profit_ratio:g}"
        f":sl{policy.stop_loss_ratio:g}"
        f":arm{policy.trailing_arm_ratio:g}"
        f":gb{policy.trailing_giveback_ratio:g}"
        f":eod{int(policy.eod_close)}"
    )


def _score(
    model: CandidateValueModel,
    rows: Sequence[dict[str, Any]],
    y_pnl: Sequence[float],
    y_profit: Sequence[int],
) -> dict[str, float]:
    """Held-out score against a no-features baseline.

    Both baselines are computed on the *held-out* targets, which is the
    strictest fair comparison: the baseline is allowed to know the held-out
    distribution, so beating it cannot be an artifact of drift.
    """
    if not rows:
        return {}
    x = model.vectorizer.transform(list(rows))
    truth = np.asarray(y_pnl, dtype=float)

    predicted = np.asarray(model.pnl_estimator.predict(x), dtype=float)
    mae = float(np.mean(np.abs(predicted - truth)))
    out: dict[str, float] = {"mae": mae}
    reference = float(np.mean(np.abs(float(np.median(truth)) - truth)))
    if reference > 0.0:
        out["mae_skill"] = 1.0 - mae / reference

    wins = np.asarray(y_profit, dtype=float)
    if isinstance(model.profit_estimator, tuple):
        probabilities = np.full_like(wins, float(model.profit_estimator[1]))
    else:
        probabilities = np.asarray(
            model.profit_estimator.predict_proba(x)[:, 1], dtype=float
        )
    brier = float(np.mean((probabilities - wins) ** 2))
    base_rate = float(wins.mean())
    out["brier"] = brier
    out["base_rate"] = base_rate
    baseline = float(np.mean((np.full_like(wins, base_rate) - wins) ** 2))
    if baseline > 0.0:
        out["brier_skill"] = 1.0 - brier / baseline

    # Does the ranking put money in the right order? This is the metric that
    # matters operationally: the decision layer does not consume the predicted
    # P&L, it consumes the *order*. A model can have poor MAE and still rank
    # correctly, and a model can have good MAE and rank no better than chance.
    if len(truth) >= 2 and float(np.std(predicted)) > 0.0:
        order = np.argsort(predicted)[::-1]
        top = max(1, len(order) // 10)
        out["top_decile_mean_pnl"] = float(np.mean(truth[order[:top]]))
        out["all_mean_pnl"] = float(np.mean(truth))
        out["selection_edge"] = out["top_decile_mean_pnl"] - out["all_mean_pnl"]
    return out


def _out_of_fold_metrics(
    observations: CandidateValueObservations,
    folds: Sequence[dict[str, tuple[str, ...]]],
) -> dict[str, float]:
    """Average held-out score across walk-forward folds.

    Empty when no fold could be fitted. An unearned metric is worse than a
    missing one, because the registry's audit fields are what a promotion
    decision reads.
    """
    collected: dict[str, list[float]] = {}
    for fold in folds:
        train = set(fold.get("train_sessions") or ())
        test = set(fold.get("test_sessions") or fold.get("validation_sessions") or ())
        if not train or not test:
            continue
        tr_rows, tr_pnl, tr_win = [], [], []
        te_rows, te_pnl, te_win = [], [], []
        for row, pnl, win, session in zip(
            observations.rows,
            observations.y_pnl,
            observations.y_profit,
            observations.row_sessions,
            strict=True,
        ):
            if session in train:
                tr_rows.append(row)
                tr_pnl.append(pnl)
                tr_win.append(win)
            elif session in test:
                te_rows.append(row)
                te_pnl.append(pnl)
                te_win.append(win)
        if len(tr_rows) < MIN_ROWS // 4 or not te_rows:
            continue
        try:
            fitted = CandidateValueModel().fit(tr_rows, tr_pnl, tr_win)
        except (ValueError, RuntimeError) as exc:
            log.warning("candidate-value fold failed to fit: %s", exc)
            continue
        for name, value in _score(fitted, te_rows, te_pnl, te_win).items():
            collected.setdefault(name, []).append(value)
    return {k: float(np.mean(v)) for k, v in collected.items() if v}


def train_candidate_value_model(
    observations: CandidateValueObservations,
    *,
    registry: Any,
    status: str = "research",
    min_rows: int = MIN_ROWS,
) -> CandidateValueTrainingResult:
    """Fit the candidate-value model, score it out of fold, and register it."""
    n_rows = len(observations)
    n_sessions = len(observations.sessions)
    if n_rows < min_rows:
        return CandidateValueTrainingResult(
            model_id=None,
            n_rows=n_rows,
            n_sessions=n_sessions,
            status="skipped",
            oof_metrics={},
            skipped_reason=f"{n_rows} rows (< {min_rows})",
        )
    if len(set(observations.y_profit)) < 2:
        # Every candidate settled the same way. A classifier fitted here would
        # be a constant dressed as a model.
        return CandidateValueTrainingResult(
            model_id=None,
            n_rows=n_rows,
            n_sessions=n_sessions,
            status="skipped",
            oof_metrics={},
            skipped_reason="every row settled the same side of zero",
        )

    folds: list[dict[str, tuple[str, ...]]] = []
    if n_sessions >= MIN_SESSIONS_FOR_FOLDS:
        folds = build_expanding_session_folds(sorted(set(observations.sessions)))
    oof = _out_of_fold_metrics(observations, folds) if folds else {}

    model = CandidateValueModel(config=CandidateValueConfig()).fit(
        observations.rows, observations.y_pnl, observations.y_profit
    )

    # Imported lazily: `training.registry` pulls in the model substrate, and the
    # caller already owns a registry instance.
    from spy_der.training.pipeline import (
        _dependency_versions,
        _feature_distribution_hash,
        _git_commit,
        _hash,
    )

    required = sorted({name for row in observations.rows for name in row})
    model_id = registry.save(
        model,
        model_type="candidate_value",
        target="managed_net_pnl",
        horizon="session_close",
        feature_version=CANDIDATE_VALUE_VERSION,
        label_version=_label_version(observations.exit_policy),
        crossfit_config={
            "scheme": "expanding_session_walk_forward",
            "n_folds": len(folds),
            "embargo_sessions": 1,
        },
        fold_hash=_hash([sorted(f.get("train_sessions") or ()) for f in folds]),
        oof_metrics=oof,
        calibration_artifact={
            "method": "none",
            "reason": (
                "regression on settled P&L plus an uncalibrated profit "
                "classifier; no calibration map is fitted"
            ),
        },
        uncertainty_method="training_set_quantiles",
        training_feature_distribution_hash=_feature_distribution_hash(
            [{k: float(v) for k, v in row.items()} for row in observations.rows]
        ),
        required_input_fields=required,
        dependency_versions=_dependency_versions(),
        git_commit=_git_commit(),
        hyperparameters={"estimator": "huber+logistic", "min_rows": min_rows},
        metrics=oof,
        training_sessions=list(observations.sessions),
        data_hash=_hash([observations.sessions, n_rows]),
        status=status,
    )
    return CandidateValueTrainingResult(
        model_id=model_id,
        n_rows=n_rows,
        n_sessions=n_sessions,
        status=status,
        oof_metrics=oof,
    )
