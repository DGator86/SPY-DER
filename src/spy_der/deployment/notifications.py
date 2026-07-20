"""Operational notifications (in-process sink; no external network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

__all__ = ["Notification", "NotificationBus", "NotificationLevel"]


class NotificationLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Notification:
    level: NotificationLevel
    topic: str
    message: str
    created_at: datetime
    payload: tuple[tuple[str, str], ...] = ()


@dataclass
class NotificationBus:
    _messages: list[Notification] = field(default_factory=list)

    def publish(
        self,
        *,
        level: NotificationLevel,
        topic: str,
        message: str,
        payload: tuple[tuple[str, str], ...] = (),
        now: datetime | None = None,
    ) -> Notification:
        note = Notification(
            level=level,
            topic=topic,
            message=message,
            created_at=now or datetime.now(tz=UTC),
            payload=payload,
        )
        self._messages.append(note)
        return note

    def history(self, *, topic: str | None = None) -> tuple[Notification, ...]:
        if topic is None:
            return tuple(self._messages)
        return tuple(n for n in self._messages if n.topic == topic)
