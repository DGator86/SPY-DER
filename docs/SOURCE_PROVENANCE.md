# SOURCE PROVENANCE

Authoritative record of how the System A (`DGator86/0DTE`) baseline was obtained,
pinned, and verified for SPY-DER. Produced during **Phase 0 — Source access and
baseline** (`docs/SPY_DER_MASTER_SPEC.md` §63, §71).

This document establishes that a real, inspectable System A source tree backs the
Phase 0 inventory and migration map. It makes no migration, parity, or performance
claims (spec §5, §67).

---

## 1. System A identity

| Field | Value |
|---|---|
| Repository | `DGator86/0DTE` |
| Role | System A — behavioral baseline, migration source, comparison/rollback system (spec §4.1) |
| Pinned commit | `de4a6e7ced98ff97c778e8b4418c08848d7ce82d` |
| Pinned ref | `refs/heads/main` |
| HEAD subject | `Merge pull request #128 from DGator86/agent/canonical-snapshot-shadow-runtime` |
| Source runtime | Python 3.11+ (`HANDOFF.md` §1; CI `.github/workflows/ci.yml` uses `3.11`) |
| Captured (UTC) | `2026-07-20T04:46:18Z` |

## 2. Access method

- **Method:** GitHub-authorized repository (`source_access_method: github`), satisfying
  spec §5 option 2.
- **Clone:** shallow (`git clone --depth 1`) into `/workspace/0dte`, giving the preferred
  side-by-side workspace of spec §5:

  ```
  /workspace/
  ├── SPY-DER/   (this repo, System B)   -> checked out at /home/user/SPY-DER
  └── 0dte/      (System A, pinned)       -> /workspace/0dte
  ```

- A shallow clone is sufficient for Phase 0 (tree inventory + baseline pin). Full history
  (`git fetch --unshallow`) is only required later for blame/log/bisect during parity work.

## 3. Baseline lock

The immutable pin lives in [`baseline/system_a.lock.json`](../baseline/system_a.lock.json)
with the schema required by spec §4.1. Verifiable hash artifacts are stored under
`baseline/manifests/`:

| Artifact | Contents | Hash (sha256) |
|---|---|---|
| `baseline/manifests/system_a_tree.txt` | `git ls-tree -r <commit>` (305 entries) | `6d532176e7f391acb8a079b48772be8e581bc8a69002ca4599006fd09afd8e31` |
| `baseline/manifests/system_a_tests.txt` | Sorted list of 112 pinned test files | `b3e834dd93dc998f24e6187d8f8ed0b3ba808d52e3bc8a5de00b5b7e3619fc10` |
| `baseline/manifests/system_a_requirements.txt` | Verbatim `requirements.txt` at the pin | `ed4c336e5df9f2065040657a2063ef961a70cfd8cb7facd890216ba899a5ed9d` |

## 4. Verification procedure

Any reviewer with access to `DGator86/0DTE` can reproduce every hash in the lock file:

```bash
git clone --depth 1 https://github.com/DGator86/0DTE /tmp/0dte-verify
cd /tmp/0dte-verify
test "$(git rev-parse HEAD)" = "de4a6e7ced98ff97c778e8b4418c08848d7ce82d"

git ls-tree -r HEAD                          | sha256sum   # -> 6d532176...
git ls-files 'tests/' 'test_*.py' '**/test_*.py' | sort | sha256sum  # -> b3e834dd...
sha256sum requirements.txt                                 # -> ed4c336e...
```

If the pinned commit is force-removed or rewritten upstream, the tree/test/requirements
hashes will no longer reproduce; treat any mismatch as a **fail-closed** baseline
invalidation (spec §5) and re-run Phase 0 rather than proceeding.

## 5. Source runtime dependencies (pinned)

From `baseline/manifests/system_a_requirements.txt`:

```
numpy, scipy, pandas, fastapi, uvicorn[standard], exchange-calendars, pyyaml,
pyarrow, scikit-learn, joblib, tastytrade>=12.4,<13
```

Notes carried from the source `requirements.txt`:
- `pyarrow` is lazy-imported (Parquet export of the V2 research dataset); the SQLite store
  works without it.
- `scikit-learn` + `joblib` back the V2 baseline models and artifact serialization.
- `tastytrade` is pinned to the `12.x` line (auth changed across majors) and only activates
  when `TASTYTRADE_*` credentials are set.

## 6. Scope and honesty statement

- This provenance record covers **source acquisition and pinning only**.
- No System A module is described here as migrated, validated, or parity-checked.
- The companion inventory (`docs/CURRENT_SYSTEM_INVENTORY.md`) and source-validated map
  (`docs/MIGRATION_MAP.md`) are derived strictly from the tree pinned above.
