# Capital Actions Preregistered Result

## Protocol

- Protocol: `capital-actions-preregistered-v1`
- Development window: 2018-01-01 through 2024-12-31
- Historical diagnostic: 2025 onward, not opened
- Live OOS start: 2026-08-18
- Primary horizon: 20 sessions; 5 and 60 sessions are diagnostics
- Round-trip cost: 21 bps; stress cost: 31.5 bps
- Model training: prohibited
- Formal strategy impact: none

The event base uses structured Tushare `repurchase` and `stk_holdertrade`
records. Completed repurchases require realized amount / PIT market value >=
0.5%; holder increases require aggregated same-code, same-date, same-type
`change_ratio` >= 0.1%. Entry is the first open strictly after announcement.
Account scope is fixed from point-in-time index membership at entry, and every
reported horizon uses the same cohort with complete 5/20/60-session outcomes.

## Data and evidence

- Backfill: 168/168 monthly endpoint partitions complete
- Raw structured rows: 154,356
- Normalized unique events: 92,270; duplicate event IDs: 0
- Coverage: every year from 2018 through 2024
- Completed-repurchase PIT market-cap coverage by year: 95.2% to 98.2%
- Manifest SHA-256: `0c37219661107936a0035e29914b6f1d7f335dcce374567a7e269864aeff5e5c`

| Positive family | Mature events | Securities | HS300 | ZZ500 | Evidence gate |
|---|---:|---:|---:|---:|---|
| Completed repurchase | 2,324 | 400 | 848 | 1,476 | Pass |
| Company-holder increase | 1,205 | 420 | 423 | 782 | Pass |
| Management increase | 119 | 78 | 28 | 91 | Pass |
| Individual increase | 96 | 64 | 20 | 76 | Pass |

All four families independently pass the preregistered event, security, year,
and two-scope evidence floors.

## Economic result

The table shows net benchmark-relative returns. `P` is the deterministic
year-cluster bootstrap probability that mean net active return is positive.

| Family | Horizon | Mean net | Median net | Stress mean | Positive years | P | Max year share | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Completed repurchase | 5 | -0.34% | -0.75% | -0.45% | 1/7 | 0.032 | 1.000 | Fail |
|  | **20** | **-0.39%** | **-1.41%** | **-0.49%** | **2/7** | **0.214** | **0.838** | **Fail** |
|  | 60 | -0.10% | -2.26% | -0.20% | 3/7 | 0.475 | 0.661 | Fail |
| Company-holder increase | 5 | -0.27% | -0.54% | -0.37% | 1/7 | 0.002 | 1.000 | Fail |
|  | **20** | **-0.62%** | **-1.23%** | **-0.72%** | **1/7** | **0.001** | **1.000** | **Fail** |
|  | 60 | +0.16% | -1.72% | +0.05% | 6/7 | 0.687 | 0.266 | Fail |
| Management increase | 5 | -0.25% | -0.56% | -0.36% | 3/7 | 0.321 | 0.807 | Fail |
|  | **20** | **-0.57%** | **-0.45%** | **-0.68%** | **3/7** | **0.307** | **0.745** | **Fail** |
|  | 60 | -1.33% | -3.79% | -1.43% | 2/7 | 0.205 | 0.965 | Fail |
| Individual increase | 5 | +0.36% | -0.52% | +0.25% | 5/7 | 0.767 | 0.329 | Fail |
|  | **20** | **+0.02%** | **-1.49%** | **-0.09%** | **4/7** | **0.505** | **0.530** | **Fail** |
|  | 60 | +2.37% | -0.04% | +2.27% | 5/7 | 0.949 | 0.312 | Fail |

At the frozen primary 20-session horizon, completed repurchases, company-holder
increases, and management increases have negative mean and median returns.
Individual increases have a negligible positive mean, but fail the positive
median, stress-cost, year-stability, bootstrap, concentration, and both-scope
requirements (HS300 +1.36%, ZZ500 -0.33%). No family passes.

Final status: **falsified**.

## Decision

No model is trained, no Shadow candidate is admitted, and no formal strategy or
overlay is changed. The 60-session individual-increase result is not rescued:
it is a secondary diagnostic, its median remains negative, its bootstrap
probability is 0.9494 below the frozen 0.95 gate, and the primary 20-session
result failed. The unused 2025+ diagnostic window remains closed.

Immutable ECS report checksums:

- JSON: `b0aa3ae4df6892607ca332110e239e3db73242d89c1e47ec8ab9a982ba0953db`
- Markdown: `4647b118260100068beceee7caf11fde9b68521400542c95334a619d1f938643`
