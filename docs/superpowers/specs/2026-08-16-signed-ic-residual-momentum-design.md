# Signed IC Residual Momentum Design

## Objective

Build a new A-share signal hypothesis for both `hs300` and `zz500` after the
full-history rebuild showed that the previous quality/value/momentum feature
pool had negative inner and outer evidence.

The new protocol is `signed-ic-residual-momentum-v1`. It must answer one
falsifiable question:

> Does ex-ante market/industry residual momentum, combined only with
> directionally stable complementary factors, produce positive
> benchmark-relative net excess return after exact execution costs?

Zero passing models and zero Shadow admissions remain valid outcomes.

## Non-negotiable data isolation

- Development data: `2018-01-01` through `2024-12-31`.
- Outer purged walk-forward folds remain:
  - train 2018-2020, validate 2021;
  - train 2018-2021, validate 2022;
  - train 2018-2022, validate 2023;
  - train 2018-2023, validate 2024.
- Every outer training fold uses three chronological inner purged folds.
- Every training row must satisfy `label_end_date < validation_start`.
- Feature direction, feature eligibility, redundancy removal, estimator
  parameters, and ensemble weights are learned from the current training fold
  only.
- `2025-01-01` onward remains closed until a development candidate passes all
  gates. It is diagnostic-only and cannot be used for redesign.
- HS300 and ZZ500 use the same hypothesis and thresholds but learn signs,
  selected features, parameters, and weights independently.

## Alternatives considered

### A. Signed IC filter on the existing raw factors

This is cheap and interpretable, but it retains the old momentum definition
that already failed. It is suitable only as an ablation baseline.

### B. Signed IC filter plus true residual momentum

This is the recommended design. It changes the economic signal while keeping
the model family bounded and auditable. It directly addresses market/industry
beta contamination and short-term reversal.

### C. Regime-conditioned deep sequence model

This could model non-linear state changes, but it adds substantial search
degrees of freedom before a stable base signal exists. It remains out of scope
for v1.

## Residual momentum construction

All calculations use adjusted prices and information available at or before
the signal date.

1. Compute daily adjusted log return `r_i,t`.
2. Build account-benchmark return `r_m,t` and point-in-time industry
   equal-weight return `r_g,t`.
3. For each stock and date, estimate a ridge regression from the previous
   252 sessions, excluding the current session:

   `r_i = alpha_i + beta_m * r_m + beta_g * r_g + epsilon_i`

   Require at least 126 valid observations. Ridge alpha is fixed in the
   protocol and is not tuned.
4. Compute the current residual with coefficients frozen at `t-1`.
5. Create volatility-scaled residual momentum:
   - `exante_residual_momentum_20_5`: residual sessions `t-20` through `t-5`;
   - `exante_residual_momentum_60_5`: residual sessions `t-60` through `t-5`;
   - `exante_residual_momentum_120_20`: residual sessions `t-120` through `t-20`.

   Each value is `sum(residual) / residual_std * sqrt(valid_sessions)`.
6. Cross-sectionally winsorize at 1%/99%, remove log-market-cap exposure, and
   convert to an industry-neutral percentile on each signal date.
7. Fail closed when benchmark, industry, adjusted-price, or minimum-history
   requirements are unavailable.

The existing `account_residual_momentum_*` columns remain unchanged for
backward compatibility.

## Signed IC stability filter

The old selector ranked features by `abs(mean IC)` and always filled the
feature budget. The new selector can return no features.

For every candidate feature inside the current fit window:

1. Calculate daily cross-sectional Spearman IC against the 20-session excess
   return label.
2. Set the canonical direction from the training-only mean IC:
   `direction = sign(mean_ic)`.
3. Multiply the feature by that sign so a higher transformed value always
   means higher expected excess return.
4. Use stationary block bootstrap with block length 20 and fixed seed.
5. Retain the feature only when all conditions hold:
   - coverage is at least 70%;
   - absolute mean IC is at least 0.01;
   - 95% bootstrap lower bound of signed mean IC is greater than zero;
   - at least 55% of monthly IC values are positive after signing;
   - at least two of three chronological training subperiods have the same
     sign;
   - bootstrap p-values pass Benjamini-Hochberg FDR at `q <= 0.10`.
6. Remove redundant features with absolute training Spearman correlation above
   0.80, keeping the feature with the stronger bootstrap lower bound.
7. Select at most eight features, at most two from one family, and require:
   - at least two total features;
   - at least one `exante_residual_momentum_*` feature.

If these requirements fail, the fold and candidate are rejected rather than
filled with weak features.

## Candidate families

Only three bounded families are allowed:

1. `signed_ic_composite`
   - Transparent weighted sum.
   - Weight is proportional to clipped positive signed ICIR.
   - Maximum absolute weight 35%; weights sum to one.
2. `positive_elastic_net`
   - Standardized signed features.
   - Positive coefficients only.
   - Three preregistered alpha/l1-ratio variants.
3. `monotone_lambdarank`
   - LightGBM LambdaRank with monotone `+1` constraints on every signed
     feature.
   - Two preregistered bounded variants.

CatBoost and temporal neural models are excluded from v1. The objective is to
prove the signal, not search additional model capacity.

## Inner and outer rejection rules

- A parameter variant must have positive aggregate inner net excess return and
  positive net excess in at least two of three inner folds.
- If no variant passes, the family is marked `inner_signal_rejected`; no outer
  prediction is generated for that family/fold.
- The campaign ledger still records every declared family, variant, feature
  audit, and rejection reason.
- Development admission remains fail closed:
  - four valid outer folds;
  - every outer fold net excess return above zero;
  - aggregate net excess return above zero;
  - 1.5x-cost net excess return at least zero;
  - stationary-bootstrap probability at least 95%;
  - deflated-Sharpe probability at least 95%;
  - probability of backtest overfit at most 20%;
  - target fill ratio at least 95%.

## Required ablations

Each scope reports raw momentum with signed filtering, residual momentum only,
residual momentum plus complementary factors, and the final estimator families.
Ablations are diagnostic and cannot be selected as additional trials.

## Artifacts and success criteria

Every report includes data fingerprints, residual-momentum coverage, signed IC
audits, redundancy decisions, inner rejection evidence, outer metrics, exact
costs, 1.5x stress, DSR, PBO, bootstrap evidence, ablations, historical-test
open count, and exact Shadow reasons.

The implementation succeeds when it produces an auditable answer. No 2025+
data may influence design or fitting, both scopes must complete all declared
diagnostics, and a scope may open historical diagnostics only after every
development gate passes. Otherwise the final result remains `0 Shadow`.
