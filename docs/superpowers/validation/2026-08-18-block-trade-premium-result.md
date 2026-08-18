# Block Trade Premium Preregistered Result

## Protocol and data

- Protocol: `block-trade-premium-preregistered-v1`
- Development: 2018-2024; 2025+ not opened; live OOS starts 2026-08-18
- Primary horizon: 20 sessions; normal/stress cost: 21/31.5 bps
- No model, Shadow, buyer/seller filter, or formal strategy change

Tushare `block_trade` was backfilled into 2,557/2,557 daily partitions with
392,696 raw transactions. Same-stock same-day trades were aggregated to
amount/volume VWAP. Price, volume, and amount units were checked row by row; any
bad row invalidated its entire stock-day, excluding 299 stock-days. Raw same-day
close came from the frozen materialization and PIT total market cap came from the
same trading date. Of 219,375 aggregated stock-days, 52,348 were in the frozen
full-history pricing scope; market-cap coverage was complete. Missing closes were
range exclusions, not imputed.

Manifest SHA-256: `b16a2957bb01bd8f03cb98eec0210ccf8ef88f655e509548648cf9a31c02e282`.

Frozen thresholds produced 701 premium events (VWAP >= close +2%, amount >=
0.1% of market cap) and 6,933 discount diagnostics (VWAP <= close -5%, same
amount floor) across all seven years. The mature candidate cohort contains 401
events, 206 securities, 108 HS300 and 293 ZZ500 observations. All evidence floors
passed.

## Candidate result

| Horizon | Mean net | Median net | Stress mean | Positive years | Bootstrap | Scope means | Result |
|---:|---:|---:|---:|---:|---:|---|---|
| 5 | +0.05% | -0.41% | -0.06% | 5/7 | 0.602 | HS300 +0.19%; ZZ500 -0.00% | Fail |
| **20** | **-0.37%** | **-1.56%** | **-0.48%** | **2/7** | **0.189** | **HS300 +0.68%; ZZ500 -0.76%** | **Fail** |
| 60 | -0.77% | -3.50% | -0.88% | 3/7 | 0.180 | HS300 +0.29%; ZZ500 -1.16% | Fail |

The primary result fails mean, median, stress, year stability, bootstrap, year
concentration, and scope consistency. The HS300 subset is not selected after the
result, and buyer/seller names are not mined for a rescue. Final status:
**falsified**.

The 3,983 mature discount diagnostics also have negative 5/20/60-day mean and
median net returns; they are diagnostic-only and do not alter the decision.

No model is trained, no Shadow is admitted, no formal strategy is changed, and
2025+ remains closed.

- JSON SHA-256: `dacaf9462c02950f17718f316ccf9cdcd38846ab72c3c8adcd7182161e0d4844`
- Markdown SHA-256: `716aca2a57a80e8435941574db8fb546fe1d7b3d909278e2a50e497ad187616f`
