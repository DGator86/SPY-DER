# SAFETY INVARIANTS

- All contracts include schema versions.
- Market and journal timestamps must be timezone-aware.
- Candidates are immutable and must have defined maximum loss.
- Undefined-risk and stock-dependent candidates are rejected.
- Synthesis cannot override hard vetoes.
- Synthesis cannot increase deterministic risk envelope.
- Missing required data must cause abstention/failure.
- Order/position transitions are explicit and validated.
- Decision provenance fields are retained in `SystemDecision`.
