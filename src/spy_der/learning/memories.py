"""Episodic lessons / memory store under /var/lib/spy-der."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "FailureEpisode",
    "Lesson",
    "append_failure_episode",
    "append_lesson",
    "load_failure_episodes",
    "load_lessons",
]


@dataclass(frozen=True, slots=True)
class Lesson:
    lesson_id: str
    text: str
    tags: tuple[str, ...]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "text": self.text,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class FailureEpisode:
    episode_id: str
    summary: str
    details: dict[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "summary": self.summary,
            "details": dict(self.details),
            "created_at": self.created_at.isoformat(),
        }


def _lessons_path(root: str | Path) -> Path:
    path = Path(root) / "lessons" / "lessons.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _episodes_path(root: str | Path) -> Path:
    path = Path(root) / "memories" / "failure_episodes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_lessons(state_root: str | Path) -> list[Lesson]:
    path = _lessons_path(state_root)
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        return []
    out: list[Lesson] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        created = datetime.fromisoformat(
            str(item.get("created_at") or datetime.now(UTC).isoformat()).replace(
                "Z", "+00:00"
            )
        )
        out.append(
            Lesson(
                lesson_id=str(item.get("lesson_id") or ""),
                text=str(item.get("text") or ""),
                tags=tuple(str(t) for t in (item.get("tags") or [])),
                created_at=created,
            )
        )
    return out


def append_lesson(
    state_root: str | Path,
    *,
    lesson_id: str,
    text: str,
    tags: tuple[str, ...] = (),
) -> Lesson:
    lesson = Lesson(
        lesson_id=lesson_id,
        text=text,
        tags=tags,
        created_at=datetime.now(UTC),
    )
    existing = [item.to_dict() for item in load_lessons(state_root)]
    # Replace same lesson_id to keep the store idempotent across Dojo reruns.
    existing = [row for row in existing if row.get("lesson_id") != lesson_id]
    existing.append(lesson.to_dict())
    path = _lessons_path(state_root)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return lesson


def load_failure_episodes(state_root: str | Path) -> list[FailureEpisode]:
    path = _episodes_path(state_root)
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        return []
    out: list[FailureEpisode] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        created = datetime.fromisoformat(
            str(item.get("created_at") or datetime.now(UTC).isoformat()).replace(
                "Z", "+00:00"
            )
        )
        raw_details = item.get("details")
        details: dict[str, Any] = (
            {str(k): v for k, v in raw_details.items()}
            if isinstance(raw_details, dict)
            else {}
        )
        out.append(
            FailureEpisode(
                episode_id=str(item.get("episode_id") or ""),
                summary=str(item.get("summary") or ""),
                details=details,
                created_at=created,
            )
        )
    return out


def append_failure_episode(
    state_root: str | Path,
    *,
    episode_id: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> FailureEpisode:
    episode = FailureEpisode(
        episode_id=episode_id,
        summary=summary,
        details=dict(details or {}),
        created_at=datetime.now(UTC),
    )
    existing = [item.to_dict() for item in load_failure_episodes(state_root)]
    existing = [row for row in existing if row.get("episode_id") != episode_id]
    existing.append(episode.to_dict())
    path = _episodes_path(state_root)
    path.write_text(json.dumps(existing, indent=2, default=str) + "\n", encoding="utf-8")
    return episode
