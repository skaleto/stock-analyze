# Signed IC Residual Momentum Results

## Scope

Protocol: `signed-ic-residual-momentum-v1`

Markets:

- HS300
- ZZ500

Development data only: 2018-2024. No 2025+ historical diagnostic was opened.

## Implementation

The campaign added:

- ex-ante rolling market and industry residual returns;
- `20_5`, `60_5`, and `120_20` volatility-scaled residual momentum;
- industry and log-market-cap neutralization;
- fold-local signed Spearman IC direction;
- stationary block-bootstrap lower bounds;
- monthly and chronological-subperiod sign stability;
- Benjamini-Hochberg FDR;
- feature-family caps and redundancy removal;
- transparent signed-IC composite, positive ElasticNet, and monotone
  LambdaRank;
- strict rejection when fewer than two stable features survive or when inner
  net excess is not positive in at least two of three folds.

## Results

### HS300

Status: `no_valid_trials`

The first two residual-aware inner folds retained fewer than two stable
features. The third retained:

- `exante_residual_momentum_20_5`
- `cash_conversion`

Exact-cost inner net excess:

- signed-IC composite: `-16.18%`
- positive ElasticNet: `-19.01%`
- monotone LambdaRank: `-6.42%`

### ZZ500

Status: `no_valid_trials`

The first inner fold retained no residual feature and the second retained
fewer than two stable features. The third retained:

- `exante_residual_momentum_20_5`
- `exante_residual_momentum_60_5`

Exact-cost inner net excess:

- signed-IC composite: `-21.25%`
- positive ElasticNet: `-23.43%`
- monotone LambdaRank: `-24.24%`

## Decision

Both hypotheses were rejected before outer OOS evaluation. Historical
diagnostics remain closed and no model enters Shadow. Final admission count:
`0 Shadow`.

This result rejects the current residual-momentum hypothesis for these two
A-share universes. It must not be rescued by relaxing IC, FDR, cost, or Shadow
thresholds, and 2025+ data must not be used for redesign.
