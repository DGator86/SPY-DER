"""Append-only journal event contracts (master spec §53)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from spy_der.contracts.common import content_hash
from spy_der.contracts.serialization import to_canonical_json

__all__ = [
    "JOURNAL_SCHEMA",
    "AggregateType",
    "JournalEvent",
    "JournalEventType",
    "event_content_hash",
]

JOURNAL_SCHEMA = "journal.event.v1"


class JournalEventType(StrEnum):
    SNAPSHOT_CREATED = "snapshot_created"
    SNAPSHOT_REJECTED = "snapshot_rejected"
    FEATURES_COMPUTED = "features_computed"
    FEATURE_STAGE_FAILED = "feature_stage_failed"
    STRUCTURAL_STATE_CREATED = "structural_state_created"
    LEGACY_POLICY_EVALUATED = "legacy_policy_evaluated"
    FORECAST_GENERATED = "forecast_generated"
    FORECAST_UNAVAILABLE = "forecast_unavailable"
    CANDIDATES_GENERATED = "candidates_generated"
    CANDIDATE_REJECTED = "candidate_rejected"
    ECONOMICS_CALCULATED = "economics_calculated"
    CANDIDATE_VALUE_GENERATED = "candidate_value_generated"
    POLICY_EVALUATED = "policy_evaluated"
    DECISION_PACKET_CREATED = "decision_packet_created"
    AGENT_REQUESTED = "agent_requested"
    AGENT_DECIDED = "agent_decided"
    AGENT_FAILED = "agent_failed"
    SYSTEM_DECIDED = "system_decided"
    RISK_EVALUATED = "risk_evaluated"
    ORDER_INTENT_CREATED = "order_intent_created"
    ORDER_SUBMITTED_SIMULATED = "order_submitted_simulated"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELED = "order_canceled"
    ORDER_REJECTED = "order_rejected"
    POSITION_OPENED = "position_opened"
    POSITION_MARKED = "position_marked"
    POSITION_REDUCED = "position_reduced"
    POSITION_CLOSED = "position_closed"
    OUTCOME_SETTLED = "outcome_settled"
    COUNTERFACTUAL_SETTLED = "counterfactual_settled"
    MODEL_DRIFT_CHANGED = "model_drift_changed"
    PROMOTION_REVIEWED = "promotion_reviewed"
    DEPLOYMENT_CHANGED = "deployment_changed"
    DEPLOYMENT_ROLLED_BACK = "deployment_rolled_back"
    SYSTEM_FAILURE = "system_failure"
    # Replay compatibility types.
    REPLAY_START = "replay.start"
    REPLAY_END = "replay.end"


class AggregateType(StrEnum):
    SYSTEM = "system"
    SNAPSHOT = "snapshot"
    ORDER = "order"
    POSITION = "position"
    SESSION = "session"
    DEPLOYMENT = "deployment"
    CANDIDATE = "candidate"


def event_content_hash(
    *,
    event_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    sequence_number: int,
    occurred_at: datetime,
    recorded_at: datetime,
    schema_version: str,
    payload_hash: str,
    previous_event_hash: str | None,
    deployment_id: str,
    snapshot_id: str | None,
    correlation_id: str,
    causation_id: str | None,
) -> str:
    return content_hash(
        {
            "event_id": event_id,
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "sequence_number": sequence_number,
            "occurred_at": occurred_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "schema_version": schema_version,
            "payload_hash": payload_hash,
            "previous_event_hash": previous_event_hash,
            "deployment_id": deployment_id,
            "snapshot_id": snapshot_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
        }
    )


@dataclass(frozen=True, slots=True)
class JournalEvent:
    """Spec §53 journal event with Phase-0 timestamp/payload_json compatibility."""

    event_id: str = ""
    event_type: str = ""
    aggregate_type: str = AggregateType.SYSTEM.value
    aggregate_id: str = "default"
    sequence_number: int = 0
    occurred_at: datetime | None = None
    recorded_at: datetime | None = None
    schema_version: str = JOURNAL_SCHEMA
    payload: Mapping[str, Any] = field(default_factory=dict)
    payload_hash: str = ""
    previous_event_hash: str | None = None
    event_hash: str = ""
    deployment_id: str = ""
    snapshot_id: str | None = None
    correlation_id: str = ""
    causation_id: str | None = None
    # Compatibility fields used by early replay helpers.
    timestamp: datetime | None = None
    payload_json: str = "{}"

    def __post_init__(self) -> None:
        occurred = self.occurred_at or self.timestamp or datetime.now(tz=UTC)
        recorded = self.recorded_at or occurred
        if occurred.tzinfo is None or recorded.tzinfo is None:
            raise ValueError("journal timestamps must be timezone-aware")

        payload: Mapping[str, Any]
        if self.payload and self.payload_json in {"", "{}"}:
            payload = dict(self.payload)
            payload_json = to_canonical_json(payload)
        elif self.payload_json and self.payload_json != "{}" and not self.payload:
            parsed = json.loads(self.payload_json)
            if not isinstance(parsed, dict):
                raise ValueError("payload_json must decode to an object")
            payload = parsed
            payload_json = self.payload_json
        else:
            payload = dict(self.payload)
            payload_json = (
                self.payload_json
                if self.payload_json and self.payload_json != "{}"
                else to_canonical_json(payload)
            )

        p_hash = self.payload_hash or content_hash(payload)
        object.__setattr__(self, "occurred_at", occurred)
        object.__setattr__(self, "recorded_at", recorded)
        object.__setattr__(self, "timestamp", occurred)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "payload_json", payload_json)
        object.__setattr__(self, "payload_hash", p_hash)
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", self.event_id or "uncorrelated")
