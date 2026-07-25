"""The 0DTE disposition ledger is complete and unambiguous.

The migration plan says "every 0DTE module must receive one disposition —
nothing should remain ambiguous". That is only true if it is checked, so these
tests are the enforcement:

* every tracked path in the pinned 0DTE tree matches at least one rule
* no rule is dead (each matches at least one real path)
* every rule declares a legal disposition, a legal status, and a legal parity gate
* a non-``delete``/``archive`` disposition names a SPY-DER owner

A new 0DTE file, or a renamed one, fails the first test until someone decides
what happens to it.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER_PATH = _ROOT / "migrations" / "inventory" / "zerodte_disposition.json"
_TREE_PATH = _ROOT / "migrations" / "inventory" / "zerodte_tree.txt"

_VALID_STATUS = {"done", "partial", "pending"}
_VALID_PARITY_GATES = {
    "none",
    "raw_market_snapshots",
    "feature_values",
    "regime_labels",
    "forecast_outputs",
    "candidate_sets",
    "risk_values",
    "vetoes",
    "settlement",
    "journal_output",
    "recorded_session_replay",
    "synthetic_world_parity",
    "live_shadow_parity",
    "dashboard_compatibility",
}
#: Dispositions that legitimately have no SPY-DER destination.
_OWNERLESS = {"delete", "archive"}


@pytest.fixture(scope="module")
def ledger() -> dict[str, Any]:
    return json.loads(_LEDGER_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tracked_paths() -> list[str]:
    lines = _TREE_PATH.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def _matches(path: str, pattern: str) -> bool:
    """Directory patterns (``pkg/**``) match anything beneath, plus the dir itself."""
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatch(path, pattern)


def _rule_for(path: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First matching rule wins — the ledger documents that ordering."""
    for rule in rules:
        if _matches(path, str(rule["match"])):
            return rule
    return None


# --------------------------------------------------------------------------- #
# Completeness                                                                #
# --------------------------------------------------------------------------- #
def test_tree_manifest_matches_declared_count(
    ledger: dict[str, Any], tracked_paths: list[str]
) -> None:
    assert len(tracked_paths) == ledger["tracked_paths"]


def test_every_tracked_path_has_a_disposition(
    ledger: dict[str, Any], tracked_paths: list[str]
) -> None:
    """No 0DTE file may be left without a decision."""
    rules = ledger["rules"]
    unassigned = [p for p in tracked_paths if _rule_for(p, rules) is None]
    assert not unassigned, (
        f"{len(unassigned)} 0DTE path(s) have no disposition: {unassigned[:10]}"
    )


def test_no_rule_is_dead(ledger: dict[str, Any], tracked_paths: list[str]) -> None:
    """A rule matching nothing means the ledger has drifted from the tree."""
    dead = [
        str(rule["match"])
        for rule in ledger["rules"]
        if not any(_matches(p, str(rule["match"])) for p in tracked_paths)
    ]
    assert not dead, f"rules match no tracked path: {dead}"


def test_every_python_module_is_covered(
    ledger: dict[str, Any], tracked_paths: list[str]
) -> None:
    """Belt and braces: runtime code specifically must be dispositioned."""
    rules = ledger["rules"]
    modules = [p for p in tracked_paths if p.endswith(".py")]
    assert modules
    assert all(_rule_for(p, rules) is not None for p in modules)


# --------------------------------------------------------------------------- #
# Well-formedness                                                             #
# --------------------------------------------------------------------------- #
def test_dispositions_are_the_five_declared_kinds(ledger: dict[str, Any]) -> None:
    declared = set(ledger["dispositions"])
    assert declared == {"move", "reimplement", "replace", "archive", "delete"}
    for rule in ledger["rules"]:
        assert rule["disposition"] in declared, rule["match"]


def test_every_rule_declares_a_valid_status(ledger: dict[str, Any]) -> None:
    for rule in ledger["rules"]:
        assert rule["status"] in _VALID_STATUS, rule["match"]


def test_every_rule_declares_a_known_parity_gate(ledger: dict[str, Any]) -> None:
    for rule in ledger["rules"]:
        assert rule["parity_gate"] in _VALID_PARITY_GATES, rule["match"]


def test_carried_forward_rules_name_a_spy_der_owner(ledger: dict[str, Any]) -> None:
    """If code is not deleted or archived, something in SPY-DER must own it."""
    for rule in ledger["rules"]:
        if rule["disposition"] in _OWNERLESS:
            continue
        owner = rule.get("spy_der_owner")
        assert owner, f"{rule['match']} is {rule['disposition']} but names no owner"


def test_every_rule_names_a_capability(ledger: dict[str, Any]) -> None:
    for rule in ledger["rules"]:
        assert str(rule.get("capability", "")).strip(), rule["match"]


def test_match_patterns_are_unique(ledger: dict[str, Any]) -> None:
    patterns = [str(rule["match"]) for rule in ledger["rules"]]
    assert len(patterns) == len(set(patterns))


# --------------------------------------------------------------------------- #
# The target architecture the ledger encodes                                  #
# --------------------------------------------------------------------------- #
def test_the_pr150_bridge_is_marked_for_deletion(ledger: dict[str, Any]) -> None:
    """0DTE is absorbed and retired, so the bridge is temporary by construction."""
    by_match = {str(r["match"]): r for r in ledger["rules"]}
    assert by_match["zerodte/**"]["disposition"] == "delete"
    assert by_match["integrations/**"]["disposition"] == "delete"


def test_no_capability_is_left_owned_by_zerodte(ledger: dict[str, Any]) -> None:
    """Nothing may name a 0DTE path as its final owner."""
    for rule in ledger["rules"]:
        owner = str(rule.get("spy_der_owner") or "")
        assert "/opt/zerodte" not in owner
        assert "/var/lib/zerodte" not in owner
        assert "/etc/zerodte" not in owner


def test_synthetic_universe_production_is_already_migrated(
    ledger: dict[str, Any],
) -> None:
    """The Dojo's 0DTE dependency is gone, so these must read as done."""
    by_match = {str(r["match"]): r for r in ledger["rules"]}
    for pattern in ("matrix_universe.py", "synthetic_world.py", "regime_calibration.py"):
        rule = by_match[pattern]
        assert rule["disposition"] == "move"
        assert rule["status"] == "done"
        assert rule["spy_der_owner"].startswith("spy_der.synthetic")


def test_source_commit_is_pinned(ledger: dict[str, Any]) -> None:
    """A disposition is only meaningful against a specific 0DTE revision."""
    assert len(str(ledger["source_commit"])) == 40
    assert ledger["source_repository"] == "DGator86/0DTE"
