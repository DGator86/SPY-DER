# Alpha V2 Market-Model-First Architecture

Status: canonical architecture reset

This document defines the target architecture for Alpha V2. It supersedes any workflow in which historical trade profitability is used to invent increasingly specific trading rules.

The governing rule is simple:

> Alpha models the market independently of the trade.
>
> The Market Model determines what exists. The Forecast Engine estimates what comes next. The Trader determines whether that forecast is mispriced and monetizable. Verification judges the forecast separately from the trade result.

## 1. Canonical pipeline

```text
RAW SENSOR DATA
    |
    v
OBSERVATION ENGINE
    |
    v
STANDARDIZED MEASUREMENTS
    |
    v
MARKET STATE
    |
    v
REGIME POSTERIOR
    |
    v
FUTURE DISTRIBUTION (P)
    |
    +-------------------+
    |                   |
    |                   v
    |             MARKET-IMPLIED Q
    |             + EXECUTABLE PRICES
    |                   |
    +---------+---------+
              |
              v
            TRADER
              |
     WAIT / NO_EDGE / ABSTAIN / TRADE
              |
              v
       EXECUTION + MANAGEMENT
              |
              v
            RESULT
              |
              v
       FORECAST VERIFICATION
              |
              v
        ERROR ATTRIBUTION
              |
              v
          CALIBRATION
```

A Scientific Control Layer surrounds every stage and owns versioning, point-in-time integrity, experiments, calibration, validation, promotion, and rollback.

## 2. Hard system boundaries

### 2.1 Observation Engine

Purpose: measure reality.

Allowed inputs include market feeds, constituent data, options, cross-asset context, market internals, event state, and source-quality metadata.

Allowed outputs are stable point-in-time measurements only.

The Observation Engine must not output:

- trade direction recommendations;
- candidate IDs or option structures;
- position or account state;
- entry/exit decisions;
- P&L-derived features;
- post-outcome labels at prediction time.

The existing SPY UTPM variable dictionary is the canonical vocabulary. It already covers point-in-time infrastructure, raw market data, technical state, breadth and constituents, options/dealer state, auction behavior, order flow, intermarket context, and structural positioning. Alpha V2 must reuse and version that dictionary rather than create a parallel indicator vocabulary.

The Decision and Output modules of that dictionary are downstream of the market model and are not admissible model sensors.

### 2.2 Market Model

Purpose: answer **what market environment exists now?**

The Market Model has two outputs:

1. a continuous state representation;
2. a probability distribution over latent regimes.

The canonical state axes begin with:

- trend;
- breadth / participation;
- volatility pressure;
- dispersion / correlation;
- liquidity / flow;
- auction / location;
- options / dealer state;
- cross-asset risk;
- positioning / actor state;
- cross-horizon agreement;
- transition pressure.

Each axis is a measurement-backed state variable, not a trade opinion. The state vector may evolve by version, but a versioned model must produce the same state for the same point-in-time inputs.

The regime output is a full posterior probability vector. A dominant regime label is diagnostic only and must never erase posterior uncertainty.

Initial regime vocabulary may include:

- QUIET_BULLISH_TREND
- QUIET_BEARISH_TREND
- MEAN_REVERTING_AUCTION
- COMPRESSION
- DIRECTIONAL_EXPANSION_UP
- DIRECTIONAL_EXPANSION_DOWN
- VOLATILITY_EXPANSION
- VOLATILITY_DECAY
- DISLOCATION
- TRANSITION

This vocabulary is not sacred. Regimes may be merged, split, or replaced when out-of-sample evidence supports a better latent-state representation. They may not be renamed or split merely to improve historical trade P&L.

### 2.3 Forecast Engine

Purpose: answer **given the current state and regime uncertainty, what is likely to happen next, when, and with what uncertainty?**

The forecast is a distribution, not a trade signal.

Required forecast families:

#### Regime persistence

- P(current regime survives +5m)
- P(current regime survives +15m)
- P(current regime survives +30m)
- P(current regime survives +60m), when supported
- expected remaining duration
- transition-time quantiles

Survival probabilities must be monotone non-increasing with horizon.

#### Regime transition

- probability distribution over successor regimes;
- transition uncertainty;
- conditional direction if the successor is directional;
- transition-time distribution.

The successor model is separate from the persistence model. A strong duration model does not imply a strong destination model.

#### Returns

For supported horizons, emit:

