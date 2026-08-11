# Model Decision Transparency Design

## Goal

Make every model-iteration decision legible in the Dashboard, including cash-only outcomes. The displayed explanation must come from the exact prediction snapshot and eligibility policy used to create simulated orders.

## Experience

The model-iteration workspace adds a compact "本期决策" section immediately after the version lifecycle. It presents:

1. A direct outcome: selected securities or cash-only, with the persisted reason.
2. A sequential eligibility funnel: prediction rows, valid rows, confidence pass, bullish-probability pass, positive-excess pass, and final selection.
3. Up to five near-miss securities with name, code, probabilities, expected excess return, confidence, and failed rules.

The section preserves the existing dark terminal language. It uses dense rows and restrained cyan/amber accents, not decorative cards or red/green buy-sell semantics.

## Data Contract

`build_model_candidates` persists a `decision_diagnostics` object into the model version status:

- `outcome`, `summary`, and `regime`
- `funnel`, with stable stage keys and counts
- `near_misses`, ranked by expected excess return, probability spread, then confidence

Near-miss reasons are derived from the same four eligibility conditions used by order generation. Names are attached from the run's point-in-time name lookup; missing names fall back to the security code.

## Safety And Performance

The Dashboard never reads Parquet or recomputes model policy. It renders the small persisted status payload, preserving the split-resource performance design. Existing status files without diagnostics continue to render normally.

## Verification

Backend tests assert funnel counts, failed-rule labels, near-miss ordering, and status persistence. Frontend tests assert the cash-only explanation, funnel, and near-miss details. ECS verification reruns the A-share model iteration and checks the live API and rendered Dashboard.
