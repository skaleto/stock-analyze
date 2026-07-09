# CN QDII ETF Market Design

## Goal

Add a new paper-trading market, `cn_qdii_etf`, for mainland-listed cross-border ETFs and QDII ETFs that provide US/HK market exposure through domestic exchange products. This replaces the active need to simulate direct US/HK stocks while keeping the old HK/US implementations available as historical references.

## Scope

The MVP supports two domestic ETF accounts:

- `us_exposure`: US-facing QDII/cross-border ETFs such as Nasdaq 100 and S&P 500 ETFs.
- `hk_exposure`: HK-facing cross-border ETFs such as Hang Seng Tech, Hang Seng, internet, healthcare, and high-dividend ETFs.

The market is long-only, CNY-denominated, uses 100-share lots, uses zero stamp tax, and starts with weekly signals plus next-business-day execution. Cross-border ETF intraday/T+0 behavior is intentionally not enabled in the MVP because the existing run loop is weekly and next-day oriented.

## Architecture

`stock_analyze.markets.cn_qdii_etf` follows the existing HK/US market API:

- `make_provider(cache_dir, offline, as_of)`
- `build_signals(config, provider, as_of, repo_root)`
- `initialize(config, store)`
- `generate_rebalance_orders(config, store, provider, as_of, run_id)`
- `execute_due_orders(config, store, provider, as_of)`
- `update_nav(config, store, provider, as_of, notes)`

The provider uses Tushare fund APIs:

- `fund_basic(market="E")` for fund metadata.
- `fund_daily(ts_code=...)` for OHLCV and amount.
- `fund_nav(ts_code=...)` for NAV/discount factors when available.
- `fund_adj(ts_code=...)` is kept as a provider helper for future adjusted-return work.

The provider normalizes fund codes to Tushare exchange form (`513100.SH`, `159941.SZ`) and keeps them text-only in CSV/cache paths to preserve leading zeros.

## Data Flow

1. CLI resolves `--market cn_qdii_etf` through `competition.MARKETS`.
2. The merged config comes from `configs/competition_cn_qdii_etf.yaml` plus `configs/agents/<agent>_cn_qdii_etf.yaml`.
3. `provider.spot(scope)` resolves a static domestic ETF universe and returns one row per ETF with:
   - `code`, `name`, `trade_date`, `close`, `open`, `high`, `low`, `volume`, `amount`
   - `avg_amount_20`, `momentum_20`, `momentum_60`, `low_volatility_60`
   - `nav`, `nav_date`, `discount_premium`, `industry`
4. `strategy.build_signals` runs the shared factor pipeline over ETF-only factors.
5. The existing settlement simulator creates pending orders and updates NAV.

## Factors

The MVP factor set is ETF-native:

- `momentum_20`: high is better.
- `momentum_60`: high is better.
- `low_volatility_60`: low is better.
- `avg_amount_20`: high is better.
- `discount_premium`: low is better when NAV data is available.

No stock-fundamental factors (`pe`, `pb`, `roe`, etc.) are used for this market because they do not apply directly to exchange-traded funds.

## Error Handling

- Missing `TUSHARE_TOKEN` raises the same style of provider setup error as the A-share Tushare provider.
- Empty or failed fund history returns paused snapshots instead of crashing strategy generation.
- Offline mode reads provider cache only; cache misses raise a clear provider error.
- NAV factors are optional. If `fund_nav` is unavailable for a fund/date, `discount_premium` is left blank and the factor pipeline rescales coverage.

## Testing

Tests cover:

- Market dispatch and CLI parser accept `cn_qdii_etf`.
- Competition/agent configs load without baseline-lock errors.
- Provider normalization, factor math, NAV discount, execution quote slippage, and offline cache behavior.
- Strategy scoring emits per-account rows from mocked provider data.
- Simulator mechanics use 100-share lots, zero stamp tax, and persist NAV market value correctly.
- Dashboard/sync scripts include the new market namespace.

## Out Of Scope

- Real broker integration or real orders.
- Direct US/HK stock simulation changes.
- Intraday T+0 execution.
- Full historical backtest engine for ETF market.
- Automated systemd timers for this market until the first manual ECS run is verified.
