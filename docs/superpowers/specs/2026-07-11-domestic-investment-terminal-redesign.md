# Domestic Investment Terminal Redesign

Date: 2026-07-11

## Decision

The active product supports two investment accounts only:

1. `a_share`: mainland A-share paper trading.
2. `cn_qdii_etf`: mainland-listed cross-border ETF and QDII ETF paper trading.

Direct `hk` and `us` market simulations are retired from runtime dispatch,
dashboard navigation, sync automation, and operator commands. Their source and
historical artifacts remain available as an explicitly inactive archive so the
decision is reversible and prior audit trails remain readable.

This is a product-scope decision, not a claim that mainland investors have no
lawful Hong Kong access. Eligible mainland investors can use Stock Connect for
permitted Hong Kong securities. Direct overseas securities investment is not a
general-purpose domestic retail path, while QDII and mainland-listed
cross-border ETFs fit this system's requirement that the simulated instrument
can be bought through a mainland securities account.

## Research Direction

The interface borrows interaction principles, not visual copies, from:

- TradingView: interactive crosshair, price tooltips, candlesticks, volume, and
  normalized benchmark comparison.
- OpenBB Workspace: portfolio-first information hierarchy, linked widgets, and
  progressive disclosure from overview to raw data.
- KLineChart and TradingView Lightweight Charts: open-source, mobile-capable
  financial charting rather than hand-written SVG chart behavior.

Implementation uses TradingView Lightweight Charts because it is Apache-2.0,
small, React-compatible, and covers both performance lines and candlesticks.

## Information Architecture

The dashboard is a single professional workbench in this order:

1. **Account overview**: NAV, cumulative return, cash ratio, position count,
   next scheduled action, and runtime health.
2. **Performance comparison**: portfolio cumulative return against a normalized
   composite benchmark, with hover values and date-range controls.
3. **Portfolio**: exposure groups first, then individual holdings inside each
   group. Empty live portfolios show a clearly labelled planned allocation
   derived from pending orders without presenting it as an actual holding.
4. **Trade timeline**: events grouped by date. Completed buys and sells are
   visually distinct from future planned orders.
5. **Strategy brief**: structured, data-derived action, rationale, and risk
   rows. Raw weekly Markdown is not rendered inline; a full-report link remains.
6. **Runtime history**: a compact diagnostic table, collapsed by default.
7. **Target orders**: the detailed pending-order table appears last.

The dark visual language remains. The redesign uses a dense terminal grid,
cool charcoal surfaces, cyan selection, green/red market movement, tabular
numbers, restrained motion, 8px-or-smaller radii, and no decorative gradients
or marketing composition.

## Navigation Semantics

Replace ambiguous labels:

- `市场` becomes `账户范围`.
- `A股` becomes `A股组合`.
- `跨境ETF` becomes `全球ETF组合`.
- `Agent` becomes `策略模型`.
- `claude` and `codex` become `Claude 策略` and `Codex 策略`.

The selected strategy profile exposes its configured factor weights using
Chinese labels. This makes the account dimension (instrument universe) visibly
different from the strategy dimension (selection and weighting logic).

## Portfolio Grouping

Every row receives normalized display metadata:

- `exposure_group`: broad destination such as `美国市场` or `香港市场`.
- `theme`: underlying index/theme such as `纳斯达克100`, `标普500`,
  `恒生科技`, `港股红利`, or the A-share industry.
- `side_label`: `买入` / `卖出`.
- `account_label`: a readable account name.

Group totals include market value, portfolio weight, unrealized P&L, and item
count. The UI uses allocation bars and grouped rows instead of a flat list.

## Performance Benchmark

`daily_nav.csv` already stores per-account benchmark code and close. The detail
aggregator normalizes each account benchmark to its first available close and
combines account benchmark returns using first-date account NAV weights. The
result is `benchmark_return` on every NAV series point.

For a cross-border ETF portfolio, this produces a composite benchmark from
`513100.SH` and `159920.SZ`. The frontend compares percentage returns, not raw
prices, because the series have different units.

## Trade Timeline

The detail API returns a chronological activity stream built from:

- completed trades (`status=completed`), and
- pending orders (`status=planned`).

