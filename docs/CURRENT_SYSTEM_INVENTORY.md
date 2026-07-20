# CURRENT SYSTEM INVENTORY — System A (`DGator86/0DTE`)

Complete inventory of the pinned System A source tree, produced during **Phase 0 —
Source access and baseline** (`docs/SPY_DER_MASTER_SPEC.md` §63, §71).

- **Pinned commit:** `de4a6e7ced98ff97c778e8b4418c08848d7ce82d`
- **Baseline lock:** [`baseline/system_a.lock.json`](../baseline/system_a.lock.json)
- **Raw tree manifest (verifiable):** `baseline/manifests/system_a_tree.txt`

Every path below was read from the pinned tree. Module purposes and validation status
are taken from System A's own `HANDOFF.md` (module reference §3, validated/gaps §5) and
`AGENTS.md`. This is a description of what exists, **not** a migration claim.

---

## 1. Totals

| Metric | Count |
|---|---|
| Tracked files | 305 |
| Python modules | 257 |
| Python LOC | 62,211 |
| Test files | 112 |
| Config files (`configs/`, JSON/YAML) | 5 |
| Docs (`docs/`, `*.md`) | 8 + root `HANDOFF.md`, `AGENTS.md` |

### LOC by area

| Area | Files | LOC | Role |
|---|---:|---:|---|
| `(root)` | 38 | 16,431 | Legacy + Track A/B production modules and live loop |
| `prediction/` | 57 | 18,107 | V2/V3 forecasting, calibration, datasets, model registry |
| `tests/` | 111 | 18,506 | Unit / property / parity-style tests |
| `adaptive_learning/` | 9 | 2,781 | Offline hypothesis/feature-lab learning subsystem |
| `dashboard/` | 7 | 1,804 | Read-only FastAPI observability dashboard |
| `zerodte/` | 14 | 1,074 | **In-progress canonical unification package** (see §7) |
| `policy/` | 5 | 966 | Policy adapters + router |
| `scripts/` | 3 | 969 | Feature-impact / replay / study CLIs |
| `gex/` | 7 | 887 | Modular GEX variant package |
| `execution/` | 3 | 388 | Execution-cost / fill records |
| `validation/` | 3 | 298 | Session-fold + bootstrap validation helpers |

---

## 2. System A architecture (from `HANDOFF.md`)

System A is two partially-overlapping subsystems sharing `rnd_extractor` and
`spread_selector`, now combined by `unified_loop.py` (`UnifiedOrchestrator`) into one tick
and driven live (no-order mode) by `shadow_runner.py`.

- **Track A — Premium engine + measurement:** `rnd_extractor → spread_selector → gate_scorer
  → decision_engine → journal`, driven by `orchestrator.py`. Produces fillable tickets.
- **Track B — Regime routing:** `resample → mtf_matrix → decision_matrix → live_feed_adapter`
  (`regime_classifier.py` is the standalone deterministic classifier behind the same idea).
  Decides structure family / direction / conviction, stops short of strikes.
- **Operating model:** notification + manual execution. Nothing places live orders
  (consistent with SPY-DER non-goals, spec §6).

---

## 3. Root-level modules (Track A / Track B / live loop)

Status column: as declared in `HANDOFF.md` §3/§5.

