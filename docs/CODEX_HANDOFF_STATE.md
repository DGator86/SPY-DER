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
- Canonical package (per spec §4.2): `src/spy_der/`
- Current scaffold package present in repo: `src/system_b/` (predates the spec;
  scheduled for normalization to `spy_der` in Phase 1)
- Live trading authority: excluded

## Current Phase

**Phase 0 — Source access and baseline: COMPLETE.** Next up: Phase 1.

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

Execute **Phase 1 — Package and canonical ingestion foundation** (spec §63):

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase 1 only: normalize the spy_der package; implement common and market
contracts, the market calendar, feed provenance and freshness, the canonical
snapshot assembler, the System A snapshot adapter, deterministic IDs, and initial
parity fixtures.
Do not work on later phases.
Update docs/CODEX_HANDOFF_STATE.md and migrations/manifests/phase-1.json.
Run the required tests.
Report changed files, results, blockers, and rollback.
```

System A source is available at `/workspace/0dte` (pin: `de4a6e7`). Start Phase 1
from the `zerodte/` canonical package and the Track-A snapshot types
(`gate_scorer.MarketSnapshot`, `prediction/canonical_snapshot.py`,
`prediction/feed_status.py`).

Per-run instruction for every subsequent phase (spec §70):

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase <NUMBER> only.
Do not work on later phases.
Update docs/CODEX_HANDOFF_STATE.md and the phase migration manifest.
Run the required tests.
Report changed files, results, blockers, and rollback.
```
