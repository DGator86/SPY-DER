"""Fit, validate and register a model group (master spec §24, §25).

The step that makes the forecast stage possible. `ForecastServer` has always
been able to serve a registered group; nothing produced one, so
`spy-der engine` reported `forecast: unavailable — no trained model group is
configured` and every downstream stage ran without a forecast.

The component roles are exactly the ones `ForecastServer.predict` looks for —
`direction_30m`, `return_quantiles_30m`, `volatility`, `range_survive_close`,
`touch_call_wall`, `touch_put_wall`. A role whose target is too sparse to fit is
*skipped and reported*, not filled with a degenerate model: serving already
tolerates a partial group and produces the components it has, which is a far
better outcome than a model trained on forty rows that answers with confidence.

Two properties are load-bearing:

* **Out-of-fold metrics, never in-sample ones.** Every reported metric comes
  from walk-forward folds over whole sessions with an embargo
  (:mod:`spy_der.training.folds`), because intraday rows within a session are
  heavily autocorrelated and a random split would leak the answer across the
  boundary and report a skill that does not exist. A group that cannot form a
  fold is registered with empty metrics and `research` status rather than
  metrics it did not earn.
* **`research` status by default.** The registry's mode gates mean a `research`
  group cannot be served in `candidate` or `champion` mode. Promotion is a
  separate, human-acknowledged decision (`spy_der.learning.promotion`), and
  training deliberately cannot grant it to itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from spy_der.contracts.forecasts import FEATURE_VERSION, LABEL_VERSION
from spy_der.forecasting.models.barrier_touch import BarrierTouchConfig, BarrierTouchModel
from spy_der.forecasting.models.base import brier_score, pinball_loss
from spy_der.forecasting.models.direction import DirectionModel, DirectionModelConfig
from spy_der.forecasting.models.range_survival import RangeSurvivalConfig, RangeSurvivalModel
from spy_der.forecasting.models.return_quantiles import (
    ReturnQuantileConfig,
    ReturnQuantileModel,
)
from spy_der.forecasting.models.volatility import VolatilityModel, VolatilityModelConfig
from spy_der.training.folds import build_expanding_session_folds
from spy_der.training.observations import ObservationSet
from spy_der.training.registry import ModelRegistry

__all__ = [
    "COMPONENT_ROLES",
    "MIN_ROWS_PER_ROLE",
    "RoleOutcome",
    "TrainingResult",
    "train_model_group",
]

log = logging.getLogger("spy_der.training")

#: Serving role -> (model factory, label key, kind). The roles and their names
#: are dictated by `ForecastServer.predict`; renaming one here silently drops
#: that component from every forecast.
COMPONENT_ROLES: tuple[tuple[str, str, str], ...] = (
    ("direction_30m", "up_30m", "classifier"),
    ("return_quantiles_30m", "fwd_return_30m", "quantile"),
    ("volatility", "remaining_realized_move", "regressor"),
    ("range_survive_close", "range_survive_close", "classifier"),
    ("touch_call_wall", "touch_call_wall_30m", "classifier"),
    ("touch_put_wall", "touch_put_wall_30m", "classifier"),
)

#: Below this a fit is not worth the confidence a served forecast implies.
MIN_ROWS_PER_ROLE = 200

#: Minimum distinct sessions before walk-forward folds mean anything.
MIN_SESSIONS_FOR_FOLDS = 11


#: Skill below this is treated as no demonstrated edge. Deliberately a hair
#: above zero rather than at it: a skill of +0.001 over a few folds is noise,
#: and rounding it up to "this model works" is the exact mistake the skill
#: metric exists to prevent.
MIN_SKILL = 0.01


@dataclass(frozen=True, slots=True)
class RoleOutcome:
    """What happened to one component role."""

    role: str
    trained: bool
    model_id: str = ""
    n_rows: int = 0
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def skill(self) -> float | None:
        """Held-out skill over a no-feature baseline; ``None`` when unscored.

        Each role reports one skill term, so the worst of them is the honest
        summary for a multi-quantile model — a q50 that beats its baseline while
        the tails do not is not a model you would trade the tails on.
        """
        values = [v for k, v in self.metrics.items() if k.endswith("_skill")]
        return min(values) if values else None

    @property
    def has_edge(self) -> bool:
        """Whether this component beat a model that uses no features at all."""
        skill = self.skill
        return skill is not None and skill >= MIN_SKILL

    def verdict(self) -> str:
        if not self.trained:
            return f"skipped ({self.reason})"
        skill = self.skill
        if skill is None:
            return "trained, UNSCORED (no walk-forward fold could be formed)"
        if skill >= MIN_SKILL:
            return f"trained, edge +{skill:.3f} over baseline"
        return f"trained, NO EDGE ({skill:+.3f} vs baseline)"


@dataclass
class TrainingResult:
    """The registered group plus an honest account of what it contains."""

    group_id: str = ""
    roles: tuple[RoleOutcome, ...] = ()
    sessions: tuple[str, ...] = ()
    n_observations: int = 0
    fold_count: int = 0
    status: str = "research"

    @property
    def trained_roles(self) -> tuple[str, ...]:
        return tuple(r.role for r in self.roles if r.trained)

    @property
    def is_servable(self) -> bool:
        """Serving needs at least one of the components `predict` reads.

        Servable is *not* the same as useful — see :attr:`has_edge`. A group can
        serve perfectly well and forecast nothing of value.
        """
        return bool(
            {"direction_30m", "return_quantiles_30m", "volatility"}
            & set(self.trained_roles)
        )

    @property
    def edge_roles(self) -> tuple[str, ...]:
        return tuple(r.role for r in self.roles if r.has_edge)

    @property
    def has_edge(self) -> bool:
        """Whether any component beat a model that uses no features.

        The question that separates "the pipeline ran" from "the model predicts".
        A group that trains cleanly, registers, and serves can still be worthless,
        and nothing about a raw loss value would tell you.
        """
        return bool(self.edge_roles)

    def describe(self) -> str:
        text = f"group {self.group_id or '(none)'}"
        for outcome in self.roles:
            text += f"\n  {outcome.role:22s} {outcome.verdict()}"
        if not self.has_edge:
            text += (
                "\n  VERDICT: no component demonstrated edge over a no-feature "
                "baseline — this group forecasts nothing of value yet"
            )
        else:
            text += f"\n  VERDICT: edge in {', '.join(self.edge_roles)}"
        return text


def _git_commit() -> str:
    """Best-effort provenance; absence is recorded rather than faked."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _dependency_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in ("numpy", "sklearn", "joblib"):
        try:
            module = __import__(name)
        except ImportError:
            continue
        versions[name] = str(getattr(module, "__version__", "unknown"))
    return versions


