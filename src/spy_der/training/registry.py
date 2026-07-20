"""Fail-closed model registry (master spec §24 / System A prediction/registry.py).

Schema version 2 requires calibration/fold/OOF audit fields. Artifact SHA-256
must match metadata on load. Status gates which serving modes are allowed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

import joblib

__all__ = [
    "ALLOWED_MODES",
    "SCHEMA_VERSION",
    "STATUSES",
    "ModelGroupMetadata",
    "ModelRegistry",
    "RegistryError",
]

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
STATUSES = (
    "research",
    "shadow",
    "candidate",
    "pending_review",
    "champion",
    "rejected",
    "archived",
)

ALLOWED_MODES: dict[str, frozenset[str]] = {
    "research": frozenset({"research"}),
    "shadow": frozenset({"research", "shadow"}),
    "candidate": frozenset({"research", "shadow", "candidate"}),
    "pending_review": frozenset({"research", "shadow"}),
    "champion": frozenset({"research", "shadow", "candidate", "champion"}),
    "rejected": frozenset({"research"}),
    "archived": frozenset({"research"}),
}

_V2_REQUIRED = (
    "label_version",
    "crossfit_config",
    "fold_hash",
    "oof_metrics",
    "calibration_artifact",
    "uncertainty_method",
    "training_feature_distribution_hash",
    "required_input_fields",
    "dependency_versions",
    "git_commit",
)


class RegistryError(RuntimeError):
    """Fail-closed load/save error — never serve a questionable artifact."""


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _atomic_write_bytes(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".reg_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _atomic_write_text(path: str, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _validate_v2_metadata(meta: dict[str, Any]) -> None:
    mid = meta.get("model_id", "?")
    for key in _V2_REQUIRED:
        if key not in meta or meta[key] is None:
            raise RegistryError(f"v2 metadata missing required field {key!r} for {mid!r}")
    if not isinstance(meta.get("crossfit_config"), dict):
        raise RegistryError(f"malformed crossfit_config for {mid!r}")
    if not isinstance(meta.get("oof_metrics"), dict):
        raise RegistryError(f"missing OOF metrics for {mid!r}")
    cal = meta.get("calibration_artifact")
    if not isinstance(cal, dict) or not cal.get("method"):
        raise RegistryError(f"calibration artifact missing for {mid!r}")
    if not meta.get("fold_hash"):
        raise RegistryError(f"fold metadata malformed for {mid!r}")


@dataclass(frozen=True, slots=True)
class ModelGroupMetadata:
    group_id: str
    component_model_ids: dict[str, str]
    feature_version: str
    label_version: str
    structural_state_version: str = ""
    configuration_hash: str = ""
    training_sessions: tuple[str, ...] = ()
    calibration_sessions: tuple[str, ...] = ()
    outer_test_sessions: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    status: str = "research"

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "component_model_ids": dict(self.component_model_ids),
            "feature_version": self.feature_version,
            "label_version": self.label_version,
            "structural_state_version": self.structural_state_version,
            "configuration_hash": self.configuration_hash,
            "training_sessions": list(self.training_sessions),
            "calibration_sessions": list(self.calibration_sessions),
            "outer_test_sessions": list(self.outer_test_sessions),
            "metrics": dict(self.metrics),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelGroupMetadata:
        return cls(
            group_id=str(data["group_id"]),
            component_model_ids=dict(data.get("component_model_ids") or {}),
            feature_version=str(data["feature_version"]),
            label_version=str(data["label_version"]),
            structural_state_version=str(data.get("structural_state_version") or ""),
            configuration_hash=str(data.get("configuration_hash") or ""),
            training_sessions=tuple(data.get("training_sessions") or ()),
            calibration_sessions=tuple(data.get("calibration_sessions") or ()),
            outer_test_sessions=tuple(data.get("outer_test_sessions") or ()),
            metrics=dict(data.get("metrics") or {}),
            status=str(data.get("status") or "research"),
        )


@dataclass
class ModelRegistry:
    directory: str = "models"

    def __post_init__(self) -> None:
        os.makedirs(self.directory, exist_ok=True)
        os.makedirs(os.path.join(self.directory, "groups"), exist_ok=True)

    def _artifact_path(self, model_id: str) -> str:
        return os.path.join(self.directory, f"{model_id}.joblib")

    def _meta_path(self, model_id: str) -> str:
        return os.path.join(self.directory, f"{model_id}.json")

    def _group_path(self, group_id: str) -> str:
        return os.path.join(self.directory, "groups", f"{group_id}.json")

    def save(
        self,
        model: Any,
        *,
        model_type: str,
        target: str,
        horizon: str | None,
        feature_version: str,
        label_version: str,
        crossfit_config: dict[str, Any],
        fold_hash: str,
        oof_metrics: dict[str, Any],
        calibration_artifact: dict[str, Any],
        uncertainty_method: str,
        training_feature_distribution_hash: str,
        required_input_fields: list[str],
        dependency_versions: dict[str, str],
        git_commit: str,
        hyperparameters: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        training_sessions: list[str] | None = None,
        calibration_sessions: list[str] | None = None,
        data_hash: str | None = None,
        author: str = "",
        status: str = "research",
        optional_input_fields: list[str] | None = None,
        feature_schema_hash: str | None = None,
        model_id: str | None = None,
    ) -> str:
        if status not in STATUSES:
            raise RegistryError(f"invalid status {status!r}")
        cfg_hash = _config_hash(hyperparameters or {})[:12]
        mid = model_id or f"{model_type}-{target}-{horizon or 'na'}-{cfg_hash}"
        artifact_path = self._artifact_path(mid)
        joblib.dump(model, artifact_path)
        artifact_hash = _sha256_file(artifact_path)
        meta: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "model_id": mid,
            "model_type": model_type,
            "target": target,
            "horizon": horizon,
            "feature_version": feature_version,
            "feature_schema_hash": feature_schema_hash or "",
            "label_version": label_version,
            "training_sessions": list(training_sessions or []),
            "calibration_sessions": list(calibration_sessions or []),
            "data_hash": data_hash or "",
            "configuration_hash": _config_hash(hyperparameters or {}),
            "hyperparameters": hyperparameters or {},
            "metrics": metrics or {},
            "crossfit_config": crossfit_config,
            "fold_hash": fold_hash,
            "oof_metrics": oof_metrics,
            "calibration_artifact": calibration_artifact,
            "uncertainty_method": uncertainty_method,
            "training_feature_distribution_hash": training_feature_distribution_hash,
            "required_input_fields": list(required_input_fields),
            "optional_input_fields": list(optional_input_fields or []),
            "dependency_versions": dependency_versions,
            "git_commit": git_commit,
            "artifact_path": artifact_path,
            "artifact_hash": artifact_hash,
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "author": author,
            "status": status,
            "status_history": [{"status": status, "at": dt.datetime.now(dt.UTC).isoformat()}],
        }
        _validate_v2_metadata(meta)
        _atomic_write_text(self._meta_path(mid), json.dumps(meta, indent=2, sort_keys=True))
        return mid

    def load_metadata(self, model_id: str, *, validate_v2: bool = True) -> dict[str, Any]:
        path = self._meta_path(model_id)
        if not os.path.exists(path):
            raise RegistryError(f"metadata missing for {model_id!r}")
        try:
            with open(path, encoding="utf-8") as handle:
                loaded: dict[str, Any] = json.load(handle)
        except json.JSONDecodeError as exc:
            raise RegistryError(f"unreadable metadata for {model_id!r}") from exc
        schema = loaded.get("schema_version")
        if schema not in SUPPORTED_SCHEMA_VERSIONS:
            raise RegistryError(f"unsupported schema version {schema!r} for {model_id!r}")
        if validate_v2 and int(schema) >= 2:
            _validate_v2_metadata(loaded)
        return loaded

    def load(
        self,
        model_id: str,
        *,
        expected_feature_version: str | None = None,
        expected_target: str | None = None,
        expected_horizon: str | None = None,
        required_input_fields: list[str] | None = None,
        load_mode: str = "research",
    ) -> tuple[Any, dict[str, Any]]:
        meta = self.load_metadata(model_id, validate_v2=True)
        status = meta.get("status", "research")
        allowed = ALLOWED_MODES.get(status, frozenset())
        if load_mode not in allowed:
            raise RegistryError(
                f"status {status!r} does not allow load_mode {load_mode!r} for {model_id!r}"
            )
        artifact = meta.get("artifact_path") or self._artifact_path(model_id)
        if not os.path.exists(artifact):
            raise RegistryError(f"artifact missing for {model_id!r}")
        digest = _sha256_file(artifact)
        if digest != meta.get("artifact_hash"):
            raise RegistryError(f"artifact hash mismatch for {model_id!r}")
        if expected_feature_version and meta.get("feature_version") != expected_feature_version:
            raise RegistryError(
                f"feature_version mismatch for {model_id!r}: "
                f"{meta.get('feature_version')!r} != {expected_feature_version!r}"
            )
        if expected_target and meta.get("target") != expected_target:
            raise RegistryError(f"target mismatch for {model_id!r}")
        if expected_horizon is not None and meta.get("horizon") != expected_horizon:
            raise RegistryError(f"horizon mismatch for {model_id!r}")
        if required_input_fields:
            required = set(meta.get("required_input_fields") or [])
            missing = [f for f in required_input_fields if f not in required]
            # Callers pass fields they have; ensure model-required ⊆ provided.
            model_required = list(meta.get("required_input_fields") or [])
            provided = set(required_input_fields)
            missing_inputs = [f for f in model_required if f not in provided]
            if missing_inputs:
                raise RegistryError(
                    f"missing required input fields for {model_id!r}: {missing_inputs}"
                )
            del missing
        model = joblib.load(artifact)
        return model, meta

    def save_group(
        self,
        *,
        component_model_ids: dict[str, str],
        feature_version: str,
        label_version: str,
        structural_state_version: str = "",
        training_sessions: list[str] | None = None,
        calibration_sessions: list[str] | None = None,
        outer_test_sessions: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        status: str = "research",
        group_id: str | None = None,
    ) -> ModelGroupMetadata:
        if status not in STATUSES:
            raise RegistryError(f"invalid status {status!r}")
        gid = group_id or f"group-{_config_hash(component_model_ids)[:12]}"
        meta = ModelGroupMetadata(
            group_id=gid,
            component_model_ids=dict(component_model_ids),
            feature_version=feature_version,
            label_version=label_version,
            structural_state_version=structural_state_version,
            configuration_hash=_config_hash(component_model_ids),
            training_sessions=tuple(training_sessions or ()),
            calibration_sessions=tuple(calibration_sessions or ()),
            outer_test_sessions=tuple(outer_test_sessions or ()),
            metrics=dict(metrics or {}),
            status=status,
        )
        payload = json.dumps(meta.to_dict(), indent=2, sort_keys=True)
        _atomic_write_text(self._group_path(gid), payload)
        return meta

    def load_group(self, group_id: str) -> ModelGroupMetadata:
        path = self._group_path(group_id)
        if not os.path.exists(path):
            raise RegistryError(f"group metadata missing for {group_id!r}")
        with open(path, encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)
        return ModelGroupMetadata.from_dict(data)

    def validate_group(self, meta: ModelGroupMetadata, *, load_mode: str = "research") -> None:
        allowed = ALLOWED_MODES.get(meta.status, frozenset())
        if load_mode not in allowed:
            raise RegistryError(
                f"group status {meta.status!r} does not allow load_mode {load_mode!r}"
            )
        if not meta.component_model_ids:
            raise RegistryError(f"group {meta.group_id!r} has no component models")
        for mid in meta.component_model_ids.values():
            self.load_metadata(mid, validate_v2=True)
