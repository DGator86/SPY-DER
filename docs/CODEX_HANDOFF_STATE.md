# SPY-DER Codex Handoff State

This file is an **operational progress log**. It records completed work, active
blockers, decisions, and the next phase. It is subordinate to
`docs/SPY_DER_MASTER_SPEC.md`, which is the governing specification and may not
be overridden here.

## Repository Context

- Target system (System B): `DGator86/SPY-DER`
- Source system (System A): `DGator86/0DTE`
- Governing spec: `docs/SPY_DER_MASTER_SPEC.md`
- Target Python runtime: 3.12
- Canonical package (per spec §4.2): `src/spy_der/` (normalized from `src/system_b/`
  in Phase 1)
- Live trading authority: excluded

## Current Phase

**Phase 3 — RND and structural features: COMPLETE.** Next up: Phase 4.

Phase 3 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Structural contracts — `contracts/structure.py`: `GexLevels`,
  `VolatilitySummary`, `RndSummary`, `StructuralState` (spec §22).
- ✅ GEX (OI) — `features/gex.py` `compute_oi_gex`, migrated from System A
  `gex/base.py`: net GEX, gamma flip, call/put walls, concentration (spec §18).
- ✅ Persistent adaptive state — `features/gex.py` `GexRankWindow`, migrated from
  `gex_window.py`: neutral 0.5 until warm, multi-day |net GEX| percentile,
  survives restarts via atomic JSON.
- ✅ Volatility — `features/volatility.py`: ATM straddle, expected move,
  expected-move-consumed (spec §19).
- ✅ RND — `features/rnd.py`: bounded Breeden-Litzenberger density summary
  (forward/mean/std/skew, P(S<spot)); validated to recover a lognormal sigma
  (spec §17).
- ✅ Structural service — `features/structural.py` `StructuralStateService`
  assembles `StructuralState`; history-dependent fields flagged missing.
- ✅ Parity — `baseline/expected_outputs/phase3/structural_state.json` golden
  fixture + parity test.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (54 tests) all pass.
See `migrations/manifests/phase-3.json`. GEX variants beyond OI, history-dependent
dynamics, and the full RND smoothing pipeline are deferred.

---

### Phase 2 — Providers and replay: COMPLETE

Phase 2 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Provider protocol + offline substrate — `market_data/providers/base.py`
  (`MarketDataProvider`, `RawTick`) and `providers/static.py` (`StaticProvider`).
- ✅ Composite feed with ordered failover — `market_data/composite.py`, migrated
  from System A `composite_feed.CompositeFeed`; records winner/fallback provenance
  and a dedicated settlement backstop, assembling canonical snapshots.
- ✅ Recording — `market_data/recording.py` (`SnapshotRecorder`): deterministic,
  self-describing JSONL with per-record content hash and per-session sequence.
- ✅ Replay + corruption detection — `market_data/replay.py` (`ReplayFeed`,
  `CorruptRecordingError`): fails closed on hash mismatch, sequence gap, schema
  mismatch, and malformed records; deterministic, no network.
- ✅ Deterministic replay fixture — `baseline/fixtures/phase2/recording.jsonl`
  with a frozen parity test.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (43 tests) all pass.
See `migrations/manifests/phase-2.json`. Live vendor network adapters are
deferred (protocol + offline substrate in place).

---

### Phase 1 — Package and canonical ingestion foundation: COMPLETE

Phase 1 deliverables (spec §63) — implemented against pinned System A source:

- ✅ `spy_der` package normalization — `src/system_b/` → `src/spy_der/` (imports,
  pyproject, tests updated).
- ✅ Common contracts — `contracts/common.py`: tz-aware/finite/probability
  validation, canonical JSON, SHA-256 content hashing, deterministic
  content-addressed IDs, typed `ErrorCode`, and `Provenance`.
- ✅ Market contracts — `contracts/market.py` (spec §13): `FeedComponent`,
  `FeedStatus`, `SessionStatus`, `OptionType`, `Bar`, `OptionContract`,
  `OptionQuote`, `FeedObservation`, `CanonicalMarketSnapshot`, coverage/quality.
- ✅ Market calendar — `market_data/calendar.py`, migrated from System A
  `market_calendar.py`: sessions, holidays, half-days, DST, open/close, ET
  session date, minutes from/to open, entry lockout, settlement availability.
- ✅ Feed provenance + freshness — `market_data/freshness.py` (fail-closed
  LIVE/DELAYED/STALE/MISSING/INVALID/FALLBACK classification).
- ✅ Canonical snapshot assembler — `market_data/assembler.py` with deterministic
  `snapshot_id`/`content_hash` (identity independent of clock/host/order).
- ✅ System A snapshot adapter — `market_data/legacy_adapter.py`, consuming System
  A's serialized `CanonicalSnapshot.to_dict()`; fails closed on missing inputs.
- ✅ Deterministic IDs — `contracts/common.deterministic_id` / `content_hash`.
- ✅ Initial parity fixtures — `baseline/fixtures/phase1/` +
  `baseline/expected_outputs/phase1/` with a frozen golden-output parity test.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (30 tests) all pass.
See `migrations/manifests/phase-1.json`.

---

### Phase 0 — Source access and baseline: COMPLETE

Phase 0 deliverables (spec §63) — all produced against real, pinned source:

