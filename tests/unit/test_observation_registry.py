from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from spy_der.observations.registry import ObservationRegistryError, load_observation_registry

_COLUMNS = (
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


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    dictionary = tmp_path / "utpm.csv"
    rows = [
        {
            "variable_id": "V0001",
            "module": "Infrastructure",
            "family": "PIT",
            "variable": "receive_timestamp",
            "scope": "event/source",
            "kind": "raw",
            "units": "ns",
            "cadence": "event",
            "primary_sources": "SIP;OPRA",
            "definition": "receive time",
            "method": "",
            "point_in_time_rule": "never overwrite",
            "required_tier": "Core",
            "nullable": "No",
        },
        {
            "variable_id": "V0002",
            "module": "Technical",
            "family": "Breadth",
            "variable": "breadth",
            "scope": "SPY",
            "kind": "derived",
            "units": "fraction",
            "cadence": "1m",
            "primary_sources": "SIP",
            "definition": "advancing fraction",
            "method": "point in time",
            "point_in_time_rule": "membership as known then",
            "required_tier": "Core",
            "nullable": "Yes",
        },
        {
            "variable_id": "V0003",
            "module": "Decision",
            "family": "Trader",
            "variable": "selected_action",
            "scope": "decision",
            "kind": "derived",
            "units": "category",
            "cadence": "decision",
            "primary_sources": "internal",
            "definition": "selected action",
            "method": "",
            "point_in_time_rule": "downstream only",
            "required_tier": "Core",
            "nullable": "No",
        },
    ]
    with dictionary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    source_hash = hashlib.sha256(dictionary.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "test.v1",
                "source_dictionary": dictionary.name,
                "source_sha256": source_hash,
                "source_rows": 3,
                "admissible_pre_trader_modules": ["Infrastructure", "Technical"],
                "prohibited_pre_trader_modules": ["Decision"],
                "admissible_variable_count": 2,
            }
        ),
        encoding="utf-8",
    )
    return dictionary, manifest


def test_registry_excludes_decision_module_and_preserves_metadata(tmp_path: Path) -> None:
    dictionary, manifest = _write_fixture(tmp_path)
    registry = load_observation_registry(dictionary, manifest)
    assert len(registry.variables) == 2
    assert registry.by_id("V0003") is None
    breadth = registry.by_id("V0002")
    assert breadth is not None
    assert breadth.variable == "breadth"
    assert breadth.nullable is True
    assert breadth.primary_sources == ("SIP",)
    assert len(registry.core_variables) == 2


def test_registry_rejects_dictionary_hash_drift(tmp_path: Path) -> None:
    dictionary, manifest = _write_fixture(tmp_path)
    dictionary.write_text(dictionary.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ObservationRegistryError, match="hash mismatch"):
        load_observation_registry(dictionary, manifest)


def test_registry_rejects_unclassified_module(tmp_path: Path) -> None:
    dictionary, manifest = _write_fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["admissible_pre_trader_modules"] = ["Infrastructure"]
    payload["admissible_variable_count"] = 1
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ObservationRegistryError, match="unclassified module"):
        load_observation_registry(dictionary, manifest)