def _hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _feature_distribution_hash(rows: Sequence[dict[str, float]]) -> str:
    """Hash of the per-feature medians the model was fitted against.

    Recorded so a later serving row can be compared against the distribution the
    model actually saw — the input to drift and OOD checks.
    """
    if not rows:
        return _hash({})
    names = sorted({name for row in rows for name in row})
    medians: dict[str, float] = {}
    for name in names:
        values = [row[name] for row in rows if name in row]
        if values:
            medians[name] = round(float(np.median(values)), 6)
    return _hash(medians)


def _build_model(role: str) -> Any:
    if role == "direction_30m":
        return DirectionModel(config=DirectionModelConfig(horizon="30m"))
    if role == "return_quantiles_30m":
        return ReturnQuantileModel(config=ReturnQuantileConfig(horizon="30m"))
    if role == "volatility":
        return VolatilityModel(config=VolatilityModelConfig())
    if role == "range_survive_close":
        return RangeSurvivalModel(config=RangeSurvivalConfig(horizon="close"))
    if role == "touch_call_wall":
        return BarrierTouchModel(config=BarrierTouchConfig(target="touch_call_wall"))
    if role == "touch_put_wall":
        return BarrierTouchModel(config=BarrierTouchConfig(target="touch_put_wall"))
    msg = f"unknown component role {role!r}"
    raise ValueError(msg)


def _fit(
    model: Any,
    rows: Sequence[dict[str, float]],
    y: Sequence[Any],
    sessions: Sequence[str],
) -> Any:
    return model.fit(rows, y, sessions)


