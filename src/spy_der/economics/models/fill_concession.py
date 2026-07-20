"""Stage-2 E(concession|filled) model (System A fill_concession.py, simplified)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import HuberRegressor

from spy_der.contracts.economics import FillRecord
from spy_der.economics.fill_prior import base_fill_fraction
from spy_der.economics.fill_training import blend_with_prior
from spy_der.execution.fill_records import fill_fraction
from spy_der.forecasting.models.base import FeatureVectorizer

__all__ = ["FILL_CONCESSION_VERSION", "FillConcessionForecast", "FillConcessionModel"]

FILL_CONCESSION_VERSION = "fill-concession.v1"


@dataclass(frozen=True, slots=True)
class FillConcessionForecast:
    expected_fill_fraction: float
    fill_q10: float
    fill_q50: float
    fill_q90: float
    conservative_fill_fraction: float
    support_rows: int
    support_sessions: int
    family_support: int
    uncertainty: float
    model_version: str = FILL_CONCESSION_VERSION
    diagnostics: tuple[tuple[str, str], ...] = ()


def _features_from_record(rec: FillRecord) -> dict[str, Any]:
    return {
        "n_legs": float(rec.n_legs),
        "relative_spread": float(rec.relative_spread),
        "absolute_spread": float(rec.absolute_spread),
        "quote_age_seconds": float(rec.quote_age_seconds),
        "minutes_to_close": float(rec.minutes_to_close),
        "option_price_scale": float(rec.option_price_scale),
    }


def _ordered_quantiles(q10: float, q50: float, q90: float, expected: float) -> tuple[float, ...]:
    qs = sorted([float(q10), float(q50), float(q90)])
    exp = min(max(float(expected), qs[0]), qs[2])
    return qs[0], qs[1], qs[2], exp


@dataclass
class FillConcessionModel:
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    expected_model: Any = None
    fitted: bool = False
    support_rows: int = 0
    support_sessions: int = 0
    family_counts: dict[str, int] = field(default_factory=dict)
    y_train: np.ndarray | None = None
    model_version: str = FILL_CONCESSION_VERSION

    def fit(self, records: list[FillRecord] | tuple[FillRecord, ...]) -> FillConcessionModel:
        filled = [r for r in records if r.filled and r.fill_credit is not None]
        if not filled:
            raise ValueError("no filled records for concession model")
        rows = [_features_from_record(r) for r in filled]
        y: list[float] = []
        for rec in filled:
            assert rec.fill_credit is not None
            _, clipped = fill_fraction(
                rec.mid_credit_at_submit,
                rec.natural_credit_at_submit,
                rec.fill_credit,
            )
            y.append(clipped)
        x = self.vectorizer.fit_transform(rows)
        self.y_train = np.asarray(y, dtype=float)
        self.expected_model = HuberRegressor(epsilon=1.35, max_iter=500)
        self.expected_model.fit(x, self.y_train)
        self.support_rows = len(filled)
        self.support_sessions = len({r.session_date for r in filled})
        self.family_counts = {}
        for rec in filled:
            self.family_counts[rec.family] = self.family_counts.get(rec.family, 0) + 1
        self.fitted = True
        return self

    def predict(
        self,
        features: dict[str, Any],
        *,
        family: str = "",
        n_legs: int = 2,
    ) -> FillConcessionForecast:
        if not self.fitted or self.expected_model is None or self.y_train is None:
            raise RuntimeError("FillConcessionModel.predict before fit")
        x = self.vectorizer.transform([features])
        emp = float(np.clip(self.expected_model.predict(x)[0], 0.0, 1.0))
        prior = base_fill_fraction(family, n_legs)
        blended, weight = blend_with_prior(emp, prior, self.support_rows)
        q10, q50, q90 = np.quantile(self.y_train, [0.1, 0.5, 0.9])
        q10, q50, q90, expected = _ordered_quantiles(float(q10), float(q50), float(q90), blended)
        cons = min(max(expected + 0.25, 0.0), 1.0)
        unc = float(1.0 / (1.0 + self.support_rows / 50.0))
        return FillConcessionForecast(
            expected_fill_fraction=float(expected),
            fill_q10=float(q10),
            fill_q50=float(q50),
            fill_q90=float(q90),
            conservative_fill_fraction=float(cons),
            support_rows=self.support_rows,
            support_sessions=self.support_sessions,
            family_support=self.family_counts.get(family, 0),
            uncertainty=unc,
            diagnostics=(("empirical_weight", str(weight)), ("prior", str(prior))),
        )
