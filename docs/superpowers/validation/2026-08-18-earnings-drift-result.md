# Earnings Drift Preregistered Result

## Protocol

- Protocol: earnings-drift-preregistered-v1
- Development window: 2018-01-01 through 2024-12-31
- Historical diagnostic: 2025 onward, not opened
- Live OOS start: 2026-08-18
- Model training: prohibited
- Formal strategy impact: none

The structured input came from Tushare forecast and express, not bulk LLM
extraction. All 2,641 declared partitions completed: 2,557 daily forecast
partitions and 84 monthly express partitions, containing 60,713 normalized
records.

## Evidence

- 8,919 mature 60-session events
- 4,357 positive events
- 1,151 unique securities
- Seven event years: 2018 through 2024
- Positive-event scope observations: HS300 1,526; ZZ500 2,832
- Structured backfill complete: yes

Every preregistered evidence gate passed.

## Economic result

| Horizon | Observations | Mean net active | Stress-cost mean | Median net active | Positive years | Bootstrap P(>0) | Max year share | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 5 | 4,502 | +0.06% | -0.04% | -0.31% | 4/7 | 0.741 | 0.590 | Fail |
| 20 | 4,500 | +0.65% | +0.54% | -0.58% | 5/7 | 0.964 | 0.363 | Pass |
| 60 | 4,358 | +1.03% | +0.92% | -1.89% | 5/7 | 0.919 | 0.504 | Fail |

The preregistered family required all three horizons to pass. Final status:
falsified.

## Decision

No model is trained, no Shadow candidate is admitted, and no formal strategy
changes. Selecting the 20-session result after observing the table would be a
new, post-selection hypothesis. It may be recorded for future-only observation
from 2026-08-18, but it cannot be presented as validated historical alpha or
rescued by opening the unused 2025+ diagnostic window.

The next preregistered research family should use a different economic
mechanism, starting with structured buyback and insider/shareholder increase
events. The earnings study remains immutable.