- P(up);
- expected return;
- median return;
- calibrated quantiles / intervals;
- tail probabilities;
- touch probabilities for relevant structural levels.

#### Volatility

Emit distributions or calibrated expectations for:

- realized volatility / realized move;
- range survival;
- volatility expansion / contraction;
- implied-volatility change when supported.

#### Path

Terminal price alone is insufficient for options. The forecast layer must also describe path behavior such as:

- MFE distribution;
- MAE distribution;
- reversal probability;
- first-passage / touch probabilities;
- path roughness / directional persistence;
- transition timing.

Two forecasts with the same terminal return but different paths can imply different option values.

### 2.4 Trader

Purpose: answer **is the physical forecast P different enough from market pricing Q to create executable value after friction and uncertainty?**

Only this layer may reason about:

- option candidate families;
- strikes / widths / expirations;
- market-implied distribution Q;
- bid/ask and fill uncertainty;
- commissions and fees;
- account/risk constraints;
- timing of entry;
- size;
- management and exit.

No trade is a first-class action.

The Trader may return:

- WAIT: forecast edge may exist but entry timing is poor;
- NO_EDGE: P and Q do not differ enough after costs;
- ABSTAIN: required evidence, calibration support, data quality, or execution certainty is inadequate;
- TRADE: an approved bounded-risk expression has positive expected value after friction and uncertainty.

A directional probability by itself is not an edge. The economic question is the expected payoff under P relative to the executable market price implied by Q, with explicit model and execution uncertainty.

## 3. HGB and Beta

HGB and Beta are forecasting instruments, not trading-rule generators.

They may contribute measurements or forecast components such as:

- directional probability;
- expected move;
- confidence / uncertainty;
- signal stability;
- constituent participation;
- breadth acceleration;
- disagreement with the core model.

They may not directly create option structures or become rules such as "take the first HGB trade." A historically profitable HGB expression is evidence about the Trader layer, not a definition of the market model.

Beta should remain an independent witness where practical. Correlated agreement is useful evidence; independent disagreement is also useful evidence. Merging every model into one opaque learner destroys diagnostic value.

## 4. P and Q are different objects

P is the physical probability distribution estimated from the market model and forecast engine.

Q is the risk-neutral / market-implied distribution represented by the option surface and executable prices.

They must be stored and versioned separately.

A candidate can be evaluated only after P exists. Candidate geometry, candidate rank, selected action, fill, and future P&L are prohibited forecast inputs.

Risk-neutral density can be an observed market measurement describing Q, but it must never be mislabeled as the physical forecast P.

## 5. Determinism and replay invariants

For a frozen code version, feature dictionary, calibration state, and model artifact set:

```text
same point-in-time raw observations
    -> same standardized measurements
    -> same market state
    -> same regime posterior
    -> same forecast P
```

A later trade result must not alter the historical forecast when the same frozen version is replayed.

Any calibrated replacement is a new version and must remain reproducible alongside the old version.

Replay is therefore split into two independent experiments.

### Forecast replay

```text
historical sensor stream
    -> observation engine
    -> market model
    -> forecast P
    -> freeze forecast
    -> advance clock
    -> observe realized future
    -> verify forecast
```

### Trading replay

```text
frozen historical forecast P
    + historical option market / Q
    + executable-price model
    -> Trader
    -> candidate / no trade
    -> realized economic result
```

Trading replay is not allowed to feed information back into the historical forecast replay in the same experiment.

## 6. Learning and error attribution

Every matured forecast and every completed trade is decomposed by failure domain.

### Observation errors

Examples: stale quote, bad constituent membership, incorrect correction handling, timestamp error, missing option surface, bad corporate-action adjustment.

Update: ingestion, data quality, point-in-time rules, or measurement computation.

### State errors

The raw measurements were valid but the compact state representation was misleading.

Update: state representation only.

### Regime-classification errors

The state was measured correctly but assigned to the wrong latent environment.

Update: regime model only.

### Persistence errors

Example: forecasts labeled 70% survival mature only about 52% of the time.

Update: duration / hazard model calibration only.

### Transition errors

Persistence timing is correct, but the successor regime is repeatedly wrong.

Update: transition model only.

### Direction / magnitude / path errors

The correct environment and transition were identified, but future returns, volatility, or path shape were wrong.

Update: the corresponding forecast head only.

### Q / pricing errors

Physical forecast is good, but the market-implied distribution or executable-price estimate is wrong.

