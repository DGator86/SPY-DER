# CODEX HANDOFF STATE

## Permanent Project Rules

The following Packet 0 rules are permanent and apply to every subsequent packet.

1. Treat **System A** as the behavioral baseline and source of truth until an explicitly approved replacement has demonstrated parity or an intentional, documented change.
2. Preserve System A behavior through adapters and comparison; do not silently rewrite, replace, or discard validated behavior.
3. Keep System B modular, typed, deterministic, and testable.
4. Keep structural evidence, permissions, and hard vetoes separate from forecasts and policy decisions.
5. Keep prediction outputs separate from policy outputs.
6. Hard vetoes are non-bypassable; no downstream component may override them.
7. The deterministic risk firewall is the final risk authority.
8. Synthesis may not increase or bypass the deterministic risk envelope.
9. Generate only immutable, bounded-risk options candidates with a defined maximum loss.
10. Reject undefined-risk and stock-dependent candidates.
11. Treat missing required inputs as explicit abstention or failure, never as a silent default.
12. Use canonical, schema-versioned contracts at subsystem boundaries.
13. Require timezone-aware market and journal timestamps.
14. Retain decision provenance in the canonical system decision.
15. Make order and position transitions explicit, validated state-machine transitions.
16. Persist journal events using deterministic serialization and support deterministic replay.
17. Compare systems only with exactly matching replay manifests and inputs; otherwise fail closed.
18. Test every behavioral change for System A parity or document and test it as an intentional change.
19. Do not add live brokerage integrations, order-routing code, order submission capability, or autonomous trading authority.
20. Do not fabricate predictive models, migration claims, performance claims, or validated-module mappings without supporting evidence.

## Work Completed

- Created this durable handoff record.
- Recorded the Packet 0 permanent rules as the governing constraints for future work.

## Files Inspected

- `.github/copilot-instructions.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/COMPARISON_PROTOCOL.md`
- `docs/CONTRACT_CATALOG.md`
- `docs/DECISION_LIFECYCLE.md`
- `docs/IMPLEMENTATION_ROADMAP.md`
- `docs/MIGRATION_MAP.md`
- `docs/SAFETY_INVARIANTS.md`
- `docs/SYSTEM_A_VS_SYSTEM_B.md`

## Files Changed

- `docs/CODEX_HANDOFF_STATE.md` (created)

## Tests Run

- Not run: this packet adds documentation only.

## Decisions Made

- This document is the persistent handoff location for completed work, decisions, unresolved issues, and the expected next packet.
- Packet 0 constraints are recorded as permanent project rules rather than task-local guidance.

## Unresolved Issues

- The legacy System A adapter and validated module mapping remain intentionally unimplemented.
- No System A replay corpus or parity baseline has yet been recorded in the repository.

## Next Expected Packet

- Begin the next approved implementation packet while preserving the permanent rules above; update this handoff record with the packet scope, changed files, checks, decisions, and remaining issues.
