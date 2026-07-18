from __future__ import annotations

import pytest

from system_b.replay.deterministic import (
    ManifestMismatchError,
    ReplayInputManifest,
    deterministic_events,
    ensure_matching_manifests,
    journal_hash,
)


def _manifest(seed: str) -> ReplayInputManifest:
    return ReplayInputManifest(
        timestamp="2026-01-01T00:00:00+00:00",
        market_snapshot_hash=f"snap-{seed}",
        underlying_bars_hash=f"bars-{seed}",
        option_chain_hash=f"chain-{seed}",
        candidate_universe_hash=f"cand-{seed}",
        fees_hash=f"fees-{seed}",
        slippage_hash=f"slip-{seed}",
        fill_assumptions_hash=f"fill-{seed}",
        account_size_hash=f"acct-{seed}",
        risk_ceilings_hash=f"risk-{seed}",
        exit_policy_hash=f"exit-{seed}",
        settlement_hash=f"settle-{seed}",
    )


def test_identical_replay_inputs_produce_identical_journal_hashes() -> None:
    events_a = deterministic_events("same")
    events_b = deterministic_events("same")
    assert journal_hash(events_a) == journal_hash(events_b)


def test_adapters_cannot_be_compared_when_manifests_differ() -> None:
    with pytest.raises(ManifestMismatchError):
        ensure_matching_manifests(_manifest("a"), _manifest("b"))