Update: Q extraction / pricing model only.

### Strategy-expression errors

P and Q were correctly estimated, but the chosen option payoff was inferior.

Update: Trader candidate valuation / selection only.

### Execution errors

The candidate had edge at modeled prices but fills, slippage, fees, or latency destroyed it.

Update: executable-economics and execution models only.

### Management errors

Entry expression was valid but management / exit reduced value.

Update: position management only.

No component may be patched merely because the final trade lost.

## 7. Calibration and validation

### 7.1 Primary unit of independence

Complete market sessions are the primary grouping unit for statistical inference. Intraday rows from the same session are dependent and must not be treated as independent samples.

### 7.2 Splitting

Use expanding or rolling walk-forward folds with:

- complete-session grouping;
- purge windows appropriate to label horizons;
- embargo between train and validation/test when needed;
- untouched outer test data;
- final holdout not used for threshold selection.

### 7.3 Probability calibration

Evaluate probability heads with:

- Brier score;
- log loss where appropriate;
- reliability diagrams / calibration curves;
- calibration error by probability bin;
- calibration by regime, time of day, volatility environment, and data-quality tier;
- bootstrap confidence intervals grouped by session.

Calibration changes probability mapping. It does not invent new trading rules.

### 7.4 Distribution calibration

Evaluate:

- empirical quantile coverage;
- interval width;
- CRPS / pinball loss where appropriate;
- tail coverage;
- touch / first-passage calibration;
- conformal coverage under walk-forward evaluation;
- coverage drift under distribution shift.

### 7.5 Duration / lifecycle validation

Evaluate:

- survival Brier score by horizon;
- integrated Brier score;
- duration MAE / median absolute error;
- calibration of transition-time quantiles;
- successor-regime log loss / Brier score / accuracy;
- calibration conditional on current regime age.

Duration and successor quality are reported separately.

## 8. Modeling approach

No single algorithm is mandated. The architecture is contract-driven.

Recommended model families are deliberately heterogeneous:

- latent regime posterior: HMM / hierarchical HMM / discriminative classifier ensemble;
- regime duration: discrete-time hazard or hidden semi-Markov duration model;
- successor regime: calibrated multiclass transition model conditioned on current posterior, state, age, and transition pressure;
- returns: quantile models and/or calibrated distributional ensemble;
- volatility: realized-move / variance models conditioned on state and regime posterior;
- path: state-conditioned empirical residual-block bootstrap with conservative fallback;
- uncertainty: ensemble disagreement + calibration health + OOD + data quality + model age.

Use the full regime posterior as a conditioning input. Do not collapse uncertain states to a hard dominant label before forecasting.

## 9. Scientific Control Layer

The Scientific Control Layer owns:

- source data lineage;
- event-time and receive-time integrity;
- feature definitions and versions;
- model artifact hashes;
- calibration versions;
- experiment manifests;
- random seeds;
- walk-forward splits;
- benchmark definitions;
- ablation tests;
- drift monitoring;
- model promotion and rollback;
- immutable forecast tape;
- immutable verification tape.

A model or calibration update cannot overwrite historical artifacts. It creates a new version.

## 10. Canonical sensor boundary

The UTPM variable dictionary is divided by architectural role.

### Admissible before the Market Model

- Infrastructure / point-in-time integrity
- Raw Market Data
- Technical measurements
- Psychological / positioning measurements, when point-in-time and supportable

### Prohibited before the Trader

- Decision module fields
- Output module fields that encode selected actions or trade outcomes
- candidate geometry / candidate rank
- account risk / buying power as a market-state feature
- order / fill state
- realized trade P&L
- future labels

Market data that also influences execution, such as spread or liquidity, may exist in both observation and Trader contexts. Its market-model meaning must be defined independently of whether a candidate was selected.

## 11. Repository migration plan

The existing SPY-DER repository has useful infrastructure and should be refactored, not discarded.

### Phase A — contracts and safety

1. Preserve `CanonicalMarketSnapshot` and point-in-time feed provenance.
2. Preserve `MarketForecastBundle` as a P-only forecast object.
3. Fix all silent missing-value fallbacks in forecast-facing APIs.
4. Add explicit `MarketState`, `RegimePosterior`, and `RegimeLifecycleForecast` contracts.
5. Add tests that prohibit trade/candidate/execution fields in market-model contracts.
6. Keep all changes behind reversible pull requests.

### Phase B — Observation Engine

