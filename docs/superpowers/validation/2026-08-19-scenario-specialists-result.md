# Scenario Specialists Result — 2026-08-19

## Decision

The preregistered `scenario-specialists-v1` experiment did **not** authorize a
model Challenger. No model enters Shadow, no Registry is mutated, and no formal
strategy, overlay, order, position, or ledger changes. The 2025+ historical test
remained closed.

The frozen contract hash was
`f7cb2d64bba38b50caf4cbd99ead0ba8ac5b08370b40920d921dc96d3c4c4b64`.
The implementation was committed before the real A-share results were opened.
The exact inputs were the qualified `20260814` full-history feature/label
snapshots; development returns ended at 2024-12-31.

## Outcomes

| Scope | Outcome | Main reason |
|---|---|---|
| HS300 | no pass | Specialist loses to router and pooled residual; negative net excess and Rank IC; expansion history below 120 days in early folds |
| ZZ500 | no pass | Specialist does not clear router/pooled margins or fold counts; negative net excess and Rank IC; expansion history below 120 days |
| QDII HK exposure | insufficient data | `global_index_momentum` full-history coverage is 0% |
| QDII US exposure | insufficient data | `global_index_momentum` coverage is 0%; `sma_distance_200` is 68.92%, below the frozen 70% floor |

No missing feature was substituted and no threshold was relaxed after observing
these results. The QDII candidates were not fitted.

## A-share same-window ablations

All values below are 2018-2024 exact-cost net benchmark-relative returns.

| Scope | Transparent reference | Router only | Pooled 10% residual | Scenario specialists |
|---|---:|---:|---:|---:|
| HS300 | -13.12% | -2.36% | -3.51% | -3.58% |
| ZZ500 | -11.46% | -4.79% | -4.22% | -4.69% |

### HS300

- Specialist minus transparent reference: **+9.54 percentage points**, with 3/4
  folds improving.
- Specialist minus router only: **-1.21 percentage points**, with 0/4 folds
  improving.
- Specialist minus pooled residual: **-0.07 percentage points**, with 1/4 folds
  improving.
- Specialist Rank IC: **-0.0362**; net excess remains negative.
- Expansion training dates by fold: 65, 70, 70, 124. The first three folds fail
  the preregistered 120-date expert minimum.

### ZZ500

- Specialist minus transparent reference: **+6.77 percentage points**, with 3/4
  folds improving.
- Specialist minus router only: **+0.10 percentage points**, with 2/4 folds
  improving; the gate required at least +0.50 points and 3/4 folds.
- Specialist minus pooled residual: **-0.47 percentage points**, with 2/4 folds
  improving.
- Specialist Rank IC: **-0.0443**; net excess remains negative.
- Expansion training dates by fold: 47, 48, 58, 108. Every fold fails the
  preregistered 120-date expert minimum.

## Interpretation

The large improvement relative to the fully invested transparent reference must
not be credited to machine learning. In both A-share scopes, most improvement
comes from the transparent scene exposure router, which reduced average risky
exposure and turnover. Once that same router is given to every ablation, the
three specialist regressions add no stable incremental value.

This experiment therefore answers the authorized question negatively: the fixed
three-scene expert architecture is not a useful Challenger under the declared
features, costs, windows, and gates. It also provides a narrower independent
observation: transparent risk budgeting may deserve a separate future
preregistration, but this result cannot promote it because the router itself was
an ablation, its aggregate net excess remained negative, and this campaign was
registered as a model test rather than a router promotion study.

## Immutable stop

Do not rescue this campaign by lowering the 120-date minimum, redefining
expansion, removing the failing QDII features, selecting only 2023, or opening
2025+. A new experiment requires a newly committed economic question and new
independent information or a separately authorized transparent risk-budget
study.
