# Holder Concentration Preregistered Result

## Protocol

- Protocol: `holder-concentration-preregistered-v1`
- Development window: 2018-01-01 through 2024-12-31
- Historical diagnostic: 2025 onward, not opened
- Live OOS start: 2026-08-18
- Primary horizon: 20 sessions; 5 and 60 sessions are diagnostics
- Round-trip cost: 21 bps; stress cost: 31.5 bps
- Model training: prohibited
- Formal strategy impact: none

The economic mechanism is independent of the prior earnings and capital-action
studies. It uses only structured Tushare `stk_holdernumber` disclosures. A
quarterly shareholder-count decline of at least 10% is the positive candidate;
an increase of at least 10% is a long-side risk diagnostic only. Entry is the
first open strictly after announcement, with PIT index membership fixed at
entry and identical complete 5/20/60-session cohorts.

## Data and PIT quality

- Backfill: 84/84 monthly partitions complete
- Raw structured rows: 249,531
- Valid earliest stock-quarter observations: 103,932
- Conflicting earliest quarter observations: 0
- Directional adjacent-quarter events before threshold: 76,651
- Duplicate event IDs: 0
- Coverage: every year from 2018 through 2024
- Manifest SHA-256: `07d185652575516aa6ec6e8b4e765ada839863f42232b39be22a6fc752a69f43`

Before return unblinding, data QA found pre-IPO legal-holder counts as low as
two followed by tens of thousands of public holders. The preregistration was
amended and committed before any return calculation: both adjacent quarter ends
must be on or after the frozen snapshot's listing date. This excluded 54,524
quarter pairs, removed the IPO transition artifact, and left prior holder count
at a minimum of 2,221 in the threshold-selected sample. The +/-10% thresholds
and all economic gates were unchanged.

After the frozen threshold, structured coverage was:

| Family | Structured events | Securities | Years | Role |
|---|---:|---:|---:|---|
| Holder concentration <= -10% | 3,155 | 1,956 | 7 | Candidate |
| Holder dispersion >= +10% | 4,153 | 2,249 | 7 | Diagnostic only |

The mature PIT index-scoped candidate cohort contains 1,807 events across 740
securities: 753 HS300 observations and 1,054 ZZ500 observations. Every frozen
evidence floor passed.

## Candidate economic result

| Horizon | Events | Mean net active | Median net active | Stress mean | Positive years | Bootstrap P(>0) | Max year share | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 5 | 1,807 | -0.06% | -0.26% | -0.16% | 5/7 | 0.448 | 0.474 | Fail |
| **20** | **1,807** | **+0.45%** | **-0.91%** | **+0.34%** | **4/7** | **0.857** | **0.423** | **Fail** |
| 60 | 1,807 | +1.96% | -0.72% | +1.86% | 5/7 | 0.995 | 0.465 | Fail |

At the frozen primary 20-session horizon, mean and stress-cost returns are
positive and both scopes are positive (HS300 +0.09%, ZZ500 +0.70%). However,
the median is negative, only four of seven years are positive, and the
year-cluster bootstrap probability is 0.857 rather than the required 0.95. The
primary gate therefore fails.

The 60-session result cannot rescue the hypothesis: it is a preregistered
secondary diagnostic, and its median remains negative even though its mean,
stress result, scope consistency, bootstrap, and year concentration are strong.

Final candidate status: **falsified**.

## Dispersion diagnostic

The dispersion family is never a candidate or an assumed short portfolio. Its
2,619 mature events show:

| Horizon | Mean net active | Median net active | Stress mean |
|---:|---:|---:|---:|
| 5 | +0.03% | -0.37% | -0.07% |
| 20 | -0.04% | -1.30% | -0.15% |
| 60 | +0.74% | -2.36% | +0.63% |

These diagnostic returns do not alter the concentration decision.

## Decision

No model is trained, no Shadow candidate is admitted, and no formal strategy or
overlay is changed. The 2025+ diagnostic remains closed. Selecting the 60-day
mean or adding valuation, momentum, or industry filters after observing this
result would be a new post-selection hypothesis and is not permitted here.

Immutable ECS report checksums:

- JSON: `eec1a34aff092201d96bc9112b6ca1cb7a169776ced4172e8b8de7e9991c781e`
- Markdown: `6ead1ad64627251c122a1ffb9cb3a32a7f90d82135dadaf457c399e0408de602`