def _score(
    kind: str, model: Any, rows: Sequence[dict[str, float]], y: Sequence[Any]
) -> dict[str, float]:
    """Score a fitted model on held-out rows against a no-edge baseline.

    **A raw loss is not evidence.** A Brier score of 0.257 reads as respectable
    until you notice that always predicting the base rate scores 0.248 on the
    same data — the model is *worse than guessing*, and nothing about the number
    says so. Every metric here is therefore paired with a ``*_skill`` term:
    the fractional improvement over a baseline that uses no features at all.

    Positive skill means the features carried information. Zero means they did
    not. Negative means the fitted model is actively worse than the constant it
    replaced. The baselines are deliberately computed on the *held-out* targets,
    which is the strictest fair comparison: the baseline is allowed to know the
    held-out distribution, so beating it cannot be an artifact of drift.
    """
    if not rows:
        return {}
    truth = np.asarray(y, dtype=float)

    if kind == "classifier":
        p = np.asarray(model.predict_proba(rows), dtype=float)
        brier = float(brier_score(truth, p))
        base_rate = float(truth.mean())
        out = {"brier": brier, "base_rate": base_rate}
        reference = float(brier_score(truth, np.full_like(truth, base_rate)))
        if reference > 0.0:
            out["brier_skill"] = 1.0 - brier / reference
        return out

    if kind == "quantile":
        pred = model.predict(rows)
        out = {}
        for name, level in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9)):
            loss = float(pinball_loss(truth, np.asarray(pred[name], float), level))
            out[f"pinball_{name}"] = loss
            # Baseline: the unconditional quantile of the held-out targets.
            flat = float(np.quantile(truth, level))
            reference = float(pinball_loss(truth, np.full_like(truth, flat), level))
            if reference > 0.0:
                out[f"pinball_{name}_skill"] = 1.0 - loss / reference
        return out

    pred = model.predict(rows)
    expected = np.asarray(pred["expected_move"], dtype=float)
    mae = float(np.mean(np.abs(expected - truth)))
    out = {"mae": mae}
    # Baseline: the unconditional median, which minimizes MAE with no features.
    reference = float(np.mean(np.abs(float(np.median(truth)) - truth)))
    if reference > 0.0:
        out["mae_skill"] = 1.0 - mae / reference
    return out


def _out_of_fold_metrics(
    role: str,
    kind: str,
    rows: Sequence[dict[str, float]],
    y: Sequence[Any],
    sessions: Sequence[str],
    folds: Sequence[dict[str, tuple[str, ...]]],
) -> dict[str, float]:
    """Average held-out score across walk-forward folds.

    Returns empty when no fold could be fitted — an unearned metric is worse
    than a missing one, because the registry's v2 audit fields are what a
    promotion decision reads.
    """
    scores: list[dict[str, float]] = []
    for fold in folds:
        train = set(fold.get("train_sessions", ()))
        # `build_expanding_session_folds` emits `test_sessions`; `FoldDefinition`
        # names the same thing `validation_sessions`. Accept both — reading only
        # one silently yields an empty held-out set, and empty metrics are
        # indistinguishable from "not enough data" at the call site.
        test = set(fold.get("test_sessions", ())) | set(fold.get("validation_sessions", ()))
        train_idx = [i for i, s in enumerate(sessions) if s in train]
        test_idx = [i for i, s in enumerate(sessions) if s in test]
        if len(train_idx) < MIN_ROWS_PER_ROLE or not test_idx:
            continue
        try:
            model = _fit(
                _build_model(role),
                [rows[i] for i in train_idx],
                [y[i] for i in train_idx],
                [sessions[i] for i in train_idx],
            )
            scores.append(
                _score(kind, model, [rows[i] for i in test_idx], [y[i] for i in test_idx])
            )
        except (ValueError, RuntimeError) as exc:
            # A fold whose training slice lacks both classes is a data condition,
            # not a defect; it costs that fold rather than the whole role.
            log.info("fold skipped for %s: %s", role, exc)
            continue

    if not scores:
        return {}
    keys = sorted({k for s in scores for k in s})
    return {
        f"oof_{k}": float(np.mean([s[k] for s in scores if k in s]))
        for k in keys
        if any(k in s for s in scores)
    }