- ✅ System A source access — `DGator86/0DTE` cloned to `/workspace/0dte`
  (GitHub-authorized, shallow), side-by-side with System B per spec §5.
- ✅ Exact System A source commit — `de4a6e7ced98ff97c778e8b4418c08848d7ce82d`.
- ✅ `baseline/system_a.lock.json` — spec §4.1 schema, real SHA + reproducible
  `sha256` hashes for tree / test inventory / requirements.
- ✅ `docs/SOURCE_PROVENANCE.md` — access method, pin, verification procedure.
- ✅ `docs/CURRENT_SYSTEM_INVENTORY.md` — full inventory (305 files, 257 Python
  modules, 62,211 LOC, 112 tests) from the pinned tree.
- ✅ Source-validated `docs/MIGRATION_MAP.md` — replaces the empty provisional
  map; every spec §62 source path confirmed to exist, plus the real source the
  provisional map omitted (`gex/`, `zerodte/`, `adaptive_learning/`, etc.).
- ✅ `migrations/manifests/phase-0.json` — spec §64 manifest.

## Completed Work

- Authored the authoritative master specification at
  `docs/SPY_DER_MASTER_SPEC.md`.
- Consolidated this handoff log under the master spec.
- **Established System A access and executed Phase 0 against the pinned source**
  (commit `de4a6e7`): pinned the baseline, wrote source provenance, inventoried
  the full System A repository, and replaced the provisional migration map with a
  source-validated one.
- **Key finding:** System A is itself mid-migration into a `zerodte/` package
  (`contracts/`, `runtime/`, `agent/`, `adapters/`) that mirrors SPY-DER's target
  layout (spec §10). It is the strongest migration anchor for the contracts,
  runtime, and AI-layer phases — see `CURRENT_SYSTEM_INVENTORY.md` §7 and
  `MIGRATION_MAP.md` §11.

## Files Changed (this run)

- `baseline/system_a.lock.json` (created)
- `baseline/manifests/system_a_tree.txt` (created)
- `baseline/manifests/system_a_tests.txt` (created)
- `baseline/manifests/system_a_requirements.txt` (created)
- `docs/SOURCE_PROVENANCE.md` (created)
- `docs/CURRENT_SYSTEM_INVENTORY.md` (created)
- `docs/MIGRATION_MAP.md` (replaced provisional/empty map with source-validated map)
- `migrations/manifests/phase-0.json` (created)
- `docs/CODEX_HANDOFF_STATE.md` (this update)

## Tests Run

- No code was migrated, so no runtime tests apply. Baseline integrity was
  verified by reproducing the lock-file hashes against the pinned tree (see
  `migrations/manifests/phase-0.json` `tests[]`): tree-manifest, test-inventory,
  and requirements `sha256` all reproduce, and the pinned commit resolves to
  `de4a6e7…`.

## Active Blockers

- **None.** The prior blocker (System A source access) is resolved — `DGator86/0DTE`
  is cloned at `/workspace/0dte` and pinned in `baseline/system_a.lock.json`.

## Decisions

- `docs/SPY_DER_MASTER_SPEC.md` is the single authoritative specification. Prior
  scaffold docs are subordinate to it.
- No System A module is described as migrated, validated, or at parity: the
  migration map is validated for source **existence** only; behavioral parity is
  proven per-phase via parity tests (spec §65).
- The baseline pin is immutable by hash; an upstream rewrite/removal of commit
  `de4a6e7` invalidates it (fail-closed, spec §5) and requires re-running Phase 0
  against a new pin.
- `baseline/fixtures/` and `baseline/expected_outputs/` are created empty; parity
  fixtures are captured starting in Phase 1.
- The existing `src/system_b/` scaffold does not count as migration; it is
  normalized to `src/spy_der/` beginning in Phase 1.

## Next Phase

Execute **Phase 4 — MTF and Legacy** (spec §63):

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase 4 only: resampling, multi-timeframe features, normalization,
dynamics, the Legacy analyzer, permissions, veto classification, and
LegacyDecisionView.
Do not work on later phases.
Update docs/CODEX_HANDOFF_STATE.md and migrations/manifests/phase-4.json.
Run the required tests.
Report changed files, results, blockers, and rollback.
```

System A source is available at `/workspace/0dte` (pin: `de4a6e7`). Start Phase 4
from `resample.py` (bars -> per-TF indicators, spec §20), `mtf_matrix.py`
(standardized matrix + normalization), `market_dynamics.py` (spec §21),
`gate_scorer.py` / `decision_matrix.py` / `regime_classifier.py` /
`regime_alignment.py` (Legacy gates/permissions/vetoes, spec §23), producing a
`LegacyDecisionView` (spec §23).

Phases 1-3 provide ingestion, record/replay, and structural features:
`spy_der.market_data` exposes the calendar/assembler/adapter/composite/record/
replay; `spy_der.features` exposes `compute_oi_gex`, `GexRankWindow`,
`compute_volatility`, `compute_rnd`, and `StructuralStateService`.

Per-run instruction for every subsequent phase (spec §70):

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase <NUMBER> only.
Do not work on later phases.
Update docs/CODEX_HANDOFF_STATE.md and the phase migration manifest.
Run the required tests.
Report changed files, results, blockers, and rollback.
```
