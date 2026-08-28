"""Fail-closed Beta-spy forecast witness for Alpha V2.

Beta is an independent market-forecast witness. Only its timestamped forecast
heads and data-coverage metadata are admissible here. Decision, option-plan,
ledger, sizing, and P&L fields are deliberately ignored so Beta cannot leak a
trading policy into the physical market forecast.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from spy_der.contracts.common import (
    ErrorCode,
    ValidationError,
    require_probability,
    require_tz_aware,
)

BETA_WITNESS_VERSION = "beta-spy@ac2ac8dae796"
_SUPPORTED_HORIZONS = frozenset({5, 15, 30})

__all__ = [
    "BETA_WITNESS_VERSION",
    "BetaHorizonWitness",
    "BetaStateClient",
    "BetaWitnessError",
    "BetaWitnessSnapshot",
    "parse_beta_state",
]


class BetaWitnessError(ValueError):
    """Beta state was unavailable or unsuitable for forecast use."""


@dataclass(frozen=True, slots=True)
class BetaHorizonWitness:
    """One Beta physical-market forecast head.

    ``expected_return`` is a decimal simple return. Beta publishes basis
    points, so the adapter performs the unit conversion at this boundary.
    """

    horizon_minutes: int
    probability_up: float
    expected_return: float
    confidence: float
    model_ready: bool
    sample_count: int

    def __post_init__(self) -> None:
        if self.horizon_minutes not in _SUPPORTED_HORIZONS:
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                f"unsupported Beta horizon {self.horizon_minutes}",
            )
        require_probability(self.probability_up, "beta.probability_up")
        require_probability(self.confidence, "beta.confidence")
        if not math.isfinite(self.expected_return):
            raise ValidationError(
                ErrorCode.NON_FINITE_NUMBER,
                "beta.expected_return must be finite",
            )
        if self.sample_count < 0:
            raise ValidationError(
                ErrorCode.NEGATIVE_VALUE,
                "beta.sample_count must be non-negative",
            )

    @property
    def eligible(self) -> bool:
        """Whether this head may be considered by an ensemble."""
        return self.model_ready and self.sample_count > 0


@dataclass(frozen=True, slots=True)
class BetaWitnessSnapshot:
    """Point-in-time Beta witness snapshot with explicit freshness/coverage."""

    source_timestamp: datetime
    status: str
    stale_seconds: float
    coverage_ratio: float
    covered_weight: float
    horizons: tuple[BetaHorizonWitness, ...]
    source_version: str = BETA_WITNESS_VERSION

    def __post_init__(self) -> None:
        require_tz_aware(self.source_timestamp, "beta.source_timestamp")
        require_probability(self.coverage_ratio, "beta.coverage_ratio")
        require_probability(self.covered_weight, "beta.covered_weight")
        if not math.isfinite(self.stale_seconds) or self.stale_seconds < 0:
            raise ValidationError(
                ErrorCode.NEGATIVE_VALUE,
                "beta.stale_seconds must be finite and non-negative",
            )
        horizon_values = [item.horizon_minutes for item in self.horizons]
        if len(horizon_values) != len(set(horizon_values)):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "Beta witness contains duplicate horizons",
            )

    def horizon(self, horizon_minutes: int) -> BetaHorizonWitness | None:
        """Return an eligible horizon, otherwise explicit missingness."""
        for item in self.horizons:
            if item.horizon_minutes == horizon_minutes:
                return item if item.eligible else None
        return None


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BetaWitnessError(f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BetaWitnessError(f"{field} is not a valid ISO timestamp") from exc
    try:
        require_tz_aware(parsed, field)
    except ValidationError as exc:
        raise BetaWitnessError(str(exc)) from exc
    return parsed


def _finite_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BetaWitnessError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise BetaWitnessError(f"{field} must be finite")
    return result


def parse_beta_state(
    payload: Any,
    *,
    as_of: datetime,
    max_age_seconds: float = 90.0,
    minimum_coverage_ratio: float = 0.90,
    minimum_covered_weight: float = 0.90,
    future_tolerance_seconds: float = 5.0,
) -> BetaWitnessSnapshot:
    """Validate Beta ``/api/state`` and project it into forecast-only evidence.

    The parser intentionally never reads ``decision``, ``option_plan`` or
    ``ledger``. A change in those downstream fields cannot change the returned
    witness object.
    """

    require_tz_aware(as_of, "as_of")
    if not isinstance(payload, dict):
        raise BetaWitnessError("Beta state must be a JSON object")

    status = str(payload.get("status") or "").upper()
    if status != "LIVE":
        raise BetaWitnessError(f"Beta status is not LIVE: {status or 'missing'}")

    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise BetaWitnessError("Beta snapshot is missing")

    source_timestamp = _parse_timestamp(
        snapshot.get("timestamp") or payload.get("timestamp"),
        "beta.timestamp",
    )
    age = (as_of - source_timestamp).total_seconds()
    if age < -float(future_tolerance_seconds):
        raise BetaWitnessError(
            f"Beta timestamp is {abs(age):.1f}s in the future relative to as_of"
        )
    stale_seconds = max(0.0, age)
    if stale_seconds > float(max_age_seconds):
        raise BetaWitnessError(
            f"Beta state is stale by {stale_seconds:.1f}s (max {max_age_seconds:.1f}s)"
        )

    factors = snapshot.get("factors")
    if not isinstance(factors, dict):
        raise BetaWitnessError("Beta factors are missing")
    coverage_ratio = _finite_float(factors.get("coverage_ratio"), "beta.coverage_ratio")
    covered_weight = _finite_float(factors.get("covered_weight"), "beta.covered_weight")
    try:
        require_probability(coverage_ratio, "beta.coverage_ratio")
        require_probability(covered_weight, "beta.covered_weight")
    except ValidationError as exc:
        raise BetaWitnessError(str(exc)) from exc
    if coverage_ratio < minimum_coverage_ratio:
        raise BetaWitnessError(
            f"Beta coverage ratio {coverage_ratio:.3f} < {minimum_coverage_ratio:.3f}"
        )
    if covered_weight < minimum_covered_weight:
        raise BetaWitnessError(
            f"Beta covered weight {covered_weight:.3f} < {minimum_covered_weight:.3f}"
        )

    rows = snapshot.get("forecasts")
    if not isinstance(rows, list):
        raise BetaWitnessError("Beta forecasts are missing")

    horizons: list[BetaHorizonWitness] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            horizon = int(row.get("horizon_minutes"))
        except (TypeError, ValueError):
            continue
        if horizon not in _SUPPORTED_HORIZONS:
            continue
        if horizon in seen:
            raise BetaWitnessError(f"duplicate Beta horizon {horizon}")
        seen.add(horizon)

        probability_up = _finite_float(
            row.get("probability_up"),
            f"beta.{horizon}m.probability_up",
        )
        confidence = _finite_float(
            row.get("confidence"),
            f"beta.{horizon}m.confidence",
        )
        expected_return_bps = _finite_float(
            row.get("expected_return_bps"),
            f"beta.{horizon}m.expected_return_bps",
        )
        try:
            require_probability(probability_up, f"beta.{horizon}m.probability_up")
            require_probability(confidence, f"beta.{horizon}m.confidence")
        except ValidationError as exc:
            raise BetaWitnessError(str(exc)) from exc
        try:
            sample_count = int(row.get("sample_count"))
        except (TypeError, ValueError) as exc:
            raise BetaWitnessError(
                f"beta.{horizon}m.sample_count must be an integer"
            ) from exc

        horizons.append(
            BetaHorizonWitness(
                horizon_minutes=horizon,
                probability_up=probability_up,
                expected_return=expected_return_bps / 10_000.0,
                confidence=confidence,
                model_ready=bool(row.get("model_ready")),
                sample_count=sample_count,
            )
        )

    if not horizons:
        raise BetaWitnessError("Beta has no supported forecast horizons")

    return BetaWitnessSnapshot(
        source_timestamp=source_timestamp,
        status=status,
        stale_seconds=stale_seconds,
        coverage_ratio=coverage_ratio,
        covered_weight=covered_weight,
        horizons=tuple(sorted(horizons, key=lambda item: item.horizon_minutes)),
    )


@dataclass(frozen=True, slots=True)
class BetaStateClient:
    """Minimal stdlib client for Beta-spy's read-only ``/api/state`` endpoint."""

    base_url: str
    timeout_seconds: float = 2.0
    max_age_seconds: float = 90.0
    minimum_coverage_ratio: float = 0.90
    minimum_covered_weight: float = 0.90

    @property
    def state_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/api/state") else f"{base}/api/state"

    def fetch(self, *, as_of: datetime) -> BetaWitnessSnapshot:
        request = urllib.request.Request(
            self.state_url,
            headers={"Accept": "application/json", "User-Agent": "SPY-DER/BetaWitness"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise BetaWitnessError(f"unable to fetch Beta state: {exc}") from exc
        try:
            payload: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BetaWitnessError("Beta state response is not valid JSON") from exc
        return parse_beta_state(
            payload,
            as_of=as_of,
            max_age_seconds=self.max_age_seconds,
            minimum_coverage_ratio=self.minimum_coverage_ratio,
            minimum_covered_weight=self.minimum_covered_weight,
        )
