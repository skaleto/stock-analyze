# Paper Candidate Gate v1 Result — 2026-08-19

## Decision

The first-layer Research-to-isolated-paper gate has been replaced by
`paper-candidate-gate-v1`. The second-layer Active/Champion gate was not changed.
Applying the new first layer to the current QDII scenario report qualifies the
Hong Kong exposure scenario specialist for an isolated paper candidate. US
exposure remains `insufficient_data`. No formal strategy or Registry state was
changed.

This is explicitly a `known_development_evidence_reassessment`: the release
policy was adopted after the development evidence was known at the operator's
instruction. It is not new unseen evidence and it does not open the 2025+
historical window.

## First-layer policy

The sole comparator is `router_only`. The hard checks retained are current PIT
and label contracts, exact-cost paper replay, reconciled attribution, positive
net excess, trade activity, strictly positive four-fold delta median, no
disaster fold, maximum drawdown of 25%, and annual turnover of 8x.

Economic increment passes when annualized improvement is at least 0.5% or when
cumulative improvement exceeds twice the incremental execution-cost return. A
fold is disastrous below -15% candidate net excess, below -5 percentage points
versus Router, or above 25% drawdown.

DSR, PBO, simultaneous victory over several comparators, mandatory 3/4 winning
folds, and twelve weeks are removed only from the first layer. The existing
second-layer Active gate still requires twelve Shadow and forward-evidence
cycles and is covered by an explicit regression test.

## Qualification result

Qualification ledger:
`data/research/paper_candidates/paper-candidate-gate-v1/339d5318362dc1de.json`.

| Scope | First-layer result | Reason |
|---|---|---|
| HK exposure | Qualified | All 17 checks passed |
| US exposure | Insufficient data | Frozen `sma_distance_200` development coverage is 68.92% |

Hong Kong metrics:

- Candidate annualized net excess: **+0.813%**
- Router annualized net excess: **+0.611%**
- Annualized increment: **+0.202%**
- Cumulative relative-wealth increment: **+0.7895%**
- Incremental execution cost: **545.47 yuan** over **2,000,000 yuan** fold capital
- Incremental cost return: **0.0273%**; cumulative increment exceeds 2x cost
- Fold deltas: 0.0000%, 0.0000%, +0.1434%, +0.5591%
- Fold delta median: **+0.0717%**
- Disaster folds: **0**
- Maximum drawdown: **24.37%**
- Annual turnover: **5.63x**

HS300 and ZZ500 were not in the current QDII source report. Their already
recorded candidates have negative aggregate net excess and therefore do not
satisfy the new first-layer positive-net-excess check.

## What qualification means

The Hong Kong candidate is eligible for artifact freeze and an isolated paper
portfolio. The current scenario report contains fold predictions and evaluation
metrics, not a final executable fitted artifact. Therefore qualification does
not yet create orders or a model account. The next engineering step is to fit
the frozen 2018-2024 scenario experts once, serialize feature medians/scalers/
coefficients/router contract and source hashes, and connect that artifact only
to the isolated model-iteration portfolio.

At evaluation time there were zero Active or Shadow models. Formal A-share and
QDII account ledgers were unchanged.
