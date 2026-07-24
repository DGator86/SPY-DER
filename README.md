# SPY-DER

SPY-DER owns **decision intelligence** for the SPY 0DTE stack: AI agents, Dojo
training, adaptive learning, promotion governance, and the local decision
service consumed by `DGator86/0DTE`.

See `docs/OWNERSHIP_BOUNDARY.md` for the repository boundary.

## Package highlights

- `spy_der.agents` / `spy_der.decisions` — provider-neutral decision agents
- `spy_der.dojo` — protocol-driven Dojo (no 0DTE internal imports)
- `spy_der.learning` — diagnose / optimize / stage `pending_review`
- `spy_der.contracts.integration` — versioned 0DTE ↔ SPY-DER packets
- `spy_der.runtime.decision_service` — `POST /v1/decision` on localhost:8787

## Local checks

```bash
python -m pip install -e .[dev]
python -m ruff check .
python -m mypy src
python -m pytest
```

## Intentionally not included

- No live brokerage integration
- No autonomous promotion (human ack required)
- No in-process ownership of 0DTE market/forecast pipelines
