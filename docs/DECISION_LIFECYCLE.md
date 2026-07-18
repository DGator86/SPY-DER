# DECISION LIFECYCLE

1. Ingest canonical snapshot.
2. Build features.
3. Compute structural state and hard vetoes.
4. Forecast underlying market (prediction output).
5. Generate immutable bounded-risk candidates.
6. Score/rank candidates (prediction output).
7. Synthesize policy decision from approved candidates only.
8. Apply deterministic risk firewall.
9. Transition execution/position states.
10. Persist journal events with deterministic serialization.
