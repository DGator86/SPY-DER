# COMPARISON PROTOCOL

Adapters are compared only when replay input manifests exactly match:

- timestamps
- market snapshots
- underlying bars
- option-chain snapshots
- candidate universes where applicable
- fees
- slippage
- fill assumptions
- account size
- risk ceilings
- exit policies
- settlement data

If manifests differ, comparison must fail closed.
