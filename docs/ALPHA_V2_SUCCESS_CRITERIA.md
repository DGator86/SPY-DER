# Alpha V2 Success Criteria

Alpha V2 uses two separate definitions of success: economic success and operational success. Neither is inferred from a single backtest, and neither grants live broker authority automatically.

## Economic success

### Paper evidence floor

The minimum evidence floor is designed to reject small-sample, serially dependent, poorly calibrated, execution-free results.

A candidate must have at least:

- 60 independent paper sessions;
- 5,000 matured forecasts;
- 750 non-overlapping formal 15-minute forecast anchors;
- 750 non-overlapping formal 30-minute forecast anchors;
- 60 closed sandbox trades;
- 99% verified input/outcome data;
- 90% P/Q-ready observations;
- 90% trained primary-model support;
- 15-minute direction accuracy >= 53% with Wilson lower bound >= 50%;
- 30-minute direction accuracy >= 52.5% with Wilson lower bound >= 50%;
- 15-minute Brier <= 0.245 and Brier skill >= 0.01;
- 30-minute Brier <= 0.25 and Brier skill >= 0;
- 72%-88% empirical forecast-interval coverage;
- net executable P&L >= 0;
- profit factor >= 1.15;
- one-sided lower-bound expectancy >= 0;
- maximum drawdown <= $600 at the validation risk scale;
- doubled-friction P&L >= 0;
- last-20-trade net P&L >= 0 and profit factor >= 1.0;
- sandbox fill fraction >= 95%;
- mean fill slippage <= $12 at the validation risk scale;
- realized loss <= 1.10x modeled maximum loss;
- at least 3 tested regimes covering >= 75% of trades;
- no sampled regime bucket with negative expectancy;
- zero replay mismatches.

Passing this tier means only that the system has cleared the minimum paper evidence floor.

### Significant-edge tier

For this project, "significantly profitable" requires every base gate plus all of the following:

- profit factor >= 1.30;
- one-sided lower-bound expectancy strictly > 0;
- doubled-friction P&L strictly > 0;
- aggregate P&L remains positive after removing the ten largest winning trades;
- candidate-ranking score is not negatively correlated with realized P&L;
- candidate-ranking score is not negatively correlated with realized win outcome.

These additional tests explicitly attack two failure modes observed in prior research: profit concentration and inverted ranking quality.

Synthetic or replay-only profitability can identify a candidate and guide research, but it cannot by itself satisfy this tier.

## Operational success

The project target is zero observed critical system incidents during the qualifying evidence window. Every one of the following must be zero:

- critical stage failures;
- journal-persistence failures;
- unresolved broker-reconciliation errors;
- duplicate-order breaches;
- stale-decision executions;
- non-sandbox orders during sandbox qualification;
- deterministic replay mismatches;
- realized-loss/model-loss boundary violations.

In addition, CI must pass dependency consistency, lint, formatting, compilation, strict typing, the complete test suite, at least 80% branch coverage, focused parity/replay/safety suites, and wheel/sdist build.

This does not mean software can be proven mathematically defect-free. It means no known critical issue is tolerated in the qualifying evidence and failures are designed to fail closed rather than become trades.

## Authority

Research may autonomously generate hypotheses, train models, run ablations, execute recorded/synthetic replay, paper trade, calculate evidence, reject challengers, and stage qualified challengers for review.

Research may not autonomously turn a challenger into the champion or enable real-money broker authority. Champion promotion requires objective evidence plus explicit human approval. Real-money authority remains a separate release and operational-risk decision.

## Donor systems

Alpha-SPY and Beta-SPY are engineering donors, not ground truth. Their pinned commits are recorded in `config/alpha_v2/donor_systems.json`.

Alpha-SPY is the preferred donor for production-market-data integrity, sandbox execution, P/Q, forecast audit, validation, reconciliation and replay. Beta-SPY is retained as an independent breadth/constituent forecasting witness. Donor strategy rules are not copied upstream into MarketState or physical P merely because they were historically profitable.
