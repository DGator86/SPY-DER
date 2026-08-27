"""Load and verify the Alpha V2 observation-variable registry.

The source UTPM dictionary is treated as a scientific-control artifact. A
registry load verifies its SHA-256 and declared row count before exposing only
pre-Trader modules to the Observation Engine.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ObservationRegistry",
    "ObservationRegistryError",
    "ObservationVariable",
    "load_observation_registry",
]

_EXPECTED_COLUMNS = (
    "variable_id",
    "module",
    "family",
    "variable",
    "scope",
    "kind",
    "units",
    "cadence",
    "primary_sources",
    "definition",
    "method",
    "point_in_time_rule",
    "required_tier",
    "nullable",
)


class ObservationRegistryError(ValueError):
    """The observation dictionary or its manifest failed an integrity rule."""


@dataclass(frozen=True, slots=True)
class ObservationVariable:
    variable_id: str
    module: str
    family: str
    variable: str
    scope: str
    kind: str
    units: str
    cadence: str
    primary_sources: tuple[str, ...]
    definition: str
    method: str
    point_in_time_rule: str
    required_tier: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class ObservationRegistry:
    manifest_version: str
    source_dictionary: str
    source_sha256: str
    dictionary_version: str
    variables: tuple[ObservationVariable, ...]
    admitted_modules: tuple[str, ...]
    prohibited_modules: tuple[str, ...]
    source_rows: int

    def by_id(self, variable_id: str) -> ObservationVariable | None:
        for item in self.variables:
            if item.variable_id == variable_id:
                return item
        return None

    @property
    def core_variables(self) -> tuple[ObservationVariable, ...]:
        return tuple(item for item in self.variables if item.required_tier == "Core")


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ObservationRegistryError(f"unable to read registry manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ObservationRegistryError("registry manifest must be a JSON object")
    data: dict[str, Any] = {str(key): value for key, value in raw.items()}
    required = (
        "manifest_version",
        "source_dictionary",
        "source_sha256",
        "source_rows",
        "admissible_pre_trader_modules",
        "prohibited_pre_trader_modules",
        "admissible_variable_count",
    )
    missing = [field for field in required if field not in data]
    if missing:
        raise ObservationRegistryError(f"manifest missing required fields: {missing}")
    return data


def _sha256(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ObservationRegistryError(f"unable to read source dictionary: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def _parse_nullable(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"yes", "true", "1"}:
        return True
    if normalized in {"no", "false", "0"}:
        return False
    raise ObservationRegistryError(f"invalid nullable value {value!r}")


def load_observation_registry(
    dictionary_path: str | Path,
    manifest_path: str | Path,
) -> ObservationRegistry:
    """Verify a UTPM CSV and expose only variables admitted by its manifest."""

    dictionary = Path(dictionary_path)
    manifest_file = Path(manifest_path)
    manifest = _load_manifest(manifest_file)

    actual_hash = _sha256(dictionary)
    expected_hash = str(manifest["source_sha256"])
    if actual_hash != expected_hash:
        raise ObservationRegistryError(
            "source dictionary hash mismatch: "
            f"expected {expected_hash}, got {actual_hash}"
        )

    try:
        handle = dictionary.open(newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ObservationRegistryError(f"unable to open source dictionary: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _EXPECTED_COLUMNS:
            raise ObservationRegistryError(
                "dictionary schema mismatch: "
                f"expected {_EXPECTED_COLUMNS!r}, got {tuple(reader.fieldnames or ())!r}"
            )
        rows = list(reader)

    expected_rows = int(manifest["source_rows"])
    if len(rows) != expected_rows:
        raise ObservationRegistryError(
            f"source row count mismatch: expected {expected_rows}, got {len(rows)}"
        )

    admitted = tuple(str(value) for value in manifest["admissible_pre_trader_modules"])
    prohibited = tuple(str(value) for value in manifest["prohibited_pre_trader_modules"])
    overlap = set(admitted) & set(prohibited)
    if overlap:
        raise ObservationRegistryError(
            f"modules cannot be both admitted and prohibited: {sorted(overlap)}"
        )

    seen_ids: set[str] = set()
    selected: list[ObservationVariable] = []
    for row in rows:
        variable_id = row["variable_id"].strip()
        if not variable_id:
            raise ObservationRegistryError("dictionary contains a blank variable_id")
        if variable_id in seen_ids:
            raise ObservationRegistryError(f"duplicate variable_id {variable_id!r}")
        seen_ids.add(variable_id)

        module = row["module"].strip()
        if module in prohibited:
            continue
        if module not in admitted:
            raise ObservationRegistryError(
                f"unclassified module {module!r}; manifest must admit or prohibit it"
            )

        selected.append(
            ObservationVariable(
                variable_id=variable_id,
                module=module,
                family=row["family"].strip(),
                variable=row["variable"].strip(),
                scope=row["scope"].strip(),
                kind=row["kind"].strip(),
                units=row["units"].strip(),
                cadence=row["cadence"].strip(),
                primary_sources=tuple(
                    source.strip()
                    for source in row["primary_sources"].split(";")
                    if source.strip()
                ),
                definition=row["definition"].strip(),
                method=row["method"].strip(),
                point_in_time_rule=row["point_in_time_rule"].strip(),
                required_tier=row["required_tier"].strip(),
                nullable=_parse_nullable(row["nullable"]),
            )
        )

    expected_selected = int(manifest["admissible_variable_count"])
    if len(selected) != expected_selected:
        raise ObservationRegistryError(
            "admissible variable count mismatch: "
            f"expected {expected_selected}, got {len(selected)}"
        )

    dictionary_version = f"{manifest['manifest_version']}:{actual_hash[:12]}"
    return ObservationRegistry(
        manifest_version=str(manifest["manifest_version"]),
        source_dictionary=str(manifest["source_dictionary"]),
        source_sha256=actual_hash,
        dictionary_version=dictionary_version,
        variables=tuple(selected),
        admitted_modules=admitted,
        prohibited_modules=prohibited,
        source_rows=len(rows),
    )
