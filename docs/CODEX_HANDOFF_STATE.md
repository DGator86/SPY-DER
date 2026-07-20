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

**Phase 17 — Controlled cutover: COMPLETE** (owner approved: "Phase 17 activate.").

System B is the primary research/shadow runtime. System A is retained rollback.
Agent authority is independently controlled. Live execution remains disabled.

### Phase 17 — Controlled cutover: COMPLETE

Owner approval received. Deliverables (spec §63):

- ✅ System B primary research/shadow — `activate_controlled_cutover` +
  `PrimaryResearchRuntime`.
- ✅ System A retained rollback — `rollback_manifest` + `rollback_to_system_a`.
- ✅ Agent authority independently controlled — `AgentAuthorityControl` /
  `AgentDeploymentManifest` (spec §58); toggle without changing primary.
- ✅ Live execution still disabled — `LiveExecutionGate` fail-closed; enable forbidden.
- ✅ Cutover runbook + journal/notification events.
- ✅ Parity — `baseline/expected_outputs/phase17/cutover_snapshot.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (193 tests) all pass.
See `migrations/manifests/phase-17.json`.

### VPS + 0DTE parallel panel (post-Phase-17)

- ✅ `spy_der.integrations.zerodte` — AI decision provider for 0DTE ticks.
- ✅ `spy_der` CLI / `vps-runner` + `deploy/spy-der-shadow.service`.
- ✅ 0DTE fourth paper track `spy_der` + `forecast.parallel_tracks` panel
  (Legacy · V2 · V3 · SPY-DER). See `0DTE` PR / `deploy/SPY_DER_PARALLEL.md`.

---

### AI Decision Authority (post-Phase-16 gap close)

Owner intent: the AI is the decision maker - entry, exit, tracker, analyzer;
every major lever is watched by the AI.

Deliverables:

- ✅ Position decision contracts — `PositionDecisionPacket`,
  `AgentPositionAction` (HOLD/REDUCE/CLOSE), `AgentPositionResponse`.
- ✅ `DecisionAgent.decide_position` on Grok / Deterministic / Mock / Recorded.
- ✅ `HttpGrokTransport` - stdlib xAI HTTP; auto-attached when `XAI_API_KEY` set.
- ✅ `AiDecisionAuthority` - entry + exit + tracker + analyzer + journal events.
- ✅ `ShadowAiLoop` - paper shadow loop wiring AI into open/manage/close.
- ✅ Hard exit floors still override AI HOLD (stop/eod/emergency/ras/expiration).
- ✅ Parity — `baseline/expected_outputs/ai_authority/live_state.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (184 tests) all pass.
See `migrations/manifests/ai-decision-authority.json`.

---

### Phase 16 — Dual-runtime parity: COMPLETE

Phase 16 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Identical live-shadow / replay inputs — `runtime/parity.py` `ParityInputs`
  fail-closed via `ReplayInputManifest` equality.
- ✅ Snapshot / feature / candidate / outcome parity buckets.
- ✅ Decision-difference reports.
- ✅ Performance samples (latency + peak memory).
- ✅ Rollback rehearsal via `DeploymentPointer`.
- ✅ Parity — `baseline/expected_outputs/phase16/dual_runtime_parity.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (176 tests) all pass.
See `migrations/manifests/phase-16.json`.

---

### Phase 15 — Deployment and operations: COMPLETE

Phase 15 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Deployment manifests + modes — `deployment/manifest.py`.
- ✅ Human-gated promotion — `deployment/promotion.py` (no auto-promote).
- ✅ Drift gates — `deployment/drift.py`.
- ✅ Freeze + rollback — `deployment/rollback.py` `DeploymentPointer`.
- ✅ Notifications + runbooks + ops dashboard — `notifications.py`,
  `runbooks.py`, `dashboard.py`.
- ✅ Model registry re-export — existing `training/registry.py` via deployment.
- ✅ Parity — `baseline/expected_outputs/phase15/ops_dashboard.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (172 tests) all pass.
See `migrations/manifests/phase-15.json`. External notification transports and
rich UI dashboards remain deferred.

---

### Phase 14 — Evaluation and comparison: COMPLETE

