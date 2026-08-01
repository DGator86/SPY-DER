# SPY-DER frozen model baseline v1

This directory is an **immutable research baseline**, not a production deployment.
It records the best configuration reported by the portable synthetic-research
handoff so that later reproduction work has a stable, reviewable target.

## Authority and limitations

- Synthetic reproducibility is not proof of real-market validity. Timestamp-accurate
  real-data VPS replay is still required.
- Synthetic worlds 800–999 are untouched test worlds and must not be used for
  retuning.
- The 60-minute model is advisory only and has no trade authority.
- State alerts are shadow only.
- Path and forecast-fan models are disabled.
- Live execution and broker routing are excluded.
- Original full-package artifacts are not vendored here and must be added and
  verified separately when they become available.

The files in this directory are checksum-protected. Never silently modify this
baseline: any configuration or evidence change requires a new versioned baseline
directory and its own provenance and checksums.
