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
# SPY-DER Codex Handoff State

## Repository State

- System B workspace: `/workspace/SPY-DER`
- System B branch: `work`
- Initial detected commit: `e4106b1315836d302f4c82079fb13acd3b9e4002`
- System A repository: `DGator86/0DTE`
- System A workspace access: not yet established
- Current phase: handoff initialization

## Permanent Project Rules

1. System A is the existing implementation and migration baseline.
2. System B is the rebuilt production architecture.
3. Do not create another empty scaffold.
4. Migrate real Legacy, V2, and V3 implementations.
5. Legacy supplies structural interpretation, permissions, evidence, and vetoes.
6. V2 forecasts future underlying behavior.
7. V3 supplies advanced forecasts, candidate economics, ranking, uncertainty, and abstention.
8. One deterministic candidate factory constructs every legal option candidate.
9. Every candidate must have deterministically bounded maximum loss.
10. No strategy may require stock ownership.
11. Grok is the initial default AI decision agent.
12. The AI-agent interface must remain provider-neutral and replaceable.
13. AI may select only an existing candidate ID.
14. AI may reduce size but may never increase deterministic risk.
15. Deterministic risk remains final authority.
16. Live broker execution is not authorized.
17. Every migration requires parity tests or documented intentional-change tests.
18. Every migration must be reversible.
19. Do not claim a component is migrated without working code and tests.
20. Missing System A access must be recorded as a blocker, not guessed around.

## Completed Work

- Persistent handoff file initialized.

## Files Inspected

- Repository metadata only.

## Files Changed

- `docs/CODEX_HANDOFF_STATE.md`

## Tests Run

- File existence check.
- Markdown content inspection.

## Active Blockers

- System A is not currently available in the workspace.
- The System B checkout currently has no configured Git remote.

## Decisions

- System A inspection and baseline pinning will not be attempted until repository access exists.
- System B-local work may proceed only when the current packet does not require System A source inspection.

## Next Expected Packet

Establish or verify access to `DGator86/0DTE` and record source provenance.
