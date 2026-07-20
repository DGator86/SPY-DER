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

**Phase 0 — Source access and baseline.**

Phase 0 deliverables (spec §63):

- System A source access
- Exact System A source commit
- `baseline/system_a.lock.json`
- `docs/SOURCE_PROVENANCE.md`
- `docs/CURRENT_SYSTEM_INVENTORY.md` (full System A inventory)
- Source-validated `docs/MIGRATION_MAP.md` (replacing the provisional map)

## Completed Work

- Authored the authoritative master specification at
  `docs/SPY_DER_MASTER_SPEC.md`.
- Consolidated this handoff log, removing the previously duplicated Packet 0
  sections and reconciling the permanent rules under the master spec.

## Files Changed (this run)

- `docs/SPY_DER_MASTER_SPEC.md` (created)
- `docs/CODEX_HANDOFF_STATE.md` (rewritten and de-duplicated)

## Tests Run

- None. This run adds and consolidates documentation only; no code changed.

## Active Blockers

1. **System A source access is not established.** `DGator86/0DTE` is not present
   in this workspace and is outside the current session's repository scope. Per
   spec §5, source-dependent Phase 0 work is blocked until access is provided
   via a local sibling checkout, an authorized GitHub repository, or a complete
   source bundle carrying the original commit SHA.
2. Because of (1), the following remain unproduced and must not be fabricated
   (spec §5, §67): `baseline/system_a.lock.json` with a real SHA, the full
   System A inventory, and the source-validated migration map.

## Decisions

- `docs/SPY_DER_MASTER_SPEC.md` is the single authoritative specification. Prior
  scaffold docs (`ARCHITECTURE.md`, `MIGRATION_MAP.md`, etc.) are provisional
  and subordinate to it.
- No System A module may be described as migrated, validated, or at parity until
  the pinned source is inspected (spec §4.1, §5).
- The provisional migration map in spec §62 stands only until confirmed against
  the pinned System A tree.
- The existing `src/system_b/` scaffold does not count as migration; it will be
  normalized to `src/spy_der/` beginning in Phase 1.

## Next Phase

Complete **Phase 0** once System A source access is available:

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase 0 only: establish System A source access, pin the exact System A
baseline, create source provenance, inventory the full System A repository, and
replace the provisional migration map with a source-validated map.
Do not migrate production code yet.
Do not fabricate unavailable source behavior.
Update the handoff state and the Phase 0 migration manifest.
```

After Phase 0 is complete, execute **Phase 1** (package and canonical ingestion
foundation).

Per-run instruction for every subsequent phase (spec §70):

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase <NUMBER> only.
Do not work on later phases.
Update docs/CODEX_HANDOFF_STATE.md and the phase migration manifest.
Run the required tests.
Report changed files, results, blockers, and rollback.
```
