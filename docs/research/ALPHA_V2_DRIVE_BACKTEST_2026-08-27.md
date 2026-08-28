# Alpha V2 Google Drive Replay — 2026-08-27

Status: **BLOCKING EVIDENCE — NOT PROMOTABLE**

## Scope

Point-in-time replay of the Google Drive `Alpha-Beta Data` daily tapes covering 24 regular-market sessions from 2026-07-27 through 2026-08-27.

The archive contains complete SPY/constituent minute bars for all 24 sessions, but recorded Alpha forecast signals are present on only 8 sessions (2026-08-18, 19, 20, 21, 24, 25, 26, 27). The paper ledger contains 10 closed positions.

This report deliberately separates:

1. market/forecast verification;
2. recorded paper-trading outcomes;
3. exploratory replacement-model diagnostics.

A winning or losing trade is not used to relabel or calibrate the market forecast.

## Replay integrity

Each of the 24 sessions contains exactly 390 SPY one-minute bars from 13:30 through 19:59 UTC, with no duplicate SPY minute timestamps, no missing SPY closes, and no greater-than-one-minute SPY bar gaps.

Forecast scoring is point-in-time:

- anchor = last SPY trade at or before the forecast's own `created_at` timestamp;
- realized intraday outcome = last SPY trade at or before `created_at + horizon`;
- realization is accepted only when the tape has a trade within five seconds of the target time;
- forecasts extending beyond the regular session are excluded;
- duplicate repeated payloads are deduplicated by `(session, horizon, created_at)`.

The stored payload SPY anchor and the tape price at forecast creation agree with 0.00 bps median error. Median source-trade age is approximately 0.32 seconds. This makes a clock/anchor mismatch an implausible explanation for the observed forecast failure.

## Matured forecast sample

| Horizon | Matured forecasts | Sessions |
|---|---:|---:|
| 5m | 2,236 | 8 |
| 15m | 2,157 | 8 |
| 30m | 2,038 | 8 |
| 60m | 362 | 8 |
| 120m | 266 | 7 |
| EOD | 458 | 8 |
| **Total** | **7,517** | **8** |

The Drive archive therefore exceeds the raw matured-forecast-count floor but does **not** satisfy the 60-independent-session production evidence floor.

## Recorded Alpha forecast results

| Horizon | Accuracy | Brier | Brier skill vs 0.50 | AUC | Expected-return correlation |
|---|---:|---:|---:|---:|---:|
| 5m | 50.00% | 0.2652 | -0.0609 | 0.4888 | -0.1120 |
| 15m | 48.91% | 0.2865 | -0.1460 | 0.4727 | -0.1720 |
| 30m | 45.14% | 0.3305 | -0.3221 | 0.4145 | -0.2501 |
| 60m | 40.61% | 0.3900 | -0.5599 | 0.3695 | -0.2564 |
| 120m | 36.47% | 0.4196 | -0.6783 | 0.3333 | -0.2800 |
| EOD | 51.31% | 0.3723 | -0.4893 | 0.4710 | -0.2709 |

The primary 15m and 30m horizons fail the configured validation gates.

### 15m

- accuracy: 48.91% vs required 53%;
- 95% Wilson lower bound: 46.80% vs required 50%;
- Brier: 0.2865 vs maximum 0.245;
- Brier skill: -0.1460 vs required +0.01.

### 30m

- accuracy: 45.14% vs required 52.5%;
- 95% Wilson lower bound: 42.99% vs required 50%;
- Brier: 0.3305 vs maximum 0.250;
- Brier skill: -0.3221 vs required >= 0.

Formal-anchor-only results are worse, not better: 15m accuracy is 44.20% over 138 formal anchors and 30m accuracy is 35.48% over 62 formal anchors.

## Anti-predictive structure

The expected-return forecast is negatively correlated with realized future return at every evaluated horizon. More importantly, the 30m and 60m expected-return correlations are negative on **all eight** forecast sessions; 120m is negative on all seven sessions with matured observations.

