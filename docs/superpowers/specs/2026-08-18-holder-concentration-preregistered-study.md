# Holder Concentration Preregistered Study

## Scope and separation

This is a new economic mechanism, independent of the falsified earnings-drift
and capital-actions hypotheses. It tests whether a material quarterly decline
in shareholder count, disclosed point in time, is followed by positive active
returns. Development is 2018-2024. The 2025+ historical diagnostic remains
closed and future OOS starts on 2026-08-18. No model fitting, Shadow admission,
or formal strategy change is authorized.

## Structured source and PIT normalization

The sole event source is Tushare `stk_holdernumber`: `ts_code`, `ann_date`,
`end_date`, and `holder_num`. The LLM announcement pipeline and existing model
predictions are not inputs. Data are backfilled by announcement month with
pagination, atomic Parquet partitions, a checksummed resumable manifest, and
fail-closed partition validation.

Only standard calendar quarter ends (March 31, June 30, September 30, December
31) with a positive shareholder count are used. For each stock and quarter end,
the earliest valid announcement is the PIT observation. If that earliest
stock-quarter announcement contains conflicting non-null counts, the quarter is
ambiguous and excluded rather than resolved with future knowledge.

An event requires the immediately preceding calendar quarter-end observation,
and that prior observation must have been announced no later than the current
one. The signed change is fixed as:

```text
holder_count_change = current_holder_num / previous_holder_num - 1
```

No interpolation or backdating is allowed. The event becomes available after
the close on its own `ann_date`.

### Pre-unblinding data-eligibility amendment

Before any forward return was calculated, structured-data QA found that some
pre-IPO quarter records contain only two to eight legal shareholders and then
jump to tens of thousands after listing. That is an IPO ownership transition,
not secondary-market holder dispersion. The frozen snapshot's Tushare
`stock_basic.list_date` is therefore used only as eligibility metadata: both
the current and immediately previous quarter end must be on or after the stock's
listing date. Missing list dates fail closed. The amendment was committed before
running the return study and does not change the +/-10% thresholds or any gate.

## Frozen event families

- `holder_concentration`: signed quarterly change <= -10%. This is the only
  positive candidate family.
- `holder_dispersion`: signed quarterly change >= +10%. This is a long-side
  risk diagnostic only; it is not treated as an A-share short portfolio and can
  never become a candidate.
- Changes between -10% and +10% are neutral and produce no event.

The thresholds are frozen before any forward-return calculation. Families may
not borrow evidence from one another.

## Execution and horizons

Entry is the first eligible open strictly after the announcement date. Account
scope is the PIT HS300 or ZZ500 membership at entry. Leaving an index after
entry does not shorten the holding period. Every horizon uses the identical
cohort with complete 5-, 20-, and 60-session outcomes. The primary horizon is
20 sessions; 5 and 60 sessions are secondary diagnostics and cannot rescue a
failed primary result.

Active return is stock return minus the matching account benchmark return.
Round-trip cost is 21 bps and stress cost is 1.5 times that amount.

## Candidate evidence gate

The concentration family requires at least 100 mature events, 50 unique
securities, four event years, and 25 events in both HS300 and ZZ500. The full
2018-2024 structured backfill must be complete. Below a floor, status is
`insufficient_data`, not a pass or a failure.

## Primary economic gate

At 20 sessions the concentration family must simultaneously have:

- positive mean net active return;
- positive median net active return;
- positive stress-cost mean active return;
- positive mean net active return in both account scopes;
- at least two thirds of event years positive;
- year-cluster bootstrap probability of a positive mean at least 95%;
- no single year above 50% of total positive contribution.

Passing creates only a `transparent_baseline_candidate` for future observation.
Failure with sufficient evidence falsifies this hypothesis. Dispersion output is
always `diagnostic_only`, has no pass/fail gate, and cannot affect the candidate
decision. No model training is approved by this protocol.
