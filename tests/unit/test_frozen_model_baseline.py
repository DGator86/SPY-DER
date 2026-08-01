import hashlib
import json
from pathlib import Path
from typing import Any

BASELINE = Path(__file__).parents[2] / "baseline" / "frozen_models" / "spy_der_v1"
PAYLOAD_FILES = {"README.md", "configuration.json", "provenance.json", "expected_results.json"}
EXPECTED_FILES = PAYLOAD_FILES | {"CHECKSUMS.sha256"}


def load_json(name: str) -> dict[str, Any]:
    return json.loads((BASELINE / name).read_text(encoding="utf-8"))


def test_baseline_has_exactly_the_immutable_allowlist() -> None:
    assert {path.name for path in BASELINE.iterdir()} == EXPECTED_FILES


def test_json_payloads_load_and_have_exact_schema_versions() -> None:
    configuration = load_json("configuration.json")
    provenance = load_json("provenance.json")
    expected = load_json("expected_results.json")

    assert configuration["schema_version"] == "spy_der.frozen_model.v1"
    assert provenance["schema_version"] == "spy_der.frozen_provenance.v1"
    assert expected["schema_version"] == "spy_der.frozen_expected_results.v1"


def test_frozen_model_authority_and_parameters() -> None:
    configuration = load_json("configuration.json")
    models = configuration["point_models"]

    assert set(models) == {"5m", "15m", "30m", "60m"}
    assert set(configuration["uncertainty"]["horizons"]) == set(models)
    for horizon in ("5m", "15m"):
        assert models[horizon]["mask"] == 2047
        assert models[horizon]["alpha"] == 1.0
    assert models["30m"]["mask"] == 1978
    assert models["30m"]["alpha"] == 0.00001
    assert set(models["30m"]["excluded_blocks"]) == {
        "SPY momentum",
        "Original tensor",
        "GEX level",
    }
    assert models["60m"]["role"] == "advisory_only"
    assert models["60m"]["trade_authority"] is False

    authority = configuration["authority"]
    assert authority["state_alerts"] == "shadow_only"
    assert configuration["state_engine"]["authority"] == "shadow_only"
    assert authority["path_forecast_fan"] == "disabled"
    assert authority["predictive_edge_trade_override"] == "disabled"
    assert authority["live_execution_authorized"] is False
    disabled = set(configuration["disabled_components"])
    assert {
        "pure_joint_recursive_path_model",
        "endpoint_controlled_hybrid_path",
        "historical_analog_fan",
        "predictive_edge_trade_override",
        "automatic_state_alert_trade_authority",
        "live_broker_routing",
    } <= disabled


def test_checksums_cover_and_match_every_payload() -> None:
    entries: dict[str, str] = {}
    for line in (BASELINE / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        entries[name] = digest

    assert set(entries) == PAYLOAD_FILES
    for name, expected_digest in entries.items():
        assert hashlib.sha256((BASELINE / name).read_bytes()).hexdigest() == expected_digest


def test_provenance_does_not_claim_unavailable_artifacts_were_verified() -> None:
    status = load_json("provenance.json")["artifact_status"]

    assert status["original_full_package_vendored"] is False
    assert status["original_artifacts_verified"] is False
    assert status["generator_package_checksum_status"] == "unavailable_in_this_workspace"
