# Annual Dividend Growth Preregistered Result

## Protocol

- Protocol: `dividend-growth-preregistered-v1`
- Development window: 2018-01-01 through 2024-12-31
- Historical diagnostic: 2025 onward, not opened
- Live OOS start: 2026-08-18
- Primary horizon: 20 sessions; 5 and 60 are diagnostics
- Round-trip cost: 21 bps; stress cost: 31.5 bps
- Model training: prohibited
- Formal strategy impact: none

The independent mechanism is confirmed annual cash-distribution growth. The sole
source is Tushare `dividend`, queried by exact implementation-announcement date.
Only implemented December-31 fiscal-year facts are used. Current total cash must
be at least 1% of PIT market value, and year-over-year total cash growth must be
at least 20%. A cut of at least 20% is diagnostic only.

## Structured PIT data

- Backfill: 2,557/2,557 daily implementation partitions complete
- Raw rows: 24,280
- Deduplicated implemented annual facts: 21,576
- Ambiguous stock/fiscal-year facts excluded: 14
- Valid annual facts: 21,548
- Missing PIT market values excluded: 339
- Consecutive-fiscal-year directional events: 13,899
- Duplicate event IDs: 0
- Manifest SHA-256: `d278da1e6136108445427cafc2183d6d576a1c479cd346363c7cc69bb7a07d0a`

The PIT date is the implementation announcement, not the proposal or meeting
date. Exact duplicate lifecycle facts are collapsed, while a stock/fiscal year
with more than one distinct implementation fact is excluded. Total cash is
`cash_div_tax * base_share * 10000`; PIT total market value is converted from
ten-thousand yuan to yuan. The immediately previous fiscal year is mandatory.

Frozen structured thresholds produced 3,467 growth events and 1,201 cut
diagnostics across all six possible event years, 2019-2024.

## Evidence

The mature PIT index-scoped growth cohort contains 935 events across 547
securities, with 379 HS300 and 556 ZZ500 observations. Every frozen evidence
floor passed.

## Candidate economic result

| Horizon | Events | Mean net | Median net | Stress mean | Positive years | Bootstrap P(>0) | Max year share | Scope means | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 5 | 935 | +0.02% | -0.27% | -0.08% | 4/6 | 0.575 | 0.405 | HS300 +0.26%; ZZ500 -0.15% | Fail |
| **20** | **935** | **+0.18%** | **-0.68%** | **+0.07%** | **4/6** | **0.811** | **0.614** | **HS300 +0.28%; ZZ500 +0.11%** | **Fail** |
| 60 | 935 | +1.27% | -1.69% | +1.17% | 4/6 | 0.897 | 0.441 | HS300 +1.57%; ZZ500 +1.07% | Fail |

At the frozen primary 20-session horizon, mean, stress, both scopes, and the
minimum positive-year fraction are positive. The hypothesis still fails because
the median is negative, bootstrap confidence is 0.811 rather than 0.95, and one
year supplies 61.4% of positive contribution. The return is right-tail and
year-concentrated rather than a stable transparent baseline.

The 60-day mean cannot rescue the result: it is secondary, its median is still
negative, and its bootstrap probability remains below the frozen floor.

Final candidate status: **falsified**.

## Dividend-cut diagnostic

The 324 mature cut observations have mean net returns of -0.44%, -0.36%, and
+0.62% at 5/20/60 sessions. Their corresponding medians are -0.63%, -0.97%, and
-0.43%. This family is diagnostic only and does not alter the growth decision.

## Decision

No model is trained, no Shadow is admitted, and no formal strategy or overlay is
changed. The 2025+ window remains closed. Adding valuation or momentum filters,
changing the 20% threshold, or selecting the 60-day mean after this result would
be a new post-selection hypothesis and is not permitted here.

Immutable ECS report checksums:

- JSON: `f0d9ebb6a0e44f9503992b7c67422ab5ad5e1f75d943e350e466fe351d4ed6ca`
- Markdown: `a522c4ecb1ef869174d01a4bb80ede0053a310c5d35c4f369233451563a29e21`
