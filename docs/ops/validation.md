# SPY-DER validation

`spy-der validate` runs the parity gates in `docs/CUTOVER_PLAN.md` over recorded
sessions and writes a report. It is **reports only**: it never promotes, never
trades, and only reads the state root.

## Timers

| Timer | When | Window |
|---|---|---|
| `spy-der-validation-daily` | daily | `--window recent --days 5` |
| `spy-der-validation-weekly` | weekly | `--window full --days 60` |

```bash
sudo cp deploy/spy-der-validation-{daily,weekly}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spy-der-validation-daily.timer spy-der-validation-weekly.timer
```

Manual run:

```bash
cd /opt/spy-der
venv/bin/spy-der validate \
    --state-root /var/lib/spy-der \
    --reports-dir /var/lib/spy-der/reports/validation \
    --window recent --days 5
```

Reports land next to the Dojo's, and the dashboard API serves them at
`/v1/validation/latest`:

```
/var/lib/spy-der/reports/validation/validation_YYYYMMDD_HHMMSS.json
/var/lib/spy-der/reports/validation/latest.json
```

## Verdicts

Each gate reports one of three verdicts. The distinction is the whole point.

| Verdict | Meaning |
|---|---|
| `pass` | Ran against real recorded data and passed |
| `fail` | Ran and failed. Exit status is non-zero, so the unit fails |
| `pending` | Could not run — needs a capability that has not landed, or a parallel 0DTE run |

**A pending gate is never counted as a pass.** A validation report that silently
green-lights an ungated quantity is worse than no report.

## Gates that run today

| Gate | What it proves |
|---|---|
| `recorded_session_replay` | Every recording in the window replays clean: content hashes match, no sequence gaps, uniform schema |
| `raw_market_snapshots` | The window carries one schema and one normalization version — snapshots either side of a normalization change are not comparable, so a parity run over them is meaningless |
| `stale_feed` | Degradation propagated: any snapshot carrying a `STALE`, `MISSING` or `INVALID` feed observation also carries a data-quality penalty or a missing-component record |

`stale_feed` is the downstream half of `spy_der.market_data.freshness`'s
fail-closed classification. A degraded feed that reached a clean-looking
snapshot is the exact failure the design exists to prevent, so it fails the
gate rather than being reported as a ratio to eyeball.

`DELAYED` is a documented non-fatal state and is not treated as degradation.

## Gates that are pending

Every quantity in the cutover plan's tolerance table that requires comparing two
runtimes — candidate IDs and geometry hashes, maximum loss, capital required,
hard vetoes, position sizing, size scalar, settlement, forecast probabilities
and cone bounds, feature values, regime labels, journal output — is reported
`pending`. Each needs a parallel 0DTE run over identical inputs, asserted first
by `spy_der.runtime.parity.assert_identical_inputs`. Validating them from one
side's recordings would mean comparing SPY-DER against itself, which passes
unconditionally and proves nothing.

They move to `pass`/`fail` when the parallel run lands (cutover step 4).

## Exit status

`0` when no gate failed (pending gates are not failures), `1` otherwise — so a
failing gate surfaces as a failed unit in `systemctl` rather than as bad news
buried in JSON under a green status.

```bash
systemctl status spy-der-validation-daily.service
curl -s http://127.0.0.1:8788/v1/validation/latest | jq '.summary, (.gates[] | select(.verdict!="pending"))'
```