That consistency is evidence of a structural forecast-construction problem rather than a single bad session.

A diagnostic inversion (`1-p_up`, `-expected_return`) improves discrimination — for example 30m AUC rises from 0.4145 to 0.5855 and 60m directional accuracy rises from 40.61% to 59.39% — but still leaves probability calibration below the required Brier standard. The inversion is **not** accepted as a trading rule or production fix; it is diagnostic evidence only.

## Likely model-layer cause

Inspection of the pinned Alpha-SPY donor shows two mechanisms consistent with the replay failure:

1. the physical P distribution can place 70% weight on a constituent Student-t distribution whose center extrapolates recent exponentially weighted constituent returns across the future horizon, with only 30% weight on the calibrated/fallback forecast;
2. regime calibration clips its slope to `[0.20, 2.0]`, which prevents calibration from representing a persistent negative forecast/realization relationship even when the walk-forward evidence is anti-correlated.

The replay also shows the legacy expected-return output positively associated with recent SPY momentum while longer-horizon realized returns in the tested period are often mean-reverting. This is consistent with over-persistence/continuation rather than an output-sign serialization error.

The timestamp check argues against a scoring-clock defect, and source inspection does not show a simple final-output sign inversion.

## Paper ledger

The Drive archive contains 10 recorded closed paper positions:

- net realized P&L: **-$135**;
- wins / losses: **5 / 5**;
- win rate: **50.0%**;
- gross profit: **$181**;
- gross loss: **$316**;
- profit factor: **0.573**;
- mean trade: **-$13.50**;
- worst trade: **-$126**;
- observed sequential max drawdown: **$147**.

This sample fails the profitability requirement and is far below the required 60 settled paper trades.

The exported high-level Alpha signal at each recorded paper-position opening was `NO_TRADE`. Therefore these ten positions cannot safely be attributed as trades generated by the recorded Alpha forecast policy; they are evidence about the archived paper ledger, not proof of the current Alpha V2 Trader's economics.

## Exploratory market-state replacement

As a diagnostic, a timestamp-only state vector was built directly from the 24 complete constituent-minute sessions using only information available at each timestamp: SPY trend/realized volatility/VWAP/range/volume state plus equal-weight constituent returns, breadth, dispersion, coherence, participation and breadth acceleration.

A session-walk-forward, regularized model was trained only on prior sessions and evaluated on the same eight held-forward sessions. No trade outcomes, option candidates, fills or P&L entered the state or forecast inputs.

This prototype removed the severe legacy anti-correlation, and one conservative 30m specification reached approximately 51.3% direction accuracy / AUC 0.515, but it did **not** clear the Alpha validation thresholds. It is therefore research evidence only and is not a challenger eligible for promotion.

## Option P/Q replay limitation

The daily Drive tapes do not contain a reconstructable full historical option chain/surface. The full Alpha/Beta database snapshots are available in Drive, but the Alpha archive is approximately 1.40 GB and the Beta archive approximately 544 MB, both above the connector's 256 MB single-file transfer ceiling.

Accordingly, this pass can verify recorded forecasts and recorded paper P&L, but it cannot honestly reconstruct every historical executable option candidate, Q surface, bid/ask, fill path and counterfactual strategy choice. No option prices were invented.

## Promotion decision

**FAIL / BLOCK.**

The available evidence does not support `SIGNIFICANT_EDGE_CANDIDATE`, paper promotion, or real-money authorization.

The correct response is model repair and a new frozen replay, not trading-rule fitting.

## Required repair direction

1. replace momentum extrapolation as the dominant center of physical P with a state/regime-conditioned forecast whose persistence and transition components are separately estimated;
2. let calibration diagnose sign/orientation failure instead of forcing a positive slope;
3. keep regime persistence, successor state, direction and magnitude as separate verification heads;
4. preserve full forecast objects before Trader evaluation;
5. rerun the same frozen Drive sessions after the repair and require improvement at both primary horizons, not merely aggregate P&L;
6. continue collecting independent paper sessions until all configured production-evidence floors are met.
