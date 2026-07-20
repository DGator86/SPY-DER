"""Stage-1 P(fill) model (System A fill_probability.py, simplified)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from spy_der.contracts.economics import FillRecord
from spy_der.forecasting.models.base import RANDOM_STATE, FeatureVectorizer, clip_probability

__all__ = ["FILL_PROBABILITY_VERSION", "FillProbabilityForecast", "FillProbabilityModel"]

FILL_PROBABILITY_VERSION = "fill-probability.v1"
DEFAULT_HORIZONS = (15.0, 30.0, 60.0, 120.0)


@dataclass(frozen=True, slots=True)
class FillProbabilityForecast:
    p_fill_15s: float
    p_fill_30s: float
    p_fill_60s: float
    p_fill_before_cancel: float
    calibration_support: int
    family_support: int
    uncertainty: float
    model_version: str = FILL_PROBABILITY_VERSION
    diagnostics: tuple[tuple[str, str], ...] = ()


def _features_from_record(rec: FillRecord) -> dict[str, Any]:
    return {
        "n_legs": float(rec.n_legs),
        "relative_spread": float(rec.relative_spread),
        "absolute_spread": float(rec.absolute_spread),
        "quote_age_seconds": float(rec.quote_age_seconds),
        "minutes_to_close": float(rec.minutes_to_close),
        "option_price_scale": float(rec.option_price_scale),
        "realized_volatility": (
            float(rec.realized_volatility) if rec.realized_volatility is not None else float("nan")
        ),
        "data_quality": float(rec.data_quality) if rec.data_quality is not None else float("nan"),
    }


def _horizon_label(rec: FillRecord, horizon_s: float) -> int:
    if not rec.filled:
        return 0
    t = rec.seconds_to_complete_fill
    if t is None:
        t = rec.seconds_to_first_fill
    if t is None:
        return 1
    return int(float(t) <= float(horizon_s))


def enforce_horizon_order(probs: list[float]) -> list[float]:
    out: list[float] = []
    running = 0.0
    for p in probs:
        running = max(running, float(p))
        out.append(min(max(running, 0.0), 1.0))
    return out


@dataclass
class FillProbabilityModel:
    horizons: tuple[float, ...] = DEFAULT_HORIZONS
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    models: dict[float, Any] = field(default_factory=dict)
    fitted: bool = False
    calibration_support: int = 0
    family_counts: dict[str, int] = field(default_factory=dict)
    model_version: str = FILL_PROBABILITY_VERSION

    def fit(self, records: list[FillRecord] | tuple[FillRecord, ...]) -> FillProbabilityModel:
        rows = [_features_from_record(r) for r in records]
        if not rows:
            raise ValueError("no fill records to fit")
        x = self.vectorizer.fit_transform(rows)
        self.family_counts = {}
        for rec in records:
            self.family_counts[rec.family] = self.family_counts.get(rec.family, 0) + 1
        self.calibration_support = len(records)
        self.models = {}
        for h in self.horizons:
            y = np.asarray([_horizon_label(r, h) for r in records], dtype=int)
            if len(np.unique(y)) < 2:
                self.models[h] = ("constant", float(np.mean(y)))
            else:
                pipe = Pipeline(
                    [
                        ("scaler", StandardScaler()),
                        (
                            "clf",
                            LogisticRegression(
                                max_iter=1000,
                                random_state=RANDOM_STATE,
                            ),
                        ),
                    ]
                )
                pipe.fit(x, y)
                self.models[h] = pipe
        self.fitted = True
        return self

    def predict(
        self,
        features: dict[str, Any],
        *,
        family: str | None = None,
        cancel_horizon_s: float = 120.0,
    ) -> FillProbabilityForecast:
        if not self.fitted:
            raise RuntimeError("FillProbabilityModel.predict before fit")
        x = self.vectorizer.transform([features])
        probs: list[float] = []
        for h in self.horizons:
            model = self.models[h]
            if isinstance(model, tuple) and model[0] == "constant":
                probs.append(float(model[1]))
            else:
                probs.append(float(model.predict_proba(x)[0, 1]))
        probs = list(clip_probability(enforce_horizon_order(probs)))
        # Map cancel horizon onto nearest trained horizon.
        nearest = min(self.horizons, key=lambda h: abs(h - cancel_horizon_s))
        idx = self.horizons.index(nearest)
        fam_support = self.family_counts.get(family or "", 0)
        unc = float(1.0 / (1.0 + self.calibration_support / 50.0))
        return FillProbabilityForecast(
            p_fill_15s=float(probs[0]) if len(probs) > 0 else 0.0,
            p_fill_30s=float(probs[1]) if len(probs) > 1 else float(probs[-1]),
            p_fill_60s=float(probs[2]) if len(probs) > 2 else float(probs[-1]),
            p_fill_before_cancel=float(probs[idx]),
            calibration_support=self.calibration_support,
            family_support=fam_support,
            uncertainty=unc,
            model_version=self.model_version,
        )
