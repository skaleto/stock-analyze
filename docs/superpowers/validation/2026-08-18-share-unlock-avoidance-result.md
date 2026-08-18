# Share Unlock Avoidance Preregistered Result

## Protocol

- Protocol: `share-unlock-avoidance-preregistered-v1`
- Development window: 2018-01-01 through 2024-12-31
- Historical diagnostic: 2025 onward, not opened
- Live OOS start: 2026-08-18
- Primary horizon: 20 sessions; 5 and 60 are diagnostics
- Avoidance cost: 21 bps; stress cost: 31.5 bps
- Model training: prohibited
- Formal strategy impact: none

The mechanism asks whether replacing a held stock with its PIT benchmark after a
large confirmed restricted-share unlock adds value. Avoidance active return is
`benchmark return - stock return`; exit/re-entry cost is then subtracted. This is
not an assumed A-share short portfolio.

## Structured PIT data

- Source: Tushare `share_float`
- Backfill: 84/84 actual-unlock-month partitions complete
- Raw holder rows: 4,659,975
- Compressed structured store: about 34 MB
- Rows older than the frozen 30-day confirmation window excluded: 917,247
- Conflicting holder/tranche snapshots excluded: 16
- Normalized stock-unlock dates before denominator gates: 11,850
- Missing PIT total-share denominators: 12
- Reported/recomputed ratio disagreements excluded: 183
- Final unique PIT events: 11,655
- Manifest SHA-256: `c0b66b7a2171adc502b436c489b09fe92fda25973362c349186b4921e66c0a88`

The provider could not deep-page some high-volume months or the single unlock
date 2024-02-19. Retrieval therefore degraded deterministically from month to
natural day, then from an oversized day to the 31 eligible confirmation dates.
All shards remained part of one atomic monthly partition; any failing shard
would have failed the partition. No truncated page was accepted.

One event can contain thousands of holder rows and repeated schedule versions.
Only the latest confirmation snapshot within 30 days of the actual unlock date
was used. Unlocked shares were recomputed against PIT total shares, and a
positive reported ratio had to agree within one percentage point. Normalization
was changed from all-history materialization to month-streamed aggregation with
identical event and audit counts, reducing ECS peak memory from 1.33 GB to about
418 MB.

## Evidence

Frozen structured thresholds produced:

- Large unlock candidate (ratio >= 5%): 5,222 structured events
- Small unlock diagnostic (0% < ratio <= 1%): 3,063 structured events
- Seven structured event years: 2018 through 2024

The mature PIT index-scoped candidate cohort contains 623 events across 413
securities, with 233 HS300 and 390 ZZ500 observations. Six mature event years
remain (2019-2024). Every frozen evidence gate passed.

## Candidate economic result

All numbers below are net benchmark-substitution avoidance returns.

| Horizon | Events | Mean net | Median net | Stress mean | Positive years | Bootstrap P(>0) | Scope means | Result |
|---:|---:|---:|---:|---:|---:|---:|---|---|
| 5 | 623 | -1.27% | -0.62% | -1.38% | 0/6 | 0.000 | HS300 -0.91%; ZZ500 -1.49% | Fail |
| **20** | **623** | **-2.21%** | **-0.79%** | **-2.32%** | **0/6** | **0.000** | **HS300 -1.47%; ZZ500 -2.66%** | **Fail** |
| 60 | 623 | -3.70% | -1.15% | -3.81% | 1/6 | 0.000 | HS300 -2.73%; ZZ500 -4.28% | Fail |

The large-unlock avoidance rule fails every primary economic direction gate. In
this development sample the affected stocks outperformed rather than
underperformed their benchmarks, so mechanically substituting benchmark
exposure destroyed value. This result does not authorize reversing the rule and
buying unlock events; that would be a new post-result hypothesis.

Final candidate status: **falsified**.

## Small-unlock diagnostic

The small-unlock family is never a candidate. Its 795 mature events show net
avoidance means of -0.36%, -0.20%, and +0.16% at 5/20/60 sessions. The 20-day
mean is negative and the scopes disagree (HS300 -0.78%, ZZ500 +0.38%). It does
not alter the large-unlock decision.

## Decision

No model is trained, no Shadow is admitted, and no formal strategy or overlay is
changed. The 2025+ window remains closed. Neither threshold retuning nor
reversing the signal after seeing the result is permitted.

Immutable ECS report checksums:

- JSON: `b2af7d0d70a02bbbe4ec201437b0e37e36920dc7cf6df87cb6ca5ea7f657b8f1`
- Markdown: `38d73958e490d29f8a0666b63e3af8693e949bdb0b05e242aa8956982420d00b`
