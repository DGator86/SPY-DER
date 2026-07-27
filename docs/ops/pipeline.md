# SPY-DER runtime pipeline

The service set and what each stage reads and writes under `/var/lib/spy-der`.

```
spy-der market       providers ──────────────▶ market/<session>.jsonl
spy-der engine       market/ ────────────────▶ candidates/ + features/ + journal
spy-der settlement   market/ + journal ──────▶ settlements/<session>.jsonl + journal
spy-der dojo         experience ─────────────▶ reports/dojo/
spy-der validate     market/ ────────────────▶ reports/validation/
spy-der dashboard-api  (read-only) ──────────▶ http://127.0.0.1:8788
```

## On-disk formats

There are exactly two, and neither is new.

**Stage artifacts** (`market/`, `candidates/`, `features/`, `settlements/`) are JSONL records
carrying `seq`, an identity, a schema version and a `record_hash` over the
payload — the envelope `spy_der.market_data.recording` already wrote for market
ticks. `ReplayFeed` verifies content hashes, sequence continuity and schema
uniformity, and fails closed on corruption, so every stage artifact is
integrity-checked by code that was already tested. `spy_der.runtime.artifacts`
is the shared writer.

**The journal** (`journal/journal.db`) is `SqliteJournalStore`: append-only,
hash-chained, WAL. It is the system of record for *what happened*; the artifact
files are the bulk output a stage produced.

## `spy-der engine`

Deterministic: `PrivateNetwork=true`, no AI, reproducible from a recording.

Reads `market/<session>.jsonl`, rebuilds each snapshot with
`spy_der.contracts.market_parse.snapshot_from_dict`, and runs the deterministic
stages. Writes the candidate universe to `candidates/` and a
`candidates_generated` event to the journal.

Idempotent: a snapshot whose artifact is already recorded is skipped, so a
restart resumes rather than duplicating.

### Stage availability is reported, never faked

| Stage | State |
|---|---|
| `candidates` | **runs** — `generate_candidate_universe` is deterministic and complete |
| `features` | **runs** — `SnapshotFeaturePipeline` builds a `FeatureBundle` per snapshot |
| `forecast` | unavailable — no trained model group is configured |

The feature stage assembles eight families — the multi-timeframe matrix, GEX,
volatility, RND, flow, breadth, the volatility surface and session context —
into a flat, sorted `(name, value)` map written to `features/` and journaled as
`features_computed`. Families are independent: one that raises is recorded in
`failed_families` and journaled as `feature_stage_failed` while the rest of the
bundle still lands, because a pathological chain should cost the RND summary
rather than the whole tick. A family with no usable inputs is reported in
`missing_families` and its keys are simply absent — consumers distinguish
"unknown" from a real reading by key presence, never by a sentinel value.

`JournalEventType` carries `FORECAST_UNAVAILABLE` and `FEATURE_STAGE_FAILED` as
first-class outcomes: the design already says a stage may legitimately not run.
With no model registry, the engine journals `forecast_unavailable` per snapshot
rather than serving `heuristic_bundle`'s neutral 0.5, which is marked
research-only and which downstream stages would read as a real forecast.

When a model group lands, the forecast stage starts producing
`forecast_generated` and the events change accordingly. Nothing else moves.

## `spy-der settlement`

Settles a closed session and labels its outcomes under the originating
`snapshot_id`.

Nothing on this box takes live positions (`SPY_DER_EXECUTION_MODE=shadow`,
`SPY_DER_ALLOW_LIVE=0`, and no unit writes a fill), so every candidate settles
as a **counterfactual** — what the outcome would have been had it been taken.
`evaluation.settlement.settle_session` already models the `traded` / `blocked`
split, and counterfactuals are what the Dojo's recorded phase learns from. Real
fills populate the `traded` side later without changing this shape.

Two choices worth knowing:

- **Settlement price** is the underlying price on the session's final recorded
  snapshot — `SettlementSource.SESSION_CLOSE`. The Yahoo adapter now exists and
  runs in the market service's `settlement_provider` slot, where it marks the
  `settlement` feed component live and backstops the volatility surface; this
  service still derives the settlement *price* from the tape, because it runs
  offline and deriving it there keeps settlement deterministic and reproducible.
  `YahooProvider.settlement_price` is available for an online backfill after an
  outage, which is the case the tape cannot cover.
- **Candidates are regenerated from the tape**, not parsed back out of the
  engine's artifacts. Generation is deterministic and the snapshot round-trip is
  byte-identical, so regeneration yields the same universe by construction and
  avoids a second deserializer that could drift from the factory. The journal
  still decides *which* snapshots to settle — only those with a
  `candidates_generated` event.

A session is never settled while it may still be trading: it must either record
`SessionStatus.CLOSED` or be dated in the past.

## Ordering

`engine` and `settlement` both declare `After=spy-der-market.service`.
Settlement additionally depends on the engine having run — it settles what the
journal says was generated — but it does not need the engine running
*concurrently*; it will settle a session on any later pass.

```bash
sudo systemctl enable --now spy-der-market.service \
                            spy-der-engine.service \
                            spy-der-settlement.service \
                            spy-der-dashboard-api.service
```

## Verifying a run

```bash
# artifacts verify through the same reader the market recordings use
ls -l /var/lib/spy-der/{market,candidates,settlements}/

# the journal chain
python - <<'PY'
from spy_der.journal.store import SqliteJournalStore
from collections import Counter
j = SqliteJournalStore("/var/lib/spy-der/journal/journal.db")
print(Counter(e.event_type for e in j.iter_events()))
print("chain verifies:", j.verify_chain())
PY
```
