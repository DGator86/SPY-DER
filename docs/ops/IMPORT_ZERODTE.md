# Importing 0DTE's recorded history

0DTE has been recording every live tick since long before SPY-DER had a market
service. That is real recorded market history sitting on the VPS, and it is the
difference between training the forecast models on actual markets **today** and
waiting weeks for SPY-DER's own recordings to accumulate.

`spy-der-import-zerodte` converts those recordings into SPY-DER canonical
market recordings. It is **read-only** with respect to 0DTE — it never writes to
the source directory, so it is safe to run while 0DTE is still live.

## Sequence

```bash
# 1. Import. --source is required; on the VPS the recorder writes to
#    /var/lib/zerodte/ticks (see 0DTE's chain_store.ChainRecorder).
spy-der-import-zerodte \
    --source /var/lib/zerodte/ticks \
    --state-root /var/lib/spy-der

# 2. Train on it.
spy-der-train --state-root /var/lib/spy-der

# 3. Read the verdict, then serve if it earned it.
spy-der-engine --forecast-group <id> --forecast-load-mode research
```

Re-running the import is cheap: sessions already under `<state-root>/market` are
skipped unless `--overwrite` is given.

**`--overwrite` will not replace a session SPY-DER recorded itself.** Once
`spy-der-market` is collecting, the two write the same `market/<session>.jsonl`,
and overwriting would silently delete live history. The importer detects any
snapshot lacking the `source:zerodte_import` flag and refuses that session,
reporting it rather than aborting the rest of the run. Move the file aside if
you genuinely mean to replace it.

Import first, then start the market service, and the situation never arises.

**The path is not baked into the package.** `--source` has no default because
SPY-DER must deploy without 0DTE present, and
`tests/unit/test_deploy_independence.py` fails on any legacy path in package
source. The operator names the directory; the package assumes nothing.

## What comes across

| 0DTE record | SPY-DER destination |
|---|---|
| `market.spot` | `underlying_price` |
| `option_rows[]` (side, strike, oi, gamma, delta, bid, ask, volume) | `option_chain` — full canonical `OptionQuote` |
| `bars[]` (incremental) | `bars_1m`, reassembled into a rolling window |
| `market.vix9d/vix/vix3m/vvix/vvix_baseline` | `volatility_term_structure` |
| `market.rsp_spy_div/sector_align/top10_pressure` | `breadth` |
| `market.has_catalyst/catalyst_label` | `catalyst_state` |
| `{"t":"settle"}` | reported by the importer per session |

Because `option_rows` carries per-contract gamma and open interest, GEX, flow
and RND are **recomputed from scratch** by SPY-DER's own feature pipeline rather
than copied from 0DTE's derived values. An imported session therefore trains
against exactly the features a live session would produce.

## Four source-format details that matter

These are the things that would silently corrupt an import if handled naively,
and each has a test:

- **Bars are stored incrementally.** Each tick record holds only the bars newer
  than the previous one. Reading a tick's `bars` as the whole window would leave
  nearly every snapshot with a handful of bars and quietly kill every
  history-dependent feature. The importer accumulates across ticks.
- **Bar timestamps are naive UTC.** They are `str(numpy.datetime64)` of an
  epoch-derived array. Localizing them to ET would shift every bar by four or
  five hours and file the session's bars under the wrong day.
- **Unavailable numbers are `NaN`, not absent.** 0DTE's flow and breadth fields
  default to `float("nan")`. Those become `None`, never `0` — a zero
  `rsp_spy_div` asserts *perfectly neutral breadth*, which is a confident claim
  to manufacture out of a missing feed.
- **A crashed recorder leaves a truncated final line.** It is skipped; the rest
  of the session imports.

## Provenance

Imported snapshots go through the same assembler as live ticks, so their ids and
content hashes are computed identically and nothing downstream has to special-
case them. They are still auditable as imported: every one carries a
`source:zerodte_import` quality flag in `data_quality.flags`.

## Older recordings

`option_rows` post-dates the recorder itself. Ticks written before it still
import — they keep spot, bars, volatility surface and breadth — but carry no
option chain, so GEX, flow and RND are absent for those snapshots and the
`option_chain` feed component reports missing. The CLI warns with a count rather
than leaving you to wonder why some sessions train thinner than others.
