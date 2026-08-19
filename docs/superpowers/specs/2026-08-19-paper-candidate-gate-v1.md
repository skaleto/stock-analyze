# Paper Candidate Gate v1

## Scope

This is the first product-release layer from Research to an isolated paper
candidate. It is not the second-layer Active/Champion gate. It does not modify
formal strategies, model registries, positions, or orders and does not open the
2025+ historical window.

The policy is intentionally adopted after seeing existing development results
at the operator's instruction. Every reassessment must therefore be labelled
`known_development_evidence_reassessment`; it is a release-policy decision, not
new unseen evidence.

## Required checks

The only comparator is the strongest transparent `router_only` ablation. A
candidate needs valid PIT folds and the current label contract, exact-cost
`paper-parity-daily-v1` replay, reconciled attribution, trades, positive
aggregate annualized net excess, drawdown no greater than 25%, annual turnover
no greater than 8x, and a strictly positive median of four fold-level deltas.

Economic increment passes by either of two predeclared paths:

1. annualized net-excess improvement versus Router is at least 0.5%; or
2. cumulative relative-wealth improvement is at least twice incremental
   execution costs divided by the sum of fold initial capitals.

A fold is disastrous if candidate net excess is below -15%, candidate minus
Router is below -5 percentage points, or drawdown exceeds 25%. One disaster
fold rejects the candidate.

## Removed from first layer

DSR, PBO, simultaneous victory over multiple comparators, mandatory 3/4 winning
folds, and twelve future weeks are not first-layer requirements. They are not
silently removed from the second-layer Active gate; the existing Active gate
and its forward-evidence requirements remain unchanged.

Passing this gate writes a checksummed qualification ledger under
`data/research/paper_candidates/`. It means eligible for artifact freeze and an
isolated paper portfolio. It does not claim a Champion and does not itself create
or trade a model artifact.
