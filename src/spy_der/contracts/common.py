"""Common contract utilities (master spec §11, §12).

Timezone-aware and finite-number validation, deterministic/canonical JSON,
content hashing, deterministic content-addressed IDs, typed error codes, and a
provenance record. Everything here is pure and deterministic: no clocks, no
network, no randomness.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from spy_der.contracts.serialization import to_canonical_json

SCHEMA_VERSION = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "ContractError",
    "ErrorCode",
    "MissingInputError",
    "Provenance",
    "ValidationError",
    "content_hash",
    "deterministic_id",
    "require_finite",
    "require_non_negative",
    "require_probability",
    "require_tz_aware",
    "to_canonical_json",
]


class ErrorCode(StrEnum):
    """Typed, stable error codes for fail-closed handling (spec §7.5)."""

    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    PROBABILITY_OUT_OF_RANGE = "PROBABILITY_OUT_OF_RANGE"
    NEGATIVE_VALUE = "NEGATIVE_VALUE"
    MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"


class ContractError(ValueError):
    """Base class for contract validation failures. Carries a typed code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


class ValidationError(ContractError):
    """A value violated a contract invariant."""


class MissingInputError(ContractError):
    """A required input was absent. Never silently defaulted (spec §7.5)."""

    def __init__(self, field: str) -> None:
        super().__init__(
            ErrorCode.MISSING_REQUIRED_INPUT,
            f"required input '{field}' is missing",
        )


def require_tz_aware(value: datetime, field: str = "timestamp") -> datetime:
    """Return ``value`` if timezone-aware, else raise (spec §11)."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValidationError(
            ErrorCode.NAIVE_TIMESTAMP,
            f"'{field}' must be timezone-aware",
        )
    return value


def require_finite(value: float, field: str = "value") -> float:
    """Return ``value`` if finite, else raise (no NaN/inf in contracts)."""
    if not math.isfinite(value):
        raise ValidationError(
            ErrorCode.NON_FINITE_NUMBER,
            f"'{field}' must be finite, got {value!r}",
        )
    return value


def require_probability(value: float, field: str = "probability") -> float:
    """Return ``value`` if a finite probability in [0, 1], else raise."""
    require_finite(value, field)
    if not 0.0 <= value <= 1.0:
        raise ValidationError(
            ErrorCode.PROBABILITY_OUT_OF_RANGE,
            f"'{field}' must be in [0, 1], got {value!r}",
        )
    return value


def require_non_negative(value: float, field: str = "value") -> float:
    """Return ``value`` if finite and >= 0, else raise."""
    require_finite(value, field)
    if value < 0:
        raise ValidationError(
            ErrorCode.NEGATIVE_VALUE,
            f"'{field}' must be non-negative, got {value!r}",
        )
    return value


def content_hash(value: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``value`` (spec §11)."""
    encoded = to_canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def deterministic_id(prefix: str, *parts: Any) -> str:
    """Content-addressed, stable ID: ``<prefix>-<12 hex chars>``.

    The hash covers the canonical JSON of ``parts``, so identical inputs always
    yield the same ID regardless of process, host, or ordering of mappings.
    """
    digest = hashlib.sha256(to_canonical_json(list(parts)).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a contract instance came from (spec §11 provenance fields)."""

    schema_version: str = SCHEMA_VERSION
    code_version: str = ""
    configuration_hash: str = ""
    source_snapshot_id: str | None = None