1. Import/version the UTPM dictionary as the canonical feature registry.
2. Map current SPY-DER features to dictionary variable IDs.
3. Separate raw, derived, standardized, missing, and quality states.
4. Implement constituent sensor aggregation without survivor leakage.
5. Freeze normalization by training fold; never normalize with future data.
6. Emit an immutable measurement bundle per decision timestamp.

### Phase C — Market State

1. Build continuous state axes from admissible measurements.
2. Add cross-horizon state alignment.
3. Measure representation stability and redundancy.
4. Version the state transformation.
5. Compare compact state against raw-feature forecast baselines through ablation.

### Phase D — Regime posterior

1. Define training labels / latent-state methodology without trade P&L.
2. Produce a full current-regime posterior.
3. Track current regime age.
4. Validate stability and transition consistency.
5. Retain the dominant label only as a diagnostic.

### Phase E — Lifecycle forecast

1. Train persistence / hazard model.
2. Train successor-regime model separately.
3. Calibrate both out of sample.
4. Add time-to-transition distribution.
5. Downgrade weak transition models to advisory rather than forcing them into trade policy.

### Phase F — Return / volatility / path forecast

1. Reuse existing V2/V3 forecast heads where valid.
2. Condition on the new market state and full regime posterior.
3. Add missing horizons only when support is adequate.
4. Produce calibrated distributions rather than isolated point estimates.
5. Record forecast uncertainty and OOD support.

### Phase G — P/Q Trader refactor

1. Move Legacy permissions, family preferences, and hard trade veto logic downstream of forecast creation.
2. Keep deterministic risk authority intact.
3. Build Q from the option surface independently of P.
4. Evaluate candidate payoffs under P at executable prices.
5. Support WAIT / NO_EDGE / ABSTAIN / TRADE distinctly.
6. Preserve bounded-risk-only candidate generation.

### Phase H — verification and calibration

1. Create matured forecast records independent of trades.
2. Attribute errors by component.
3. Build calibration dashboards by horizon / regime / session segment.
4. Prevent final P&L from becoming a generic model-training label.
5. Promote only components that improve their own out-of-sample objective without degrading critical calibration or safety gates.

## 12. Current codebase deltas discovered during reset

The reset begins with several concrete code-level observations:

1. `MarketForecastBundle` correctly states that missing forecasts should remain missing, but its legacy `prob_up` convenience property returned 0.0 when every directional head was unavailable, which implied `prob_down == 1.0`. This is a fail-closed violation and is being corrected first.
2. SPY-DER currently contains more than one concept named `StructuralState`: a lightweight compatibility contract and a richer structural state used by the feature service. The migration must converge these concepts rather than create another ambiguous state type.
3. The existing V3 forecast bundle already has useful regime-probability, path, competing-risk, uncertainty, OOD, and distribution extension points. They should be preserved and typed more strongly over time.
4. Existing policy adapters are downstream trading logic. They should not be allowed to redefine the market state or physical forecast.

## 13. Historical trading results

Historical strategy replays remain useful, but their role changes.

They can answer:

- whether a forecast was monetizable at historical option prices;
- whether candidate selection was sensible;
- whether execution assumptions were robust;
- whether management improved or damaged realized value.

They cannot define the market ontology.

A profitable historical expression is evidence for the Trader layer. A losing trade is not evidence that the market-state definition should be patched.

## 14. Definition of done

Alpha V2 reaches the intended architecture when all of the following are true:

1. Every decision timestamp has an immutable point-in-time measurement bundle or an explicit unavailable state.
2. Market state is reproducible from that bundle and a versioned transformation.
3. Current regime is represented as a calibrated probability vector.
4. Persistence, successor, timing, return, volatility, and path forecasts are independently verifiable.
5. Replaying the same frozen model on the same observations reproduces the same forecast exactly.
6. Forecast objects contain no candidate, strategy, execution, position, account, or P&L information.
7. The Trader consumes P only after it exists and compares it against Q and executable economics.
8. NO_EDGE / WAIT / ABSTAIN are normal outcomes, not failures to produce a trade.
9. Forecast verification occurs whether or not a trade was taken.
10. Learning is attributed to the responsible component rather than back-propagated from trade P&L indiscriminately.
11. Model promotion requires out-of-sample improvement and calibration evidence at the component level.
12. Live execution remains behind deterministic risk and operational controls.

That is Alpha V2's architectural contract.