Each event includes date, code, name, side, shares, price or target value,
account/exposure labels, reason, and status. The frontend groups events by date
and places the latest or next event first.

## Instrument Detail API

Add:

`GET /api/dashboard/instrument.json?market=<market>&agent=<agent>&code=<code>`

The endpoint validates the market, agent, and code before filesystem access.
It never calls a network provider during a dashboard request.

For `cn_qdii_etf`, it reads the latest matching
`fund_daily_<code>_<as_of>.csv` cache. For `a_share`, it reads the latest
`history_<code>_<as_of>_<window>.csv` cache. Both normalize to:

```json
{
  "instrument": {
    "code": "513100.SH",
    "name": "国泰纳斯达克100ETF",
    "exposure_group": "美国市场",
    "theme": "纳斯达克100"
  },
  "latest": {
    "date": "2026-07-10",
    "open": 2.181,
    "high": 2.189,
    "low": 2.171,
    "close": 2.174,
    "change_pct": 0.0037,
    "volume": 1842337.41,
    "amount": 401729.423
  },
  "candles": [],
  "metrics": [],
  "related_trades": []
}
```

The endpoint returns at most 260 candles. Missing cache is a successful empty
state with a readable warning; malformed existing cache is an explicit API
error.

## Chinese Field Dictionary

The frontend has one typed dictionary for labels, formats, and explanations.
It covers at least:

- `pe`: 市盈率, current price divided by earnings; lower is usually cheaper but
  must be interpreted with growth and industry.
- `pb`: 市净率, current price divided by net assets.
- `roe`: 净资产收益率, profitability of shareholder equity.
- `gross_margin`: 毛利率, gross profit as a share of revenue.
- `debt_ratio`: 资产负债率, liabilities as a share of assets.
- `net_profit_growth`: 净利润增速.
- `dividend_yield`: 股息率.
- `momentum_20` / `momentum_60`: 20/60-session price momentum.
- `low_volatility_60`: 60-session volatility; lower is more stable.
- `avg_amount_20`: 20-session average turnover.
- `discount_premium`: ETF discount/premium versus NAV.

Raw keys may appear only in secondary monospace text, never as the primary
label.

## Instrument Drawer

Selecting a security opens a wide right-side research panel containing:

1. Chinese name, code, latest close, and daily change.
2. Candlestick chart with volume, crosshair, zoom, and hover OHLC values.
3. Related simulated buys and sells.
4. Chinese indicator values with plain-language definitions.
5. The selected row's transaction or position fields translated to Chinese.

Run-ledger rows continue to use the compact generic drawer, also translated.

## Runtime Archive

Active market dispatch becomes:

```python
MARKETS = ["a_share", "cn_qdii_etf"]
ARCHIVED_MARKETS = ["hk", "us"]
```

Archived markets are rejected by CLI parsing and dashboard APIs. Direct-market
routes disappear. `sync-to-ecs.sh` no longer syncs HK/US runtime state, and the
legacy overseas runner exits with an archive notice rather than fetching or
trading. Existing data, reports, configs, and source are not deleted.

This is a logical archive: code stays in Git history and on disk for audit, but
there is no supported runtime entrypoint.

## Error Handling

- Stale dashboard requests remain abortable and cannot overwrite a newer
  account or strategy selection.
- Instrument fetches have their own abort controller and request id.
- Missing price history renders an inline empty state.
- Malformed price history returns a generic API error without absolute paths.
- Chart rendering cleans up ResizeObserver and chart instances on selection or
  unmount.

## Verification

1. Unit tests prove only A-share and cross-border ETF markets are active.
2. Dashboard API tests cover composite benchmarks, grouped metadata, timeline,
   strategy profiles, instrument candles, validation, and missing history.
3. Frontend tests cover information order, account/strategy labels, grouped
   holdings, timeline, translated fields, instrument loading, and error states.
4. Full Python and frontend suites pass.
5. Production build has zero production dependency vulnerabilities.
6. ECS deployment records the final SHA, keeps QDII timers active, and returns
   HTTP 200 for the new instrument endpoint with real cached OHLCV.
7. Desktop and 390x844 browser checks verify hover/crosshair behavior, drawer
   navigation, no incoherent overlap, no page-level horizontal overflow, and no
   console errors.
