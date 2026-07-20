"""Fail-closed V2 forecast serving (master spec §24 / System A prediction/inference.py).

Trained model groups are loaded through the registry with status/mode gates.
Missing required inputs or registry failures raise — they never become silent
neutral forecasts. A research-only heuristic path exists for shadow plumbing
and must not be used for candidate/champion serving.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from spy_der.contracts.common import ErrorCode, MissingInputError, ValidationError
from spy_der.contracts.forecasts import FEATURE_VERSION, LABEL_VERSION, MarketForecastBundle
from spy_der.training.registry import ModelRegistry, RegistryError

__all__ = [
    "ForecastServer",
    "ForecastServingError",
    "heuristic_bundle",
]


class ForecastServingError(RuntimeError):
    """Fail-closed serving error."""


def heuristic_bundle(
    *,
    snapshot_id: str,
    ts: str,
    session_date: str,
    symbol: str = "SPY",
    feature_coverage: float | None = None,
    data_quality: float | None = None,
    net_gex: float | None = None,
) -> MarketForecastBundle:
    """Research/shadow-only neutral heuristic. Not for candidate/champion modes."""
    tilt = 0.0
    if net_gex is not None:
        tilt = max(-0.05, min(0.05, float(net_gex) * 0.01))
    p = 0.5 + tilt
    return MarketForecastBundle(
        snapshot_id=snapshot_id,
        ts=ts,
        session_date=session_date,
        symbol=symbol,
        model_version="v2-heuristic-bundle-v1",
        model_group_id="heuristic",
        feature_version=FEATURE_VERSION,
        label_version=LABEL_VERSION,
        p_up_5m=p,
        p_up_15m=p,
        p_up_30m=p,
        p_up_60m=p,
        p_up_close=p,
        uncertainty=0.5,
        data_quality=data_quality,
        feature_coverage=feature_coverage,
        fallback_state="heuristic",
        model_versions={"group": "v2-heuristic-bundle-v1"},
        diagnostics={"mode": "heuristic"},
    )


@dataclass
class ForecastServer:
    """Load a registered model group and serve MarketForecastBundle fail-closed."""

    registry: ModelRegistry
    group_id: str
    load_mode: str = "shadow"
    required_input_fields: tuple[str, ...] = ()
    _models: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _metas: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _group_meta: Any = field(default=None, init=False, repr=False)

    def load(self) -> ForecastServer:
        try:
            group = self.registry.load_group(self.group_id)
            self.registry.validate_group(group, load_mode=self.load_mode)
        except RegistryError as exc:
            raise ForecastServingError(str(exc)) from exc
        if self.load_mode in {"candidate", "champion"} and group.status in {
            "research",
            "rejected",
            "archived",
        }:
            raise ForecastServingError(
                f"refusing {self.load_mode!r} serve from group status {group.status!r}"
            )
        models: dict[str, Any] = {}
        metas: dict[str, dict[str, Any]] = {}
        for role, mid in group.component_model_ids.items():
            try:
                model, meta = self.registry.load(
                    mid,
                    expected_feature_version=group.feature_version,
                    load_mode=self.load_mode,
                    required_input_fields=list(self.required_input_fields)
                    if self.required_input_fields
                    else None,
                )
            except RegistryError as exc:
                raise ForecastServingError(str(exc)) from exc
            models[role] = model
            metas[role] = meta
        self._models = models
        self._metas = metas
        self._group_meta = group
        return self

    def _require_loaded(self) -> None:
        if self._group_meta is None:
            raise ForecastServingError("ForecastServer.load() was not called")

    def predict(
        self,
        *,
        snapshot_id: str,
        ts: str,
        session_date: str,
        symbol: str,
        feature_row: Mapping[str, Any],
        data_quality: float | None = None,
        feature_coverage: float | None = None,
    ) -> MarketForecastBundle:
        self._require_loaded()
        if not snapshot_id or not ts or not session_date:
            raise MissingInputError("snapshot_id/ts/session_date")

        # Fail closed on missing required inputs declared by any component.
        required: set[str] = set(self.required_input_fields)
        for meta in self._metas.values():
            required.update(meta.get("required_input_fields") or [])
        missing = [name for name in sorted(required) if feature_row.get(name) is None]
        if missing:
            raise MissingInputError(",".join(missing))

        rows: Sequence[dict[str, Any]] = [dict(feature_row)]
        p_up_30m: float | None = None
        expected_return_30m: float | None = None
        q10 = q50 = q90 = None
        expected_move_30m: float | None = None
        p_range: float | None = None
        p_touch_call: float | None = None
        p_touch_put: float | None = None
        uncertainty: float | None = None
        versions: dict[str, str] = {"group": self.group_id}

        if "direction_30m" in self._models:
            model = self._models["direction_30m"]
            p_up_30m = float(model.predict_proba(rows)[0])
            versions["direction_30m"] = self._metas["direction_30m"]["model_id"]
        if "return_quantiles_30m" in self._models:
            model = self._models["return_quantiles_30m"]
            pred = model.predict(rows)
            q10 = float(pred["q10"][0])
            q50 = float(pred["q50"][0])
            q90 = float(pred["q90"][0])
            expected_return_30m = q50
            versions["return_quantiles_30m"] = self._metas["return_quantiles_30m"]["model_id"]
        if "volatility" in self._models:
            model = self._models["volatility"]
            pred = model.predict(rows)
            expected_move_30m = float(pred["expected_move"][0])
            uncertainty = float(pred["uncertainty"][0])
            versions["volatility"] = self._metas["volatility"]["model_id"]
        if "range_survive_close" in self._models:
            model = self._models["range_survive_close"]
            p_range = float(model.predict_proba(rows)[0])
            versions["range_survive_close"] = self._metas["range_survive_close"]["model_id"]
        if "touch_call_wall" in self._models:
            model = self._models["touch_call_wall"]
            p_touch_call = float(model.predict_proba(rows)[0])
            versions["touch_call_wall"] = self._metas["touch_call_wall"]["model_id"]
        if "touch_put_wall" in self._models:
            model = self._models["touch_put_wall"]
            p_touch_put = float(model.predict_proba(rows)[0])
            versions["touch_put_wall"] = self._metas["touch_put_wall"]["model_id"]

        if p_up_30m is None and expected_return_30m is None and expected_move_30m is None:
            raise ForecastServingError("model group produced no forecast components")

        try:
            return MarketForecastBundle(
                snapshot_id=snapshot_id,
                ts=ts,
                session_date=session_date,
                symbol=symbol,
                model_group_id=self.group_id,
                model_version=self.group_id,
                feature_version=self._group_meta.feature_version,
                label_version=self._group_meta.label_version,
                p_up_30m=p_up_30m,
                expected_return_30m=expected_return_30m,
                return_q10_30m=q10,
                return_q50_30m=q50,
                return_q90_30m=q90,
                expected_realized_move_30m=expected_move_30m,
                p_range_survive_close=p_range,
                p_touch_call_wall_30m=p_touch_call,
                p_touch_put_wall_30m=p_touch_put,
                uncertainty=uncertainty,
                data_quality=data_quality,
                feature_coverage=feature_coverage,
                model_versions=versions,
                fallback_state="",
                diagnostics={"load_mode": self.load_mode},
                artifact_hashes={
                    role: meta.get("artifact_hash", "") for role, meta in self._metas.items()
                },
            )
        except (ValidationError, ValueError) as exc:
            raise ForecastServingError(str(exc)) from exc


def assert_serving_inputs(feature_row: Mapping[str, Any], required: Sequence[str]) -> None:
    missing = [name for name in required if feature_row.get(name) is None]
    if missing:
        raise ValidationError(
            ErrorCode.MISSING_REQUIRED_INPUT,
            f"missing required forecast inputs: {missing}",
        )
