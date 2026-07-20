# SPY-DER Codex Handoff State

## Repository State

- System B workspace: `/workspace/SPY-DER`
- System B branch: `work`
- Initial detected commit: `e4106b1315836d302f4c82079fb13acd3b9e4002`
- System B remote: not yet established
- System A repository: `DGator86/0DTE`
- System A workspace access: not yet established
- Current phase: handoff initialized; repository access pending

## Permanent Project Rules

1. System A is the existing implementation, behavioral baseline, and migration source.
2. System B is the rebuilt unified architecture.
3. Do not create another empty scaffold.
4. Migrate actual working Legacy, V2, and V3 implementations.
5. Preserve System A behavior through parity tests or document and test intentional changes.
6. Legacy supplies structural interpretation, evidence, permissions, and hard vetoes.
7. V2 forecasts future underlying behavior.
8. V3 supplies advanced forecasting, candidate economics, ranking, uncertainty, and abstention.
9. Forecast outputs must remain separate from policy outputs.
10. Structural evidence and permissions must remain separate from forecasts.
11. One deterministic candidate factory constructs every legal option candidate.
12. Candidates must be immutable and have deterministically bounded maximum loss.
13. Undefined-risk and stock-dependent candidates are prohibited.
14. Missing required inputs produce explicit failure or abstention, never silent defaults.
15. Grok is the initial default AI decision agent.
16. The AI-agent interface must remain provider-neutral and replaceable.
17. The AI may select only an existing candidate ID.
18. The AI may reduce size but may never increase or bypass deterministic risk.
19. Hard vetoes are non-bypassable.
20. The deterministic risk firewall is final risk authority.
21. Canonical subsystem contracts must be typed, immutable, and schema-versioned.
22. Market and journal timestamps must be timezone-aware.
23. Order and position changes must use validated state-machine transitions.
24. Journal events must use deterministic serialization and support deterministic replay.
25. System comparisons require exactly matching replay manifests and inputs.
26. Live brokerage integration, order submission, and autonomous live-trading authority are prohibited.
27. Every migration must be reversible.
28. Do not claim migration, validation, performance, or parity without supporting code and tests.
29. Missing System A access must be recorded as a blocker rather than guessed around.
30. Every completed packet must update this file.

## Completed Work

- Created the durable Codex handoff record.
- Consolidated the duplicated Packet 0 state.
- Recorded the permanent project rules.

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
- `docs/CODEX_HANDOFF_STATE.md`

## Files Changed

- `docs/CODEX_HANDOFF_STATE.md`

## Tests Run

- File existence check.
- Markdown content inspection.
- Duplicate-heading check.

## Active Blockers

- System A is not currently available in the workspace.
- The System B checkout does not yet have a confirmed Git remote.
- No pinned System A replay corpus or parity baseline exists yet.

## Decisions

- System A inspection and baseline pinning will wait until repository access is established.
- No System A implementation mapping may be described as validated until the source repository is inspected.
- System B-local work may proceed only when a packet does not require unavailable System A source code.

## Next Expected Packet

Establish and verify access to `DGator86/SPY-DER` and `DGator86/0DTE`.
