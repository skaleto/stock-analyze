# Share Unlock Avoidance Preregistered Study

## Scope and separation

This study tests a supply-shock risk mechanism rather than another positive
announcement drift: whether temporarily avoiding a currently held A-share after
a large confirmed restricted-share unlock improves benchmark-relative return.
Development is 2018-2024; 2025+ remains closed; future OOS starts 2026-08-18.
No model fitting, Shadow admission, short sale, or formal strategy change is
authorized.

## Structured source and PIT normalization

The sole event source is Tushare `share_float`: `ts_code`, `ann_date`,
`float_date`, `float_share`, `float_ratio`, `holder_name`, and `share_type`.
The endpoint is partitioned by actual unlock month and paginated. Atomic Parquet
partitions and a checksummed resumable manifest fail closed on tampering or
range leakage. Only SH/SZ A-shares are eligible.

One corporate unlock can contain thousands of holder rows and repeated schedule
versions. Normalization is frozen before return calculation:

1. keep rows with positive shares and `ann_date <= float_date`;
2. require the announcement to be no more than 30 calendar days before the
   actual unlock date, excluding stale plans and superseded dates;
3. within each stock, unlock date, and share type, use only the latest confirmed
   announcement snapshot;
4. exact duplicates are removed; blank holder names or conflicting duplicate
   holder rows invalidate that share-type tranche rather than being guessed;
5. sum valid holder rows across share types for one stock/unlock-date event;
6. divide unlocked shares by PIT total shares from the latest `daily_basic`
   observation no later than `float_date` (`total_share` is converted from ten
   thousand shares);
7. the recomputed ratio must be in `(0, 1]`; the summed reported ratio must be
   positive and differ by no more than one percentage point. Disagreement fails
   closed.

The event date is the actual `float_date`, not the first historical schedule.
Entry is the first eligible open strictly after that date, conservatively
excluding same-day unlock impact.

## Frozen families

- `large_unlock_avoidance`: confirmed aggregate unlock ratio >= 5%. This is the
  only candidate.
- `small_unlock_avoidance`: confirmed ratio > 0% and <= 1%. This is a diagnostic
  control only and can never become a candidate.
- Ratios between 1% and 5% are neutral and not studied.

## Avoidance return and horizons

The portfolio interpretation is a temporary substitution of benchmark exposure
for a stock that would otherwise be held. Gross avoidance active return is:

```text
benchmark return - stock return
```

Net avoidance return subtracts a 21 bps exit/re-entry round-trip cost; stress
cost is 31.5 bps. There is no assumed A-share short position. PIT HS300/ZZ500
membership is fixed at entry. Leaving the index does not shorten the outcome.
All horizons use the identical cohort with complete 5/20/60-session data. The
primary horizon is 20 sessions; 5 and 60 are diagnostics only.

## Evidence and economic gates

The candidate requires at least 100 mature events, 50 securities, four event
years, and 25 events in both scopes, with all 84 monthly partitions complete.
At 20 sessions it must simultaneously have positive mean and median net
avoidance return, positive stress-cost mean, positive means in both scopes, at
least two thirds positive years, year-cluster bootstrap P(mean > 0) >= 0.95,
and no year above 50% of total positive contribution.

Passing creates only a future-observation transparent baseline candidate.
Failure with sufficient evidence falsifies the avoidance hypothesis. The small
unlock family is always `diagnostic_only` and cannot affect the decision. No
model training is approved.
