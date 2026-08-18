# Dividend Growth Preregistered Study

## Scope and separation

This study tests a capital-distribution and cash-quality mechanism independent
of earnings-announcement drift, buybacks, shareholder-count concentration, and
share unlocks: whether a material confirmed increase in annual cash dividends
creates positive post-implementation active return. Development is 2018-2024,
2025+ remains closed, and live OOS starts 2026-08-18. No model fitting, Shadow
admission, or formal strategy change is authorized.

## Structured source and PIT normalization

The sole source is Tushare `dividend`, queried by exact `imp_ann_date`. Only SH/SZ
A-shares with `div_proc` equal to implemented, annual report period ending
December 31, positive tax-inclusive cash dividend per share, positive base
shares, and an implementation announcement within the development window are
eligible. The implementation announcement date is the PIT availability date;
proposal, shareholder-meeting, record, ex-dividend, and payment dates are not
backdated into it.

Daily partitions are paginated, atomically written to Parquet, checksummed in a
resumable manifest, and validated fail-closed. Duplicate lifecycle source rows
are collapsed only when stock, fiscal year, implementation date, record date,
ex-date, pay date, per-share cash dividend, and base shares are identical. If a
stock/fiscal year retains more than one distinct implementation fact or has a
conflicting same-date amount, that fiscal year is ambiguous and excluded.

Implemented total cash distribution is fixed as:

```text
total_cash_yuan = cash_div_tax * base_share * 10000
```

`cash_div_tax` is yuan per share and `base_share` is ten-thousand shares. The
current annual fact requires the immediately prior fiscal year fact; no skipped
year or interpolation is allowed. The current event is available only on its own
implementation announcement date, and the previous fact must already have been
implemented by then.

Growth is:

```text
dividend_growth = current_total_cash / previous_total_cash - 1
```

Current dividend materiality is current total cash divided by PIT total market
value no later than the implementation announcement date. Tushare total market
value is converted from ten-thousand yuan to yuan. Missing denominators fail
closed.

## Frozen families

- `annual_dividend_growth`: growth >= 20% and current dividend / PIT market cap
  >= 1%. This is the sole positive candidate.
- `annual_dividend_cut`: growth <= -20%, with the same current 1% materiality
  floor. This is a long-side risk diagnostic only and cannot become a candidate.
- Changes between -20% and +20% are neutral and not studied.

## Execution, evidence, and gates

Entry is the first open strictly after the implementation announcement. PIT
HS300/ZZ500 scope is fixed at entry. All horizons use one complete 5/20/60-day
cohort. The primary horizon is 20 sessions; 5 and 60 are diagnostics. Active
return subtracts the matching benchmark, then 21 bps round-trip cost; stress cost
is 31.5 bps.

The candidate requires at least 100 mature events, 50 securities, four event
years, 25 events in both scopes, and a complete structured backfill. At 20 days
it must have positive mean, median, stress-cost mean, both-scope means, at least
two-thirds positive years, year-cluster bootstrap P(mean > 0) >= 0.95, and no
single year above 50% of positive contribution.

Passing creates only a future transparent-baseline candidate. Failure with
sufficient evidence falsifies the hypothesis. The cut family is always
`diagnostic_only`. No model training is approved.