Phase 14 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Metrics — `evaluation/metrics.py` `evaluate_trades` / `EvaluationResult`.
- ✅ Native + controlled comparison — `evaluation/comparison.py`.
- ✅ Policy + agent comparison + ablations — same module; fail-closed manifests.
- ✅ Session-safe reports — `evaluation/reports.py`.
- ✅ Replay variants expanded — `replay/comparison.py` SystemVariant set.
- ✅ Parity — `baseline/expected_outputs/phase14/comparison_report.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (166 tests) all pass.
See `migrations/manifests/phase-14.json`. Full live dual-runtime harness remains
deferred to Phase 16.

---

### Phase 13 — Journal and settlement: COMPLETE

Phase 13 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Journal contracts — `contracts/events.py` spec §53 `JournalEvent` +
  event types; `contracts/outcomes.py` settlement/counterfactual records.
- ✅ Append-only store — `journal/store.py` in-memory + SQLite WAL.
- ✅ Hash chain — `journal/hash_chain.py` per-event digests + verify.
- ✅ Projections — `journal/projections.py` orders/positions/outcomes/sessions.
- ✅ Settlement — `evaluation/settlement.py` traded + counterfactual no-trades.
- ✅ Reconstruction — `journal/reconstruction.py` deterministic rebuild.
- ✅ Parity — `baseline/expected_outputs/phase13/journal_settlement.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (160 tests) all pass.
See `migrations/manifests/phase-13.json`. Full dashboard projection UI and
promotion/deployment event workflows remain deferred.

---

### Phase 12 — Execution and positions: COMPLETE

Phase 12 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Execution contracts — `contracts/execution.py`: full §51 `OrderStatus`,
  `OrderIntent`, `OrderState`, `OrderFill`, isolated `PaperAccount` IDs.
- ✅ Position contracts — `contracts/positions.py`: full §52 `PositionStatus`,
  approved exit-policy registry, `ReconciliationResult`.
- ✅ Order state machine — `execution/state_machine.py`.
- ✅ Fill simulator — `execution/simulator.py` `PaperExecutionSimulator`
  (limit fills, partials, cancel, expire, fees, deterministic RNG).
- ✅ Isolated accounts — `execution/accounts.py` `IsolatedAccountBook`.
- ✅ Position manager + exits — `positions/manager.py`, `positions/exits.py`.
- ✅ Restart + reconciliation — `positions/restart.py`,
  `execution/reconciliation.py` (block entries on mismatch).
- ✅ Parity — `baseline/expected_outputs/phase12/execution_position.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (153 tests) all pass.
See `migrations/manifests/phase-12.json`. Live broker routing and persistent
sqlite paper store remain deferred.

---

### Phase 11 — Risk: COMPLETE

Phase 11 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Risk contracts — `contracts/risk.py`: `RiskEnvelope`, `RiskDecision`,
  `RiskVeto`, `RiskCheck`, `RiskLimits`, `OperationalState`, `PortfolioState`,
  `LockoutState` (compat aliases `allowed`/`max_defined_risk_per_trade`).
- ✅ Envelope — `risk/envelope.py` `build_risk_envelope` (budgets, slots, vetoes).
- ✅ Firewall — `risk/firewall.py` §50 checks + sizing; `RiskFirewallService`.
- ✅ Sizing — `risk/sizing.py` half-Kelly `scale_risk` + `contracts_for_risk`.
- ✅ Portfolio limits — `risk/portfolio.py` session-scoped `PortfolioTracker`.
- ✅ Lockouts — `risk/lockout.py` warmup / entry cutoff / emergency / catalyst.
- ✅ Stale + duplicates — `risk/duplicates.py` TTL freshness + signature guard.
- ✅ Parity — `baseline/expected_outputs/phase11/risk_decision.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (145 tests) all pass.
See `migrations/manifests/phase-11.json`. Live broker account sync and RAS
position monitor remain deferred.

---

### Phase 10 — Agent framework and Grok: COMPLETE

Phase 10 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Agent contracts — `contracts/agents.py`: `AgentDecisionPacket`,
  `AgentCandidateView`, `AgentDecisionResponse`, identity/capabilities/health.
- ✅ Security + validation — `agents/security.py` (no secrets in packets/prompts),
  `agents/validation.py` (whitelist, size, veto, expiry, hash).
- ✅ Runtime — `agents/runtime.py` `FailClosedAgentRuntime`; registry;
  deterministic / mock / recorded / Grok agents.
- ✅ Grok — `agents/prompts.py`, `agents/parser.py`, `agents/grok.py`
  (injectable transport; no network by default).
