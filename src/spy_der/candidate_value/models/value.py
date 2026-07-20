"""Candidate-value model (System A candidate_value.py, Phase-8 bounded port)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
from sklearn.linear_model import HuberRegressor, LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from spy_der.candidate_value.utility import UtilityConfig, candidate_utility
from spy_der.contracts.candidates import Candidate
from spy_der.contracts.economics import CandidateEconomics
from spy_der.contracts.value import CANDIDATE_VALUE_VERSION, CandidateValueForecast
from spy_der.forecasting.models.base import RANDOM_STATE, FeatureVectorizer, clip_probability

__all__ = ["CandidateValueConfig", "CandidateValueModel", "build_feature_row"]


@dataclass
class CandidateValueConfig:
    random_state: int = RANDOM_STATE
    model_id: str = CANDIDATE_VALUE_VERSION


def build_feature_row(
    candidate: Candidate,
    economics: CandidateEconomics,
    *,
    market_features: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Leakage-safe features from candidate + economics (+ optional market)."""
    row: dict[str, Any] = {
        "n_legs": float(len(candidate.legs)),
        "maximum_loss": float(candidate.maximum_loss),
        "capital_required": float(candidate.capital_required),
        "entry_credit": float(candidate.entry_credit),
        "quote_quality": float(candidate.quote_quality),
        "fill_probability": float(economics.fill_probability),
        "expected_fill_fraction": float(economics.expected_fill_fraction),
        "fees": float(economics.fees),
        "entry_slippage": float(economics.entry_slippage),
        "exit_slippage": float(economics.exit_slippage),
        "liquidity_score": float(economics.liquidity_score),
        "data_quality_penalty": float(economics.data_quality_penalty),
        "mid_price": (
            float(economics.mid_price)
            if economics.mid_price is not None
            else float("nan")
        ),
        "natural_price": (
            float(economics.natural_price)
            if economics.natural_price is not None
            else float("nan")
        ),
        "expected_fill_price": (
            float(economics.expected_fill_price)
            if economics.expected_fill_price is not None
            else float("nan")
        ),
    }
    if market_features:
        for key, value in market_features.items():
            if key.startswith(("realized_", "future_", "label_", "human_")):
                continue
            row[f"m_{key}"] = float(value)
    return row


@dataclass
class CandidateValueModel:
    config: CandidateValueConfig = field(default_factory=CandidateValueConfig)
    vectorizer: FeatureVectorizer = field(default_factory=FeatureVectorizer)
    pnl_estimator: Any = None
    profit_estimator: Any = None
    fitted: bool = False
    mean_pnl: float = 0.0
    base_profit: float = 0.5
    model_uncertainty: float = 0.5
    y_pnl_train: np.ndarray | None = None

    def fit(
        self,
        rows: list[dict[str, Any]],
        y_pnl: list[float] | np.ndarray,
        y_profit: list[int] | np.ndarray,
    ) -> CandidateValueModel:
        if not rows:
            raise ValueError("no candidate rows to fit")
        x = self.vectorizer.fit_transform(rows)
        y_p = np.asarray(y_pnl, dtype=float)
        y_b = np.asarray(y_profit, dtype=int)
        self.y_pnl_train = y_p
        self.mean_pnl = float(np.mean(y_p)) if len(y_p) else 0.0
        self.pnl_estimator = HuberRegressor(epsilon=1.35, max_iter=500)
        self.pnl_estimator.fit(x, y_p)
        if len(np.unique(y_b)) < 2:
            self.profit_estimator = ("constant", float(np.mean(y_b)))
            self.base_profit = float(np.mean(y_b))
        else:
            pipe = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "clf",
                        LogisticRegression(
                            max_iter=1000,
                            random_state=self.config.random_state,
                        ),
                    ),
                ]
            )
            pipe.fit(x, y_b)
            self.profit_estimator = pipe
            self.base_profit = float(np.mean(y_b))
        self.model_uncertainty = float(1.0 / (1.0 + len(rows) / 50.0))
        self.fitted = True
        return self

    def predict_one(
        self,
        row: dict[str, Any],
        *,
        candidate: Candidate,
        economics: CandidateEconomics,
        ood_score: float = 0.0,
        utility_cfg: UtilityConfig | None = None,
    ) -> CandidateValueForecast:
        if not self.fitted or self.pnl_estimator is None or self.y_pnl_train is None:
            raise RuntimeError("CandidateValueModel.predict before fit")
        x = self.vectorizer.transform([row])
        ev = float(self.pnl_estimator.predict(x)[0])
        if isinstance(self.profit_estimator, tuple):
            p_profit = float(self.profit_estimator[1])
        else:
            p_profit = float(self.profit_estimator.predict_proba(x)[0, 1])
        p_profit = float(clip_probability([p_profit])[0])
        q05, q10, q25, q50, q75, q90, q95 = (
            float(v) for v in np.quantile(self.y_pnl_train, [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95])
        )
        # Shift empirical quantiles around predicted mean.
        shift = ev - float(np.mean(self.y_pnl_train))
        qs = [q + shift for q in (q05, q10, q25, q50, q75, q90, q95)]
        qs = sorted(qs)
        shortfall = max(-(qs[0]), 0.0)
        forecast = CandidateValueForecast(
            candidate_id=candidate.candidate_id,
            model_id=self.config.model_id,
            expected_net_pnl=Decimal(str(round(ev, 8))),
            p_positive_net_pnl=p_profit,
            p_positive_utility=p_profit,
            pnl_q05=Decimal(str(round(qs[0], 8))),
            pnl_q10=Decimal(str(round(qs[1], 8))),
            pnl_q25=Decimal(str(round(qs[2], 8))),
            pnl_q50=Decimal(str(round(qs[3], 8))),
            pnl_q75=Decimal(str(round(qs[4], 8))),
            pnl_q90=Decimal(str(round(qs[5], 8))),
            pnl_q95=Decimal(str(round(qs[6], 8))),
            expected_shortfall=Decimal(str(round(shortfall, 8))),
            fill_probability=economics.fill_probability,
            fill_concession=Decimal(str(economics.expected_fill_fraction)),
            model_uncertainty=self.model_uncertainty,
            forecast_uncertainty=self.model_uncertainty,
            execution_uncertainty=float(1.0 - economics.fill_probability),
            ood_score=float(ood_score),
            capital_required=candidate.capital_required,
            maximum_loss=candidate.maximum_loss,
        )
        util = candidate_utility(forecast, capital=candidate.capital_required, cfg=utility_cfg)
        return CandidateValueForecast(
            candidate_id=forecast.candidate_id,
            model_id=forecast.model_id,
            expected_net_pnl=forecast.expected_net_pnl,
            p_positive_net_pnl=forecast.p_positive_net_pnl,
            p_positive_utility=forecast.p_positive_utility,
            pnl_q05=forecast.pnl_q05,
            pnl_q10=forecast.pnl_q10,
            pnl_q25=forecast.pnl_q25,
            pnl_q50=forecast.pnl_q50,
            pnl_q75=forecast.pnl_q75,
            pnl_q90=forecast.pnl_q90,
            pnl_q95=forecast.pnl_q95,
            expected_shortfall=forecast.expected_shortfall,
            fill_probability=forecast.fill_probability,
            fill_concession=forecast.fill_concession,
            model_uncertainty=forecast.model_uncertainty,
            forecast_uncertainty=forecast.forecast_uncertainty,
            execution_uncertainty=forecast.execution_uncertainty,
            ood_score=forecast.ood_score,
            utility=float(util),
            capital_required=forecast.capital_required,
            maximum_loss=forecast.maximum_loss,
        )
