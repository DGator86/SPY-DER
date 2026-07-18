# System B Foundation

This repository contains the initial architecture scaffold for **System B**, a typed, deterministic, testable reorganization of the existing 0DTE options research stack.

## Included in this PR

- Python 3.12 `src/` package layout (`system_b`)
- Immutable canonical contracts and schema versions
- Interfaces/protocols for core subsystems
- Deterministic serialization and replay scaffolding
- Safety checks for vetoes, risk envelopes, and state transitions
- Unit tests for required invariants
- CI for ruff, mypy (strict), and pytest+coverage

## Intentionally not included

- No live brokerage integration
- No order submission capability
- No fabricated predictive models
- No autonomous trading authority

## Local checks

```bash
python -m pip install -e .[dev]
python -m ruff check .
python -m mypy src
python -m pytest
```