- ✅ Comparison — `agents/comparison.py` shadow comparison (observation-only).
- ✅ Parity — `baseline/expected_outputs/phase10/agent_decision.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (135 tests) all pass.
See `migrations/manifests/phase-10.json`. Live HTTP Grok client and other LLM
providers remain deferred.

---

### Phase 9 — Policies and deterministic synthesis: COMPLETE

Phase 9 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Policy contracts — `contracts/policies.py` (`PolicyInputPacket`,
  `PolicyDecisionView`, `PolicyDisagreement`, modes).
- ✅ Legacy / V2 / V3 policy adapters — `policies/{legacy,v2,v3}.py`.
- ✅ Ensemble + disagreement — `policies/ensemble.py`,
  `policies/disagreement.py` (shadow/champion/legacy modes).
- ✅ Deterministic decision agent — `synthesis/deterministic.py`;
  `synthesis/engine.py` delegates to the ensemble.
- ✅ Parity — `baseline/expected_outputs/phase9/policy_synthesis.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (126 tests) all pass.
See `migrations/manifests/phase-9.json`. Full legacy matrix routing and AI agent
packets are deferred to Phase 10.

---

### Phase 8 — Economics and candidate value: COMPLETE

Phase 8 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Fill records — `execution/fill_records.py` + `contracts/economics.py`
  `FillRecord` with provenance validation and fill-fraction enrichment.
- ✅ Fill models — deterministic prior, Stage-1 `FillProbabilityModel`,
  Stage-2 `FillConcessionModel`, support/fallback helpers.
- ✅ Fees / slippage / executable economics — `economics/pricing.py` +
  `economics/service.py` producing spec §33 `CandidateEconomics`.
- ✅ Candidate-value model — `candidate_value/models/value.py` with utility.
- ✅ Ranking + regret — `candidate_value/models/ranking.py`.
- ✅ Meta-action — threshold TRADE/NO_EDGE/ABSTAIN + hard-veto overlay.
- ✅ Parity — `baseline/expected_outputs/phase8/candidate_economics.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (119 tests) all pass.
See `migrations/manifests/phase-8.json`. Pairwise ranker training and nested
HP search are deferred.

---

### Phase 7 — Candidate factory: COMPLETE

Phase 7 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Family registry — `candidates/registry.py`: approved bounded families;
  naked/CSP/covered permanently rejected.
- ✅ Geometry — `candidates/geometry.py`: credit/debit verticals, condors,
  flies, broken-wing, longs, straddle/strangle, bounded backspreads.
- ✅ Payoff + max-loss proof — `candidates/payoff.py`: terminal intrinsic
  payoff, piecewise-linear breakpoints, short-call tail rejection.
- ✅ Stable IDs — `contracts/candidates.py`: `geometry_hash`,
  `terminal_payoff_hash`, content-addressed `candidate_id`.
- ✅ Deterministic dominance — `candidates/dominance.py`: duplicate geometry,
  identical-payoff higher cost, strict payoff dominance.
- ✅ Factory — `candidates/factory.py` `generate_candidate_universe` +
  `CandidateFactoryService`.
- ✅ Contract — `contracts/candidates.py` replaces Phase-0 Candidate stubs
  (`max_loss` property kept for synthesis/risk).
- ✅ Parity — `baseline/expected_outputs/phase7/candidate_universe.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (111 tests) all pass.
See `migrations/manifests/phase-7.json`. Executable economics and ranking are
deferred to Phase 8.

---

### Phase 6 — V3 forecasting: COMPLETE

Phase 6 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Uncertainty — `forecasting/uncertainty.py`: component composition with
  missing≠zero reweighting, data-quality/model-age helpers, session bootstrap.
- ✅ OOD — `forecasting/ood.py`: robust range + NN detector.
- ✅ Conformal — `forecasting/conformal.py`: session-grouped split conformal,
  OOD-aware widening.
- ✅ Regime probabilities — `forecasting/models/regime_moe.py` + `regime_labels.py`.
- ✅ Mixture-of-experts — `forecasting/models/mixture_experts.py`.
- ✅ Competing risks — `forecasting/models/competing_risk.py` (sum≈1, survival).
- ✅ Path model — `forecasting/path_model.py`: deterministic seed, adverse-first
  scoring, `PathForecastV3` (bounded bootstrap + labeled Gaussian fallback).
- ✅ Forecast ensemble — `forecasting/ensemble.py`.
- ✅ Bundle attachment — `forecasting/v3.py` `attach_v3_fields`.
- ✅ Parity — `baseline/expected_outputs/phase6/v3_forecast_bundle.json`.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (101 tests) all pass.
See `migrations/manifests/phase-6.json`. Full path backoff hierarchy and live
ForecastServer V3 group wiring are deferred.

