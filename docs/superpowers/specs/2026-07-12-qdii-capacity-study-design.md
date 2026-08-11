# QDII Capacity Study Design

## Decision

Implement P2.0 as an offline, vectorized research pipeline over one immutable
historical panel. Do not replay the production provider once per historical
week: that would multiply Tushare requests, create date-keyed cache churn, and
make reproducibility depend on API timing. Do not introduce a generic quant
framework: the existing paper accounts have project-specific universe,
100-share lot, premium, execution, and audit rules that the study must match.

## Scope

The study evaluates the current defensive and offensive QDII overlays for
`us_exposure` and `hk_exposure` with `top_n` values `4, 5, 6, 8, 10`.
It writes research artifacts only and never changes active overlays, pending
orders, NAV ledgers, account cash, or the competition baseline.

P2.1 fund events and P2.2 global scopes remain separate follow-up releases.

## Data Contract

Input comes from the production QDII shared cache:

- `universe_latest.json` for code, scope, index, theme, listing date, fee, and
  current catalog provenance;
- the newest full `fund_daily_<code>_<as_of>.csv` for OHLCV and amount;
- the newest `fund_adj`, `fund_nav`, and `fund_share` cache when available;
- current competition and overlay JSON files for costs, accounts, factors, and
  risk controls.

Every coded identifier and date is loaded as text. The panel excludes dates
before each fund's listing date and never uses a row published after the signal
date. NAV/share values are backward-as-of joined. Missing premium or size stays
missing and is measured; it is never invented as zero.

The current catalog is not a historical membership archive. The report must
therefore label the result `current-catalog historical replay` and expose a
`survivorship_bias=true` limitation. It is acceptable for capacity research,
not sufficient alone for promotion.

## Components

### Historical Panel

`research_panel.py` discovers the latest cache per code, normalizes units,
merges adjustments/NAV/shares, and emits a daily long-form frame. It also
returns coverage and source metadata. The builder fails closed when the
universe snapshot or full daily history is absent.

### Walk-Forward Engine

`capacity_study.py` computes 20/60-day momentum, 60-day volatility, 20-day
liquidity, premium, size, fee, and peer tracking error using only data available
at each weekly signal. It applies the existing risk gates and factor pipeline,
then enforces one-index-one-seat before a relaxed fill.

Signals use each week's last trading day. Orders execute at the next available
open with five-basis-point slippage, 0.03% commission, 100-share lots, 2% cash
reserve, and the configured 20% single-name cap. Daily NAV is marked from
available closes. Benchmark funds remain `513100.SH` and `159920.SZ`.

### Capacity Output

For every strategy, scope, and `top_n`, write cumulative/annualized return,
volatility, Sharpe, maximum drawdown, benchmark excess, information ratio,
turnover, cost bps, average eligible count, index concentration, and effective
return-correlation clusters. Also write weekly selections and trades so any
metric can be traced back to a date and code.

Outputs:

- `data/cn_qdii_etf/research/capacity/<run_id>/summary.json`
- `data/cn_qdii_etf/research/capacity/<run_id>/metrics.csv`
- `data/cn_qdii_etf/research/capacity/<run_id>/selections.csv`
- `data/cn_qdii_etf/research/capacity/<run_id>/trades.csv`
- `reports/competition/research/qdii_capacity_<end_date>.md`

The report recommends a candidate `top_n` only when it improves diversification
without breaching liquidity, cost, drawdown, or minimum eligible-count gates.
The recommendation is research evidence, never an automatic baseline edit.

## CLI And Failure Behavior

Add `qdii-capacity-study` with `--start`, `--end`, `--top-n`, `--cache-dir`,
and `--output-root`. Default dates are the latest three years available and
default `top_n` is `4 5 6 8 10`.

The command exits non-zero for a missing universe, missing full histories,
fewer than 20 signal weeks, invalid date order, or an unavailable benchmark.
Partial optional NAV/share coverage is reported, not fatal. It must not make
network calls; preparation remains an explicit provider responsibility.

## Verification

Synthetic tests prove point-in-time filtering, no look-ahead joins, lot/cost
execution, one-index diversification, sensitivity differences, metric output,
and CLI failure modes. Production verification runs the command against the
ECS cache, checks at least three years or since-listing coverage, inspects the
generated report, confirms active config/pending hashes did not change, and
refreshes the Dashboard research payload only after the study succeeds.