| Module | LOC | Purpose | Status |
|---|---:|---|---|
| `rnd_extractor.py` | 553 | Breeden–Litzenberger risk-neutral density + physical-vs-RN edge | Validated |
| `gate_scorer.py` | 589 | Hard pre-trade gates + weighted 0–100 confidence → Kelly | Validated |
| `spread_selector.py` | 905 | Generates defined-risk (+ optional naked) structures, prices vs density, ranks by risk-adjusted EV | Validated |
| `decision_engine.py` | 238 | Pure composition of gate + selector; captures would-be candidate on no-trades | Validated |
| `journal.py` | 1,123 | SQLite persistence; settlement of trades AND no-trades; effectiveness/calibration readouts | Validated |
| `orchestrator.py` | 151 | Track-A tick loop + settlement; `DataFeed` protocol | Validated |
| `regime_classifier.py` | 550 | Deterministic regime classifier w/ adaptive `ScaleBook`, engine vetoes | Validated |
| `mtf_matrix.py` | 379 | Multi-timeframe standardized feature matrix + per-TF regime rows | Validated |
| `decision_matrix.py` | 467 | 27-cell (exec×context×direction) → structure/conviction/size; dealer vetoes override | Validated (hardened) |
| `resample.py` | 509 | Raw bars → per-TF indicators (ADX/RSI/EMA/BB/RV/CVD) → `MTFInput.native` | Validated |
| `live_feed_adapter.py` | 311 | Vendor-agnostic feed adapter; Track-B standalone harness; `route_ticket` | Legacy harness |
| `unified_loop.py` | 1,774 | **The** live tick loop combining Track A + Track B → risk → journal | Validated (seam tests) |
| `shadow_runner.py` | 554 | Drives `UnifiedOrchestrator` live in no-order mode; auto-settle; paper broker + dashboard state | Works |
| `composite_feed.py` | 142 | Live `DataFeed` w/ provider failover (Tradier→Tastytrade→Massive), Yahoo backstop | Works |
| `chain_store.py` | 317 | Record live ticks to gzipped JSONL; replay as `DataFeed` (real-data walk-forward) | Validated |
| `synthetic_world.py` | 269 | Coupled synthetic market (GEX regime drives price; chains reprice per tick) | Validated |
| `validation_pipeline.py` | 510 | Scheduled daily/weekly validation: walk-forward + journal health + degradation flags | Works |
| `config_loader.py` | 99 | YAML run-config overlays for controlled experiments | Works |
| `market_calendar.py` | 140 | Sessions/holidays/half-days, open/close, session date, minutes-from/to | Present |
| `market_dynamics.py` | 184 | Dealer-dynamics window (flip/wall velocities, ruptures) — observation-only | Present |
| `gex_window.py` | 101 | Persistent \|GEX\| percentile-rank window shared across feeds | Present |
| `pin_regime.py` | 197 | Pin-regime detection | Present |
| `risk_manager.py` | 211 | Risk/sizing/portfolio limits | Present |
| `paper_broker.py` | 796 | Paper execution + position management | Present |
| `execution_cost.py` | 369 | Execution cost / slippage modeling | Present |
| `backtest.py` | 322 | Backtest engine over recorded/synthetic feeds | Present |
| `walk_forward.py` | 687 | Expanding-fold walk-forward w/ untouched holdout | Present |
| `optimizer.py` | 523 | Parameter search (`OptimizerConfig`, holdout_frac) | Present |
| `mc.py` | 165 | Monte-Carlo path baseline + calibration readout | Present |
| `mtf`/feed providers | — | see §4 | — |
| `tradier_feed.py` | 415 | Tradier provider (+ breadth-lite) | Present |
| `tastytrade_feed.py` | 362 | Tastytrade provider (lazy) | Present |
| `massive_feed.py` | 699 | Massive provider (+ flow-lite) | Present |
| `yahoo_feed.py` | 212 | Yahoo backstop (bars/settlement only) | Present |
| `volatility_channel_features.py` | 66 | Volatility-channel feature block | Present |
| `notifier.py` | 351 | Alert delivery (validation/degradation) | Present |
| `spy0dte.py` | 387 | SPY 0DTE entry-point wiring | Present |
| `acceptance.py` | 78 | Acceptance checks | Present |

## 4. Data-provider modules

`composite_feed.py` (failover assembler) over provider adapters `tradier_feed.py`,
`tastytrade_feed.py`, `massive_feed.py`, `yahoo_feed.py`, and `live_feed_adapter.py`
(vendor-agnostic adapter). Recording/replay via `chain_store.py`.

## 5. `prediction/` — V2/V3 forecasting engine (57 files, 18,107 LOC)

Top-level (selected):
`asof.py`, `calibration.py`, `candidate_dataset.py`, `candidate_ranker.py`,
`canonical_snapshot.py`, `conformal.py`, `contracts.py`, `crossfit.py`, `dataset.py`,
`deployment.py`, `drift.py`, `dynamic_weights.py`, `ensemble.py`, `event_dataset.py`,
`feed_status.py`, `fill_training.py`, `inference.py`, `labels.py`, `ood.py`,
`part2_shadow.py`, `part3_shadow.py`, `path_model.py`, `path_model_v3.py`,
`physical_distribution.py`, `promotion.py`, `regime_labels.py`, `registry.py`,
`return_distribution.py`, `scalers.py`, `session_bootstrap.py`, `sigma_cone.py`,
`storage.py`, `structural_state.py`, `training.py`, `uncertainty.py`,
`v3_part2_config.py`.

`prediction/models/` (16 files): `barrier_touch.py`, `base.py`, `candidate_rank.py`,
`candidate_value.py`, `competing_risk.py`, `direction.py`, `fill.py`, `fill_concession.py`,
`fill_probability.py`, `mixture_experts.py`, `range_survival.py`, `regime_moe.py`,
`return_quantiles.py`, `trade_meta.py`, `volatility.py`, `__init__.py`.

`prediction/reports/` (4 files): `part2_evaluation.py`, `part3_evaluation.py`,
`promotion_packet.py`, `__init__.py`.

