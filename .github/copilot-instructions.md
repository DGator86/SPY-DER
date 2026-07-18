# Copilot instructions for System B

- Preserve deterministic behavior and explicit validation.
- Keep prediction outputs separate from policy outputs.
- Never bypass hard vetoes or deterministic risk limits.
- Do not add live brokerage integrations or order-routing code.
- Treat missing required inputs as explicit abstention/failure, never silent defaults.
