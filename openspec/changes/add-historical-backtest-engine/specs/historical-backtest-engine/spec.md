## ADDED Requirements

### Requirement: Engine exposes `run_backtest` library function

`stock_analyze.backtest.engine.run_backtest(overlay, start, end, universe, market_data_root, out_dir, *, in_memory=False) -> BacktestResult` SHALL drive a day-by-day historical simulation by reusing `stock_analyze.simulator` (`execute_due_orders`, `update_nav`, `generate_rebalance_orders`) with parameterized `as_of` clock and `data_root` / `market_data_root` paths.

The function SHALL:

- Load the historical trading calendar from `market_data_root/trade_cal.csv` and iterate every trading day in `[start, end]`.
- On each trading day `d` call (in order): `simulator.execute_due_orders(as_of=d, ...)`, `simulator.update_nav(as_of=d, ...)`, and on signal days (Friday or its trading-day-shifted equivalent) `simulator.generate_rebalance_orders(as_of=d, ...)`.
- Initialise a fresh `state.json` / `pending_orders.json` / `daily_nav.csv` / `trades.csv` under `out_dir` before the first iteration so the run is self-contained and never reads / writes forward-mode agent state.
- Return a `BacktestResult(out_dir, start, end, metrics)` whose `metrics` is a `BacktestMetrics` containing `cum_return`, `annual_return`, `sharpe`, `max_drawdown`, `information_ratio` derived from the produced `daily_nav.csv`.

#### Scenario: Engine drives simulator once per trading day for the whole window

- **GIVEN** an overlay valid against `overlay_guard`
- **AND** a `market_data_root` containing a `trade_cal.csv` with five open days from 2023-06-26 (Mon) through 2023-06-30 (Fri)
- **WHEN** `run_backtest(overlay, start=date(2023, 6, 26), end=date(2023, 6, 30), universe=['hs300', 'zz500'], market_data_root=cache, out_dir=out, in_memory=True)` is called
- **THEN** `simulator.execute_due_orders` is invoked exactly 5 times, once per trading day
- **AND** `simulator.update_nav` is invoked exactly 5 times, once per trading day
- **AND** `simulator.generate_rebalance_orders` is invoked exactly 1 time (on the Friday 2023-06-30 signal day)
- **AND** every call receives `as_of` set to that day's date plus `data_root=out_dir` and `market_data_root=cache`

#### Scenario: Engine writes the forward-simulation schema of output files

- **GIVEN** a 1-month mock dataset under `market_data_root` and a valid overlay
- **WHEN** `run_backtest` finishes
- **THEN** `out_dir/daily_nav.csv` exists with columns `date,account_id,cash,positions_value,total_value` (same schema as forward `daily_nav.csv`)
- **AND** `out_dir/trades.csv` exists with columns `date,account_id,ts_code,side,quantity,price,commission,stamp_tax,slippage` (same schema as forward `trades.csv`)
- **AND** `out_dir/signals.csv` exists holding the per-week top-N selection
- **AND** `out_dir/performance_summary.json` exists with the five `BacktestMetrics` fields

### Requirement: Engine respects point-in-time data visibility

The engine SHALL route every market-data read through `stock_analyze.backtest.data_view.PointInTimeView` so that at simulated time `t`:

- `daily` / `daily_basic` rows are restricted to `trade_date < t` (no current-day pre-open leakage).
- `fina_indicator` rows are restricted to `ann_date <= t` (announced fundamentals only).
- `index_weight` is the most recent monthly snapshot with `trade_date <= t`.
- `stock_basic` is filtered to `list_date <= t` and (`delist_date` is null or `delist_date > t`).

No future-dated row SHALL influence orders generated on `t`.

#### Scenario: No future leakage in fundamentals lookup

- **GIVEN** a `PointInTimeView(as_of=date(2024, 1, 1), cache_root=cache)`
- **AND** `fina_indicator/000001.SZ.csv` contains rows with `ann_date in {20230420, 20230820}`
- **WHEN** the engine asks for fundamentals of `000001.SZ` as of `date(2023, 4, 19)`
- **THEN** the returned dataframe is empty (the 20230420 row is not yet announced; the 20230820 row is future)

#### Scenario: Universe excludes stocks delisted before `t`

- **GIVEN** a universe call with `as_of=date(2023, 6, 30)`
- **AND** a stock with `delist_date=20230101`
- **WHEN** the engine asks the view for the active universe at `t=2023-06-30`
- **THEN** the delisted stock is not in the returned codes

### Requirement: Engine output schema matches forward simulation

The engine's output schema SHALL be byte-compatible with the forward simulator's so that reporting tooling, performance summary computation, and dashboard rendering reuse the same readers. Specifically:

- `daily_nav.csv` header equals the forward `daily_nav.csv` header verbatim.
- `trades.csv` header equals the forward `trades.csv` header verbatim.
- `performance_summary.json` carries the same five top-level metric keys (`cum_return`, `annual_return`, `sharpe`, `max_drawdown`, `information_ratio`).
- The engine SHALL NOT introduce a new column or alter the type of an existing column.

#### Scenario: Forward readers parse backtest outputs unchanged

- **GIVEN** a completed backtest under `out_dir`
- **WHEN** the existing forward-mode reporting reads `out_dir/daily_nav.csv` and `out_dir/trades.csv`
- **THEN** parsing succeeds with no schema mismatch error
- **AND** the produced performance summary contains the same five metric keys as the forward `performance_summary.json`

### Requirement: Engine does not mutate forward-mode agent state

The engine SHALL write all of its own state under `out_dir` only and SHALL NOT read from or write to `data/<agent>/state.json`, `data/<agent>/daily_nav.csv`, `data/<agent>/trades.csv`, `data/<agent>/positions.csv`, or `data/<agent>/pending_orders.json`. Market data reads use `market_data_root` (default `data/shared/backtest_cache/`); the forward-mode `data/shared/cache/` is not touched.

#### Scenario: Backtest run leaves forward state untouched

- **GIVEN** an existing `data/claude/state.json` with known content hash `H_state` and `data/claude/daily_nav.csv` with known content hash `H_nav`
- **WHEN** `run_backtest` finishes with `out_dir=data/claude/backtest/2026-05-25/`
- **THEN** `data/claude/state.json` still has content hash `H_state` (unchanged)
- **AND** `data/claude/daily_nav.csv` still has content hash `H_nav` (unchanged)
- **AND** all newly written files are under `out_dir`

### Requirement: Engine runs end-to-end on mock data and returns metrics

The engine SHALL run end-to-end without external network or external Tushare calls when given a self-contained mock `market_data_root` (covering `daily`, `daily_basic`, `fina_indicator`, `index_weight`, `stock_basic`, `trade_cal`). The returned `BacktestResult.metrics` SHALL be a `BacktestMetrics` instance with numeric (non-NaN) values for all five fields, even when the strategy is flat.

#### Scenario: One-month mock data backtest completes and returns finite metrics

- **GIVEN** a self-contained mock cache covering 2023-06-01 to 2023-06-30
- **AND** a valid overlay
- **WHEN** `run_backtest(overlay, start=date(2023, 6, 1), end=date(2023, 6, 30), universe=['hs300'], market_data_root=mock_cache, out_dir=out, in_memory=False)` is called
- **THEN** the call returns a `BacktestResult` whose `out_dir`, `start`, `end` match the inputs
- **AND** `result.metrics.cum_return`, `result.metrics.annual_return`, `result.metrics.sharpe`, `result.metrics.max_drawdown`, `result.metrics.information_ratio` are all finite floats (no NaN, no None)
