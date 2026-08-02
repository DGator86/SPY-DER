# AGENTS.md

## Cursor Cloud specific instructions

SPY-DER ("System B") is a pure-Python 3.12 library + CLI (`spy-der`). There is no
database, web server, or other background service required — tests run fully
offline (network is blocked in `tests/conftest.py`).

### Environment

- Dependencies are installed into a project virtualenv at `.venv/` (gitignored)
  by the startup update script. Activate it before running anything:
  `source .venv/bin/activate` (or prefix commands with `.venv/bin/`).
- Standard dev commands are documented in `README.md` and `.github/workflows/ci.yml`
  (ruff → mypy → pytest). Run them from the repo root with the venv active:
  `python -m ruff check .`, `python -m mypy src`, `python -m pytest`.

### Running the app

- The runnable service is the shadow state publisher:
  `spy-der vps-runner --live-state <path> --interval <seconds>`.
- It defaults to writing `/var/lib/zerodte/spy_der_state.json`; that path is not
  writable by default, so point `--live-state` at a writable location (e.g. under
  `/tmp`). It runs forever, so use a short interval and a timeout when smoke
  testing, e.g. `timeout 3 spy-der vps-runner --live-state /tmp/spy_der_state.json --interval 1`.
- The runner is shadow/paper-only by design: no live brokerage, no order
  submission. It just activates a cutover snapshot and publishes heartbeat state.

### Optional secret

- `XAI_API_KEY` enables the live Grok/xAI decision agent. It is optional; without
  it the system falls back to a deterministic agent, and tests never need it.