def train_model_group(
    observations: ObservationSet,
    *,
    registry: ModelRegistry,
    status: str = "research",
    group_id: str | None = None,
    min_rows: int = MIN_ROWS_PER_ROLE,
) -> TrainingResult:
    """Fit every component role, score it out of fold, and register the group."""
    sessions = sorted(set(observations.sessions))
    folds: list[dict[str, tuple[str, ...]]] = []
    if len(sessions) >= MIN_SESSIONS_FOR_FOLDS:
        folds = build_expanding_session_folds(sessions)

    crossfit_config = {
        "kind": "expanding_session_folds",
        "n_sessions": len(sessions),
        "n_folds": len(folds),
    }
    fold_hash = _hash(folds)
    dependency_versions = _dependency_versions()
    git_commit = _git_commit()

    outcomes: list[RoleOutcome] = []
    component_ids: dict[str, str] = {}
    group_metrics: dict[str, Any] = {}

    for role, label_key, kind in COMPONENT_ROLES:
        rows, y, row_sessions = observations.target(label_key)
        if len(rows) < min_rows:
            outcomes.append(
                RoleOutcome(
                    role=role,
                    trained=False,
                    n_rows=len(rows),
                    reason=f"{len(rows)} rows < {min_rows} minimum",
                )
            )
            continue

        degenerate = _degenerate_target(kind, y)
        if degenerate:
            # Checked up front so the reported reason names the data condition.
            # Left to the model, this surfaces as "predict_raw before fit" from
            # deep inside the calibration step — true, but it tells an operator
            # nothing about why, and the fix is more data, not a code change.
            outcomes.append(
                RoleOutcome(role=role, trained=False, n_rows=len(rows), reason=degenerate)
            )
            continue

        try:
            model = _fit(_build_model(role), rows, y, row_sessions)
        except (ValueError, RuntimeError) as exc:
            outcomes.append(
                RoleOutcome(role=role, trained=False, n_rows=len(rows), reason=str(exc))
            )
            continue

        metrics = _out_of_fold_metrics(role, kind, rows, y, row_sessions, folds)
        model_id = registry.save(
            model,
            model_type=type(model).__name__,
            target=label_key,
            horizon=getattr(getattr(model, "config", None), "horizon", None),
            feature_version=FEATURE_VERSION,
            label_version=LABEL_VERSION,
            crossfit_config=crossfit_config,
            fold_hash=fold_hash,
            oof_metrics=metrics,
            calibration_artifact=_calibration_artifact(model, kind),
            uncertainty_method=(
                "calibrated_probability" if kind == "classifier" else "quantile_spread"
            ),
            training_feature_distribution_hash=_feature_distribution_hash(rows),
            # Deliberately empty: a required field makes serving fail closed when
            # it is absent, and every feature here is legitimately optional at
            # the cold start of a session. Coverage is judged by data_quality.
            required_input_fields=[],
            dependency_versions=dependency_versions,
            git_commit=git_commit,
            hyperparameters=_hyperparameters(model),
            metrics=metrics,
            training_sessions=sessions,
            status=status,
        )
        component_ids[role] = model_id
        group_metrics[role] = metrics
        outcomes.append(
            RoleOutcome(
                role=role,
                trained=True,
                model_id=model_id,
                n_rows=len(rows),
                metrics=metrics,
            )
        )

    if not component_ids:
        return TrainingResult(
            roles=tuple(outcomes),
            sessions=tuple(sessions),
            n_observations=len(observations),
            fold_count=len(folds),
            status=status,
        )

    meta = registry.save_group(
        component_model_ids=component_ids,
        feature_version=FEATURE_VERSION,
        label_version=LABEL_VERSION,
        training_sessions=sessions,
        metrics=group_metrics,
        status=status,
        group_id=group_id,
    )
    return TrainingResult(
        group_id=meta.group_id,
        roles=tuple(outcomes),
        sessions=tuple(sessions),
        n_observations=len(observations),
        fold_count=len(folds),
        status=status,
    )


def _degenerate_target(kind: str, y: Sequence[Any]) -> str:
    """Why ``y`` cannot train ``kind``, or ``""`` when it can.

    A classifier needs both outcomes to have occurred: a target that is always 1
    over the training window carries no signal, and a model fitted on it would
    answer 1 with confidence forever. That is a data condition — the outcome
    genuinely never varied — so it is named plainly rather than surfaced as an
    internal error.
    """
    if kind != "classifier":
        return ""
    classes = {int(v) for v in y}
    if len(classes) < 2:
        only = next(iter(classes)) if classes else "none"
        return f"single-class target (always {only}) over the training window"
    return ""


def _calibration_artifact(model: Any, kind: str) -> dict[str, Any]:
    """How this model's output was calibrated, always declared.

    The registry's v2 gate refuses an artifact with no calibration method, and
    it is right to: nobody should serve a model without knowing whether its
    numbers were calibrated. But a quantile or volatility regressor has no
    post-hoc calibration step — its quantiles are fitted directly under pinball
    loss — so the honest record is an explicit "none" with the reason, not a
    borrowed method name that implies a step which never ran.
    """
    artifact = dict(getattr(model, "calibration_artifact", {}) or {})
    if artifact.get("method"):
        return artifact

    calibrator = getattr(model, "calibrator", None)
    if calibrator is not None:
        method = getattr(getattr(model, "config", None), "calibration", "") or "unknown"
        return {"method": str(method), "source": "model_config"}

    if kind == "classifier":
        # A classifier that ended up without a calibrator is a real condition
        # (too few calibration sessions), and serving must be able to see it.
        return {
            "method": "none",
            "reason": "no calibration split was available; probabilities are raw",
        }
    return {
        "method": "none",
        "reason": f"{kind} output is fitted directly; no post-hoc calibration applies",
    }


def _hyperparameters(model: Any) -> dict[str, Any]:
    config = getattr(model, "config", None)
    if config is None:
        return {}
    return {
        key: value
        for key, value in vars(config).items()
        if isinstance(value, (int, float, str, bool, type(None)))
    }


def default_registry(state_root: str | Path) -> ModelRegistry:
    """The registry under a state root, matching the runtime's layout."""
    return ModelRegistry(str(Path(state_root) / "models"))
