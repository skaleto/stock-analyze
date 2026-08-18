# Block Trade Premium Preregistered Study

## Scope and separation

This study tests a transaction-price information mechanism rather than another
corporate announcement: whether meaningful A-share block trades executed above
the same-day public close predict positive active returns. Development is
2018-2024, 2025+ remains closed, and live OOS starts 2026-08-18. No model, broker
order, Shadow admission, buyer/seller-name filter, or formal strategy change is
authorized.

## Structured source and PIT normalization

The sole event source is Tushare `block_trade`: stock, trade date, price, volume,
amount, buyer, and seller. Data are queried by exact trading date, paginated,
written as atomic daily Parquet partitions, checksummed in a resumable manifest,
and validated fail-closed. Only SH/SZ A-shares are eligible.

For each stock/trade date, all valid rows are aggregated before thresholding:

```text
block_vwap = sum(amount) / sum(volume)
premium = block_vwap / same_day_raw_close - 1
amount_market_cap_ratio = sum(amount) / same_day_PIT_total_mv
```

Tushare price is yuan/share; volume is ten-thousand shares; amount and total
market value are both ten-thousand yuan. Row-level
`abs(price * volume - amount)` must be no greater than the larger of 5
ten-thousand yuan and 0.1% of amount; otherwise the entire stock-day event is
excluded. Same-day close is the unadjusted public close from
the frozen materialization, never an adjusted price. Missing close, market cap,
non-positive price/volume/amount, date leakage, or material unit inconsistency
fails closed. Buyer and seller names are retained only for provenance and never
used to select events.

The block trade is known after that day's close. Entry is therefore the first
eligible open strictly after `trade_date`; same-day returns are not used.

## Frozen families

Both families require aggregate block amount / PIT market cap >= 0.1%.

- `block_trade_premium`: aggregate VWAP premium >= +2%. Sole candidate.
- `block_trade_discount`: aggregate VWAP discount <= -5%. Long-side diagnostic
  control only; it can never become a candidate.
- Events between -5% and +2% are neutral and not studied.

## Execution and gates

PIT HS300/ZZ500 membership is fixed at next-open entry. Every horizon uses an
identical complete 5/20/60-session cohort; 20 sessions is primary. Active return
is stock minus account benchmark, followed by 21 bps round-trip cost and 31.5
bps stress cost.

The candidate needs at least 100 mature events, 50 securities, four event years,
25 events in both scopes, and complete structured backfill. At 20 sessions it
must simultaneously have positive mean, median, stress mean, both-scope means,
at least two-thirds positive years, year-cluster bootstrap P(mean > 0) >= 0.95,
and no year above 50% of positive contribution.

Passing creates only a future-observation transparent baseline candidate.
Failure with sufficient evidence falsifies the hypothesis. The discount family
is always diagnostic-only. No model training is approved.