---

### Phase 5 — Data, labels, and V2: COMPLETE

Phase 5 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Forecast contract — `contracts/forecasts.py` `MarketForecastBundle`
  (spec §24, §30), replacing the Phase-0 stub.
- ✅ As-of datasets — `training/asof.py`, `training/datasets.py`:
  `AsOfFeatureBuilder`, `ObservationRow`, deterministic snapshot IDs.
- ✅ Labels — `evaluation/labels.py` `SessionLabeler` (spec §54.1): multi-horizon
  log returns, direction, MFE/MAE, wall/flip touches, range survival, adverse-first
  first-passage.
- ✅ Folds — `training/folds.py`: expanding session folds with embargo and
  trailing calibration carve-out.
- ✅ Calibration — `training/calibration.py`: sigmoid/isotonic/identity +
  `CalibrationArtifact` (train-only OOF scores).
- ✅ V2 models — `forecasting/models/`: vectorizer, direction, return quantiles,
  volatility, range survival, barrier touch.
- ✅ V2 registry — `training/registry.py`: schema-v2 hashed joblib artifacts,
  status-gated load modes, model groups.
- ✅ Fail-closed serving — `forecasting/runtime.py` `ForecastServer`.
- ✅ Parity — `baseline/expected_outputs/phase5/forecast_bundle.json` golden + test.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (88 tests) all pass.
See `migrations/manifests/phase-5.json`. Nested HP search, full recording→dataset
rebuild, candidate labels, and V3 heads are deferred.

---

### Phase 4 — MTF and Legacy: COMPLETE

Phase 4 deliverables (spec §63) — implemented against pinned System A source:

- ✅ Legacy contracts — `contracts/legacy.py`: `LegacyDecisionView`, `HardVeto`,
  `VetoCode`/`VetoCategory`, `DirectionPreference` (spec §23).
- ✅ Operational permissions/vetoes — `legacy/permissions.py`: immutable hard
  vetoes (stale data, missing/invalid chain, catalyst lockout, session closed,
  entry cutoff, insufficient liquidity), migrated from `gate_scorer.py`.
- ✅ Legacy analyzer — `legacy/analyzer.py` `LegacyAnalyzer`: gamma-regime
  interpretation → preferred direction, permitted/prohibited families,
  structural confidence, size cap, structural veto (short gamma), regime label.
- ✅ MTF resample + indicators — `features/mtf.py` `compute_mtf`: resample bars
  to timeframes with return/EMA-slope/RSI/realized-vol, explicit cold start.
- ✅ Normalization — `features/normalization.py` `RobustStandardizer`: rolling
  median/MAD z-score, score-before-update, neutral-until-warm, restart-safe.
- ✅ Parity — `baseline/expected_outputs/phase4/legacy_view.json` golden + test.

Checks: `ruff check .`, `mypy src` (strict), and `pytest` (72 tests) all pass.
See `migrations/manifests/phase-4.json`. The full MTF matrix, dealer-dynamics
velocities, and journal-calibrated gate weights are deferred.

---

### Phase 3 — RND and structural features: COMPLETE

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

**Migration phases 0–17 are COMPLETE** per spec §63.

System B is the primary research/shadow runtime under controlled cutover.
Live trading authority remains excluded. Optional engineering follow-ons:

1. Continuous VPS runner publishing `PrimaryResearchRuntime.live_state()`.
2. 0DTE Vercel dashboard parallel panel for `system_b_grok`.

Phases 1-17 complete:
`spy_der.market_data`, `spy_der.features`, `spy_der.legacy`,
`spy_der.training`, `spy_der.evaluation`, `spy_der.forecasting`,
`spy_der.candidates`, `spy_der.economics`, `spy_der.candidate_value`,
`spy_der.policies`, `spy_der.synthesis`, `spy_der.agents`, `spy_der.risk`,
`spy_der.execution`, `spy_der.positions`, `spy_der.journal`,
`spy_der.replay`, `spy_der.deployment` (incl. cutover), and `spy_der.runtime`.

Per-run instruction for every subsequent phase (spec §70):

```
Read docs/SPY_DER_MASTER_SPEC.md and docs/CODEX_HANDOFF_STATE.md.
Execute Phase <NUMBER> only.
Do not work on later phases.
Update docs/CODEX_HANDOFF_STATE.md and the phase migration manifest.
Run the required tests.
Report changed files, results, blockers, and rollback.
```