Reference docs in source: `docs/PREDICTION_ENGINE_V2_HANDOFF.md`,
`docs/PREDICTION_ENGINE_V3_PART1_VALIDATION.md`, `..._PART2_FORECASTING.md`,
`..._PART3_DECISION_DEPLOYMENT.md`.

## 6. Supporting packages

- **`gex/`** (7): `base.py`, `contracts.py`, `hybrid.py`, `oi.py`, `volume_proxy.py`,
  `weekly.py`, `__init__.py` — modular GEX variants (OI / volume-proxy / hybrid / weekly),
  richer than the single root `gex_window.py`.
- **`policy/`** (5): `contracts.py`, `legacy_matrix.py`, `prediction_policy.py`,
  `router.py`, `__init__.py` — policy adapters + routing.
- **`execution/`** (3): `estimate_v3.py`, `fill_records.py`, `__init__.py`.
- **`validation/`** (3): `bootstrap.py`, `session_folds.py`, `__init__.py`.
- **`adaptive_learning/`** (9): `config_store.py`, `diagnostics.py`, `feature_lab.py`,
  `hypothesis.py`, `learner.py`, `promoter.py`, `reports.py`, `stability.py`, `__init__.py`
  — offline hypothesis-driven learning/promotion subsystem.
- **`dashboard/`** (7 + static): `server.py`, `state.py`, `queries.py`, `live_schema.py`,
  `auth.py`, `__main__.py`, `__init__.py`, `static/{index.html,app.js,style.css}` —
  read-only FastAPI observability dashboard.
- **`scripts/`**: `feature_impact.py`, `replay_ras.py`, `turn_lag_study.py`,
  `vercel-build.sh`.
- **`configs/`**: `baseline.yaml`, `with_channels.yaml`, `deployment.json`,
  `prediction_v3_part2.json`, `prediction_v3_part3.json`.
- **`deploy/`**: systemd units/timers (shadow, learn, validate, update, dashboard) +
  `*.env.example`, remote-ops scripts. `api/[...path].js`, `vercel.json` — thin Vercel
  proxy to a remote VPS (not a local app).

## 7. `zerodte/` — in-progress canonical unification (14 files, 1,074 LOC)

**Key Phase 0 finding.** Per `AGENTS.md`, System A is *itself* being migrated incrementally
into the `zerodte/` package, whose structure directly parallels SPY-DER's target layout
(spec §10):

| `zerodte/` (System A) | Files | Parallel SPY-DER target (spec §10) |
|---|---|---|
| `contracts/` | `candidates.py`, `decisions.py`, `market.py`, `risk.py`, `__init__.py` | `src/spy_der/contracts/` |
| `runtime/` | `pipeline.py`, `services.py`, `__init__.py` | `src/spy_der/runtime/` |
| `agent/` | `contracts.py`, `runtime.py`, `__init__.py` | `src/spy_der/synthesis/agents/` |
| `adapters/` | `legacy_snapshot.py`, `__init__.py` | `src/spy_der/market_data/` + legacy adapters |

`AGENTS.md` states the top-level modules and `shadow_runner.py` remain the production
baseline until a separately reviewed promotion PR; new cross-stage contracts go in
`zerodte/contracts/`, orchestration in `zerodte/runtime/`, AI providers behind
`zerodte.agent.AgentProvider` (may select only canonical candidate IDs; may reduce but
never raise deterministic size). This is a direct antecedent of SPY-DER's contracts,
runtime, and plug-and-play AI layer and should anchor the corresponding migration phases.

## 8. Test corpus (112 files)

112 test files (inventory: `baseline/manifests/system_a_tests.txt`), including parity-relevant
suites: `test_baseline_contracts.py`, `test_canonical_runtime.py`,
`test_feed_status_and_canonical_snapshot.py`, `test_candidate_*` (grouping, labels, ranker,
utility, value_v3), `test_competing_risk_model.py`, `test_conformal_returns.py`,
`test_crossfit*.py`, `test_direction_model.py`, `test_drift_monitor.py`,
`test_execution_cost.py`, `test_execution_estimate_v3.py`, `test_atomic_full_stack_rollback.py`,
`test_dashboard*.py`, and more. These are the first candidates for Phase 1+ parity fixtures
(spec §65 parity tests).

## 9. Honest gaps carried from System A (`HANDOFF.md` §5)

Recorded so later phases do not mistake known System A limitations for migration defects:
OI-based GEX is stale intraday (mitigations under journal arbitration); CVD proxy returns 0
on mid-bar closes (synthetic only); `tick_two_sided` needs a real $TICK feed; several
`*_scale` constants are fixed priors, not yet adaptive; observation-only signal tranche
(dealer dynamics, expected-move-consumed, flow/breadth lite) currently carries zero
gate/veto power by test-enforced design.
